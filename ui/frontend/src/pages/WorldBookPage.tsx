import React, { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import { useToast } from "../components/shared/Toast";
import { useDialog } from "../components/shared/Dialog";
import type { WorldBookEntry, WorldBookCategory } from "../api/types";
import TagAutocomplete from "../components/shared/TagAutocomplete";
import { renderPrompt } from "../utils/promptTemplate";

interface Props {
  projectId: string;
  projects: any[];
}

const BUILTIN_CATEGORIES: { key: string; label: string; icon: string }[] = [
  { key: "power_system", label: "力量体系", icon: "\u26A1" },
  { key: "factions", label: "势力", icon: "\u2694" },
  { key: "geography", label: "地理", icon: "\u2295" },
  { key: "social_rules", label: "社会规则/习俗", icon: "\u2261" },
  { key: "history", label: "历史", icon: "\u229E" },
  { key: "hard_rules", label: "世界观规则", icon: "\u2500" },
  { key: "other", label: "其他", icon: "\u25CB" },
];

function catLabel(cat: string, customCats: { key: string; label: string }[]): string {
  return BUILTIN_CATEGORIES.find(c => c.key === cat)?.label
    || customCats.find(c => c.key === cat)?.label
    || cat;
}
function catIcon(cat: string): string {
  return BUILTIN_CATEGORIES.find(c => c.key === cat)?.icon || "\uD83D\uDCDD";
}

interface ConsistencyIssue {
  entry1: string;
  entry2: string;
  conflict: string;
  suggestion: string;
}

interface AIChatMsg {
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

export default function WorldBookPage({ projectId, projects }: Props) {
  const { toast } = useToast();
  const { confirm } = useDialog();
  const [items, setItems] = useState<WorldBookEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterCat, setFilterCat] = useState<string>("");
  const [editing, setEditing] = useState<WorldBookEntry | null>(null);
  const [dirty, setDirty] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checkIssues, setCheckIssues] = useState<ConsistencyIssue[]>([]);
  const [checkMessage, setCheckMessage] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [batchMode, setBatchMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // AI Assistant dialog state
  const [showAIChat, setShowAIChat] = useState(false);
  const [aiChatMessages, setAiChatMessages] = useState<AIChatMsg[]>([]);
  const [aiChatInput, setAiChatInput] = useState("");
  const [aiChatLoading, setAiChatLoading] = useState(false);
  const aiChatEndRef = useRef<HTMLDivElement>(null);
  const aiAbortRef = useRef<AbortController | null>(null);

  // Custom categories
  const CUSTOM_CAT_KEY = `inkocto_wb_custom_cats_${projectId}`;
  const [customCategories, setCustomCategories] = useState<{ key: string; label: string }[]>(() => {
    try { const raw = localStorage.getItem(CUSTOM_CAT_KEY); return raw ? JSON.parse(raw) : []; } catch { return []; }
  });
  const [showAddCat, setShowAddCat] = useState(false);
  const [newCatName, setNewCatName] = useState("");

  const addCustomCategory = () => {
    const name = newCatName.trim();
    if (!name) return;
    const key = `custom_${name.toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]/g, "_")}`;
    if (customCategories.some(c => c.key === key) || BUILTIN_CATEGORIES.some(c => c.key === key)) return;
    const updated = [...customCategories, { key, label: name }];
    setCustomCategories(updated);
    localStorage.setItem(CUSTOM_CAT_KEY, JSON.stringify(updated));
    setNewCatName("");
    setShowAddCat(false);
  };

  const removeCustomCategory = (key: string) => {
    const updated = customCategories.filter(c => c.key !== key);
    setCustomCategories(updated);
    localStorage.setItem(CUSTOM_CAT_KEY, JSON.stringify(updated));
  };

  const allCategories = useMemo(() => [
    ...BUILTIN_CATEGORIES,
    ...customCategories.map(c => ({ ...c, icon: "\u2022" })),
  ], [customCategories]);

  const leftPanel = useResizable({ direction: "horizontal", initialSize: 320, minSize: 240, maxSize: 450 });
  const projName = projects.find((p: any) => p.id === projectId)?.name || "未选择项目";

  // All existing tags for autocomplete
  const allTags = useMemo(() => {
    const tagSet = new Set<string>();
    items.forEach(i => (i.tags || []).forEach(t => tagSet.add(t)));
    return Array.from(tagSet);
  }, [items]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: WorldBookEntry[] }>(`/api/data/worldbook?project_id=${projectId}`);
      const newItems = r.items || [];
      setItems(newItems);
      setEditing(prev => prev ? newItems.find(i => i.id === prev.id) || prev : prev);
    } catch (e) { console.error(e); }
    setLoading(false);
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    let list = filterCat ? items.filter(i => i.category === filterCat) : items;
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(i =>
        i.title.toLowerCase().includes(q) ||
        (i.content || "").toLowerCase().includes(q) ||
        (i.tags || []).some(t => t.toLowerCase().includes(q))
      );
    }
    return list;
  }, [items, filterCat, search]);

  const catCounts = useMemo(() => {
    const m: Record<string, number> = {};
    items.forEach(i => { m[i.category] = (m[i.category] || 0) + 1; });
    return m;
  }, [items]);

  const create = async () => {
    try {
      const entry = await apiPost<WorldBookEntry>(`/api/data/worldbook`, {
        category: (filterCat as WorldBookCategory) || "power_system",
        title: "新条目", content: "", tags: [], project_id: projectId,
      });
      setItems([...items, entry]);
      setEditing(entry);
      setDirty(false);
    } catch (e: any) { toast(e?.message || "操作失败", "error"); }
  };

  const save = async () => {
    if (!editing) return;
    try {
      const updated = await apiPut<WorldBookEntry>(`/api/data/worldbook/${editing.id}`, editing);
      setDirty(false);
      setItems(prev => prev.map(item => item.id === editing.id ? { ...editing, ...updated } : item));
      setEditing({ ...editing, ...updated });
    } catch (e: any) { toast(e?.message || "操作失败", "error"); }
  };

  const remove = async (id: string) => {
    if (!(await confirm({ message: "确定删除该条目？", destructive: true }))) return;
    try {
      await apiDelete(`/api/data/worldbook/${id}`);
      if (editing?.id === id) setEditing(null);
      load();
    } catch (e: any) { toast(e?.message || "操作失败", "error"); }
  };

  const u = (key: string, val: any) => {
    if (!editing) return;
    setEditing({ ...editing, [key]: val } as WorldBookEntry);
    setDirty(true);
  };

  // Consistency check - returns table format
  const runConsistencyCheck = async () => {
    setChecking(true);
    setCheckIssues([]);
    setCheckMessage(null);
    try {
      const entries = items.map((e: any) => `[${catLabel(e.category, customCategories)}] ${e.title}: ${e.content || ""}`).join("\n");
      const resp = await apiPost<{ result: string; issues?: any[] }>("/api/worldbook/consistency-check", {
        project_id: projectId,
        entries_text: entries,
      });
      if (resp.issues && resp.issues.length > 0) {
        // Parse issues into table format
        const parsed: ConsistencyIssue[] = resp.issues.map((issue: any) => {
          if (typeof issue === "string") {
            return { entry1: "", entry2: "", conflict: issue, suggestion: "" };
          }
          return {
            entry1: issue.entry1 || issue.entries?.[0] || "",
            entry2: issue.entry2 || issue.entries?.[1] || "",
            conflict: issue.conflict || issue.description || issue,
            suggestion: issue.suggestion || issue.fix || "",
          };
        });
        setCheckIssues(parsed);
      } else {
        setCheckMessage(resp.result || "未发现明显矛盾，世界观设定一致性良好。");
      }
    } catch (e: any) {
      setCheckMessage("一致性检查失败：" + (e?.message || "请检查模型连接设置。"));
    }
    setChecking(false);
  };

  // AI Chat for worldbook
  useEffect(() => { aiChatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [aiChatMessages]);

  const sendAIChat = async () => {
    const msg = aiChatInput.trim();
    if (!msg || aiChatLoading || !editing) return;
    setAiChatMessages(prev => [...prev, { role: "user", content: msg, timestamp: Date.now() }]);
    setAiChatInput("");
    setAiChatLoading(true);

    const controller = new AbortController();
    aiAbortRef.current = controller;

    try {
      const systemHint = await renderPrompt(
        "assistant.worldbook",
        {
          entry_title: editing.title,
          category: catLabel(editing.category, customCategories),
          content: editing.content || "（空）",
        },
        `你是AI设定助手。当前正在编辑世界书条目「${editing.title}」（分类：${catLabel(editing.category, customCategories)}）。
已有内容：${editing.content || "（空）"}

帮助用户完善设定，回答设定相关问题。如果用户确认了某些内容，可以提供快速补全建议。
回答后主动追加一个追问来帮助用户进一步完善设定。
追问格式：在回答末尾加上 [FOLLOW_UP]追问内容[/FOLLOW_UP][OPTIONS]选项A|选项B|选项C[/OPTIONS]
用自然语言回答，不要使用JSON格式。`,
      );
      const resp = await fetch("/api/generation/quick-generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId || "default",
          chapter_id: "wb_chat",
          synopsis: msg,
          system_hint: systemHint,
        }),
        signal: controller.signal,
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      let content = data.text || "生成完成。";
      // Strip JSON wrapper if present
      try {
        const parsed = JSON.parse(content);
        if (parsed?.answer) content = parsed.answer;
      } catch { /* keep as-is */ }
      setAiChatMessages(prev => [...prev, { role: "assistant", content, timestamp: Date.now() }]);
    } catch (e: any) {
      if (e?.name === "AbortError") {
        setAiChatMessages(prev => [...prev, { role: "assistant", content: "（已终止生成）", timestamp: Date.now() }]);
      } else {
        setAiChatMessages(prev => [...prev, { role: "assistant", content: `生成失败：${(e?.message || "").slice(0, 300)}`, timestamp: Date.now() }]);
      }
    }
    aiAbortRef.current = null;
    setAiChatLoading(false);
  };

  return (
    <div className="page-full">
      <div className="panel-layout">
        {/* ══════ LEFT PANEL: Navigator (4.1) ══════ */}
        <div className="panel" style={{ width: leftPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderRight: "1px solid var(--border)" }}>
          <div className="panel-header" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
            <div className="flex items-center justify-between">
              <h3>世界书</h3>
              <div className="flex gap-4">
                <button className="btn" style={{ padding: "5px 10px", fontSize: 11 }}
                  onClick={() => { setBatchMode(!batchMode); setSelectedIds(new Set()); }}>
                  {batchMode ? "取消" : "批量"}
                </button>
                <button className="btn-primary" style={{ padding: "5px 12px", fontSize: 12 }} onClick={create}>
                  + 新建
                </button>
              </div>
            </div>
            <div className="text-xs text-muted">{projName}</div>
            {batchMode && selectedIds.size > 0 && (
              <div className="flex gap-4 mt-4">
                <button className="btn" style={{ fontSize: 11, flex: 1 }}
                  onClick={() => {
                    const selected = items.filter(i => selectedIds.has(i.id));
                    const blob = new Blob([JSON.stringify(selected, null, 2)], { type: "application/json" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url; a.download = `worldbook_${Date.now()}.json`;
                    document.body.appendChild(a); a.click(); document.body.removeChild(a);
                    URL.revokeObjectURL(url);
                  }}>
                  导出 ({selectedIds.size})
                </button>
                <button className="btn" style={{ fontSize: 11, flex: 1, color: "var(--error)" }}
                  onClick={async () => {
                    if (!(await confirm({ message: `确定删除 ${selectedIds.size} 个条目？`, destructive: true }))) return;
                    for (const id of selectedIds) {
                      await apiDelete(`/api/data/worldbook/${id}`).catch((e) => toast(e.message || "操作失败", "error"));
                    }
                    setSelectedIds(new Set()); setBatchMode(false); load();
                  }}>
                  删除 ({selectedIds.size})
                </button>
              </div>
            )}
          </div>

          {/* Search */}
          <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
            <input className="input" value={search} onChange={e => setSearch(e.target.value)}
              placeholder="搜索条目..." style={{ fontSize: 12 }} />
          </div>

          {/* Category filters */}
          <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", display: "flex", gap: 4, flexWrap: "wrap" }}>
            <button className={filterCat === "" ? "btn-primary" : "btn"}
              style={{ padding: "4px 10px", fontSize: 11, borderRadius: 12 }}
              onClick={() => setFilterCat("")}>
              全部 ({items.length})
            </button>
            {allCategories.map(c => (
              <button key={c.key}
                className={filterCat === c.key ? "btn-primary" : "btn"}
                style={{ padding: "4px 10px", fontSize: 11, borderRadius: 12 }}
                onClick={() => setFilterCat(filterCat === c.key ? "" : c.key)}>
                {c.icon} {c.label} ({catCounts[c.key] || 0})
              </button>
            ))}
            <button className="btn"
              style={{ padding: "4px 8px", fontSize: 11, borderRadius: 12, borderStyle: "dashed" }}
              onClick={() => setShowAddCat(!showAddCat)}>
              + 添加分类
            </button>
          </div>

          {/* Add custom category */}
          {showAddCat && (
            <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)", display: "flex", gap: 6 }}>
              <input className="input" value={newCatName} onChange={e => setNewCatName(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") addCustomCategory(); }}
                placeholder="输入新分类名称..." style={{ flex: 1, fontSize: 11, padding: "4px 8px" }} />
              <button className="btn-primary" style={{ fontSize: 11, padding: "4px 10px" }} onClick={addCustomCategory} disabled={!newCatName.trim()}>添加</button>
            </div>
          )}
          {customCategories.length > 0 && showAddCat && (
            <div style={{ padding: "4px 12px 8px", borderBottom: "1px solid var(--border)", fontSize: 10, color: "var(--text-tertiary)" }}>
              自定义分类：
              {customCategories.map(c => (
                <span key={c.key} style={{ marginLeft: 4, padding: "1px 6px", background: "var(--bg-surface-2)", borderRadius: 8, cursor: "pointer" }}
                  onClick={async () => { if (await confirm({ message: `删除自定义分类「${c.label}」？`, destructive: true })) removeCustomCategory(c.key); }}>
                  {c.label} &times;
                </span>
              ))}
            </div>
          )}

          {/* Consistency check button (4.1.1) */}
          <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
            <button className="btn w-full" onClick={runConsistencyCheck}
              disabled={checking || items.length === 0}
              style={{ justifyContent: "center" }}>
              {checking ? "检查中..." : "一致性检查"}
            </button>
          </div>

          {/* Entry list */}
          <div className="panel-body">
            {loading ? (
              <div className="loading"><div className="loading-spinner" /></div>
            ) : filtered.length === 0 ? (
              <div className="empty-state" style={{ padding: 32 }}>
                <p>{filterCat || search ? "没有匹配的条目" : "暂无条目"}</p>
              </div>
            ) : (
              filtered.map(entry => (
                <div key={entry.id}
                  className={`report-list-item ${editing?.id === entry.id ? "active" : ""}`}
                  onClick={() => {
                    if (batchMode) {
                      setSelectedIds(prev => {
                        const next = new Set(prev);
                        if (next.has(entry.id)) next.delete(entry.id); else next.add(entry.id);
                        return next;
                      });
                    } else {
                      setEditing(entry); setDirty(false);
                    }
                  }}>
                  {batchMode && (
                    <input type="checkbox" checked={selectedIds.has(entry.id)} readOnly
                      style={{ accentColor: "var(--accent)", flexShrink: 0 }} />
                  )}
                  <span className="report-icon">{catIcon(entry.category)}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="report-name" style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                      {entry.title}
                    </div>
                    <div className="text-xs text-muted truncate">
                      {catLabel(entry.category, customCategories)}
                      {entry.content ? ` \u00B7 ${entry.content.length > 40 ? entry.content.slice(0, 40) + "..." : entry.content}` : " \u00B7 (空)"}
                    </div>
                  </div>
                  {!batchMode && (
                    <button className="btn-icon" style={{ fontSize: 14 }}
                      onClick={e => { e.stopPropagation(); remove(entry.id); }}>&times;</button>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        <div className="panel-resize-h" {...leftPanel.handleProps} />

        {/* ══════ RIGHT PANEL: Entry Detail (4.2) ══════ */}
        <div className="panel flex-1" style={{ background: "var(--bg-app)", overflowY: "auto" }}>
          {/* Consistency check result as TABLE (4.1.1) */}
          {(checkIssues.length > 0 || checkMessage) && (
            <div style={{ margin: "16px 32px 0" }}>
              <div className="flex items-center justify-between mb-8">
                <span className="label" style={{ marginBottom: 0 }}>一致性检查结果</span>
                <button className="btn-icon" onClick={() => { setCheckIssues([]); setCheckMessage(null); }} style={{ fontSize: 12 }}>&times;</button>
              </div>
              {checkMessage ? (
                <div style={{
                  padding: "12px 16px", background: "var(--jade-subtle)", border: "1px solid var(--jade)",
                  borderRadius: "var(--radius-md)", fontSize: 13, color: "var(--jade)", lineHeight: 1.7,
                }}>
                  {checkMessage}
                </div>
              ) : (
                <div style={{ overflow: "auto" }}>
                  <table className="data-table" style={{ fontSize: 12 }}>
                    <thead>
                      <tr>
                        <th style={{ width: "15%" }}>条目 A</th>
                        <th style={{ width: "15%" }}>条目 B</th>
                        <th style={{ width: "40%" }}>冲突描述</th>
                        <th style={{ width: "30%" }}>修改建议</th>
                      </tr>
                    </thead>
                    <tbody>
                      {checkIssues.map((issue, i) => (
                        <tr key={i}>
                          <td style={{ fontWeight: 500, color: "var(--text-primary)" }}>{issue.entry1 || "--"}</td>
                          <td style={{ fontWeight: 500, color: "var(--text-primary)" }}>{issue.entry2 || "--"}</td>
                          <td style={{ color: "var(--error)" }}>{issue.conflict}</td>
                          <td style={{ color: "var(--text-secondary)" }}>{issue.suggestion || "--"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {!editing ? (
            <div className="empty-state" style={{ paddingTop: 120 }}>
              <h4>选择或创建一个条目</h4>
              <p>在左侧列表中选择条目进行编辑</p>
            </div>
          ) : (
            <div style={{ maxWidth: 720, margin: "0 auto", padding: "28px 32px 48px" }}>
              {/* Header with AI button */}
              <div className="flex items-center justify-between mb-24">
                <h2 className="font-serif" style={{ fontSize: 22, fontWeight: 700 }}>
                  {editing.title}
                </h2>
                <div className="flex gap-8">
                  <button className={showAIChat ? "btn-primary" : "btn"}
                    style={{ fontSize: 12 }} onClick={() => setShowAIChat(!showAIChat)}>
                    {showAIChat ? "收起 AI" : "AI 设定助手"}
                  </button>
                  <button className="btn-primary" onClick={save} disabled={!dirty}
                    style={{ opacity: dirty ? 1 : 0.5 }}>
                    {dirty ? "保存" : "已保存"}
                  </button>
                </div>
              </div>

              {/* AI Setting Assistant overlay (4.2.1) */}
              {showAIChat && (
                <div className="card mb-20" style={{ overflow: "hidden", border: "2px solid var(--indigo)", boxShadow: "0 4px 16px rgba(107,138,255,0.15)" }}>
                  <div className="card-header" style={{ background: "var(--indigo-subtle)" }}>
                    <div>
                      <h3 style={{ color: "var(--indigo)" }}>AI 设定助手</h3>
                      <div className="text-xs" style={{ color: "var(--text-tertiary)", marginTop: 2 }}>Agent: WorldBuilder</div>
                    </div>
                    <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                      onClick={() => setAiChatMessages([])}>清空</button>
                  </div>
                  <div style={{ maxHeight: 300, overflowY: "auto", padding: "12px 14px" }}>
                    {aiChatMessages.length === 0 && (
                      <div style={{ padding: "16px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 12, lineHeight: 1.6 }}>
                        与 AI 讨论设定，可选择快速补全或填入满意的内容。
                        <div className="flex gap-4 mt-8" style={{ justifyContent: "center", flexWrap: "wrap" }}>
                          {["帮我完善这个设定", "补充更多细节", "检查逻辑一致性"].map(h => (
                            <button key={h} className="btn" style={{ fontSize: 10, padding: "3px 10px", borderRadius: 12 }}
                              onClick={() => { setAiChatInput(h); }}>{h}</button>
                          ))}
                        </div>
                      </div>
                    )}
                    {aiChatMessages.map((msg, i) => (
                      <div key={i} style={{
                        display: "flex", flexDirection: msg.role === "user" ? "row-reverse" : "row",
                        alignItems: "flex-start", marginBottom: 10, gap: 8,
                      }}>
                        <div style={{
                          width: 26, height: 26, borderRadius: "50%", flexShrink: 0,
                          background: msg.role === "user" ? "var(--purple-subtle)" : "var(--indigo-subtle)",
                          border: `1.5px solid ${msg.role === "user" ? "var(--purple)" : "var(--indigo)"}`,
                          display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11,
                          color: msg.role === "user" ? "var(--purple)" : "var(--indigo)",
                        }}>
                          {msg.role === "user" ? "U" : "W"}
                        </div>
                        <div style={{ maxWidth: "80%", minWidth: 0 }}>
                          {msg.role === "assistant" && (
                            <div style={{ fontSize: 10, fontWeight: 600, color: "var(--indigo)", marginBottom: 2 }}>WorldBuilder</div>
                          )}
                          <div style={{
                            padding: "8px 12px",
                            borderRadius: msg.role === "user" ? "12px 4px 12px 12px" : "4px 12px 12px 12px",
                            background: msg.role === "user" ? "var(--purple-subtle)" : "var(--bg-surface-2)",
                            border: `1px solid ${msg.role === "user" ? "rgba(167,139,250,0.2)" : "var(--border)"}`,
                            fontSize: 12, lineHeight: 1.6, color: "var(--text-primary)",
                            whiteSpace: "pre-wrap", userSelect: "text",
                          }}>
                            {msg.content}
                          </div>
                        </div>
                      </div>
                    ))}
                    {aiChatLoading && (
                      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0" }}>
                        <div className="loading-spinner" style={{ width: 14, height: 14 }} />
                        <span className="text-xs text-muted">WorldBuilder 思考中...</span>
                      </div>
                    )}
                    <div ref={aiChatEndRef} />
                  </div>
                  <div style={{ padding: "8px 14px 12px", borderTop: "1px solid var(--border)" }}>
                    {aiChatLoading && (
                      <button className="btn mb-4" style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)" }}
                        onClick={() => { if (aiAbortRef.current) { aiAbortRef.current.abort(); aiAbortRef.current = null; } }}>
                        终止
                      </button>
                    )}
                    <div className="flex gap-4 mb-4" style={{ flexWrap: "wrap" }}>
                      {[
                        { label: "扩展设定", prompt: "帮我扩展这条世界观设定，增加更多细节" },
                        { label: "检查矛盾", prompt: "检查这条设定与已有世界观是否有矛盾" },
                        { label: "生成子条目", prompt: "基于这条设定，生成几个子条目/衍生设定" },
                      ].map(t => (
                        <button key={t.label} className="btn" style={{ fontSize: 10, padding: "2px 8px", borderRadius: 12 }}
                          onClick={() => setAiChatInput(t.prompt)}>
                          {t.label}
                        </button>
                      ))}
                    </div>
                    <div className="flex gap-6">
                      <textarea className="input" value={aiChatInput} onChange={e => setAiChatInput(e.target.value)}
                        onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendAIChat(); } }}
                        placeholder="讨论设定..." rows={1} style={{ flex: 1, fontSize: 12, minHeight: 32, maxHeight: 100, resize: "none" }} />
                      <button className="btn-primary" onClick={sendAIChat}
                        disabled={!aiChatInput.trim() || aiChatLoading}
                        style={{ fontSize: 12, padding: "6px 14px" }}>
                        {aiChatLoading ? "..." : "发送"}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {/* Entry info (4.2.2) */}
              <div className="card mb-20">
                <div className="card-header"><h3>条目信息</h3></div>
                <div className="card-body">
                  <div className="field mb-12">
                    <label className="label">标题</label>
                    <input className="input" value={editing.title}
                      onChange={e => u("title", e.target.value)} style={{ fontWeight: 600 }} />
                  </div>
                  <div className="field mb-12">
                    <label className="label">分类</label>
                    <div className="flex gap-6" style={{ flexWrap: "wrap" }}>
                      {allCategories.map(c => (
                        <button key={c.key}
                          className={editing.category === c.key ? "btn-primary" : "btn"}
                          style={{ padding: "6px 14px", fontSize: 12, borderRadius: 20 }}
                          onClick={() => u("category", c.key)}>
                          {c.icon} {c.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="field">
                    <label className="label">标签</label>
                    <TagAutocomplete
                      value={editing.tags || []}
                      onChange={(tags) => u("tags", tags)}
                      suggestions={allTags}
                      placeholder="输入标签..."
                    />
                  </div>
                </div>
              </div>

              {/* Content */}
              <div className="card">
                <div className="card-header"><h3>内容</h3></div>
                <div className="card-body">
                  <textarea className="input" value={editing.content}
                    onChange={e => u("content", e.target.value)}
                    rows={16} placeholder="在此输入世界观设定..."
                    style={{ fontFamily: "var(--font-serif)", lineHeight: 1.8, fontSize: 14, userSelect: "text" }} />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
