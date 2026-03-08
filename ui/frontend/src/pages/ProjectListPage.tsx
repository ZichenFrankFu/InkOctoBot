import React, { useEffect, useState, useCallback, useRef } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import type { Project } from "../api/types";

interface Props {
  activeProject: string;
  onSelectProject: (id: string) => void;
  onNavigate: (tab: string) => void;
}

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  tab: string;
  timestamp: number;
}

interface Snapshot {
  id: string;
  label: string;
  timestamp: number;
  messages: ChatMsg[];
  calibration: CalibrationState;
}

interface CalibrationState {
  tone: number;      // 0=轻松 100=严肃
  pacing: number;    // 0=快 100=慢
  perspective: string; // "first" | "third" | "omniscient"
  audience: string;   // "male" | "female" | "general"
}

type StudioTab = "outline" | "characters" | "world" | "calibration";

const STUDIO_TABS: { key: StudioTab; label: string; icon: string }[] = [
  { key: "outline", label: "大纲构思", icon: "📝" },
  { key: "characters", label: "角色设计", icon: "👥" },
  { key: "world", label: "世界观", icon: "🌐" },
  { key: "calibration", label: "校准", icon: "🎛️" },
];

const PLACEHOLDERS: Record<StudioTab, string> = {
  outline: "描述你的小说大纲想法...\n例：一个修仙世界中，主角意外获得上古传承...",
  characters: "描述你想设计的角色...\n例：主角是一个性格内向但天赋极高的少年...",
  world: "描述你的世界观设定...\n例：这个世界分为九大洲，修炼体系分为...",
  calibration: "",
};

const AI_RESPONSES: Record<StudioTab, string[]> = {
  outline: [
    "这是个很有潜力的设定！让我帮你梳理一下主线：\n\n1. **起点**：主角的初始状态和触发事件\n2. **发展**：第一个转折点和能力觉醒\n3. **高潮**：核心冲突的爆发\n\n你觉得主角的核心驱动力是什么？复仇、守护、还是探索？",
    "好的，我来帮你分析这个大纲的可行性。从商业网文的角度来看：\n\n- **爽点设计**：建议每 3-5 章一个小高潮\n- **金手指设定**：需要明确的成长体系\n- **冲突设计**：推荐「螺旋式升级」模式\n\n你想先深入哪个方面？",
  ],
  characters: [
    "角色设计建议：\n\n1. **性格矛盾点**：好的角色需要内在矛盾，比如外表冷漠但内心温柔\n2. **成长弧线**：从开始到结局，角色需要有明显变化\n3. **语言特征**：每个角色应该有独特的说话方式\n\n你想让这个角色的核心性格特质是什么？",
    "收到！让我帮你完善这个角色：\n\n- **外在形象**：需要一两个标志性特征\n- **内在动机**：角色行为的根本驱动力\n- **关系网络**：和其他角色的关系定位\n\n建议你先确定这个角色在故事中的「功能」——推动剧情、制造冲突、还是提供信息？",
  ],
  world: [
    "世界观构建建议：\n\n1. **核心规则**：这个世界最独特的运行法则是什么？\n2. **力量体系**：修炼/能力/科技的层级结构\n3. **社会结构**：势力分布和权力格局\n4. **历史背景**：影响当前格局的重大历史事件\n\n先确定核心规则，其他都可以围绕它展开。你的世界核心法则是什么？",
    "好的世界观！让我帮你检查一致性：\n\n- **经济逻辑**：资源分布和流通方式合理吗？\n- **战力天花板**：最强者能做到什么？做不到什么？\n- **普通人视角**：这个世界的普通人是怎么生活的？\n\n这些细节能让世界观更加可信。",
  ],
  calibration: [],
};

