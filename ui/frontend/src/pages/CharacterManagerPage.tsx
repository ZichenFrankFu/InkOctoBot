import React, { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import type { Character, CharacterLayerB, CharacterRelationship } from "../api/types";

interface CharChatMsg {
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

interface Props {
  projectId: string;
  projects: any[];
}

const ROLES = ["主角", "重要配角", "配角", "反派", "导师", "路人"];

const DEFAULT_LAYER_B: CharacterLayerB = {
  loss_aversion: 2.5,
  risk_aversion_gain: 0.5,
  risk_aversion_loss: 0.5,
  impulse_probability: 0.3,
  social_frequency: 5,
  time_discount: 0.9,
  value_weights: {},
};

export default function CharacterManagerPage({ projectId, projects }: Props) {
  const [items, setItems] = useState<Character[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Character | null>(null);
  const [dirty, setDirty] = useState(false);
  const [search, setSearch] = useState("");
  const [relTarget, setRelTarget] = useState("");

  const leftPanel = useResizable({ direction: "horizontal", initialSize: 300, minSize: 220, maxSize: 420 });

  // AI Chat state for character generation — restore from sessionStorage
  const CHAR_CHAT_KEY = `inkocto_char_chat_${projectId}`;
  const _savedCharChat = (() => {
    try { const raw = sessionStorage.getItem(CHAR_CHAT_KEY); return raw ? JSON.parse(raw) : null; } catch { return null; }
  })();
  const [charChatMessages, setCharChatMessages] = useState<CharChatMsg[]>(_savedCharChat?.messages || []);
  const [charChatInput, setCharChatInput] = useState("");
  const [charChatLoading, setCharChatLoading] = useState(false);
  const [showCharChat, setShowCharChat] = useState(_savedCharChat?.show || false);
  const [charChatCharId, setCharChatCharId] = useState<string>(_savedCharChat?.charId || "");
  const charChatEndRef = useRef<HTMLDivElement>(null);
  const charAbortRef = useRef<AbortController | null>(null);

  // Scroll to bottom on new messages
  useEffect(() => { charChatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [charChatMessages]);

  const [charChatPersisted, setCharChatPersisted] = useState(false);
  const charChatSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Pending AI-generated profile awaiting user confirmation
  const [pendingProfile, setPendingProfile] = useState<{ personality?: string; background?: string; speech_style?: string } | null>(null);
  const [selectedFields, setSelectedFields] = useState<Set<string>>(new Set());

  // Persist char chat state to sessionStorage + backend
  useEffect(() => {
    sessionStorage.setItem(CHAR_CHAT_KEY, JSON.stringify({
      messages: charChatMessages, show: showCharChat, charId: editing?.id || "",
    }));
    // Debounced save to backend
    if (charChatPersisted && charChatMessages.length > 0) {
      if (charChatSaveTimer.current) clearTimeout(charChatSaveTimer.current);
      charChatSaveTimer.current = setTimeout(() => {
        apiPut("/api/data/chat_history", {
          project_id: projectId || "default", scope: "character_ai",
          messages: charChatMessages.slice(-200),
        }).catch(() => {});
      }, 2000);
    }
  }, [charChatMessages, showCharChat, editing?.id, CHAR_CHAT_KEY, charChatPersisted, projectId]);

  // Load chat from backend on mount
  useEffect(() => {
    const pid = projectId || "default";
    apiGet<{ messages: CharChatMsg[] }>(`/api/data/chat_history?project_id=${pid}&scope=character_ai`)
      .then(r => {
        if (r.messages && r.messages.length > 0 && charChatMessages.length === 0) {
          setCharChatMessages(r.messages);
        }
        setCharChatPersisted(true);
      })
      .catch(() => setCharChatPersisted(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Reset chat when switching to a different character (but not on remount with same char)
  useEffect(() => {
    if (editing?.id && editing.id !== charChatCharId) {
      setCharChatMessages([]); setShowCharChat(false); setCharChatCharId(editing.id);
    }
  }, [editing?.id]);

  const sendCharChatMessage = async (inputOverride?: string) => {
    const msg = (inputOverride || charChatInput).trim();
    if (!msg || charChatLoading || !editing) return;
    setCharChatMessages(prev => [...prev, { role: "user", content: msg, timestamp: Date.now() }]);
    setCharChatInput("");
    setCharChatLoading(true);

    const controller = new AbortController();
    charAbortRef.current = controller;

    try {
      const resp = await fetch("/api/generation/quick-generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId || "default",
          chapter_id: "char_chat",
          synopsis: msg,
          system_hint: `你是一个专业的小说角色设计师。当前正在设计角色「${editing.name}」（定位：${editing.role || "配角"}）。\n已有信息：\n- 性格：${editing.personality || "未设定"}\n- 背景：${editing.background || "未设定"}\n- 说话风格：${editing.speech_style || "未设定"}\n\n请根据用户的需求提供角色设计建议、润色人设、或生成新的角色信息。如果用户要求生成完整人设，请以 JSON 格式输出：{"personality":"...","background":"...","speech_style":"..."}。否则用自然语言回答。`,
        }),
        signal: controller.signal,
      });
      if (!resp.ok) {
        const text = await resp.text();
        let detail = "";
        try { detail = JSON.parse(text).detail || text; } catch { detail = text; }
        throw new Error(detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const aiContent = data.text || "生成完成。";
      setCharChatMessages(prev => [...prev, { role: "assistant", content: aiContent, timestamp: Date.now() }]);

      // Try to detect JSON profile and show confirmation dialog
      try {
        let jsonStr = aiContent;
        if (jsonStr.includes("```")) {
          jsonStr = jsonStr.split("```")[1]?.replace(/^json\s*\n?/, "") || jsonStr;
        }
        const profile = JSON.parse(jsonStr);
        const hasFields = profile.personality || profile.background || profile.speech_style;
        if (hasFields) {
          const fields: Record<string, string> = {};
          if (profile.personality) fields.personality = profile.personality;
          if (profile.background) fields.background = profile.background;
          if (profile.speech_style) fields.speech_style = profile.speech_style;
          setPendingProfile(fields);
          setSelectedFields(new Set(Object.keys(fields)));
        }
      } catch { /* not JSON, that's fine */ }
    } catch (e: any) {
      if (e?.name === "AbortError") {
        setCharChatMessages(prev => [...prev, { role: "assistant", content: "（已终止生成）", timestamp: Date.now() }]);
      } else {
        setCharChatMessages(prev => [...prev, { role: "assistant", content: `生成失败: ${(e?.message || "请检查模型连接").slice(0, 300)}`, timestamp: Date.now() }]);
      }
    }
    charAbortRef.current = null;
    setCharChatLoading(false);
  };

  const stopCharChat = () => { if (charAbortRef.current) { charAbortRef.current.abort(); charAbortRef.current = null; } };

  const regenerateCharChat = () => {
    const lastUser = [...charChatMessages].reverse().find(m => m.role === "user");
    if (!lastUser) return;
    setCharChatMessages(prev => {
      const reversed = [...prev].reverse();
      const idx = reversed.findIndex(m => m.role === "assistant");
      if (idx >= 0) { const n = [...prev]; n.splice(prev.length - 1 - idx, 1); return n; }
      return prev;
    });
    sendCharChatMessage(lastUser.content);
  };

  const projName = projects.find(p => p.id === projectId)?.name || "未选择项目";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: Character[] }>(`/api/data/characters?project_id=${projectId}`);
      // Defensive: ensure relationships is always an array (backend may return {} for legacy data)
      const fixed = (r.items || []).map(c => ({
        ...c,
        relationships: Array.isArray(c.relationships) ? c.relationships : [],
      }));
      setItems(fixed);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    if (!search.trim()) return items;
    const q = search.toLowerCase();
    return items.filter(c => c.name.toLowerCase().includes(q) || c.role?.toLowerCase().includes(q));
  }, [items, search]);

  const create = async () => {
    try {
      const c = await apiPost<Character>(`/api/data/characters`, {
        name: "新角色",
        role: "配角",
        project_id: projectId,
        tags: [],
        personality: "",
        background: "",
        speech_style: "",
        layer_b: DEFAULT_LAYER_B,
        relationships: [],
      });
      setItems([...items, c]);
      setEditing(c);
      setDirty(false);
    } catch (e) {
      console.error(e);
    }
  };

  const save = async () => {
    if (!editing) return;
    try {
      await apiPut(`/api/data/characters/${editing.id}`, editing);
      setDirty(false);
      load();
    } catch (e) {
      console.error(e);
    }
  };

  const remove = async (id: string) => {
    if (!confirm("确定删除该角色？")) return;
    try {
      await apiDelete(`/api/data/characters/${id}`);
      if (editing?.id === id) setEditing(null);
      load();
    } catch (e) {
      console.error(e);
    }
  };

  const u = (key: string, val: any) => {
    if (!editing) return;
    setEditing({ ...editing, [key]: val });
    setDirty(true);
  };

  const uLayerB = (key: keyof CharacterLayerB, val: number) => {
    if (!editing) return;
    setEditing({
      ...editing,
      layer_b: { ...(editing.layer_b || DEFAULT_LAYER_B), [key]: val },
    });
    setDirty(true);
  };

  // Relationships
  const others = useMemo(() => items.filter(c => editing && c.id !== editing.id), [items, editing]);

  const addRelationship = (targetId: string) => {
    if (!editing || !targetId) return;
    const target = items.find(c => c.id === targetId);
    if (!target) return;
    const rels = editing.relationships || [];
    if (rels.some(r => r.target_id === targetId)) return;
    const newRel: CharacterRelationship = {
      target_id: targetId,
      target_name: target.name,
      affinity: 50,
      priority: (rels.length || 0) + 2,
      chapter: "",
      notes: "",
    };
    setEditing({ ...editing, relationships: [...rels, newRel] });
    setDirty(true);
    setRelTarget("");
  };

  const updateRel = (targetId: string, key: string, val: any) => {
    if (!editing) return;
    setEditing({
      ...editing,
      relationships: (editing.relationships || []).map(r =>
        r.target_id === targetId ? { ...r, [key]: val } : r
      ),
    });
    setDirty(true);
  };

  const removeRel = (targetId: string) => {
    if (!editing) return;
    setEditing({
      ...editing,
      relationships: (editing.relationships || []).filter(r => r.target_id !== targetId),
    });
    setDirty(true);
  };

  return (
    <div className="page-full">
      <div className="panel-layout">
        {/* ======== LEFT PANEL: Character List ======== */}
        <div className="panel" style={{ width: leftPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderRight: "1px solid var(--border)" }}>
          <div className="panel-header" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
            <div className="flex items-center justify-between">
              <h3>角色管理</h3>
              <button className="btn-primary" style={{ padding: "5px 12px", fontSize: 12 }} onClick={create}>
                + 新建角色
              </button>
            </div>
            <div className="text-xs text-muted">{projName}</div>
          </div>

          {/* Search */}
          <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
            <input
              className="input"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="搜索角色..."
            />
          </div>

          {/* List */}
          <div className="panel-body">
            {loading ? (
              <div className="loading"><div className="loading-spinner" /></div>
            ) : filtered.length === 0 ? (
              <div className="empty-state" style={{ padding: 32 }}>
                <p>{search ? "没有匹配的角色" : "该项目暂无角色"}</p>
              </div>
            ) : (
              filtered.map(c => (
                <div
                  key={c.id}
                  className={`report-list-item ${editing?.id === c.id ? "active" : ""}`}
                  onClick={() => { setEditing(c); setDirty(false); }}
                >
                  <div
                    className="char-avatar"
                    style={{
                      background: c.role === "主角" ? "var(--accent-subtle)" : c.role === "反派" ? "var(--purple-subtle)" : "var(--jade-subtle)",
                      color: c.role === "主角" ? "var(--accent)" : c.role === "反派" ? "var(--purple)" : "var(--jade)",
                    }}
                  >
                    {c.name.charAt(0)}
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="truncate" style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
                      {c.name}
                    </div>
                    <div className="text-xs text-muted">
                      {c.role || "角色"}
                      {(c.relationships?.length || 0) > 0 ? ` \u00B7 ${c.relationships!.length}段关系` : ""}
                    </div>
                  </div>
                  <button
                    className="btn-icon"
                    style={{ fontSize: 14 }}
                    onClick={e => { e.stopPropagation(); remove(c.id); }}
                  >
                    &times;
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Resize handle */}
        <div className="panel-resize-h" {...leftPanel.handleProps} />

        {/* ======== RIGHT PANEL: Character Detail ======== */}
        <div className="panel flex-1" style={{ background: "var(--bg-app)", overflowY: "auto" }}>
          {!editing ? (
            <div className="empty-state" style={{ paddingTop: 120 }}>
              <h4>选择或创建一个角色</h4>
              <p>在左侧列表中选择角色，或点击「新建角色」</p>
            </div>
          ) : (
            <div style={{ maxWidth: 720, margin: "0 auto", padding: "28px 32px 48px" }}>
              {/* Header */}
              <div className="flex items-center justify-between mb-24">
                <h2 className="font-serif" style={{ fontSize: 22, fontWeight: 700 }}>
                  {editing.name}
                </h2>
                <button
                  className={showCharChat ? "btn-primary" : "btn"}
                  style={{ fontSize: 12 }}
                  onClick={() => setShowCharChat(!showCharChat)}
                >
                  {showCharChat ? "收起 AI 对话" : "AI 角色助手"}
                </button>
              </div>

              {/* AI Character Chat Panel */}
              {showCharChat && (
                <div className="card mb-20" style={{ overflow: "hidden" }}>
                  <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <h3>AI 角色助手</h3>
                    <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={() => { setCharChatMessages([]); }}>清空对话</button>
                  </div>
                  <div className="card-body" style={{ padding: 0 }}>
                    {/* Chat messages */}
                    <div style={{ maxHeight: 320, overflowY: "auto", padding: "12px 14px" }}>
                      {charChatMessages.length === 0 && (
                        <div style={{ padding: "24px 16px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 13, lineHeight: 1.8 }}>
                          与 AI 讨论角色设计，或直接要求生成完整人设。
                          <br />例：「帮我生成完整人设」「让这个角色更有深度」「加入悲惨的背景故事」
                        </div>
                      )}
                      {charChatMessages.map((msg, i) => (
                        <div key={i} style={{ display: "flex", flexDirection: msg.role === "user" ? "row-reverse" : "row", alignItems: "flex-start", marginBottom: 10, gap: 8 }}>
                          <div style={{
                            width: 28, height: 28, borderRadius: "50%", flexShrink: 0,
                            background: msg.role === "user" ? "var(--purple-subtle)" : "var(--accent-subtle)",
                            border: `2px solid ${msg.role === "user" ? "var(--purple)" : "var(--accent)"}`,
                            display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13,
                          }}>
                            {msg.role === "user" ? "👤" : "🤖"}
                          </div>
                          <div style={{ maxWidth: "80%" }}>
                            <div style={{
                              padding: "8px 12px", borderRadius: 10,
                              background: msg.role === "user" ? "var(--purple-subtle)" : "var(--bg-surface-2)",
                              borderLeft: msg.role === "user" ? "none" : "3px solid var(--accent)",
                              borderRight: msg.role === "user" ? "3px solid var(--purple)" : "none",
                              fontSize: 13, lineHeight: 1.6, color: "var(--text-primary)", whiteSpace: "pre-wrap", wordBreak: "break-word",
                              maxHeight: msg.content.length > 600 ? 250 : undefined,
                              overflowY: msg.content.length > 600 ? "auto" : undefined,
                            }}>
                              {msg.content}
                            </div>
                            <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px", marginTop: 2, color: "var(--text-tertiary)" }}
                              onClick={() => navigator.clipboard.writeText(msg.content)}>
                              复制
                            </button>
                          </div>
                        </div>
                      ))}
                      {charChatLoading && (
                        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px" }}>
                          <div style={{ width: 28, height: 28, borderRadius: "50%", background: "var(--accent-subtle)", border: "2px solid var(--accent)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13 }}>🤖</div>
                          <div style={{ padding: "8px 12px", borderRadius: 10, background: "var(--bg-surface-2)", borderLeft: "3px solid var(--accent)", fontSize: 13, color: "var(--text-tertiary)" }}>
                            AI 正在思考中...
                          </div>
                        </div>
                      )}
                      {/* Profile confirmation dialog */}
                      {pendingProfile && (
                        <div style={{
                          margin: "8px 12px", padding: "12px 14px", borderRadius: 10,
                          background: "var(--accent-subtle)", border: "1px solid var(--accent)",
                        }}>
                          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--accent)", marginBottom: 8 }}>
                            AI 生成了角色信息，选择要填入的字段：
                          </div>
                          {Object.entries(pendingProfile).map(([field, value]) => {
                            const labels: Record<string, string> = { personality: "性格描述", background: "背景故事", speech_style: "说话风格" };
                            const isSelected = selectedFields.has(field);
                            return (
                              <label key={field} style={{
                                display: "flex", alignItems: "flex-start", gap: 8, marginBottom: 8,
                                cursor: "pointer", fontSize: 12, color: "var(--text-primary)",
                              }}>
                                <input type="checkbox" checked={isSelected}
                                  onChange={() => setSelectedFields(prev => {
                                    const next = new Set(prev);
                                    if (next.has(field)) next.delete(field); else next.add(field);
                                    return next;
                                  })}
                                  style={{ accentColor: "var(--accent)", marginTop: 2 }}
                                />
                                <div style={{ flex: 1 }}>
                                  <div style={{ fontWeight: 600, marginBottom: 2 }}>{labels[field] || field}</div>
                                  <div style={{
                                    fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.5,
                                    maxHeight: 60, overflow: "hidden", textOverflow: "ellipsis",
                                  }}>
                                    {(value as string).slice(0, 150)}{(value as string).length > 150 ? "..." : ""}
                                  </div>
                                </div>
                              </label>
                            );
                          })}
                          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                            <button className="btn-primary" style={{ fontSize: 11, padding: "4px 14px" }}
                              disabled={selectedFields.size === 0}
                              onClick={() => {
                                if (pendingProfile) {
                                  for (const field of selectedFields) {
                                    const val = (pendingProfile as any)[field];
                                    if (val) u(field, val);
                                  }
                                }
                                setPendingProfile(null);
                                setSelectedFields(new Set());
                              }}>
                              填入选中字段
                            </button>
                            <button className="btn" style={{ fontSize: 11, padding: "4px 14px" }}
                              onClick={() => { setPendingProfile(null); setSelectedFields(new Set()); }}>
                              取消
                            </button>
                          </div>
                        </div>
                      )}
                      <div ref={charChatEndRef} />
                    </div>
                    {/* Controls */}
                    <div style={{ padding: "8px 14px 12px", borderTop: "1px solid var(--border)" }}>
                      {(charChatLoading || charChatMessages.some(m => m.role === "assistant")) && (
                        <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                          {charChatLoading && (
                            <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)", borderColor: "var(--error)" }} onClick={stopCharChat}>
                              ⏹ 终止生成
                            </button>
                          )}
                          {!charChatLoading && charChatMessages.length > 0 && charChatMessages[charChatMessages.length - 1].role === "assistant" && (
                            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={regenerateCharChat}>
                              ↻ 重新生成
                            </button>
                          )}
                        </div>
                      )}
                      <div style={{ display: "flex", gap: 6 }}>
                        <input className="input" value={charChatInput} onChange={e => setCharChatInput(e.target.value)}
                          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendCharChatMessage(); } }}
                          placeholder="描述你想要的角色特征..." style={{ flex: 1, fontSize: 12 }} />
                        <button className="btn-primary" onClick={() => sendCharChatMessage()} disabled={!charChatInput.trim() || charChatLoading} style={{ fontSize: 12, padding: "6px 14px" }}>
                          {charChatLoading ? "生成中..." : "发送"}
                        </button>
                      </div>
                      {/* Quick actions */}
                      <div style={{ display: "flex", gap: 4, marginTop: 6, flexWrap: "wrap" }}>
                        {["生成完整人设", "丰富背景故事", "设计说话风格", "增加性格矛盾点"].map(hint => (
                          <button key={hint} className="btn" style={{ fontSize: 10, padding: "2px 8px", borderRadius: 12 }}
                            onClick={() => sendCharChatMessage(hint)} disabled={charChatLoading}>
                            {hint}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* Basic Info */}
              <div className="card mb-20">
                <div className="card-header"><h3>基本信息</h3></div>
                <div className="card-body">
                  <div className="field mb-12">
                    <label className="label">姓名</label>
                    <input className="input" value={editing.name} onChange={e => u("name", e.target.value)} />
                  </div>
                  <div className="field mb-12">
                    <label className="label">角色定位</label>
                    <div className="flex gap-6" style={{ flexWrap: "wrap" }}>
                      {ROLES.map(r => (
                        <button
                          key={r}
                          className={editing.role === r ? "btn-primary" : "btn"}
                          style={{ padding: "5px 14px", fontSize: 12, borderRadius: 20 }}
                          onClick={() => u("role", r)}
                        >
                          {r}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="field">
                    <label className="label">标签（逗号分隔）</label>
                    <input
                      className="input"
                      value={(editing.tags || []).join(", ")}
                      onChange={e => u("tags", e.target.value.split(",").map(s => s.trim()).filter(Boolean))}
                      placeholder="例：剑修, 冷酷, 天才"
                    />
                  </div>
                </div>
              </div>

              {/* Layer A: Qualitative */}
              <div className="card mb-20">
                <div className="card-header"><h3>Layer A: 定性描述</h3></div>
                <div className="card-body">
                  <div className="field mb-12">
                    <label className="label">性格描述</label>
                    <textarea
                      className="input"
                      value={editing.personality || ""}
                      onChange={e => u("personality", e.target.value)}
                      rows={3}
                      placeholder="描述角色的核心性格特质..."
                    />
                  </div>
                  <div className="field mb-12">
                    <label className="label">背景故事</label>
                    <textarea
                      className="input"
                      value={editing.background || ""}
                      onChange={e => u("background", e.target.value)}
                      rows={4}
                      placeholder="角色的过往经历、成长环境、关键转折..."
                    />
                  </div>
                  <div className="field">
                    <label className="label">说话风格</label>
                    <textarea
                      className="input"
                      value={editing.speech_style || ""}
                      onChange={e => u("speech_style", e.target.value)}
                      rows={2}
                      placeholder="口头禅、语气特点、用词偏好..."
                    />
                  </div>
                </div>
              </div>

              {/* Layer B: Quantitative */}
              <div className="card mb-20">
                <div className="card-header"><h3>Layer B: 量化决策参数</h3></div>
                <div className="card-body">
                  <ParamSlider
                    name="损失厌恶"
                    value={editing.layer_b?.loss_aversion ?? DEFAULT_LAYER_B.loss_aversion}
                    min={0} max={5} step={0.1}
                    onChange={v => uLayerB("loss_aversion", v)}
                  />
                  <ParamSlider
                    name="风险厌恶(收益)"
                    value={editing.layer_b?.risk_aversion_gain ?? DEFAULT_LAYER_B.risk_aversion_gain}
                    min={0} max={1} step={0.05}
                    onChange={v => uLayerB("risk_aversion_gain", v)}
                  />
                  <ParamSlider
                    name="风险厌恶(损失)"
                    value={editing.layer_b?.risk_aversion_loss ?? DEFAULT_LAYER_B.risk_aversion_loss}
                    min={0} max={1} step={0.05}
                    onChange={v => uLayerB("risk_aversion_loss", v)}
                  />
                  <ParamSlider
                    name="冲动概率"
                    value={editing.layer_b?.impulse_probability ?? DEFAULT_LAYER_B.impulse_probability}
                    min={0} max={1} step={0.05}
                    onChange={v => uLayerB("impulse_probability", v)}
                  />
                  <ParamSlider
                    name="社交频率"
                    value={editing.layer_b?.social_frequency ?? DEFAULT_LAYER_B.social_frequency}
                    min={0} max={10} step={0.5}
                    onChange={v => uLayerB("social_frequency", v)}
                  />
                </div>
              </div>

              {/* Relationships */}
              <div className="card">
                <div className="card-header">
                  <h3>角色关系</h3>
                  <p className="text-xs text-muted">好感度与优先级</p>
                </div>
                <div className="card-body">
                  {(editing.relationships || []).map(rel => {
                    return (
                      <div
                        key={rel.target_id}
                        style={{
                          padding: 12,
                          background: "var(--bg-surface-2)",
                          borderRadius: "var(--radius-md)",
                          marginBottom: 10,
                          border: "1px solid var(--border)",
                        }}
                      >
                        <div className="flex items-center justify-between mb-8">
                          <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary)" }}>
                            &rarr; {rel.target_name || rel.target_id}
                          </span>
                          <button
                            className="btn-ghost"
                            style={{ fontSize: 12, padding: "2px 8px" }}
                            onClick={() => removeRel(rel.target_id)}
                          >
                            移除
                          </button>
                        </div>
                        <ParamSlider
                          name={`好感度 (${rel.affinity > 0 ? "+" : ""}${rel.affinity})`}
                          value={rel.affinity}
                          min={-100} max={100} step={5}
                          onChange={v => updateRel(rel.target_id, "affinity", v)}
                        />
                        <ParamSlider
                          name={`优先级 (#${rel.priority})`}
                          value={rel.priority}
                          min={1} max={20} step={1}
                          onChange={v => updateRel(rel.target_id, "priority", v)}
                        />
                        <div className="field mt-8">
                          <label className="label">时间/章节</label>
                          <input
                            className="input"
                            value={rel.chapter || ""}
                            onChange={e => updateRel(rel.target_id, "chapter", e.target.value)}
                            placeholder="例：第3章、银河历2847年·秋"
                          />
                        </div>
                        <div className="field mt-8">
                          <label className="label">关系备注</label>
                          <input
                            className="input"
                            value={rel.notes || ""}
                            onChange={e => updateRel(rel.target_id, "notes", e.target.value)}
                            placeholder="描述两人的关系..."
                          />
                        </div>
                      </div>
                    );
                  })}

                  {/* Add relationship */}
                  <div style={{ marginTop: 12 }}>
                    <div className="label mb-8">快速添加关系</div>
                    {others.filter(o => !(editing.relationships || []).some(r => r.target_id === o.id)).length > 0 ? (
                    <div className="flex gap-8">
                      <select
                        className="select"
                        style={{ flex: 1 }}
                        value={relTarget}
                        onChange={e => setRelTarget(e.target.value)}
                      >
                        <option value="">选择角色...</option>
                        {others
                          .filter(o => !(editing.relationships || []).some(r => r.target_id === o.id))
                          .map(o => (
                            <option key={o.id} value={o.id}>{o.name}</option>
                          ))
                        }
                      </select>
                      <button
                        className="btn-primary"
                        style={{ fontSize: 12 }}
                        onClick={() => addRelationship(relTarget)}
                        disabled={!relTarget}
                      >
                        + 添加关系
                      </button>
                    </div>
                    ) : (
                      <div className="text-xs text-muted">所有角色已添加关系</div>
                    )}
                  </div>
                </div>
              </div>

              {/* Relationship Graph — placed after add-relationship so user sees it immediately */}
              <RelationshipGraph characters={items} currentId={editing.id} />

              {/* Save button at bottom */}
              <div style={{ marginTop: 24, display: "flex", justifyContent: "flex-end" }}>
                <button
                  className="btn-primary"
                  onClick={save}
                  disabled={!dirty}
                  style={{ opacity: dirty ? 1 : 0.5, padding: "10px 32px", fontSize: 14 }}
                >
                  {dirty ? "保存角色" : "已保存"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---- Relationship Graph (all characters, directed edges) ---- */
function RelationshipGraph({ characters, currentId }: { characters: Character[]; currentId: string }) {
  if (characters.length <= 1) return null;

  const n = characters.length;

  // Dynamic sizing
  const W = Math.max(500, Math.min(1000, n * 140 + 200));
  const H = Math.max(350, Math.min(700, n <= 4 ? 380 : 280 + n * 45));
  const cx = W / 2, cy = H / 2;

  // Circular layout — all characters on the circle
  const radius = Math.min(W, H) * 0.35;
  const angleStep = (2 * Math.PI) / n;
  const startAngle = -Math.PI / 2;

  const positions: Record<string, { x: number; y: number }> = {};
  characters.forEach((c, i) => {
    const angle = startAngle + i * angleStep;
    positions[c.id] = {
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
    };
  });

  // Collect ALL directed edges from all characters
  const allEdges: { fromId: string; toId: string; affinity: number; priority: number; notes?: string; chapter?: string }[] = [];
  characters.forEach(c => {
    (c.relationships || []).forEach(rel => {
      if (positions[rel.target_id]) {
        allEdges.push({
          fromId: c.id,
          toId: rel.target_id,
          affinity: rel.affinity ?? 0,
          priority: rel.priority ?? 10,
          notes: rel.notes,
          chapter: rel.chapter,
        });
      }
    });
  });

  // Node radius based on name length
  const nodeR = (name: string) => Math.max(28, name.length * 7 + 8);

  // Arrow marker id
  const markerId = "rel-arrow";

  return (
    <div className="card mb-20" style={{ marginTop: 16 }}>
      <div className="card-header"><h3>全局关系图谱</h3></div>
      <div className="card-body" style={{ padding: 8 }}>
        <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: "block" }}>
          <defs>
            <marker id={markerId} markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L8,3 L0,6 Z" fill="var(--text-tertiary)" opacity="0.7" />
            </marker>
          </defs>

          {/* Directed edges */}
          {allEdges.map((edge, idx) => {
            const from = positions[edge.fromId];
            const to = positions[edge.toId];
            if (!from || !to) return null;

            const dx = to.x - from.x;
            const dy = to.y - from.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist === 0) return null;

            const ux = dx / dist, uy = dy / dist;
            const fromChar = characters.find(c => c.id === edge.fromId);
            const toChar = characters.find(c => c.id === edge.toId);
            const r1 = nodeR(fromChar?.name || "");
            const r2 = nodeR(toChar?.name || "");

            // Offset for parallel edges (A→B and B→A)
            const hasReverse = allEdges.some(e => e.fromId === edge.toId && e.toId === edge.fromId);
            const perpX = -uy * (hasReverse ? 6 : 0);
            const perpY = ux * (hasReverse ? 6 : 0);

            const x1 = from.x + ux * (r1 + 4) + perpX;
            const y1 = from.y + uy * (r1 + 4) + perpY;
            const x2 = to.x - ux * (r2 + 10) + perpX;
            const y2 = to.y - uy * (r2 + 10) + perpY;

            // Edge color based on affinity
            const color = edge.affinity > 50 ? "var(--jade)" : edge.affinity > 0 ? "var(--accent)" : edge.affinity > -50 ? "var(--gold)" : "var(--error)";
            const strokeW = Math.max(1, Math.min(3, 1 + Math.abs(edge.affinity) / 50));

            return (
              <g key={`${edge.fromId}-${edge.toId}-${idx}`}>
                <line x1={x1} y1={y1} x2={x2} y2={y2}
                  stroke={color} strokeWidth={strokeW} opacity={0.5}
                  markerEnd={`url(#${markerId})`} />
              </g>
            );
          })}

          {/* Character nodes — circles sized to fit full name */}
          {characters.map(c => {
            const pos = positions[c.id];
            if (!pos) return null;
            const isCurrent = c.id === currentId;
            const r = nodeR(c.name);
            const fillColor = c.role === "主角" ? "var(--accent-subtle)" : c.role === "反派" ? "var(--purple-subtle)" : "var(--jade-subtle)";
            const strokeColor = isCurrent ? "var(--accent)" : "var(--border-hover)";
            return (
              <g key={c.id}>
                <circle cx={pos.x} cy={pos.y} r={r} fill={fillColor} stroke={strokeColor} strokeWidth={isCurrent ? 3 : 1.5} />
                <text x={pos.x} y={pos.y + 5} textAnchor="middle" fontSize={12} fontWeight={isCurrent ? 700 : 500} fill="var(--text-primary)">
                  {c.name}
                </text>
              </g>
            );
          })}
        </svg>
        <div style={{ display: "flex", gap: 16, justifyContent: "center", marginTop: 8, fontSize: 10, color: "var(--text-tertiary)" }}>
          <span><span style={{ color: "var(--jade)" }}>&#9632;</span> 好感 &gt;50</span>
          <span><span style={{ color: "var(--accent)" }}>&#9632;</span> 好感 0~50</span>
          <span><span style={{ color: "var(--gold)" }}>&#9632;</span> 好感 -50~0</span>
          <span><span style={{ color: "var(--error)" }}>&#9632;</span> 好感 &lt;-50</span>
        </div>
      </div>
    </div>
  );
}

/* ---- Param Slider ---- */
function ParamSlider({
  name,
  value,
  min,
  max,
  step,
  onChange,
}: {
  name: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="param-slider">
      <span className="param-name">{name}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(+e.target.value)}
      />
      <span className="param-value">{value.toFixed(step < 1 ? 2 : 0)}</span>
    </div>
  );
}
