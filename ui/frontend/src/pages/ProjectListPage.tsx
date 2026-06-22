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
  // 主分类 (大分类). Stored as `project.genre` for backward compat —
  // the legacy schema column does double-duty as the top-level taxonomy.
  const [formGenre, setFormGenre] = useState("");
  // 副分类. Stored as `project.category` (added by the
  // _ensure_projects_market_columns migration). Sub options surfaced in
  // the form are filtered to those whose `parent` matches `formGenre`.
  const [formCategory, setFormCategory] = useState("");
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

  // Items the user has pinned from the trending panel — flows into the
  // marketing-agent system hint so 「选了 玄幻」/「点过 都市异能」 directly
  // shapes the next answer. Reset on project / platform switch.
  const [focusedMarketItems, setFocusedMarketItems] = useState<string[]>([]);
  const toggleFocusedItem = useCallback((label: string) => {
    setFocusedMarketItems(prev =>
      prev.includes(label) ? prev.filter(x => x !== label) : [...prev, label],
    );
  }, []);

  // Per-platform 分类/题材 catalog used by the new-project form. Fetched
  // from /api/db/platform_categories when the form's platform select
  // changes so users can only file a work under canonical labels for the
  // platform they picked (e.g. 起点 → 玄幻/都市/…, 番茄 → DB-observed).
  type CategoryOption = { key: string; label: string; parent?: string | null; count?: number };
  const [formCategoryOptions, setFormCategoryOptions] = useState<{
    main: CategoryOption[]; sub: CategoryOption[];
  }>({ main: [], sub: [] });
  const [formCategoryLoading, setFormCategoryLoading] = useState(false);

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
  // publishes. Resolves the free-text `platform` field via PLATFORM_PROFILES
  // (e.g. "起点" / "qidian" / "起点中文" → id:"qidian", label:"起点中文网").
  // The API expects platform IDs (matches the crawler DB's `novels.platform`);
  // the label is only for display.
  const activePlatform = useMemo(() => {
    const proj = projects.find(p => p.id === activeProject);
    const raw = (proj as any)?.platform;
    if (!raw) return { id: "", label: "" };
    const prof = platformProfile(raw);
    if (prof.id === "other") return { id: "", label: "" };
    return { id: prof.id, label: prof.label };
  }, [projects, activeProject]);
  const activePlatformLabel = activePlatform.label;
  const activePlatformId = activePlatform.id;

  // Market-data hot-topic info for the 开书助手 — fetched once per platform
  // from the 基础特征提取 cache (/api/analysis/run · cached_only=true) so the
  // assistant grounds answers in the same panel the user sees on
  // 市场特征提取 → 基础特征提取. We never kick off the heavy compute from
  // here; if the cache is empty the assistant falls back to /market_brief
  // and surfaces a deep-link to run the analysis.
  type PanelRow = {
    name: string; total: number; avg_heat: number; latest_share: number;
    count_pct: number | null; heat_pct: number | null; share_pct: number | null;
    new_count?: number; parent?: string;
  };
  type AnalysisPayload = {
    empty?: boolean;
    start_date?: string; end_date?: string;
    panel?: { categories?: PanelRow[]; tags?: PanelRow[] };
  };
  const [analysis, setAnalysis] = useState<AnalysisPayload | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisStale, setAnalysisStale] = useState(false);

  // Drop pinned items when the project or its platform changes — they no
  // longer match the analysis being shown.
  useEffect(() => {
    setFocusedMarketItems([]);
  }, [activeProject, activePlatformId]);

  // Load per-platform category catalog whenever the form picks a platform.
  // Empty platform → empty options (the genre input falls back to free text
  // so users without a chosen platform aren't blocked).
  useEffect(() => {
    if (!formPlatform) {
      setFormCategoryOptions({ main: [], sub: [] });
      return;
    }
    const prof = platformProfile(formPlatform);
    if (prof.id === "other") {
      setFormCategoryOptions({ main: [], sub: [] });
      return;
    }
    setFormCategoryLoading(true);
    apiGet<{ main_categories: CategoryOption[]; sub_categories: CategoryOption[] }>(
      `/api/db/platform_categories?platform=${encodeURIComponent(prof.id)}`,
    )
      .then(r => setFormCategoryOptions({
        main: r.main_categories || [],
        sub: r.sub_categories || [],
      }))
      .catch(() => setFormCategoryOptions({ main: [], sub: [] }))
      .finally(() => setFormCategoryLoading(false));
  }, [formPlatform]);

  useEffect(() => {
    if (studioTab !== "trending") return;
    setAnalysisLoading(true);
    setAnalysisStale(false);
    const params = new URLSearchParams({
      platform: activePlatformId || "both",
      lookback: "all", top_k: "10", cached_only: "true",
    });
    apiGet<{ state: string; payload?: AnalysisPayload; stale?: boolean }>(
      `/api/analysis/run?${params}`,
    )
      .then(r => {
        if (r.state === "ready" && r.payload && !r.payload.empty) {
          setAnalysis(r.payload);
          setAnalysisStale(!!r.stale);
        } else {
          setAnalysis(null);
        }
      })
      .catch(() => setAnalysis(null))
      .finally(() => setAnalysisLoading(false));
  }, [studioTab, activePlatformId]);

  // Backup tag-stats / brief feed — used as a fallback when the rich
  // 基础特征提取 cache hasn't been computed for this platform yet. Also
  // powers the agent's RAG brief.
  useEffect(() => {
    if (studioTab !== "trending") return;
    setTrendingLoading(true);
    const q = activePlatformId
      ? `/api/db/tag_stats?limit=30&platform=${encodeURIComponent(activePlatformId)}`
      : "/api/db/tag_stats?limit=30";
    apiGet<{ rows: { tag_name: string; novel_count: number }[] }>(q)
      .then(r => setTrendingTags(r.rows || []))
      .catch(() => setTrendingTags([]))
      .finally(() => setTrendingLoading(false));
  }, [studioTab, activePlatformId]);

  // Market-data RAG for the 开书助手 — grounds answers in real data,
  // restricted to the active project's platform when available.
  useEffect(() => {
    const q = activePlatformId
      ? `/api/db/market_brief?platform=${encodeURIComponent(activePlatformId)}`
      : "/api/db/market_brief";
    apiGet<{ brief: string }>(q)
      .then(r => setMarketBrief(r.brief || ""))
      .catch(() => setMarketBrief(""));
  }, [activePlatformId]);


  const handleCreate = async () => {
    if (!formName.trim()) return;
    const res = await apiPost<Project>("/api/data/projects", {
      name: formName.trim(),
      genre: formGenre.trim() || undefined,
      category: formCategory.trim() || undefined,
      platform: formPlatform || undefined, gender_target: formGender || undefined,
      serial_status: formSerialStatus || undefined, synopsis: formSynopsis || undefined,
    });
    setFormName(""); setFormGenre(""); setFormCategory("");
    setFormPlatform(""); setFormGender("");
    setFormSerialStatus("ongoing"); setFormSynopsis(""); setShowForm(false);
    load();
    if (res?.id) onSelectProject(res.id);
  };

  const handleUpdate = async () => {
    if (!editingId || !formName.trim()) return;
    await apiPut(`/api/data/projects/${editingId}`, {
      name: formName.trim(),
      genre: formGenre.trim() || undefined,
      category: formCategory.trim() || undefined,
      platform: formPlatform || undefined, gender_target: formGender || undefined,
      serial_status: formSerialStatus || undefined, synopsis: formSynopsis || undefined,
    });
    setEditingId(null); setFormName(""); setFormGenre(""); setFormCategory("");
    setFormPlatform("");
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
    setFormCategory((p as any).category || "");
    setFormPlatform((p as any).platform || ""); setFormGender((p as any).gender_target || "");
    setFormSerialStatus((p as any).serial_status || "ongoing");
    setFormSynopsis((p as any).synopsis || ""); setShowForm(false);
  };

  const cancelForm = () => {
    setShowForm(false); setEditingId(null); setFormName(""); setFormGenre("");
    setFormCategory("");
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

  // Build a marketing-agent brief from the 基础特征提取 panel so the assistant
  // can quote real category/tag heat + trend numbers instead of generic
  // intuition. Falls back to the simpler /market_brief feed when the panel
  // cache hasn't been computed yet for this platform.
  const buildMarketBrief = useCallback((): string => {
    const panel = analysis?.panel;
    const cats = panel?.categories?.slice(0, 8) || [];
    const tags = panel?.tags?.slice(0, 12) || [];
    if (cats.length === 0 && tags.length === 0) return marketBrief;
    const pct = (v: number | null | undefined) =>
      v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)}%`;
    const fmtRow = (r: PanelRow) => {
      const trend = `数量${pct(r.count_pct)}/热度${pct(r.heat_pct)}`;
      return `${r.parent ? `${r.parent}·` : ""}${r.name}（库内 ${r.total} 部, 热度 ${Math.round(r.avg_heat || 0)}, 新书 ${r.new_count || 0} 部, ${trend}）`;
    };
    const lines: string[] = [];
    if (analysis?.start_date || analysis?.end_date) {
      lines.push(`时间区间：${analysis.start_date || "?"} ~ ${analysis.end_date || "?"}`);
    }
    if (cats.length) {
      lines.push("热门大分类（按数量+热度）：" + cats.map(fmtRow).join("；"));
    }
    if (tags.length) {
      lines.push("热门题材标签（按数量+热度）：" + tags.map(fmtRow).join("；"));
    }
    return lines.join("\n");
  }, [analysis, marketBrief]);

  // Hard cap on conversation-history characters that ride into the prompt.
  // Keeps the assistant from blowing past the model's context window once
  // the chat has run long enough to accumulate 20 turns of detailed replies
  // (rough budget: ~16K tokens once the system hint + market brief are
  // added on top). The tail end is preserved — the recent turns are the
  // ones that actually condition the next answer.
  const MAX_HISTORY_CHARS = 8000;

  // Build the prompt (conversation + system hint + market-data RAG) for the
  // current 开书助手 turn. Shared by send and the web-LLM preview, so the
  // copy-to-web-LLM panel always shows the exact prompt the API send would
  // use (including focused 题材/标签 the user clicked in the market panel).
  // When `text` is empty (panel preview before the user has typed), we omit
  // the fake "用户：..." line entirely so the copied prompt reads naturally
  // — the system_hint + conversation history are enough context for the
  // LLM to pick up from.
  const buildChatPrompt = async (text: string): Promise<{ fullPrompt: string; systemHint: string }> => {
    const config = getAgentConfig(studioTab);
    const recentMessages = chatMessages.filter(m => m.role !== "system").slice(-20);
    let conversationContext = recentMessages.map(m =>
      `${m.role === "user" ? "用户" : m.agentName || "AI"}：${m.content}`,
    ).join("\n\n");
    let truncatedNote = "";
    if (conversationContext.length > MAX_HISTORY_CHARS) {
      // Keep the tail so the most recent turns survive; mark the truncation
      // explicitly so the model understands the gap.
      const tail = conversationContext.slice(-MAX_HISTORY_CHARS);
      // Snap to the next message boundary so we don't start mid-sentence.
      const snap = tail.indexOf("\n\n");
      conversationContext = snap > 0 ? tail.slice(snap + 2) : tail;
      truncatedNote = `[较早对话已被截断，仅保留最近约 ${MAX_HISTORY_CHARS} 字]\n\n`;
    }
    const trimmedText = text.trim();
    let fullPrompt: string;
    if (recentMessages.length > 0 && trimmedText) {
      fullPrompt = `以下是对话历史：\n\n${truncatedNote}${conversationContext}\n\n用户：${trimmedText}\n\n请基于以上对话上下文回答用户最新的问题。`;
    } else if (recentMessages.length > 0) {
      // Preview mode without a typed message — surface the history only.
      fullPrompt = `以下是对话历史：\n\n${truncatedNote}${conversationContext}\n\n请基于以上对话上下文，按你的 Agent 角色给出下一轮回答。`;
    } else if (trimmedText) {
      fullPrompt = trimmedText;
    } else {
      // First-turn preview before any input — let the LLM open the conversation.
      fullPrompt = "请基于 system 中的市场数据与平台信息，以你的 Agent 角色主动开启对话。";
    }
    const promptKey = studioTab === "trending"
      ? "assistant.book_start_trending"
      : "assistant.book_start_brainstorm";
    const baseHint = await renderPrompt(promptKey, {}, config.systemHint);
    let systemHint = `${baseHint}

回答用户问题后，必须追加1个追问来引导用户进入下一步创作讨论。
追问格式：在回答末尾加上 [FOLLOW_UP]追问内容[/FOLLOW_UP][OPTIONS]选项A|选项B|选项C[/OPTIONS]
追问规则：3个选项，具体有区分度，不重复已确认内容。`;
    const brief = buildMarketBrief();
    if (brief) {
      const platformNote = activePlatformLabel ? `（${activePlatformLabel}）` : "（全平台）";
      const source = analysis?.panel ? "市场特征提取 · 基础特征提取" : "市场数据库";
      systemHint += `\n\n[市场数据参考${platformNote} · 来源：${source}——以下为真实统计，回答须据此，不要编造市场数据]\n${brief}`;
    }
    if (focusedMarketItems.length > 0) {
      // Surface the user's pinned 题材/标签 explicitly so the agent
      // weights them in its next answer (rather than re-deriving them from
      // the full panel). Order is click-order — first-clicked first.
      systemHint += `\n\n[用户关注的题材/标签]\n${focusedMarketItems.map(s => `· ${s}`).join("\n")}\n请围绕这些题材展开分析，并在追问选项中保留与之相关的选项。`;
    }
    return { fullPrompt, systemHint };
  };

  const fetchChatPrompt = async (): Promise<string> => {
    const { fullPrompt, systemHint } = await buildChatPrompt(chatInput);
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

  // Shared form body used by 新建 (renders ABOVE the grid) and 编辑
  // (renders inline inside the matching card). Same fields + handlers
  // so both flows stay in lockstep without prop-drilling 9 setters.
  const projectFormBody = (
    <>
      <div className="flex gap-12 mb-12" style={{ flexWrap: "wrap" }}>
        <div className="field" style={{ flex: 2, minWidth: 200 }}>
          <label className="label">书名 *</label>
          <input className="input" value={formName} onChange={e => setFormName(e.target.value)}
            placeholder="例：星辰大海" autoFocus
            onKeyDown={e => { if (e.key === "Enter") editingId ? handleUpdate() : handleCreate(); }} />
        </div>
        <div className="field" style={{ flex: 1, minWidth: 140 }}>
          <label className="label">发布平台</label>
          <select className="select" value={formPlatform}
            onChange={e => {
              const next = e.target.value;
              setFormPlatform(next);
              const prevProf = platformProfile(formPlatform);
              const nextProf = platformProfile(next);
              if (prevProf.id !== nextProf.id) {
                setFormGenre("");
                setFormCategory("");
              }
            }}
            style={{ width: "100%" }}>
            <option value="">未选择</option>
            {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div className="field" style={{ flex: 1, minWidth: 140 }}>
          <label className="label">
            主分类
            {formPlatform && formCategoryOptions.main.length > 0 && (
              <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                （{platformProfile(formPlatform).label}）
              </span>
            )}
          </label>
          <PlatformMainCategorySelect
            platform={formPlatform}
            value={formGenre}
            onChange={(v) => {
              setFormGenre(v);
              setFormCategory("");
            }}
            mainOptions={formCategoryOptions.main}
            loading={formCategoryLoading}
          />
        </div>
        <div className="field" style={{ flex: 1, minWidth: 140 }}>
          <label className="label">
            副分类
            {formGenre && (
              <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                （限 {formGenre} 下）
              </span>
            )}
          </label>
          <PlatformSubCategorySelect
            platform={formPlatform}
            mainCategory={formGenre}
            value={formCategory}
            onChange={setFormCategory}
            subOptions={formCategoryOptions.sub}
            loading={formCategoryLoading}
          />
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
      <div className="flex gap-8" style={{ justifyContent: "flex-end" }}>
        <button className="btn" onClick={cancelForm}>取消</button>
        <button className="btn-primary" onClick={editingId ? handleUpdate : handleCreate} disabled={!formName.trim()}>
          {editingId ? "保存" : "创建"}
        </button>
      </div>
    </>
  );

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

            {/* Create form — only shown above the card grid when creating a
                NEW project. Editing an existing project mounts the same
                fields inline inside the matching card (see below) so the
                form doesn't visually disconnect from the row it modifies. */}
            {showForm && !editingId && (
              <div className="card mb-24" style={{ animation: "slideUp 0.2s var(--ease-out)" }}>
                <div className="card-header"><h3>新建项目</h3></div>
                <div className="card-body">
                  {projectFormBody}
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
                  const isEditing = editingId === p.id;
                  return (
                    <div key={p.id} className="card" style={{
                      cursor: isEditing ? "default" : "pointer",
                      transition: "border-color 0.15s, box-shadow 0.2s, transform 0.2s var(--ease-out)",
                      borderColor: isActive ? "var(--accent)" : (isEditing ? "var(--gold)" : undefined),
                      boxShadow: isActive ? "0 0 12px var(--accent-glow)" : undefined,
                    }} onClick={() => { if (!isEditing) onSelectProject(p.id); }}>
                      <div style={{ height: 3, background: isActive ? "var(--accent)" : (isEditing ? "var(--gold)" : "var(--border)") }} />
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
                          {(p as any).category && <span className="tag category" style={{ fontSize: 10 }}>{(p as any).category}</span>}
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
                            <button className="btn-icon" title={isEditing ? "收起编辑" : "编辑"}
                              onClick={e => { e.stopPropagation(); isEditing ? cancelForm() : startEdit(p, e); }}>
                              {isEditing ? "▴" : "✎"}
                            </button>
                            <button className="btn-icon" title="进入编辑器" onClick={e => { e.stopPropagation(); onSelectProject(p.id); onNavigate("editor"); }}>&#8594;</button>
                            <button className="btn-icon" title="删除" onClick={e => handleDelete(p.id, e)} style={{ color: "var(--error)" }}>&#10005;</button>
                          </div>
                        </div>
                        {/* Inline edit form — replaces the legacy「单开一个 section」flow so
                            the form stays visually anchored to the card it edits. */}
                        {isEditing && (
                          <div onClick={e => e.stopPropagation()} style={{
                            marginTop: 12, paddingTop: 12,
                            borderTop: "1px dashed var(--gold)",
                          }}>
                            {projectFormBody}
                          </div>
                        )}
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
                  const isEditing = editingId === p.id;
                  return (
                    <div key={p.id} style={{ display: "flex", flexDirection: "column" }}>
                      <div className={`report-list-item ${isActive ? "active" : ""}`}
                        onClick={() => { if (!isEditing) onSelectProject(p.id); }}
                        style={{
                          borderRadius: "var(--radius-sm)", padding: "10px 16px",
                          borderColor: isEditing ? "var(--gold)" : undefined,
                          cursor: isEditing ? "default" : "pointer",
                        }}>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div className="flex items-center gap-8 mb-4">
                            <span className="font-serif" style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>{p.name}</span>
                            {p.genre && <span className="tag category" style={{ fontSize: 10 }}>{p.genre}</span>}
                            {(p as any).category && <span className="tag category" style={{ fontSize: 10 }}>{(p as any).category}</span>}
                            {(p as any).platform && <span className="tag qidian" style={{ fontSize: 10 }}>{(p as any).platform}</span>}
                          </div>
                          <div className="flex gap-16 text-xs text-muted">
                            <span>{(p.word_count || 0).toLocaleString()} 字</span>
                            <span>{p.chapter_count || 0} 章</span>
                            <span>创建于 {formatDate(p.created_at)}</span>
                          </div>
                        </div>
                        <div className="flex gap-4">
                          <button className="btn-icon" title={isEditing ? "收起编辑" : "编辑"}
                            onClick={e => { e.stopPropagation(); isEditing ? cancelForm() : startEdit(p, e); }}>
                            {isEditing ? "▴" : "✎"}
                          </button>
                          <button className="btn-icon" title="进入编辑器" onClick={e => { e.stopPropagation(); onSelectProject(p.id); onNavigate("editor"); }}>&#8594;</button>
                          <button className="btn-icon" title="删除" onClick={e => handleDelete(p.id, e)} style={{ color: "var(--error)" }}>&#10005;</button>
                        </div>
                      </div>
                      {isEditing && (
                        <div onClick={e => e.stopPropagation()} style={{
                          padding: "12px 16px",
                          border: "1px dashed var(--gold)",
                          borderTop: "none",
                          borderRadius: "0 0 var(--radius-sm) var(--radius-sm)",
                          background: "var(--bg-surface-2)",
                        }}>
                          {projectFormBody}
                        </div>
                      )}
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
                  <TrendingEmptyState
                    activePlatformLabel={activePlatformLabel}
                    activePlatformId={activePlatformId}
                    analysis={analysis}
                    analysisLoading={analysisLoading}
                    analysisStale={analysisStale}
                    trendingLoading={trendingLoading}
                    trendingTags={trendingTags}
                    focusedItems={focusedMarketItems}
                    onTogglePin={toggleFocusedItem}
                    onAsk={(prompt) => sendMessage(prompt)}
                    onOpenMarket={() => onNavigate("market-features")}
                  />
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


/* ── TrendingEmptyState ──
 * Hot-topic panel rendered above the empty Marketing-agent chat. Reuses the
 * 基础特征提取 (analysis/run) panel data — categories + tags + opportunity
 * scoring — so the assistant landing screen mirrors the rich market view the
 * user already curates on 市场特征提取 → 基础特征提取. Falls back to the
 * simpler tag_stats cloud when the panel cache is empty for this platform.
 */
type TrendingPanelRow = {
  name: string; total: number; avg_heat: number; latest_share: number;
  count_pct: number | null; heat_pct: number | null; share_pct: number | null;
  new_count?: number; parent?: string;
};
type TrendingAnalysis = {
  empty?: boolean;
  start_date?: string; end_date?: string;
  panel?: { categories?: TrendingPanelRow[]; tags?: TrendingPanelRow[] };
};

function TrendingEmptyState({
  activePlatformLabel, activePlatformId, analysis, analysisLoading,
  analysisStale, trendingLoading, trendingTags, focusedItems, onTogglePin,
  onAsk, onOpenMarket,
}: {
  activePlatformLabel: string;
  activePlatformId: string;
  analysis: TrendingAnalysis | null;
  analysisLoading: boolean;
  analysisStale: boolean;
  trendingLoading: boolean;
  trendingTags: { tag_name: string; novel_count: number }[];
  focusedItems: string[];
  onTogglePin: (label: string) => void;
  onAsk: (prompt: string) => void;
  onOpenMarket: () => void;
}) {
  const panel = analysis?.panel;
  const cats = (panel?.categories || []).slice(0, 8);
  const tags = (panel?.tags || []).slice(0, 10);
  const hasPanel = cats.length > 0 || tags.length > 0;
  const platformId = activePlatformId || "both";
  const isQidian = platformId === "qidian";
  const catLabel = isQidian ? "大分类" : "类目";
  const tagLabel = isQidian ? "副分类" : "标签";

  const pctText = (v: number | null | undefined): { text: string; color: string } => {
    if (v == null) return { text: "—", color: "var(--text-tertiary)" };
    const p = v * 100;
    const sign = p >= 0 ? "+" : "";
    const color = p >= 5 ? "var(--jade)" : p <= -5 ? "var(--error)" : "var(--text-tertiary)";
    return { text: `${sign}${p.toFixed(0)}%`, color };
  };

  const renderRows = (rows: TrendingPanelRow[]) => {
    const maxHeat = Math.max(1, ...rows.map(r => r.avg_heat || 0));
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {rows.map((r) => {
          const trendPct = pctText(r.heat_pct);
          const heatBar = Math.max(2, ((r.avg_heat || 0) / maxHeat) * 100);
          const displayName = r.parent ? `${r.parent}·${r.name}` : r.name;
          const pinned = focusedItems.includes(displayName);
          return (
            <div key={r.name} style={{
              display: "flex", alignItems: "center", gap: 6,
              padding: "5px 10px", borderRadius: 6,
              background: pinned ? "var(--accent-subtle)" : "var(--bg-surface-2)",
              border: `1px solid ${pinned ? "var(--accent)" : "var(--border)"}`,
              transition: "background 0.15s, border-color 0.15s",
            }}>
              <button
                onClick={() => onTogglePin(displayName)}
                title={pinned ? "取消关注（不再传入 system hint）" : "关注此题材，让 AI 围绕它展开分析"}
                style={{
                  width: 18, height: 18, padding: 0, flexShrink: 0,
                  background: pinned ? "var(--accent)" : "transparent",
                  border: `1px solid ${pinned ? "var(--accent)" : "var(--border)"}`,
                  borderRadius: 4, cursor: "pointer", fontSize: 11,
                  color: pinned ? "#fff" : "var(--text-tertiary)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}>
                {pinned ? "✓" : "+"}
              </button>
              <button
                onClick={() => onAsk(`分析一下「${r.name}」这个题材的市场前景、竞争程度和新人友好度`)}
                style={{
                  display: "flex", alignItems: "center", gap: 8,
                  padding: 0, background: "none", border: "none",
                  cursor: "pointer", textAlign: "left", flex: 1, minWidth: 0,
                }}>
                <span style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)", minWidth: 0, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {displayName}
                </span>
                <span className="font-mono" style={{ fontSize: 10, color: "var(--text-tertiary)", flexShrink: 0 }}>
                  {r.total}部
                </span>
                <div style={{ width: 50, height: 4, background: "var(--bg-app)", borderRadius: 2, overflow: "hidden", flexShrink: 0 }}>
                  <div style={{ width: `${heatBar}%`, height: "100%", background: "var(--accent)" }} />
                </div>
                <span className="font-mono" style={{ fontSize: 10, color: trendPct.color, fontWeight: 600, minWidth: 36, textAlign: "right", flexShrink: 0 }}>
                  {trendPct.text}
                </span>
              </button>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div style={{ padding: "16px" }}>
      <div style={{ padding: "12px 14px", background: "var(--accent-subtle)", borderRadius: 8, marginBottom: 12, borderLeft: "3px solid var(--accent)" }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent)", marginBottom: 4 }}>Marketing Agent</div>
        <div className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
          讨论题材在市场上是否吃香，是否新人友好等，帮助你确认想写的题材方向。
          {activePlatformLabel
            ? `数据来源于「${activePlatformLabel}」基础特征提取。`
            : "未设置发布平台时显示全平台综合数据，到项目设置中选择平台可获得平台专属市场分析。"}
        </div>
      </div>

      {focusedItems.length > 0 && (
        <div style={{
          padding: "8px 10px", background: "var(--accent-subtle)",
          border: "1px solid var(--accent)", borderRadius: 6, marginBottom: 10,
        }}>
          <div className="flex items-center justify-between mb-4">
            <span style={{ fontSize: 11, fontWeight: 600, color: "var(--accent)" }}>
              已关注 {focusedItems.length} 个题材（会进入 system hint）
            </span>
            <button
              onClick={() => focusedItems.forEach(i => onTogglePin(i))}
              className="btn" style={{ fontSize: 10, padding: "1px 8px" }}>
              清空
            </button>
          </div>
          <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
            {focusedItems.map(it => (
              <span key={it}
                onClick={() => onTogglePin(it)}
                title="点击移除关注"
                style={{
                  fontSize: 11, padding: "2px 8px", borderRadius: 12,
                  background: "var(--bg-surface)", color: "var(--accent)",
                  border: "1px solid var(--accent)", cursor: "pointer",
                }}>
                {it} ×
              </span>
            ))}
          </div>
        </div>
      )}

      {analysisStale && (
        <div style={{ padding: "6px 10px", background: "var(--gold-subtle, var(--bg-surface-2))", border: "1px solid var(--gold)", borderRadius: 6, marginBottom: 10, fontSize: 11, color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 6 }}>
          <span>市场数据已更新，当前展示上一次分析结果。</span>
          <button onClick={onOpenMarket} className="btn" style={{ fontSize: 10, padding: "1px 8px", marginLeft: "auto" }}>
            去市场特征提取重算
          </button>
        </div>
      )}

      {analysisLoading && !hasPanel ? (
        <div className="loading"><div className="loading-spinner" />加载市场数据...</div>
      ) : hasPanel ? (
        <>
          {(analysis?.start_date || analysis?.end_date) && (
            <div className="text-xs text-muted" style={{ marginBottom: 8 }}>
              时间区间 <span style={{ color: "var(--text-secondary)" }}>{analysis?.start_date || "—"} ~ {analysis?.end_date || "—"}</span>
            </div>
          )}
          {cats.length > 0 && (
            <div style={{ marginBottom: 14 }}>
              <div className="flex items-center justify-between mb-4">
                <span className="label" style={{ fontSize: 11, marginBottom: 0 }}>{catLabel} TOP {cats.length}</span>
                <span className="text-xs text-muted">数量 · 热度 · 趋势</span>
              </div>
              {renderRows(cats)}
            </div>
          )}
          {tags.length > 0 && (
            <div style={{ marginBottom: 14 }}>
              <div className="flex items-center justify-between mb-4">
                <span className="label" style={{ fontSize: 11, marginBottom: 0 }}>{tagLabel} TOP {tags.length}</span>
                <span className="text-xs text-muted">数量 · 热度 · 趋势</span>
              </div>
              {renderRows(tags)}
            </div>
          )}
          <div className="flex items-center justify-between mt-12">
            <span className="text-xs text-muted">点击任意题材让 Agent 展开分析</span>
            <button onClick={onOpenMarket} className="btn" style={{ fontSize: 10, padding: "2px 10px" }}>
              到市场特征提取查看完整面板
            </button>
          </div>
        </>
      ) : trendingLoading ? (
        <div className="loading"><div className="loading-spinner" />加载市场数据...</div>
      ) : trendingTags.length > 0 ? (
        <>
          <div className="flex items-center justify-between mb-8">
            <span className="label" style={{ fontSize: 11, marginBottom: 0 }}>
              热门题材标签 TOP {trendingTags.length}
            </span>
            <button onClick={onOpenMarket} className="btn" style={{ fontSize: 10, padding: "1px 8px" }}>
              到市场特征提取运行完整分析
            </button>
          </div>
          <div className="text-xs text-muted" style={{ marginBottom: 8, lineHeight: 1.5 }}>
            当前展示的是基础题材标签计数，运行基础特征提取后可查看带热度/趋势的完整面板。
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 16 }}>
            {trendingTags.slice(0, 20).map((tag, i) => {
              const isTop = i < 5;
              return (
                <button key={tag.tag_name}
                  onClick={() => onAsk(`分析一下「${tag.tag_name}」这个题材的市场前景和新人友好度`)}
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
        <div className="text-xs text-muted" style={{ padding: "8px 4px", lineHeight: 1.7 }}>
          {activePlatformLabel
            ? `当前平台「${activePlatformLabel}」暂无市场数据。`
            : "市场数据库暂无热门题材数据。"}
          <div style={{ marginTop: 8 }}>
            <button onClick={onOpenMarket} className="btn" style={{ fontSize: 10, padding: "2px 10px" }}>
              到市场特征提取运行分析
            </button>
          </div>
        </div>
      )}
    </div>
  );
}


/* ── PlatformMainCategorySelect ──
 * 主分类 (大分类) 单选 — 平台未选时退化为自由文本，平台已选但目录还没
 * 加载则显示占位提示。所选值会驱动 PlatformSubCategorySelect 的过滤。
 */
function PlatformMainCategorySelect({
  platform, value, onChange, mainOptions, loading,
}: {
  platform: string;
  value: string;
  onChange: (v: string) => void;
  mainOptions: { key: string; label: string; count?: number }[];
  loading: boolean;
}) {
  if (!platform || (!loading && mainOptions.length === 0)) {
    return (
      <input
        className="input"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={platform ? "该平台暂无主分类数据，可手动填写" : "请先选择发布平台"}
      />
    );
  }
  if (loading) {
    return (
      <div className="input" style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
        加载...
      </div>
    );
  }
  const validValues = new Set(mainOptions.map(o => o.key));
  const valueValid = !value || validValues.has(value);
  return (
    <div>
      <select
        className="select"
        value={valueValid ? value : ""}
        onChange={e => onChange(e.target.value)}
        style={{ width: "100%" }}>
        <option value="">未选择</option>
        {mainOptions.map(o => (
          <option key={o.key} value={o.key}>{o.label}</option>
        ))}
      </select>
      {!valueValid && (
        <div className="text-xs" style={{
          color: "var(--gold)", marginTop: 4, lineHeight: 1.5,
        }}>
          原主分类「{value}」不在所选平台的目录中，请重新选择。
        </div>
      )}
    </div>
  );
}

/* ── PlatformSubCategorySelect ──
 * 副分类 单选 — 仅显示所属主分类 (parent === mainCategory) 的副分类。
 * 主分类未选时禁用，但已选副分类值会保留显示作为弱提示。
 */
function PlatformSubCategorySelect({
  platform, mainCategory, value, onChange, subOptions, loading,
}: {
  platform: string;
  mainCategory: string;
  value: string;
  onChange: (v: string) => void;
  subOptions: { key: string; label: string; parent?: string | null; count?: number }[];
  loading: boolean;
}) {
  // Filter strictly by parent === mainCategory; subs without a known
  // parent ("其他") only show when there's no main category to scope by.
  const filtered = useMemo(() => {
    if (!mainCategory) return [];
    return subOptions.filter(s => (s.parent || "").trim() === mainCategory);
  }, [subOptions, mainCategory]);

  if (!platform) {
    return (
      <input className="input" value={value}
        onChange={e => onChange(e.target.value)}
        placeholder="请先选择发布平台" disabled />
    );
  }
  if (!mainCategory) {
    return (
      <input className="input" value={value}
        onChange={e => onChange(e.target.value)}
        placeholder="请先选择主分类" disabled />
    );
  }
  if (loading) {
    return (
      <div className="input" style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
        加载...
      </div>
    );
  }
  if (filtered.length === 0) {
    return (
      <input className="input" value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={`「${mainCategory}」下暂无副分类，可手动填写`} />
    );
  }
  const validValues = new Set(filtered.map(o => o.key));
  const valueValid = !value || validValues.has(value);
  return (
    <div>
      <select
        className="select"
        value={valueValid ? value : ""}
        onChange={e => onChange(e.target.value)}
        style={{ width: "100%" }}>
        <option value="">未选择</option>
        {filtered.map(o => (
          <option key={o.key} value={o.key}>{o.label}</option>
        ))}
      </select>
      {!valueValid && (
        <div className="text-xs" style={{
          color: "var(--gold)", marginTop: 4, lineHeight: 1.5,
        }}>
          原副分类「{value}」不属于「{mainCategory}」，请重新选择。
        </div>
      )}
    </div>
  );
}