export default function ProjectListPage({ activeProject, onSelectProject, onNavigate }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formName, setFormName] = useState("");
  const [formGenre, setFormGenre] = useState("");

  // Studio state — restore from sessionStorage on mount
  const STUDIO_SESS_KEY = "inkocto_studio_state";
  const _savedStudio = (() => {
    try { const raw = sessionStorage.getItem(STUDIO_SESS_KEY); return raw ? JSON.parse(raw) : null; } catch { return null; }
  })();
  const [studioTab, setStudioTab] = useState<StudioTab>(_savedStudio?.studioTab || "outline");
  const [messages, setMessages] = useState<ChatMsg[]>(_savedStudio?.messages || []);
  const [input, setInput] = useState("");
  const [calibration, setCalibration] = useState<CalibrationState>(
    _savedStudio?.calibration || { tone: 50, pacing: 50, perspective: "third", audience: "general" },
  );

  const [aiLoading, setAiLoading] = useState(false);

  // Snapshots
  const [snapshots, setSnapshots] = useState<Snapshot[]>(_savedStudio?.snapshots || []);
  const [showSnapshots, setShowSnapshots] = useState(false);

  // Persist studio state to sessionStorage on changes
  useEffect(() => {
    sessionStorage.setItem(STUDIO_SESS_KEY, JSON.stringify({ studioTab, messages, calibration, snapshots }));
  }, [studioTab, messages, calibration, snapshots]);

  const rightPanel = useResizable({ direction: "horizontal", initialSize: 420, minSize: 320, maxSize: 700 });
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: Project[] }>("/api/data/projects");
      setProjects(r.items || []);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreate = async () => {
    if (!formName.trim()) return;
    const res = await apiPost<Project>("/api/data/projects", { name: formName.trim(), genre: formGenre.trim() || undefined });
    setFormName(""); setFormGenre(""); setShowForm(false);
    load();
    if (res?.id) onSelectProject(res.id);
  };

  const handleUpdate = async () => {
    if (!editingId || !formName.trim()) return;
    await apiPut(`/api/data/projects/${editingId}`, { name: formName.trim(), genre: formGenre.trim() || undefined });
    setEditingId(null); setFormName(""); setFormGenre(""); load();
  };

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("确定删除该项目？此操作不可撤销。")) return;
    await apiDelete(`/api/data/projects/${id}`);
    load();
  };

  const startEdit = (p: Project, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(p.id); setFormName(p.name); setFormGenre(p.genre || ""); setShowForm(false);
  };

  const cancelForm = () => {
    setShowForm(false); setEditingId(null); setFormName(""); setFormGenre("");
  };

  const sendMessage = async () => {
    if (!input.trim() || studioTab === "calibration" || aiLoading) return;
    const userMsg: ChatMsg = { role: "user", content: input.trim(), tab: studioTab, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    const userInput = input.trim();
    setInput("");
    setAiLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const systemHints: Record<string, string> = {
        outline: "你是一个专业的网文大纲策划专家。请根据用户的描述，提供具体、可操作的大纲策划建议。用中文回答，语气专业友好。",
        characters: "你是一个专业的角色设计专家。请根据用户的描述，提供详细的角色设计建议，包括性格、外貌、背景等。用中文回答，语气专业友好。",
        world: "你是一个专业的世界观构建专家。请根据用户的描述，提供系统的世界观设定建议，包括力量体系、社会结构等。用中文回答，语气专业友好。",
      };
      const resp = await fetch("/api/generation/quick-generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: activeProject || "default",
          chapter_id: "studio_chat",
          synopsis: userInput,
          system_hint: systemHints[studioTab] || systemHints.outline,
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
      const aiMsg: ChatMsg = { role: "assistant", content: data.text || "生成完成。", tab: studioTab, timestamp: Date.now() };
      setMessages(prev => [...prev, aiMsg]);
    } catch (e: any) {
      if (e?.name === "AbortError") {
        setMessages(prev => [...prev, { role: "assistant", content: "（已终止生成）", tab: studioTab, timestamp: Date.now() }]);
      } else {
        const errMsg = e?.message || "请求失败";
        setMessages(prev => [...prev, {
          role: "assistant",
          content: `抱歉，AI 暂时无法响应。\n\n${errMsg.slice(0, 500)}`,
          tab: studioTab,
          timestamp: Date.now(),
        }]);
      }
    }
    abortRef.current = null;
    setAiLoading(false);
  };

  const stopGeneration = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
  };

  const regenerateLastMessage = () => {
    // Find the last user message in the current tab and resend it
    const tabMsgs = messages.filter(m => m.tab === studioTab);
    const lastUserMsg = [...tabMsgs].reverse().find(m => m.role === "user");
    if (!lastUserMsg) return;
    // Remove the last assistant message for this tab
    setMessages(prev => {
      const reversed = [...prev].reverse();
      const idx = reversed.findIndex(m => m.tab === studioTab && m.role === "assistant");
      if (idx >= 0) {
        const newMsgs = [...prev];
        newMsgs.splice(prev.length - 1 - idx, 1);
        return newMsgs;
      }
      return prev;
    });
    setInput(lastUserMsg.content);
    // Auto-send after a tick
    setTimeout(() => {
      const el = document.querySelector(".studio-send-btn") as HTMLButtonElement;
      if (el) el.click();
    }, 100);
  };

  const saveSnapshot = () => {
    const snap: Snapshot = {
      id: `snap_${Date.now()}`,
      label: `快照 ${new Date().toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}`,
      timestamp: Date.now(),
      messages: [...messages],
      calibration: { ...calibration },
    };
    setSnapshots(prev => [snap, ...prev]);
  };

  const restoreSnapshot = (snap: Snapshot) => {
    setMessages(snap.messages);
    setCalibration(snap.calibration);
    setShowSnapshots(false);
  };

  const tabMessages = messages.filter(m => m.tab === studioTab);

  const formatDate = (dateVal?: string | number) => {
    if (!dateVal) return "--";
    // Handle Unix timestamp (number or numeric string)
    const d = typeof dateVal === "number" || (typeof dateVal === "string" && /^\d+(\.\d+)?$/.test(dateVal))
      ? new Date(Number(dateVal) * 1000)
      : new Date(dateVal);
    if (isNaN(d.getTime())) return "--";
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };

  return (
    <div className="page-full">
      <div className="panel-layout" style={{ height: "100%" }}>
        {/* Left: Project List */}
        <div className="panel flex-1" style={{ overflowY: "auto" }}>
          <div className="page-container">
            {/* Header */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
              <div>
                <h2 style={{ fontFamily: "var(--font-serif)", fontSize: 22, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
                  开书
                </h2>
                <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
                  创建和管理你的网文创作项目 · 点击卡片切换当前项目
                </p>
              </div>
              <button className="btn-primary" onClick={() => { setShowForm(true); setEditingId(null); setFormName(""); setFormGenre(""); }}>
                + 新建项目
              </button>
            </div>

            {/* Inline create/edit form */}
            {(showForm || editingId) && (
              <div className="card mb-24" style={{ animation: "slideUp 0.2s var(--ease-out)" }}>
                <div className="card-header"><h3>{editingId ? "编辑项目" : "新建项目"}</h3></div>
                <div className="card-body">
                  <div className="field-row" style={{ alignItems: "flex-end" }}>
                    <div className="field" style={{ flex: 2 }}>
                      <label className="label">项目名称</label>
                      <input className="input" value={formName} onChange={e => setFormName(e.target.value)} placeholder="例：星辰大海" autoFocus
                        onKeyDown={e => { if (e.key === "Enter") editingId ? handleUpdate() : handleCreate(); }} />
                    </div>
                    <div className="field" style={{ flex: 1 }}>
                      <label className="label">题材 / 类型</label>
                      <input className="input" value={formGenre} onChange={e => setFormGenre(e.target.value)} placeholder="例：玄幻、都市"
                        onKeyDown={e => { if (e.key === "Enter") editingId ? handleUpdate() : handleCreate(); }} />
                    </div>
                    <button className="btn-primary" onClick={editingId ? handleUpdate : handleCreate} disabled={!formName.trim()}>
                      {editingId ? "保存" : "创建"}
                    </button>
                    <button className="btn" onClick={cancelForm}>取消</button>
                  </div>
                </div>
              </div>
            )}

            {/* Project cards */}
            {loading ? (
              <div className="loading"><div className="loading-spinner" />加载中...</div>
            ) : projects.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">&#x1F4C2;</div>
                <h4>还没有项目</h4>
                <p>点击「新建项目」开始你的创作之旅</p>
              </div>
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 16 }}>
                {projects.map(p => {
                  const isActive = p.id === activeProject;
                  return (
                    <div
                      key={p.id}
                      className="card"
                      style={{
                        cursor: "pointer",
                        transition: "border-color 0.15s, box-shadow 0.2s, transform 0.2s var(--ease-out)",
                        borderColor: isActive ? "var(--accent)" : undefined,
                        boxShadow: isActive ? "0 0 12px var(--accent-glow)" : undefined,
                      }}
                      onClick={() => onSelectProject(p.id)}
                    >
                      <div style={{ height: 3, background: isActive ? "var(--accent)" : "var(--border)" }} />
                      <div className="card-body">
                        <div className="flex items-center justify-between mb-8">
                          <h3 className="font-serif" style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>{p.name}</h3>
                          <div className="flex gap-4 items-center">
                            {isActive && <span className="tag accent" style={{ fontSize: 10 }}>当前项目</span>}
                            {p.genre && <span className="tag category">{p.genre}</span>}
                          </div>
                        </div>
                        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 14 }}>
                          <div>
                            <div className="text-xs text-muted">总字数</div>
                            <div className="font-mono" style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}>{(p.word_count || 0).toLocaleString()}</div>
                          </div>
                          <div>
                            <div className="text-xs text-muted">章节数</div>
                            <div className="font-mono" style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)" }}>{p.chapter_count || 0}</div>
                          </div>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-muted">创建于 {formatDate(p.created_at)}</span>
                          <div className="flex gap-4">
                            <button className="btn-icon" title="编辑" onClick={e => startEdit(p, e)}>&#9998;</button>
                            <button className="btn-icon" title="进入编辑器" onClick={e => { e.stopPropagation(); onSelectProject(p.id); onNavigate("editor"); }}>&#8594;</button>
                            <button className="btn-icon" title="删除" onClick={e => handleDelete(p.id, e)} style={{ color: "var(--error)" }}>&#10005;</button>
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <div className="panel-resize-h" {...rightPanel.handleProps} />

        {/* Right: Creative Studio */}
        <div className="panel" style={{ width: rightPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderLeft: "1px solid var(--border)", display: "flex", flexDirection: "column" }}>
          <div className="panel-header" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
            <div className="flex items-center justify-between">
              <h3>创作工作室</h3>
              <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={() => { setMessages([]); }}>
                清空对话
              </button>
            </div>
            <div className="text-xs text-muted">
              {projects.find(p => p.id === activeProject)?.name || "未选择项目"} — 在这里构思你的小说
            </div>
          </div>

          {/* Studio tabs */}
          <div className="tab-bar-underline" style={{ flexShrink: 0 }}>
            {STUDIO_TABS.map(t => (
              <button key={t.key} className={`tab-item ${studioTab === t.key ? "active" : ""}`} onClick={() => setStudioTab(t.key)} style={{ fontSize: 12 }}>
                {t.icon} {t.label}
              </button>
            ))}
          </div>

          {/* Studio content */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {studioTab === "calibration" ? (
              /* Calibration panel */
              <div style={{ padding: 16, overflowY: "auto" }}>
                <div className="label mb-16" style={{ color: "var(--accent)" }}>小说风格校准</div>
                <div className="card mb-16">
                  <div className="card-body">
                    <div style={{ marginBottom: 20 }}>
                      <div className="flex items-center justify-between mb-4">
                        <span className="label" style={{ marginBottom: 0 }}>文风</span>
                        <span className="text-xs font-mono" style={{ color: "var(--text-tertiary)" }}>{calibration.tone < 30 ? "轻松幽默" : calibration.tone > 70 ? "严肃深沉" : "均衡"}</span>
                      </div>
                      <div className="flex items-center gap-8">
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 36 }}>轻松</span>
                        <input type="range" min={0} max={100} value={calibration.tone} onChange={e => setCalibration(prev => ({ ...prev, tone: +e.target.value }))} style={{ flex: 1, accentColor: "var(--accent)" }} />
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 36, textAlign: "right" }}>严肃</span>
                      </div>
                    </div>
                    <div style={{ marginBottom: 20 }}>
                      <div className="flex items-center justify-between mb-4">
                        <span className="label" style={{ marginBottom: 0 }}>节奏</span>
                        <span className="text-xs font-mono" style={{ color: "var(--text-tertiary)" }}>{calibration.pacing < 30 ? "快节奏" : calibration.pacing > 70 ? "慢节奏" : "中等"}</span>
                      </div>
                      <div className="flex items-center gap-8">
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 36 }}>快</span>
                        <input type="range" min={0} max={100} value={calibration.pacing} onChange={e => setCalibration(prev => ({ ...prev, pacing: +e.target.value }))} style={{ flex: 1, accentColor: "var(--accent)" }} />
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 36, textAlign: "right" }}>慢</span>
                      </div>
                    </div>
                    <div style={{ marginBottom: 20 }}>
                      <span className="label" style={{ marginBottom: 6, display: "block" }}>叙事视角</span>
                      <div className="flex gap-6">
                        {([["first", "第一人称"], ["third", "第三人称"], ["omniscient", "全知视角"]] as const).map(([val, label]) => (
                          <button key={val} className={calibration.perspective === val ? "btn-primary" : "btn"} style={{ flex: 1, fontSize: 12, padding: "6px 0", borderRadius: 20 }}
                            onClick={() => setCalibration(prev => ({ ...prev, perspective: val }))}>{label}</button>
                        ))}
                      </div>
                    </div>
                    <div>
                      <span className="label" style={{ marginBottom: 6, display: "block" }}>目标受众</span>
                      <div className="flex gap-6">
                        {([["male", "男频"], ["female", "女频"], ["general", "大众"]] as const).map(([val, label]) => (
                          <button key={val} className={calibration.audience === val ? "btn-primary" : "btn"} style={{ flex: 1, fontSize: 12, padding: "6px 0", borderRadius: 20 }}
                            onClick={() => setCalibration(prev => ({ ...prev, audience: val }))}>{label}</button>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
                <p className="text-xs text-muted" style={{ lineHeight: 1.6 }}>
                  校准参数将影响 AI 生成内容的风格倾向。调整后在编辑器的 Pipeline 生成中自动生效。
                </p>
              </div>
            ) : (
              /* Chat panel for outline/characters/world tabs */
              <>
                <div style={{ flex: 1, overflowY: "auto", padding: "12px 14px" }}>
                  {tabMessages.length === 0 && (
                    <div style={{ padding: "40px 16px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 13, lineHeight: 1.8 }}>
                      {PLACEHOLDERS[studioTab]}
                    </div>
                  )}
                  {aiLoading && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", marginBottom: 8 }}>
                      <div style={{ width: 30, height: 30, borderRadius: "50%", background: "var(--accent-subtle)", border: "2px solid var(--accent)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14 }}>🤖</div>
                      <div style={{ padding: "8px 12px", borderRadius: 10, background: "var(--bg-surface-2)", borderLeft: "3px solid var(--accent)", fontSize: 13, color: "var(--text-tertiary)" }}>
                        AI 正在思考中...
                      </div>
                    </div>
                  )}
                  {tabMessages.map((msg, i) => (
                    <div key={i} style={{ display: "flex", flexDirection: msg.role === "user" ? "row-reverse" : "row", alignItems: "flex-start", marginBottom: 12, gap: 8 }}>
                      <div style={{
                        width: 30, height: 30, borderRadius: "50%", flexShrink: 0,
                        background: msg.role === "user" ? "var(--purple-subtle)" : "var(--accent-subtle)",
                        border: `2px solid ${msg.role === "user" ? "var(--purple)" : "var(--accent)"}`,
                        display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14,
                      }}>
                        {msg.role === "user" ? "👤" : "🤖"}
                      </div>
                      <div style={{ maxWidth: "80%" }}>
                        <div style={{
                          padding: "8px 12px", borderRadius: 10,
                          background: msg.role === "user" ? "var(--purple-subtle)" : "var(--bg-surface-2)",
                          borderLeft: msg.role === "user" ? "none" : "3px solid var(--accent)",
                          borderRight: msg.role === "user" ? "3px solid var(--purple)" : "none",
                          fontSize: 13, lineHeight: 1.6, color: "var(--text-primary)", whiteSpace: "pre-wrap",
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
                </div>
                <div style={{ padding: "8px 14px 12px", borderTop: "1px solid var(--border)", flexShrink: 0 }}>
                  {/* Stop / Regenerate bar */}
                  {(aiLoading || tabMessages.length > 0) && (
                    <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                      {aiLoading && (
                        <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)", borderColor: "var(--error)" }} onClick={stopGeneration}>
                          ⏹ 终止生成
                        </button>
                      )}
                      {!aiLoading && tabMessages.length > 0 && tabMessages[tabMessages.length - 1].role === "assistant" && (
                        <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={regenerateLastMessage}>
                          ↻ 重新生成
                        </button>
                      )}
                    </div>
                  )}
                  <div className="flex gap-6">
                    <input className="input" value={input} onChange={e => setInput(e.target.value)}
                      onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
                      placeholder={PLACEHOLDERS[studioTab]?.split("\n")[0] || "输入你的想法..."} style={{ flex: 1, fontSize: 12 }} />
                    <button className="btn-primary studio-send-btn" onClick={sendMessage} disabled={!input.trim() || aiLoading} style={{ fontSize: 12, padding: "6px 14px" }}>
                      {aiLoading ? "思考中..." : "发送"}
                    </button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
