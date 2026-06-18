import React, { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import { useDialog } from "../components/shared/Dialog";
import type { Project } from "../api/types";
import AIChatPanel, { ChatMessage } from "../components/shared/AIChatPanel";
import { renderPrompt } from "../utils/promptTemplate";
import { PLATFORMS, platformProfile } from "../utils/platforms";

interface Props {
  activeProject: string;
  onSelectProject: (id: string) => void;
  onNavigate: (tab: string) => void;
}

type StudioTab = "trending" | "brainstorm";

const STUDIO_TABS: { key: StudioTab; label: string; agent: string }[] = [
  { key: "trending", label: "热点题材", agent: "Marketing" },
  { key: "brainstorm", label: "头脑风暴", agent: "Story Architect" },
];

const GENDERS = [
  { key: "male", label: "男频" },
  { key: "female", label: "女频" },
];
const STATUS_OPTIONS = [
  { key: "ongoing", label: "连载中" },
  { key: "completed", label: "已完结" },
];

const uid = () => `msg_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;

export default function ProjectListPage({ activeProject, onSelectProject, onNavigate }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formName, setFormName] = useState("");
  const [formGenre, setFormGenre] = useState("");
  const [formPlatform, setFormPlatform] = useState("");
  const [formGender, setFormGender] = useState("");
  const [formSerialStatus, setFormSerialStatus] = useState("ongoing");
  const [formSynopsis, setFormSynopsis] = useState("");
  const [viewMode, setViewMode] = useState<"grid" | "list">("grid");

  // Studio state
  const STUDIO_SESS_KEY = `inkocto_studio_state_${activeProject}`;
  const _savedStudio = (() => {
    try { const raw = sessionStorage.getItem(STUDIO_SESS_KEY); return raw ? JSON.parse(raw) : null; } catch { return null; }
  })();
  const [studioTab, setStudioTab] = useState<StudioTab>(_savedStudio?.studioTab || "trending");
  const [trendingMessages, setTrendingMessages] = useState<ChatMessage[]>(_savedStudio?.trendingMessages || []);
  const [brainstormMessages, setBrainstormMessages] = useState<ChatMessage[]>(_savedStudio?.brainstormMessages || []);
  const [chatInput, setChatInput] = useState(_savedStudio?.chatInput || "");
  const [chatLoaded, setChatLoaded] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);

  // Trending data — pulled per the active project's publish platform so that
  // hot-topic stats reflect where the user actually plans to publish.
  const [trendingTags, setTrendingTags] = useState<{ tag_name: string; novel_count: number }[]>([]);
  const [trendingLoading, setTrendingLoading] = useState(false);
  const [marketBrief, setMarketBrief] = useState("");

  // Derived: current tab's messages and setter
  const chatMessages = studioTab === "trending" ? trendingMessages : brainstormMessages;
  const setChatMessages = studioTab === "trending" ? setTrendingMessages : setBrainstormMessages;

  // Persist studio state to sessionStorage
  useEffect(() => {
    sessionStorage.setItem(STUDIO_SESS_KEY, JSON.stringify({ studioTab, trendingMessages, brainstormMessages, chatInput }));
  }, [studioTab, trendingMessages, brainstormMessages, chatInput, STUDIO_SESS_KEY]);

  // Backend persistence for chat (debounced, per-tab)
  const trendingSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const brainstormSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (chatLoaded && trendingMessages.length > 0) {
      if (trendingSaveTimer.current) clearTimeout(trendingSaveTimer.current);
      trendingSaveTimer.current = setTimeout(() => {
        apiPut("/api/data/chat_history", {
          project_id: activeProject || "default", scope: "studio_trending",
          messages: trendingMessages.slice(-200),
        }).catch(() => {});
      }, 2000);
    }
  }, [trendingMessages, chatLoaded, activeProject]);
  useEffect(() => {
    if (chatLoaded && brainstormMessages.length > 0) {
      if (brainstormSaveTimer.current) clearTimeout(brainstormSaveTimer.current);
      brainstormSaveTimer.current = setTimeout(() => {
        apiPut("/api/data/chat_history", {
          project_id: activeProject || "default", scope: "studio_brainstorm",
          messages: brainstormMessages.slice(-200),
        }).catch(() => {});
      }, 2000);
    }
  }, [brainstormMessages, chatLoaded, activeProject]);

  // Agent switch guidance messages
  const AGENT_GUIDANCE: Record<StudioTab, string> = {
    trending: "你好，我是 Marketing Agent。我可以帮你分析市场趋势、题材热度、新人友好程度等。告诉我你感兴趣的题材，或者问我市场相关的问题吧！",
    brainstorm: "你好，我是 Story Architect Agent。我可以帮你构思世界观、设计角色、规划故事大纲。告诉我你的创意想法，我来帮你一步步完善！",
  };

  // Load chat from backend on mount / project change (both tabs independently)
  useEffect(() => {
    const pid = activeProject || "default";
    let loaded = 0;
    const markLoaded = () => { loaded++; if (loaded >= 2) setChatLoaded(true); };
    apiGet<{ messages: ChatMessage[] }>(`/api/data/chat_history?project_id=${pid}&scope=studio_trending`)
      .then(r => { if (r.messages && r.messages.length > 0 && trendingMessages.length === 0) setTrendingMessages(r.messages); markLoaded(); })
      .catch(() => markLoaded());
    apiGet<{ messages: ChatMessage[] }>(`/api/data/chat_history?project_id=${pid}&scope=studio_brainstorm`)
      .then(r => { if (r.messages && r.messages.length > 0 && brainstormMessages.length === 0) setBrainstormMessages(r.messages); markLoaded(); })
      .catch(() => markLoaded());
  }, [activeProject]);

  const rightPanel = useResizable({ direction: "horizontal", initialSize: 460, minSize: 340, maxSize: 720 });
  const [rightPanelOpen, setRightPanelOpen] = useState(true);
  const { confirm } = useDialog();
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: Project[] }>("/api/data/projects");
      setProjects(r.items || []);
    } catch (e) { console.error(e); }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  // Active project's publish platform — drives the trending-tag query and
  // the marketing-agent RAG so hot-topic stats match where the user actually
  // publishes. Resolves the free-text platform field via PLATFORM_PROFILES
  // (e.g. "起点" / "qidian" / "起点中文" → "起点中文网").
  const activePlatformLabel = useMemo(() => {
    const proj = projects.find(p => p.id === activeProject);
    const raw = (proj as any)?.platform;
    if (!raw) return "";
    const prof = platformProfile(raw);
    return prof.id === "other" ? "" : prof.label;
  }, [projects, activeProject]);

  // Load trending tags scoped to the active project's platform. Re-fetches when
  // the active project (or its platform) changes so the trending tab matches
  // the current work's publishing platform — driven by 市场特征提取 data.
  useEffect(() => {
    if (studioTab !== "trending") return;
    setTrendingLoading(true);
    const q = activePlatformLabel
      ? `/api/db/tag_stats?limit=30&platform=${encodeURIComponent(activePlatformLabel)}`
      : "/api/db/tag_stats?limit=30";
    apiGet<{ rows: { tag_name: string; novel_count: number }[] }>(q)
      .then(r => setTrendingTags(r.rows || []))
      .catch(() => setTrendingTags([]))
      .finally(() => setTrendingLoading(false));
  }, [studioTab, activePlatformLabel]);

  // Market-data RAG for the 开书助手 — grounds answers in real data,
  // restricted to the active project's platform when available.
  useEffect(() => {
    const q = activePlatformLabel
      ? `/api/db/market_brief?platform=${encodeURIComponent(activePlatformLabel)}`
      : "/api/db/market_brief";
    apiGet<{ brief: string }>(q)
      .then(r => setMarketBrief(r.brief || ""))
      .catch(() => setMarketBrief(""));
  }, [activePlatformLabel]);


  const handleCreate = async () => {
    if (!formName.trim()) return;
    const res = await apiPost<Project>("/api/data/projects", {
      name: formName.trim(), genre: formGenre.trim() || undefined,
      platform: formPlatform || undefined, gender_target: formGender || undefined,
      serial_status: formSerialStatus || undefined, synopsis: formSynopsis || undefined,
    });
    setFormName(""); setFormGenre(""); setFormPlatform(""); setFormGender("");
    setFormSerialStatus("ongoing"); setFormSynopsis(""); setShowForm(false);
    load();
    if (res?.id) onSelectProject(res.id);
  };

  const handleUpdate = async () => {
    if (!editingId || !formName.trim()) return;
    await apiPut(`/api/data/projects/${editingId}`, {
      name: formName.trim(), genre: formGenre.trim() || undefined,
      platform: formPlatform || undefined, gender_target: formGender || undefined,
      serial_status: formSerialStatus || undefined, synopsis: formSynopsis || undefined,
    });
    setEditingId(null); setFormName(""); setFormGenre(""); setFormPlatform("");
    setFormGender(""); setFormSerialStatus("ongoing"); setFormSynopsis(""); load();
  };

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!(await confirm({ message: "确定删除该项目？此操作不可撤销。", destructive: true }))) return;
    await apiDelete(`/api/data/projects/${id}`);
    load();
  };

  const startEdit = (p: Project, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(p.id); setFormName(p.name); setFormGenre(p.genre || "");
    setFormPlatform((p as any).platform || ""); setFormGender((p as any).gender_target || "");
    setFormSerialStatus((p as any).serial_status || "ongoing");
    setFormSynopsis((p as any).synopsis || ""); setShowForm(false);
  };

  const cancelForm = () => {
    setShowForm(false); setEditingId(null); setFormName(""); setFormGenre("");
    setFormPlatform(""); setFormGender(""); setFormSerialStatus("ongoing"); setFormSynopsis("");
  };

  // Agent-specific chat configuration
  const getAgentConfig = (tab: StudioTab) => {
    const configs: Record<StudioTab, { agentName: string; systemHint: string; placeholder: string; quickPrompts: string[] }> = {
      trending: {
        agentName: "Marketing",
        systemHint: `你是Marketing Agent，专注于网文市场趋势分析。分析题材热度、新人友好程度、竞争程度等。
回答后必须追加一个追问，格式：在回答末尾加上 [FOLLOW_UP]问题内容[/FOLLOW_UP][OPTIONS]选项A|选项B|选项C[/OPTIONS]`,
        placeholder: "询问某个题材的市场前景...",
        quickPrompts: [
          "分析一下目前最热门的网文题材",
          "玄幻题材目前市场竞争大吗？新人友好吗？",
          "都市异能和系统流哪个更适合新人起步？",
          "帮我分析一下女频市场最近的趋势变化",
        ],
      },
      brainstorm: {
        agentName: "Story Architect",
        systemHint: `你是Story Architect，专注于帮助用户构建世界观、设计角色、规划故事大纲。
回答后必须追加一个追问，格式：在回答末尾加上 [FOLLOW_UP]问题内容[/FOLLOW_UP][OPTIONS]选项A|选项B|选项C[/OPTIONS]
如果用户确认了满意的内容，在末尾追加 [QUICK_FILL]可快速填入的字段名:内容[/QUICK_FILL] 标记供用户确认。`,
        placeholder: "构思你的故事世界...",
        quickPrompts: [
          "帮我思考整体故事梗概",
          "帮我构思一个玄幻小说的核心设定和卖点",
          "我想写一个都市异能题材，帮我设计主角的金手指",
          "帮我设计三个有辨识度的配角",
          "帮我搭建一个修仙世界的力量等级体系",
          "帮我设计一条主线剧情的起承转合",
        ],
      },
    };
    return configs[tab];
  };

  // Build the prompt (conversation + system hint + market-data RAG) for the
  // current 开书助手 turn. Shared by send and the web-LLM preview.
  const buildChatPrompt = async (text: string): Promise<{ fullPrompt: string; systemHint: string }> => {
    const config = getAgentConfig(studioTab);
    const recentMessages = chatMessages.filter(m => m.role !== "system").slice(-20);
    const conversationContext = recentMessages.map(m =>
      `${m.role === "user" ? "用户" : m.agentName || "AI"}：${m.content}`,
    ).join("\n\n");
    const fullPrompt = recentMessages.length > 0
      ? `以下是对话历史：\n\n${conversationContext}\n\n用户：${text}\n\n请基于以上对话上下文回答用户最新的问题。`
      : text;
    const promptKey = studioTab === "trending"
      ? "assistant.book_start_trending"
      : "assistant.book_start_brainstorm";
    const baseHint = await renderPrompt(promptKey, {}, config.systemHint);
    let systemHint = `${baseHint}

回答用户问题后，必须追加1个追问来引导用户进入下一步创作讨论。
追问格式：在回答末尾加上 [FOLLOW_UP]追问内容[/FOLLOW_UP][OPTIONS]选项A|选项B|选项C[/OPTIONS]
追问规则：3个选项，具体有区分度，不重复已确认内容。`;
    if (marketBrief) {
      const platformNote = activePlatformLabel ? `（${activePlatformLabel}）` : "";
      systemHint += `\n\n[市场数据参考${platformNote}——以下为市场数据库的真实统计，回答须据此，不要编造市场数据]\n${marketBrief}`;
    }
    return { fullPrompt, systemHint };
  };

  const fetchChatPrompt = async (): Promise<string> => {
    const { fullPrompt, systemHint } = await buildChatPrompt(chatInput.trim() || "（用户尚未输入问题）");
    const r = await apiPost<{ prompt: string }>("/api/generation/quick-generate", {
      project_id: activeProject || "default",
      chapter_id: `studio_${studioTab}`,
      synopsis: fullPrompt, system_hint: systemHint, prompt_only: true,
    });
    return r.prompt || "";
  };

  const applyChatResult = (text: string) => {
    const cfg = getAgentConfig(studioTab);
    const msgs: ChatMessage[] = [];
    if (chatInput.trim()) {
      msgs.push({ id: uid(), role: "user", content: chatInput.trim(), timestamp: Date.now(), status: "done" });
    }
    msgs.push({ id: uid(), role: "assistant", content: text, agentName: cfg.agentName, timestamp: Date.now(), status: "done" });
    setChatMessages(prev => [...prev, ...msgs]);
    setChatInput("");
  };

  const sendMessageInternal = async (text: string, skipUserMsg = false) => {
    const config = getAgentConfig(studioTab);
    if (!skipUserMsg) {
      const userMsg: ChatMessage = {
        id: uid(), role: "user", content: text, timestamp: Date.now(), status: "done",
      };
      setChatMessages(prev => [...prev, userMsg]);
    }
    setAiLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const { fullPrompt, systemHint } = await buildChatPrompt(text);

      const resp = await fetch("/api/generation/quick-generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: activeProject || "default",
          chapter_id: `studio_${studioTab}`,
          synopsis: fullPrompt,
          system_hint: systemHint,
        }),
        signal: controller.signal,
      });
      if (!resp.ok) {
        const errText = await resp.text();
        let detail = "";
        try { detail = JSON.parse(errText).detail || errText; } catch { detail = errText; }
        throw new Error(detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const rawText = data.text || "生成完成。";

      // Parse response: extract answer and follow-up question
      let answerText = rawText;
      let followUp: { text: string; options: string[] } | undefined;

      // Try JSON format first
      try {
        const parsed = JSON.parse(rawText);
        if (parsed?.answer) {
          answerText = parsed.answer;
          if (Array.isArray(parsed.follow_up) && parsed.follow_up[0]) {
            followUp = { text: parsed.follow_up[0].text, options: parsed.follow_up[0].options };
          }
        }
      } catch {
        // Try tag-based format [FOLLOW_UP]...[/FOLLOW_UP][OPTIONS]...[/OPTIONS]
        const fuMatch = rawText.match(/\[FOLLOW_UP\]([\s\S]*?)\[\/FOLLOW_UP\]/);
        const optMatch = rawText.match(/\[OPTIONS\]([\s\S]*?)\[\/OPTIONS\]/);
        if (fuMatch && optMatch) {
          answerText = rawText.replace(/\[FOLLOW_UP\][\s\S]*$/, "").trim();
          followUp = {
            text: fuMatch[1].trim(),
            options: optMatch[1].split("|").map((s: string) => s.trim()).filter(Boolean),
          };
        } else {
          // Try JSON block extraction
          const idx = rawText.lastIndexOf('{"answer"');
          if (idx >= 0) {
            try {
              const parsed = JSON.parse(rawText.slice(idx));
              if (parsed?.answer) {
                answerText = parsed.answer;
                if (Array.isArray(parsed.follow_up) && parsed.follow_up[0]) {
                  followUp = { text: parsed.follow_up[0].text, options: parsed.follow_up[0].options };
                }
              }
            } catch { /* keep raw */ }
          }
        }
      }

      const aiMsg: ChatMessage = {
        id: uid(), role: "assistant", content: answerText,
        agentName: config.agentName, timestamp: Date.now(),
        status: "done", canRegenerate: true,
        followUpQuestion: followUp,
      };
      setChatMessages(prev => [...prev, aiMsg]);
    } catch (e: any) {
      if (e?.name === "AbortError") {
        setChatMessages(prev => [...prev, {
          id: uid(), role: "assistant", content: "（已终止生成）",
          agentName: config.agentName, timestamp: Date.now(), status: "aborted",
        }]);
      } else {
        setChatMessages(prev => [...prev, {
          id: uid(), role: "assistant",
          content: `抱歉，AI 暂时无法响应。\n\n${(e?.message || "请求失败").slice(0, 500)}`,
          agentName: config.agentName, timestamp: Date.now(), status: "error",
        }]);
      }
    }
    abortRef.current = null;
    setAiLoading(false);
  };

  const sendMessage = (text: string) => sendMessageInternal(text, false);

  const stopGeneration = () => {
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
  };

  const handleFollowUpSelect = (msgId: string, option: string) => {
    // Find the original follow-up question text
    const originalMsg = chatMessages.find(m => m.id === msgId);
    const questionText = originalMsg?.followUpQuestion?.text || "";

    // Remove follow-up from the message and add Q/A formatted user message
    const qaText = questionText ? `Q: ${questionText}\nA: ${option}` : option;
    setChatMessages(prev => [
      ...prev.map(m => m.id === msgId ? { ...m, followUpQuestion: undefined } : m),
      { id: uid(), role: "user" as const, content: qaText, timestamp: Date.now(), status: "done" as const },
    ]);

    // Send the option to AI for continuation (skip adding another user msg)
    sendMessageInternal(option, true);
  };

  const handleRegenerate = (msgId: string) => {
    const idx = chatMessages.findIndex(m => m.id === msgId);
    if (idx <= 0) return;
    // Find the preceding user message
    let userMsgIdx = idx - 1;
    while (userMsgIdx >= 0 && chatMessages[userMsgIdx].role !== "user") userMsgIdx--;
    if (userMsgIdx < 0) return;
    const userText = chatMessages[userMsgIdx].content;
    // Remove old AI message
    setChatMessages(prev => prev.filter(m => m.id !== msgId));
    sendMessage(userText);
  };

  const deleteChatMessage = (messageId: string) => {
    const pid = activeProject || "default";
    if (studioTab === "trending") {
      const updated = trendingMessages.filter(m => m.id !== messageId);
      setTrendingMessages(updated);
      apiPut("/api/data/chat_history", {
        project_id: pid, scope: "studio_trending",
        messages: updated.slice(-200),
      }).catch(() => {});
    } else if (studioTab === "brainstorm") {
      const updated = brainstormMessages.filter(m => m.id !== messageId);
      setBrainstormMessages(updated);
      apiPut("/api/data/chat_history", {
        project_id: pid, scope: "studio_brainstorm",
        messages: updated.slice(-200),
      }).catch(() => {});
    }
  };

  const formatDate = (dateVal?: string | number) => {
    if (!dateVal) return "--";
    const d = typeof dateVal === "number" || (typeof dateVal === "string" && /^\d+(\.\d+)?$/.test(dateVal))
      ? new Date(Number(dateVal) * 1000)
      : new Date(dateVal);
    if (isNaN(d.getTime())) return "--";
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  };

  const currentTabAgent = STUDIO_TABS.find(t => t.key === studioTab)?.agent || "";

  return (
    <div className="page-full">
      <div className="panel-layout" style={{ height: "100%" }}>
        {/* LEFT PANEL: Project Management */}
        <div className="panel flex-1" style={{ overflowY: "auto" }}>
          <div className="page-container">
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
              <div>
                <h2 style={{ fontFamily: "var(--font-serif)", fontSize: 22, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
                  开书
                </h2>
                <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
                  创建和管理你的网文创作项目
                </p>
              </div>
              <div className="flex gap-8 items-center">
                {/* View mode toggle */}
                <div className="tab-bar" style={{ padding: 2 }}>
                  <button className={`tab-item ${viewMode === "grid" ? "active" : ""}`}
                    style={{ padding: "4px 10px", fontSize: 11 }}
                    onClick={() => setViewMode("grid")}>网格</button>
                  <button className={`tab-item ${viewMode === "list" ? "active" : ""}`}
                    style={{ padding: "4px 10px", fontSize: 11 }}
                    onClick={() => setViewMode("list")}>列表</button>
                </div>
                <button className="btn-primary" onClick={() => { setShowForm(true); setEditingId(null); setFormName(""); setFormGenre(""); }}>
                  + 新建项目
                </button>
              </div>
            </div>

            {/* Create/Edit Form */}
            {(showForm || editingId) && (
              <div className="card mb-24" style={{ animation: "slideUp 0.2s var(--ease-out)" }}>
                <div className="card-header"><h3>{editingId ? "编辑项目" : "新建项目"}</h3></div>
                <div className="card-body">
                  <div className="flex gap-12 mb-12" style={{ flexWrap: "wrap" }}>
                    <div className="field" style={{ flex: 2, minWidth: 200 }}>
                      <label className="label">书名 *</label>
                      <input className="input" value={formName} onChange={e => setFormName(e.target.value)}
                        placeholder="例：星辰大海" autoFocus
                        onKeyDown={e => { if (e.key === "Enter") editingId ? handleUpdate() : handleCreate(); }} />
                    </div>
                    <div className="field" style={{ flex: 1, minWidth: 140 }}>
                      <label className="label">分类/题材</label>
                      <input className="input" value={formGenre} onChange={e => setFormGenre(e.target.value)}
                        placeholder="例：玄幻、都市" />
                    </div>
                    <div className="field" style={{ flex: 1, minWidth: 140 }}>
                      <label className="label">发布平台</label>
                      <select className="select" value={formPlatform} onChange={e => setFormPlatform(e.target.value)} style={{ width: "100%" }}>
                        <option value="">未选择</option>
                        {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
                      </select>
                    </div>
                  </div>
                  <div className="flex gap-12 mb-12" style={{ flexWrap: "wrap" }}>
                    <div className="field" style={{ flex: 1 }}>
                      <label className="label">男频/女频</label>
                      <div className="flex gap-6">
                        {GENDERS.map(g => (
                          <button key={g.key} className={formGender === g.key ? "btn-primary" : "btn"}
                            style={{ flex: 1, fontSize: 12, padding: "6px 0", borderRadius: 20, textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center" }}
                            onClick={() => setFormGender(formGender === g.key ? "" : g.key)}>{g.label}</button>
                        ))}
                      </div>
                    </div>
                    <div className="field" style={{ flex: 1 }}>
                      <label className="label">连载状态</label>
                      <div className="flex gap-6">
                        {STATUS_OPTIONS.map(s => (
                          <button key={s.key} className={formSerialStatus === s.key ? "btn-primary" : "btn"}
                            style={{ flex: 1, fontSize: 12, padding: "6px 0", borderRadius: 20, textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center" }}
                            onClick={() => setFormSerialStatus(s.key)}>{s.label}</button>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className="field mb-12">
                    <label className="label">简介</label>
                    <textarea className="input" value={formSynopsis} onChange={e => setFormSynopsis(e.target.value)}
                      placeholder="简要描述你的小说..." rows={2} style={{ fontSize: 13 }} />
                  </div>
                  <div className="field mb-12">
                    <label className="label">整体故事梗概</label>
                    <textarea className="input" value={formSynopsis} onChange={e => setFormSynopsis(e.target.value)}
                      placeholder="描述整体故事走向..." rows={3} style={{ fontSize: 13, fontFamily: "var(--font-serif)" }} />
                  </div>
                  <div className="flex gap-8" style={{ justifyContent: "flex-end" }}>
                    <button className="btn" onClick={cancelForm}>取消</button>
                    <button className="btn-primary" onClick={editingId ? handleUpdate : handleCreate} disabled={!formName.trim()}>
                      {editingId ? "保存" : "创建"}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Project cards */}
            {loading ? (
              <div className="loading"><div className="loading-spinner" />加载中...</div>
            ) : projects.length === 0 ? (
              <div className="empty-state">
                <h4>还没有项目</h4>
                <p>点击「新建项目」开始你的创作之旅</p>
              </div>
            ) : viewMode === "grid" ? (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 16 }}>
                {projects.map(p => {
                  const isActive = p.id === activeProject;
                  return (
                    <div key={p.id} className="card" style={{
                      cursor: "pointer",
                      transition: "border-color 0.15s, box-shadow 0.2s, transform 0.2s var(--ease-out)",
                      borderColor: isActive ? "var(--accent)" : undefined,
                      boxShadow: isActive ? "0 0 12px var(--accent-glow)" : undefined,
                    }} onClick={() => onSelectProject(p.id)}>
                      <div style={{ height: 3, background: isActive ? "var(--accent)" : "var(--border)" }} />
                      <div className="card-body">
                        <div className="flex items-center justify-between mb-8">
                          <h3 className="font-serif" style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>{p.name}</h3>
                          <div className="flex gap-4 items-center">
                            {isActive && <span className="tag accent" style={{ fontSize: 10 }}>当前</span>}
                            {p.genre && <span className="tag category">{p.genre}</span>}
                          </div>
                        </div>
                        <div className="flex gap-8 mb-8" style={{ flexWrap: "wrap" }}>
                          {(p as any).platform && <span className="tag qidian" style={{ fontSize: 10 }}>{(p as any).platform}</span>}
                          {(p as any).gender_target && <span className="tag purple" style={{ fontSize: 10 }}>{(p as any).gender_target === "male" ? "男频" : "女频"}</span>}
                          {(p as any).serial_status && <span className={`tag ${(p as any).serial_status === "ongoing" ? "status-ongoing" : "status-completed"}`} style={{ fontSize: 10 }}>
                            {(p as any).serial_status === "ongoing" ? "连载" : "完结"}
                          </span>}
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
            ) : (
              /* List view */
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {projects.map(p => {
                  const isActive = p.id === activeProject;
                  return (
                    <div key={p.id} className={`report-list-item ${isActive ? "active" : ""}`}
                      onClick={() => onSelectProject(p.id)} style={{ borderRadius: "var(--radius-sm)", padding: "10px 16px" }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div className="flex items-center gap-8 mb-4">
                          <span className="font-serif" style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>{p.name}</span>
                          {p.genre && <span className="tag category" style={{ fontSize: 10 }}>{p.genre}</span>}
                          {(p as any).platform && <span className="tag qidian" style={{ fontSize: 10 }}>{(p as any).platform}</span>}
                        </div>
                        <div className="flex gap-16 text-xs text-muted">
                          <span>{(p.word_count || 0).toLocaleString()} 字</span>
                          <span>{p.chapter_count || 0} 章</span>
                          <span>创建于 {formatDate(p.created_at)}</span>
                        </div>
                      </div>
                      <div className="flex gap-4">
                        <button className="btn-icon" title="编辑" onClick={e => startEdit(p, e)}>&#9998;</button>
                        <button className="btn-icon" title="进入编辑器" onClick={e => { e.stopPropagation(); onSelectProject(p.id); onNavigate("editor"); }}>&#8594;</button>
                        <button className="btn-icon" title="删除" onClick={e => handleDelete(p.id, e)} style={{ color: "var(--error)" }}>&#10005;</button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {rightPanelOpen && <div className="panel-resize-h" {...rightPanel.handleProps} />}

        {/* RIGHT PANEL: AI 开书助手 */}
        {rightPanelOpen ? (
        <div className="panel" style={{ width: rightPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderLeft: "1px solid var(--border)", display: "flex", flexDirection: "column" }}>
          <div className="panel-header" style={{ flexDirection: "column", alignItems: "stretch", gap: 6, paddingBottom: 8 }}>
            <div className="flex items-center justify-between">
              <h3 style={{ fontSize: 15, fontWeight: 700 }}>AI 开书助手</h3>
              <button onClick={() => setRightPanelOpen(false)} style={{ background: "none", border: "none", color: "var(--text-tertiary)", cursor: "pointer", fontSize: 14, padding: "2px 6px" }} title="收起 AI 开书助手">&#9654;</button>
            </div>
            <div className="text-xs text-muted" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span>{projects.find(p => p.id === activeProject)?.name || "未选择项目"}</span>
              {activePlatformLabel && (
                <span className="tag" style={{ fontSize: 10, padding: "1px 7px", borderRadius: 10, background: "var(--accent-subtle)", color: "var(--accent)" }}>
                  {activePlatformLabel}
                </span>
              )}
              {currentTabAgent && <span style={{ color: "var(--accent)" }}>Agent: {currentTabAgent}</span>}
            </div>
          </div>

          {/* Studio subtabs */}
          <div className="tab-bar-underline" style={{ flexShrink: 0 }}>
            {STUDIO_TABS.map(t => (
              <button key={t.key} className={`tab-item ${studioTab === t.key ? "active" : ""}`}
                onClick={() => setStudioTab(t.key)} style={{ fontSize: 12 }}>
                {t.label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {studioTab === "trending" ? (
              /* 热点题材 (Marketing Agent chat) */
              <AIChatPanel
                messages={trendingMessages}
                fetchPrompt={fetchChatPrompt}
                onApplyResult={applyChatResult}
                onSendMessage={sendMessage}
                onStopGeneration={stopGeneration}
                onRegenerateMessage={handleRegenerate}
                onSelectFollowUpOption={handleFollowUpSelect}
                isGenerating={aiLoading}
                preservedInput={chatInput}
                onInputChange={setChatInput}
                onDeleteMessage={deleteChatMessage}
                onClearHistory={() => {
                  setTrendingMessages([]);
                  apiPut("/api/data/chat_history", {
                    project_id: activeProject || "default", scope: "studio_trending",
                    messages: [],
                  }).catch(() => {});
                }}
                placeholder="询问某个题材的市场前景..."
                quickPrompts={getAgentConfig("trending").quickPrompts}
                templates={[
                  { label: "题材分析", prompt: "分析一下「」这个题材的市场前景、竞争程度和新人友好度" },
                  { label: "市场对比", prompt: "对比「」和「」两个题材，哪个更适合新人入行？" },
                  { label: "趋势预测", prompt: "目前网文市场有哪些新兴趋势值得关注？" },
                ]}
                emptyState={
                  <div style={{ padding: "16px" }}>
                    <div style={{ padding: "12px 14px", background: "var(--accent-subtle)", borderRadius: 8, marginBottom: 16, borderLeft: "3px solid var(--accent)" }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent)", marginBottom: 4 }}>Marketing Agent</div>
                      <div className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
                        讨论题材在市场上是否吃香，是否新人友好等，帮助你确认想写的题材方向。
                        {activePlatformLabel
                          ? `数据来源于「${activePlatformLabel}」市场特征提取。`
                          : "未设置发布平台时显示全平台综合数据，到项目设置中选择平台可获得平台专属市场分析。"}
                      </div>
                    </div>
                    {/* Trending tag cloud */}
                    {trendingLoading ? (
                      <div className="loading"><div className="loading-spinner" />加载市场数据...</div>
                    ) : trendingTags.length > 0 ? (
                      <>
                        <div className="flex items-center justify-between mb-8">
                          <span className="label" style={{ fontSize: 11, marginBottom: 0 }}>
                            热门题材标签 TOP {trendingTags.length}
                          </span>
                          {activePlatformLabel && (
                            <span className="tag" style={{ fontSize: 10, padding: "2px 8px", borderRadius: 10, background: "var(--accent-subtle)", color: "var(--accent)", border: "1px solid var(--accent)" }}>
                              {activePlatformLabel}
                            </span>
                          )}
                        </div>
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 16 }}>
                          {trendingTags.slice(0, 20).map((tag, i) => {
                            const isTop = i < 5;
                            return (
                              <button key={tag.tag_name}
                                onClick={() => sendMessage(`分析一下「${tag.tag_name}」这个题材的市场前景和新人友好度`)}
                                style={{
                                  padding: "5px 12px", borderRadius: 16,
                                  border: `1px solid ${isTop ? "var(--accent)" : "var(--border)"}`,
                                  background: isTop ? "var(--accent-subtle)" : "var(--bg-surface-2)",
                                  color: isTop ? "var(--accent)" : "var(--text-secondary)",
                                  fontSize: 12, fontWeight: isTop ? 600 : 400,
                                  cursor: "pointer", transition: "all 0.15s",
                                }}>
                                {tag.tag_name} <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{tag.novel_count}</span>
                              </button>
                            );
                          })}
                        </div>
                      </>
                    ) : (
                      <div className="text-xs text-muted" style={{ padding: "8px 4px", lineHeight: 1.6 }}>
                        {activePlatformLabel
                          ? `当前平台「${activePlatformLabel}」暂无热门题材数据，建议先到 市场分析 中抓取该平台数据。`
                          : "市场数据库暂无热门题材数据。"}
                      </div>
                    )}
                  </div>
                }
              />
            ) : studioTab === "brainstorm" ? (
              /* 头脑风暴 (Story Architect chat) */
              <AIChatPanel
                messages={brainstormMessages}
                fetchPrompt={fetchChatPrompt}
                onApplyResult={applyChatResult}
                onSendMessage={sendMessage}
                onStopGeneration={stopGeneration}
                onRegenerateMessage={handleRegenerate}
                onSelectFollowUpOption={handleFollowUpSelect}
                isGenerating={aiLoading}
                preservedInput={chatInput}
                onInputChange={setChatInput}
                onDeleteMessage={deleteChatMessage}
                onClearHistory={() => {
                  setBrainstormMessages([]);
                  apiPut("/api/data/chat_history", {
                    project_id: activeProject || "default", scope: "studio_brainstorm",
                    messages: [],
                  }).catch(() => {});
                }}
                placeholder="构思你的故事世界..."
                quickPrompts={getAgentConfig("brainstorm").quickPrompts}
                templates={[
                  { label: "世界观设定", prompt: "帮我设计一个世界观：" },
                  { label: "角色构思", prompt: "帮我构思一个角色，要求：" },
                  { label: "剧情大纲", prompt: "帮我设计一个剧情大纲，类型是「」，核心卖点是：" },
                  { label: "开篇设计", prompt: "帮我设计一个引人入胜的开篇，要求在前三章内建立核心冲突" },
                ]}
                emptyState={
                  <div style={{ padding: "16px" }}>
                    <div style={{ padding: "12px 14px", background: "var(--indigo-subtle)", borderRadius: 8, marginBottom: 16, borderLeft: "3px solid var(--indigo)" }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--indigo)", marginBottom: 4 }}>Story Architect Agent</div>
                      <div className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
                        讨论具体的世界观设定/人物角色/整体故事梗概。满意后可快速创建世界书条目、角色、或填入整体故事梗概。
                      </div>
                    </div>
                  </div>
                }
              />
            ) : null}
          </div>
        </div>
        ) : (
        <div style={{ width: 36, flexShrink: 0, background: "var(--bg-surface)", borderLeft: "1px solid var(--border)", display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 12 }}>
          <button onClick={() => setRightPanelOpen(true)} style={{ background: "none", border: "none", color: "var(--text-tertiary)", cursor: "pointer", fontSize: 13, padding: "4px", writingMode: "vertical-rl", letterSpacing: 2 }} title="展开 AI 开书助手">
            &#9664; AI 开书助手
          </button>
        </div>
        )}
      </div>
    </div>
  );
}
