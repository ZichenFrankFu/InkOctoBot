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

interface CalibrationState {
  tone: number;      // 0=轻松 100=严肃
  pacing: number;    // 0=快 100=慢
  perspective: string; // "first" | "third" | "omniscient"
  audience: string;   // "male" | "female" | "general"
}

type StudioTab = "trending" | "brainstorm" | "calibration";

const STUDIO_TABS: { key: StudioTab; label: string; icon: string; desc: string }[] = [
  { key: "trending", label: "热门题材", icon: "🔥", desc: "浏览市场趋势" },
  { key: "brainstorm", label: "头脑风暴", icon: "💡", desc: "AI 创意构思" },
  { key: "calibration", label: "风格校准", icon: "🎛️", desc: "确认写作风格" },
];

const SAMPLE_TYPES: { key: string; label: string }[] = [
  { key: "opening", label: "开篇" },
  { key: "dialogue", label: "对话" },
  { key: "action", label: "动作" },
  { key: "inner", label: "心理" },
];

export default function ProjectListPage({ activeProject, onSelectProject, onNavigate }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formName, setFormName] = useState("");
  const [formGenre, setFormGenre] = useState("");

  // Studio state — restore from sessionStorage + backend on mount
  const STUDIO_SESS_KEY = "inkocto_studio_state";
  const _savedStudio = (() => {
    try { const raw = sessionStorage.getItem(STUDIO_SESS_KEY); return raw ? JSON.parse(raw) : null; } catch { return null; }
  })();
  const [studioTab, setStudioTab] = useState<StudioTab>(_savedStudio?.studioTab || "trending");
  const [messages, setMessages] = useState<ChatMsg[]>(_savedStudio?.messages || []);
  const [input, setInput] = useState("");
  const [calibration, setCalibration] = useState<CalibrationState>(
    _savedStudio?.calibration || { tone: 50, pacing: 50, perspective: "third", audience: "general" },
  );
  const [chatLoaded, setChatLoaded] = useState(false);

  const [aiLoading, setAiLoading] = useState(false);

  // Trending data
  const [trendingTags, setTrendingTags] = useState<{ tag_name: string; novel_count: number }[]>([]);
  const [trendingLoading, setTrendingLoading] = useState(false);

  // Calibration sample
  const [sampleType, setSampleType] = useState("opening");
  const [calibrationSample, setCalibrationSample] = useState("");
  const [sampleLoading, setSampleLoading] = useState(false);

  // Persist studio state to sessionStorage on changes
  useEffect(() => {
    sessionStorage.setItem(STUDIO_SESS_KEY, JSON.stringify({ studioTab, messages, calibration }));
  }, [studioTab, messages, calibration]);

  // Backend persistence for chat (debounced)
  const chatSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (chatLoaded && messages.length > 0) {
      if (chatSaveTimer.current) clearTimeout(chatSaveTimer.current);
      chatSaveTimer.current = setTimeout(() => {
        apiPut("/api/data/chat_history", {
          project_id: activeProject || "default", scope: "studio",
          messages: messages.slice(-200),
        }).catch(() => {});
      }, 2000);
    }
  }, [messages, chatLoaded, activeProject]);

  // Load chat from backend on mount
  useEffect(() => {
    const pid = activeProject || "default";
    apiGet<{ messages: ChatMsg[] }>(`/api/data/chat_history?project_id=${pid}&scope=studio`)
      .then(r => {
        if (r.messages && r.messages.length > 0 && messages.length === 0) {
          setMessages(r.messages);
        }
        setChatLoaded(true);
      })
      .catch(() => setChatLoaded(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProject]);

  const rightPanel = useResizable({ direction: "horizontal", initialSize: 440, minSize: 340, maxSize: 720 });
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

  // Load trending tags
  useEffect(() => {
    if (studioTab === "trending" && trendingTags.length === 0) {
      setTrendingLoading(true);
      apiGet<{ rows: { tag_name: string; novel_count: number }[] }>("/api/rankings/tag_stats?limit=30")
        .then(r => setTrendingTags(r.rows || []))
        .catch(() => {})
        .finally(() => setTrendingLoading(false));
    }
  }, [studioTab, trendingTags.length]);

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
    if (!input.trim() || aiLoading) return;
    const userMsg: ChatMsg = { role: "user", content: input.trim(), tab: studioTab, timestamp: Date.now() };
    setMessages(prev => [...prev, userMsg]);
    const userInput = input.trim();
    setInput("");
    setAiLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const systemHint = "你是一个顶级网文创作顾问，精通大纲策划、角色设计、世界观构建、市场趋势分析。请根据用户的描述，提供具体、可操作的创作建议。涵盖剧情结构、人物塑造、力量体系、读者心理等各个方面。用中文回答，语气专业友好，善用结构化排版。";
      const resp = await fetch("/api/generation/quick-generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: activeProject || "default",
          chapter_id: "studio_chat",
          synopsis: userInput,
          system_hint: systemHint,
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

  const generateCalibrationSample = async () => {
    setSampleLoading(true);
    setCalibrationSample("");
    try {
      const toneDesc = calibration.tone < 30 ? "轻松幽默" : calibration.tone > 70 ? "严肃深沉" : "均衡";
      const pacingDesc = calibration.pacing < 30 ? "快节奏" : calibration.pacing > 70 ? "慢节奏" : "中等节奏";
      const perspDesc = calibration.perspective === "first" ? "第一人称" : calibration.perspective === "third" ? "第三人称" : "全知视角";
      const typeDesc: Record<string, string> = {
        opening: "小说开篇（300-500字）",
        dialogue: "一段对话场景（2-3个角色互动）",
        action: "一段动作/打斗场景",
        inner: "一段内心独白/心理描写",
      };
      const prompt = `请生成一段${typeDesc[sampleType] || "短文"}的校准样本。\n\n风格要求：\n- 文风：${toneDesc}\n- 节奏：${pacingDesc}\n- 视角：${perspDesc}\n- 目标受众：${calibration.audience === "male" ? "男频" : calibration.audience === "female" ? "女频" : "大众"}\n\n请直接输出样本内容，不需要解释。`;

      const resp = await apiPost<{ text: string }>("/api/generation/quick-generate", {
        project_id: activeProject || "default",
        chapter_id: "calibration_sample",
        synopsis: prompt,
        system_hint: "你是一个专业的网文写手。根据给定的风格参数生成校准样本段落。直接输出文本内容，不要加标题或说明。",
      });
      setCalibrationSample(resp.text || "");
    } catch (e: any) {
      setCalibrationSample(`生成失败：${e?.message || "请检查模型连接"}`);
    }
    setSampleLoading(false);
  };

  const tabMessages = messages.filter(m => m.tab === studioTab);

  const formatDate = (dateVal?: string | number) => {
    if (!dateVal) return "--";
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
          <div className="panel-header" style={{ flexDirection: "column", alignItems: "stretch", gap: 6, paddingBottom: 8 }}>
            <div className="flex items-center justify-between">
              <h3 style={{ fontSize: 15, fontWeight: 700 }}>创作工作室</h3>
              {studioTab === "brainstorm" && (
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={() => { setMessages([]); }}>
                  清空对话
                </button>
              )}
            </div>
            <div className="text-xs text-muted">
              {projects.find(p => p.id === activeProject)?.name || "未选择项目"}
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
            {studioTab === "trending" ? (
              /* Trending topics panel */
              <div style={{ padding: 16, overflowY: "auto", flex: 1 }}>
                <div style={{ padding: "12px 14px", background: "var(--accent-subtle)", borderRadius: 8, marginBottom: 16, borderLeft: "3px solid var(--accent)" }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent)", marginBottom: 4 }}>浏览市场趋势</div>
                  <div className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
                    了解当前读者偏好和热门题材，为你的创作找到最佳切入点。点击任意标签可直接用于新项目的题材设定。
                  </div>
                </div>

                {trendingLoading ? (
                  <div className="loading"><div className="loading-spinner" />加载市场数据...</div>
                ) : trendingTags.length === 0 ? (
                  <div style={{ padding: "24px 16px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 13, lineHeight: 1.8 }}>
                    暂无市场数据。请先在「市场数据库」中导入排行榜数据。
                  </div>
                ) : (
                  <>
                    <div className="label mb-8" style={{ fontSize: 11, color: "var(--text-tertiary)", letterSpacing: 1 }}>热门题材标签 TOP {trendingTags.length}</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                      {trendingTags.map((tag, i) => {
                        const isTop = i < 5;
                        const size = Math.max(12, 18 - Math.floor(i / 3));
                        return (
                          <button
                            key={tag.tag_name}
                            onClick={() => { setFormGenre(tag.tag_name); setShowForm(true); }}
                            style={{
                              padding: "6px 14px", borderRadius: 20,
                              border: `1px solid ${isTop ? "var(--accent)" : "var(--border)"}`,
                              background: isTop ? "var(--accent-subtle)" : "var(--bg-surface-2)",
                              color: isTop ? "var(--accent)" : "var(--text-secondary)",
                              fontSize: size, fontWeight: isTop ? 600 : 400,
                              cursor: "pointer", transition: "all 0.15s",
                              display: "flex", alignItems: "center", gap: 6,
                            }}
                          >
                            {isTop && <span style={{ fontSize: 10, opacity: 0.8 }}>#{i + 1}</span>}
                            {tag.tag_name}
                            <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{tag.novel_count}</span>
                          </button>
                        );
                      })}
                    </div>

                    <div style={{ marginTop: 20, padding: "10px 14px", background: "var(--bg-surface-2)", borderRadius: 8 }}>
                      <div className="text-xs text-muted" style={{ lineHeight: 1.6 }}>
                        提示：点击标签将自动填入新建项目的题材字段。你也可以在「头脑风暴」中和 AI 讨论具体的题材方向和创新点。
                      </div>
                    </div>
                  </>
                )}
              </div>
            ) : studioTab === "calibration" ? (
              /* Calibration panel */
              <div style={{ padding: 16, overflowY: "auto", flex: 1 }}>
                <div style={{ padding: "12px 14px", background: "var(--accent-subtle)", borderRadius: 8, marginBottom: 16, borderLeft: "3px solid var(--accent)" }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent)", marginBottom: 4 }}>风格校准</div>
                  <div className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
                    调整文风参数，生成样本段落以确认风格方向。校准参数将影响编辑器中 Pipeline 生成的内容风格。
                  </div>
                </div>

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

                {/* Sample generation */}
                <div className="card">
                  <div className="card-body">
                    <div className="label mb-8">生成校准样本</div>
                    <div className="flex gap-6 mb-12">
                      {SAMPLE_TYPES.map(t => (
                        <button key={t.key} className={sampleType === t.key ? "btn-primary" : "btn"}
                          style={{ flex: 1, fontSize: 11, padding: "5px 0", borderRadius: 16 }}
                          onClick={() => setSampleType(t.key)}>
                          {t.label}
                        </button>
                      ))}
                    </div>
                    <button className="btn-primary" style={{ width: "100%", marginBottom: 8 }}
                      onClick={generateCalibrationSample} disabled={sampleLoading}>
                      {sampleLoading ? "生成中..." : "生成样本段落"}
                    </button>
                    {calibrationSample && (
                      <div style={{
                        marginTop: 8, padding: "12px 14px", background: "var(--bg-surface-2)", borderRadius: 8,
                        borderLeft: "3px solid var(--jade)", fontSize: 13, lineHeight: 1.8, color: "var(--text-primary)",
                        whiteSpace: "pre-wrap", maxHeight: 300, overflowY: "auto", fontFamily: "var(--font-serif)",
                      }}>
                        {calibrationSample}
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ) : (
              /* Brainstorm chat panel */
              <>
                <div style={{ flex: 1, overflowY: "auto", padding: "12px 14px" }}>
                  {tabMessages.length === 0 && !aiLoading && (
                    <div style={{ padding: "20px 16px" }}>
                      <div style={{ padding: "12px 14px", background: "var(--accent-subtle)", borderRadius: 8, marginBottom: 16, borderLeft: "3px solid var(--accent)" }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent)", marginBottom: 4 }}>AI 头脑风暴</div>
                        <div className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
                          和 AI 一起构思你的小说——大纲、角色、世界观、任何灵感。AI 将从市场分析、剧情结构、人物塑造等多维度给出建议。
                        </div>
                      </div>
                      <div style={{ display: "grid", gap: 8 }}>
                        {[
                          "帮我构思一个玄幻小说的核心设定和卖点",
                          "我想写一个都市异能题材，帮我设计主角的金手指",
                          "分析一下目前男频最流行的套路和创新方向",
                          "帮我设计三个有辨识度的配角",
                        ].map((hint, i) => (
                          <button key={i} onClick={() => { setInput(hint); }}
                            style={{
                              padding: "10px 14px", borderRadius: 8, border: "1px solid var(--border)",
                              background: "var(--bg-surface-2)", color: "var(--text-secondary)",
                              fontSize: 12, textAlign: "left", cursor: "pointer", lineHeight: 1.5,
                              transition: "all 0.15s",
                            }}
                            onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--accent)"; e.currentTarget.style.background = "var(--accent-subtle)"; }}
                            onMouseLeave={e => { e.currentTarget.style.borderColor = "var(--border)"; e.currentTarget.style.background = "var(--bg-surface-2)"; }}
                          >
                            {hint}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {aiLoading && (
                    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", marginBottom: 8 }}>
                      <div style={{ width: 30, height: 30, borderRadius: "50%", background: "var(--accent-subtle)", border: "2px solid var(--accent)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14 }}>💡</div>
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
                        {msg.role === "user" ? "👤" : "💡"}
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
                  {/* Stop bar */}
                  {aiLoading && (
                    <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                      <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)", borderColor: "var(--error)" }} onClick={stopGeneration}>
                        终止生成
                      </button>
                    </div>
                  )}
                  <div className="flex gap-6">
                    <input className="input" value={input} onChange={e => setInput(e.target.value)}
                      onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
                      placeholder="描述你的想法、问题或灵感..." style={{ flex: 1, fontSize: 12 }} />
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
