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

  // AI Chat state for character generation
  const [charChatMessages, setCharChatMessages] = useState<CharChatMsg[]>([]);
  const [charChatInput, setCharChatInput] = useState("");
  const [charChatLoading, setCharChatLoading] = useState(false);
  const [showCharChat, setShowCharChat] = useState(false);
  const charChatEndRef = useRef<HTMLDivElement>(null);
  const charAbortRef = useRef<AbortController | null>(null);

  // Scroll to bottom on new messages
  useEffect(() => { charChatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [charChatMessages]);

  // Reset chat when switching characters
  useEffect(() => { setCharChatMessages([]); setShowCharChat(false); }, [editing?.id]);

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

      // Try to auto-apply JSON profile if present
      try {
        let jsonStr = aiContent;
        if (jsonStr.includes("```")) {
          jsonStr = jsonStr.split("```")[1]?.replace(/^json\s*\n?/, "") || jsonStr;
        }
        const profile = JSON.parse(jsonStr);
        if (profile.personality) u("personality", profile.personality);
        if (profile.background) u("background", profile.background);
        if (profile.speech_style) u("speech_style", profile.speech_style);
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
      trust_alpha: 5,
      trust_beta: 2,
      loyalty: 0.7,
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
                <div className="flex gap-6">
                  <button
                    className={showCharChat ? "btn-primary" : "btn"}
                    style={{ fontSize: 12 }}
                    onClick={() => setShowCharChat(!showCharChat)}
                  >
                    {showCharChat ? "收起 AI 对话" : "AI 角色助手"}
                  </button>
                  <button
                    className="btn-primary"
                    onClick={save}
                    disabled={!dirty}
                    style={{ opacity: dirty ? 1 : 0.5 }}
                  >
                    {dirty ? "保存" : "已保存"}
                  </button>
                </div>
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
                          <div style={{
                            maxWidth: "80%", padding: "8px 12px", borderRadius: 10,
                            background: msg.role === "user" ? "var(--purple-subtle)" : "var(--bg-surface-2)",
                            borderLeft: msg.role === "user" ? "none" : "3px solid var(--accent)",
                            borderRight: msg.role === "user" ? "3px solid var(--purple)" : "none",
                            fontSize: 13, lineHeight: 1.6, color: "var(--text-primary)", whiteSpace: "pre-wrap", wordBreak: "break-word",
                          }}>
                            {msg.content}
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

              <RelationshipGraph characters={items} currentId={editing.id} />

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
                  <p className="text-xs text-muted">对不同角色的信任度和忠诚度</p>
                </div>
                <div className="card-body">
                  {(editing.relationships || []).map(rel => {
                    const trustPct = ((rel.trust_alpha / (rel.trust_alpha + rel.trust_beta)) * 100).toFixed(0);
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
                          name={`信任 \u03B1 (${trustPct}%)`}
                          value={rel.trust_alpha}
                          min={1} max={20} step={1}
                          onChange={v => updateRel(rel.target_id, "trust_alpha", v)}
                        />
                        <ParamSlider
                          name="信任 \u03B2"
                          value={rel.trust_beta}
                          min={1} max={20} step={1}
                          onChange={v => updateRel(rel.target_id, "trust_beta", v)}
                        />
                        <ParamSlider
                          name="忠诚度"
                          value={rel.loyalty}
                          min={0} max={1} step={0.05}
                          onChange={v => updateRel(rel.target_id, "loyalty", v)}
                        />
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
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/* ---- Relationship Graph ---- */
function RelationshipGraph({ characters, currentId }: { characters: Character[]; currentId: string }) {
  const current = characters.find(c => c.id === currentId);
  if (!current) return null;

  const relatedIds = new Set((current.relationships || []).map(r => r.target_id));
  const visibleChars = characters.filter(c => c.id === currentId || relatedIds.has(c.id));
  if (visibleChars.length <= 1) return null;

  const W = 600, H = 200;
  const cx = W / 2, cy = H / 2;
  const others = visibleChars.filter(c => c.id !== currentId);
  const angleStep = (2 * Math.PI) / Math.max(others.length, 1);
  const radius = Math.min(W, H) * 0.35;

  const positions: Record<string, { x: number; y: number }> = { [currentId]: { x: cx, y: cy } };
  others.forEach((c, i) => {
    const angle = -Math.PI / 2 + i * angleStep;
    positions[c.id] = { x: cx + radius * Math.cos(angle), y: cy + radius * Math.sin(angle) };
  });

  return (
    <div className="card mb-20">
      <div className="card-header"><h3>关系图谱</h3></div>
      <div className="card-body" style={{ padding: 8 }}>
        <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: "block" }}>
          {(current.relationships || []).map(rel => {
            const from = positions[currentId];
            const to = positions[rel.target_id];
            if (!from || !to) return null;
            const trustPct = rel.trust_alpha / (rel.trust_alpha + rel.trust_beta);
            const color = trustPct > 0.6 ? "var(--jade)" : trustPct > 0.4 ? "var(--gold)" : "var(--accent)";
            return (
              <g key={rel.target_id}>
                <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} stroke={color} strokeWidth={2} opacity={0.5} />
                <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 6} textAnchor="middle" fontSize={9} fill="var(--text-tertiary)">
                  {rel.notes ? rel.notes.slice(0, 8) : `信任${(trustPct * 100).toFixed(0)}%`}
                </text>
              </g>
            );
          })}
          {visibleChars.map(c => {
            const pos = positions[c.id];
            if (!pos) return null;
            const isCurrent = c.id === currentId;
            const r = isCurrent ? 24 : 18;
            const fillColor = c.role === "主角" ? "var(--accent-subtle)" : c.role === "反派" ? "var(--purple-subtle)" : "var(--jade-subtle)";
            const strokeColor = isCurrent ? "var(--accent)" : "var(--border-hover)";
            return (
              <g key={c.id}>
                <circle cx={pos.x} cy={pos.y} r={r} fill={fillColor} stroke={strokeColor} strokeWidth={isCurrent ? 2 : 1} />
                <text x={pos.x} y={pos.y + 4} textAnchor="middle" fontSize={isCurrent ? 12 : 10} fontWeight={isCurrent ? 700 : 500} fill="var(--text-primary)">
                  {c.name.slice(0, 2)}
                </text>
                <text x={pos.x} y={pos.y + r + 14} textAnchor="middle" fontSize={9} fill="var(--text-tertiary)">
                  {c.name}
                </text>
              </g>
            );
          })}
        </svg>
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
