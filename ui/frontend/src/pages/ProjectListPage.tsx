import React, { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import type { Project, FollowUpQuestion, SampleFeedback, CalibrationHistory } from "../api/types";
import AIChatPanel, { ChatMessage } from "../components/shared/AIChatPanel";

interface Props {
  activeProject: string;
  onSelectProject: (id: string) => void;
  onNavigate: (tab: string) => void;
}

interface CalibrationState {
  tone: number;       // 0=轻松 100=严肃
  pacing: number;     // 0=快 100=慢
  rhetoric: number;   // 0=白描 100=华丽
  perspective: string; // "first" | "third"
  websiteStyle: string; // "qidian" | "fanqie" | "literary"
  audience: string;    // "male" | "female" | "general"
}

type StudioTab = "trending" | "brainstorm" | "calibration" | "preferences";

const STUDIO_TABS: { key: StudioTab; label: string; agent: string }[] = [
  { key: "trending", label: "热点题材", agent: "Marketing" },
  { key: "brainstorm", label: "头脑风暴", agent: "Story Architect" },
  { key: "calibration", label: "风格校准", agent: "Editor-Writer" },
  { key: "preferences", label: "偏好记忆", agent: "EditAnalyzer" },
];

const SAMPLE_TYPES: { key: string; label: string }[] = [
  { key: "opening", label: "开篇" },
  { key: "action", label: "动作打斗" },
  { key: "inner", label: "角色内心戏" },
  { key: "scenery", label: "场景描写" },
];

const PLATFORMS = ["起点中文网", "番茄小说", "晋江文学", "其他"];
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
  const [calibration, setCalibration] = useState<CalibrationState>(
    _savedStudio?.calibration || { tone: 50, pacing: 50, rhetoric: 50, perspective: "third", websiteStyle: "qidian", audience: "general" },
  );
  const [chatLoaded, setChatLoaded] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);

  // Trending data
  const [trendingTags, setTrendingTags] = useState<{ tag_name: string; novel_count: number }[]>([]);
  const [trendingLoading, setTrendingLoading] = useState(false);

  // Calibration sample
  const [sampleType, setSampleType] = useState("opening");
  const [calibrationSample, setCalibrationSample] = useState("");
  const [sampleGeneratedType, setSampleGeneratedType] = useState("");  // tracks which type the current sample was generated for
  const [sampleLoading, setSampleLoading] = useState(false);
  const [sampleFeedback, setSampleFeedback] = useState<SampleFeedback>({ score: 0, comment: "", confirmed: false });
  const [calibrationHistory, setCalibrationHistory] = useState<CalibrationHistory[]>([]);
  const [styleConfirmed, setStyleConfirmed] = useState(false);
  const [calibrationAnalysis, setCalibrationAnalysis] = useState("");
  const [lockedSamples, setLockedSamples] = useState<Record<string, string>>({});

  // Preferences (偏好记忆) state
  const [prefEntries, setPrefEntries] = useState<{ id: string; timestamp: string; action: string; detail: string; role?: string; scope?: string }[]>([]);
  // prefFilter removed — backend now only returns user inputs
  const [prefSummary, setPrefSummary] = useState("");
  const [prefLoading, setPrefLoading] = useState(false);
  // Extracted interaction memories (like project memory)
  const [extractedMemories, setExtractedMemories] = useState<{ id: string; content: string; source: string; timestamp: string }[]>([]);
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null);
  const [editingMemoryText, setEditingMemoryText] = useState("");

  // Derived: current tab's messages and setter
  const chatMessages = studioTab === "trending" ? trendingMessages : brainstormMessages;
  const setChatMessages = studioTab === "trending" ? setTrendingMessages : setBrainstormMessages;

  // Persist studio state to sessionStorage
  useEffect(() => {
    sessionStorage.setItem(STUDIO_SESS_KEY, JSON.stringify({ studioTab, trendingMessages, brainstormMessages, calibration, chatInput }));
  }, [studioTab, trendingMessages, brainstormMessages, calibration, chatInput, STUDIO_SESS_KEY]);

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
    calibration: "",
    preferences: "",
  };

  // Track previous tab for switch detection — no longer inject cross-tab guidance
  const prevStudioTabRef = useRef<StudioTab>(studioTab);
  useEffect(() => {
    prevStudioTabRef.current = studioTab;
  }, [studioTab]);

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

  // Load locked samples from backend on mount
  useEffect(() => {
    const pid = activeProject || "default";
    apiGet<{ locked_samples?: Record<string, string> }>(`/api/data/calibration/${pid}`)
      .then(r => {
        if (r.locked_samples) setLockedSamples(r.locked_samples);
      })
      .catch(() => {});
  }, [activeProject]);

  const rightPanel = useResizable({ direction: "horizontal", initialSize: 460, minSize: 340, maxSize: 720 });
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

  // Load trending tags
  const trendingLoaded = useRef(false);
  useEffect(() => {
    if (studioTab === "trending" && !trendingLoaded.current) {
      trendingLoaded.current = true;
      setTrendingLoading(true);
      apiGet<{ rows: { tag_name: string; novel_count: number }[] }>("/api/db/tag_stats?limit=30")
        .then(r => setTrendingTags(r.rows || []))
        .catch(() => {})
        .finally(() => setTrendingLoading(false));
    }
  }, [studioTab]);

  // Load preferences on tab switch
  useEffect(() => {
    if (studioTab === "preferences" && prefEntries.length === 0) {
      const pid = activeProject || "default";
      apiGet<{ entries: any[]; summary: string; extracted_memories?: any[] }>(`/api/data/preferences?project_id=${pid}`)
        .then(r => {
          if (r.entries) setPrefEntries(r.entries);
          if (r.summary) setPrefSummary(r.summary);
          if (r.extracted_memories) setExtractedMemories(r.extracted_memories);
        })
        .catch(() => {});
    }
  }, [studioTab, activeProject]);

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
    if (!confirm("确定删除该项目？此操作不可撤销。")) return;
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
      calibration: {
        agentName: "Editor-Writer",
        systemHint: "你是Editor-Writer，专注于风格校准。根据用户设置的文风参数生成校准样本。",
        placeholder: "描述你想要的文风...",
        quickPrompts: [],
      },
      preferences: {
        agentName: "EditAnalyzer",
        systemHint: "你是EditAnalyzer，专注于分析用户的编辑偏好和创作习惯，总结偏好以改进AI辅助。",
        placeholder: "询问你的创作偏好分析...",
        quickPrompts: [
          "分析一下我目前的创作偏好",
          "根据我的编辑历史，我倾向于什么样的文风？",
        ],
      },
    };
    return configs[tab];
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
      const recentMessages = chatMessages.filter(m => m.role !== "system").slice(-20);
      const conversationContext = recentMessages.map(m =>
        `${m.role === "user" ? "用户" : m.agentName || "AI"}：${m.content}`
      ).join("\n\n");

      const fullPrompt = recentMessages.length > 0
        ? `以下是对话历史：\n\n${conversationContext}\n\n用户：${text}\n\n请基于以上对话上下文回答用户最新的问题。`
        : text;

      const systemHint = `${config.systemHint}

回答用户问题后，必须追加1个追问来引导用户进入下一步创作讨论。
追问格式：在回答末尾加上 [FOLLOW_UP]追问内容[/FOLLOW_UP][OPTIONS]选项A|选项B|选项C[/OPTIONS]
追问规则：3个选项，具体有区分度，不重复已确认内容。`;

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

  const generateCalibrationSample = async () => {
    setSampleLoading(true);
    setCalibrationSample("");
    setCalibrationAnalysis("");
    setSampleFeedback({ score: 0, comment: "", confirmed: false });
    try {
      const toneDesc = calibration.tone < 30 ? "轻松幽默" : calibration.tone > 70 ? "严肃深沉" : "均衡";
      const pacingDesc = calibration.pacing < 30 ? "快节奏" : calibration.pacing > 70 ? "慢热" : "中等节奏";
      const rhetoricDesc = calibration.rhetoric < 30 ? "白描直接、修辞极少" : calibration.rhetoric > 70 ? "华丽文笔、大量修辞" : "适度修辞";
      const perspDesc = calibration.perspective === "first" ? "第一人称" : "第三人称";
      const websiteMap: Record<string, string> = { qidian: "起点中文网风格", fanqie: "番茄小说风格", literary: "出版文学风格" };
      const websiteDesc = websiteMap[calibration.websiteStyle] || "起点中文网风格";
      const audienceMap: Record<string, string> = { male: "男频", female: "女频", general: "大众（不特化目标受众）" };
      const audienceDesc = audienceMap[calibration.audience] || "大众";
      const typeDesc: Record<string, string> = {
        opening: "小说开篇（300-500字）",
        action: "一段动作/打斗场景（300-500字）",
        inner: "一段角色内心戏/心理描写（300-500字）",
        scenery: "一段场景描写（300-500字）",
      };

      // Only include the most recent 3 rounds of feedback to avoid prompt bloat
      let feedbackContext = "";
      const recentHistory = calibrationHistory.slice(-3);
      if (recentHistory.length > 0) {
        feedbackContext = "\n\n用户最近的反馈（请据此调整）：\n" + recentHistory.map((h, i) =>
          `第${calibrationHistory.length - recentHistory.length + i + 1}轮（${h.feedback.score}/5分）：${h.feedback.comment || "无具体评论"}`
        ).join("\n");
      }

      const prompt = `请生成一段${typeDesc[sampleType] || "短文"}的校准样本。

风格要求：
- 文风：${toneDesc}
- 剧情节奏：${pacingDesc}
- 修辞力度：${rhetoricDesc}
- 叙事视角：${perspDesc}
- 网站风格：${websiteDesc}
- 目标受众：${audienceDesc}${feedbackContext}

【重要】请直接输出纯文学内容，不要包含标题、说明文字、JSON、代码块或任何格式标记。只输出小说正文。`;

      const resp = await apiPost<{ text: string }>("/api/generation/quick-generate", {
        project_id: activeProject || "default",
        chapter_id: "calibration_sample",
        synopsis: prompt,
        system_hint: "你是一个专业的网文写手。你的任务是根据给定的风格参数生成一段校准样本。【关键规则】只输出纯小说正文，绝对不要输出JSON、代码块、标题、分析、说明文字。直接以小说内容开头。",
      });

      let sampleText = resp.text || "";
      // Strip potential JSON wrapper (defense in depth)
      try {
        const parsed = JSON.parse(sampleText);
        if (parsed?.text) sampleText = parsed.text;
        else if (typeof parsed === "string") sampleText = parsed;
        if (parsed?.analysis) setCalibrationAnalysis(parsed.analysis);
      } catch { /* use raw text — expected path */ }
      // Aggressively clean up any remaining format artifacts
      sampleText = sampleText
        .replace(/^```(?:json|text|markdown)?\s*/i, "")
        .replace(/\s*```\s*$/, "")
        .replace(/^["']|["']$/g, "")
        .replace(/^\{[\s\S]*?"text"\s*:\s*"/, "")
        .replace(/"\s*\}\s*$/, "")
        .replace(/^#+\s+.*\n/gm, "")  // remove markdown headers
        .replace(/^【[^】]*】\s*/gm, "")  // remove 【标题】 prefixes
        .replace(/^(?:校准样本|样本|以下是|生成内容)[：:]\s*/i, "")
        .trim();
      setCalibrationSample(sampleText);
      setSampleGeneratedType(sampleType);
    } catch (e: any) {
      setCalibrationSample(`生成失败：${e?.message || "请检查模型连接"}`);
      setSampleGeneratedType(sampleType);
    }
    setSampleLoading(false);
  };

  const submitCalibrationFeedback = async () => {
    if (!calibrationSample || sampleFeedback.score === 0) return;
    const entry: CalibrationHistory = {
      sample: calibrationSample,
      feedback: { ...sampleFeedback },
      analysis: calibrationAnalysis,
    };
    setCalibrationHistory(prev => [...prev, entry]);
    const pid = activeProject || "default";
    apiPut(`/api/data/calibration/${pid}`, {
      history: [...calibrationHistory, entry], style_params: calibration, confirmed: false, locked_samples: lockedSamples,
    }).catch(() => {});
    generateCalibrationSample();
  };

  const confirmStyle = async () => {
    setStyleConfirmed(true);
    const pid = activeProject || "default";
    apiPut(`/api/data/calibration/${pid}`, {
      history: calibrationHistory, style_params: calibration, confirmed: true, locked_samples: lockedSamples,
    }).catch(() => {});
  };

  const cancelConfirmStyle = () => {
    setStyleConfirmed(false);
    const pid = activeProject || "default";
    apiPut(`/api/data/calibration/${pid}`, {
      history: calibrationHistory, style_params: calibration, confirmed: false, locked_samples: lockedSamples,
    }).catch(() => {});
  };

  const toggleLockSample = () => {
    if (!calibrationSample) return;
    setLockedSamples(prev => {
      const next = { ...prev };
      if (next[sampleType]) {
        delete next[sampleType];
      } else {
        next[sampleType] = calibrationSample;
      }
      const pid = activeProject || "default";
      apiPut(`/api/data/calibration/${pid}`, {
        history: calibrationHistory, style_params: calibration, confirmed: styleConfirmed, locked_samples: next,
      }).catch(() => {});
      return next;
    });
  };

  const analyzePrefences = async () => {
    setPrefLoading(true);
    try {
      const resp = await apiPost<{ summary: string; entries: any[]; extracted_memories?: any[] }>("/api/data/preferences/analyze", {
        project_id: activeProject || "default",
      });
      if (resp.summary) setPrefSummary(resp.summary);
      if (resp.entries) setPrefEntries(resp.entries);
      if (resp.extracted_memories) setExtractedMemories(resp.extracted_memories);
    } catch { /* ignore */ }
    setPrefLoading(false);
  };

  const removeExtractedMemory = (id: string) => {
    const pid = activeProject || "default";
    setExtractedMemories(prev => prev.filter(m => m.id !== id));
    apiDelete(`/api/data/preferences/memory/${id}?project_id=${pid}`).catch(() => {});
  };

  const updateExtractedMemory = (id: string, newContent: string) => {
    const pid = activeProject || "default";
    setExtractedMemories(prev => prev.map(m => m.id === id ? { ...m, content: newContent } : m));
    setEditingMemoryId(null);
    apiPut(`/api/data/preferences/memory/${id}`, { project_id: pid, content: newContent }).catch(() => {});
  };

  const removePrefEntry = (id: string) => {
    const pid = activeProject || "default";
    setPrefEntries(prev => prev.filter(e => e.id !== id));
    apiDelete(`/api/data/preferences/${id}?project_id=${pid}`).catch(() => {});
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
        {/* ══════ LEFT PANEL: Project Management ══════ */}
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
                <div className="empty-icon">&#x1F4C2;</div>
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

        <div className="panel-resize-h" {...rightPanel.handleProps} />

        {/* ══════ RIGHT PANEL: AI 开书助手 ══════ */}
        <div className="panel" style={{ width: rightPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderLeft: "1px solid var(--border)", display: "flex", flexDirection: "column" }}>
          <div className="panel-header" style={{ flexDirection: "column", alignItems: "stretch", gap: 6, paddingBottom: 8 }}>
            <div className="flex items-center justify-between">
              <h3 style={{ fontSize: 15, fontWeight: 700 }}>AI 开书助手</h3>
            </div>
            <div className="text-xs text-muted">
              {projects.find(p => p.id === activeProject)?.name || "未选择项目"}
              {currentTabAgent && <span style={{ marginLeft: 8, color: "var(--accent)" }}>Agent: {currentTabAgent}</span>}
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
              /* ── 热点题材 (Marketing Agent chat) ── */
              <AIChatPanel
                messages={trendingMessages}
                onSendMessage={sendMessage}
                onStopGeneration={stopGeneration}
                onRegenerateMessage={handleRegenerate}
                onSelectFollowUpOption={handleFollowUpSelect}
                isGenerating={aiLoading}
                preservedInput={chatInput}
                onInputChange={setChatInput}
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
                      </div>
                    </div>
                    {/* Trending tag cloud */}
                    {trendingLoading ? (
                      <div className="loading"><div className="loading-spinner" />加载市场数据...</div>
                    ) : trendingTags.length > 0 && (
                      <>
                        <div className="label mb-8" style={{ fontSize: 11 }}>热门题材标签 TOP {trendingTags.length}</div>
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
                    )}
                  </div>
                }
              />
            ) : studioTab === "brainstorm" ? (
              /* ── 头脑风暴 (Story Architect chat) ── */
              <AIChatPanel
                messages={brainstormMessages}
                onSendMessage={sendMessage}
                onStopGeneration={stopGeneration}
                onRegenerateMessage={handleRegenerate}
                onSelectFollowUpOption={handleFollowUpSelect}
                isGenerating={aiLoading}
                preservedInput={chatInput}
                onInputChange={setChatInput}
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
            ) : studioTab === "calibration" ? (
              /* ── 风格校准 (Editor-Writer) ── */
              <div style={{ padding: 16, overflowY: "auto", flex: 1 }}>
                <div style={{ padding: "12px 14px", background: "var(--jade-subtle)", borderRadius: 8, marginBottom: 16, borderLeft: "3px solid var(--jade)" }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--jade)", marginBottom: 4 }}>Editor-Writer Agent</div>
                  <div className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
                    生成样本文字供你确认行文风格。打分并评论后可重新生成，确认满意后写入参考风格。
                  </div>
                </div>

                <div className="card mb-16">
                  <div className="card-body">
                    {/* A) 文风 slider */}
                    <div style={{ marginBottom: 18 }}>
                      <div className="flex items-center justify-between mb-4">
                        <span className="label" style={{ marginBottom: 0 }}>文风</span>
                        <span className="text-xs font-mono" style={{ color: "var(--text-tertiary)" }}>
                          {calibration.tone < 30 ? "轻松幽默" : calibration.tone > 70 ? "严肃深沉" : "均衡"}
                        </span>
                      </div>
                      <div className="flex items-center gap-8">
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 50 }}>轻松幽默</span>
                        <input type="range" min={0} max={100} value={calibration.tone}
                          onChange={e => setCalibration(prev => ({ ...prev, tone: +e.target.value }))}
                          style={{ flex: 1, accentColor: "var(--accent)" }} />
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 50, textAlign: "right" }}>沉稳严肃</span>
                      </div>
                    </div>

                    {/* B) 剧情节奏 slider */}
                    <div style={{ marginBottom: 18 }}>
                      <div className="flex items-center justify-between mb-4">
                        <span className="label" style={{ marginBottom: 0 }}>剧情节奏</span>
                        <span className="text-xs font-mono" style={{ color: "var(--text-tertiary)" }}>
                          {calibration.pacing < 30 ? "快节奏" : calibration.pacing > 70 ? "慢热" : "中等"}
                        </span>
                      </div>
                      <div className="flex items-center gap-8">
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 50 }}>快节奏</span>
                        <input type="range" min={0} max={100} value={calibration.pacing}
                          onChange={e => setCalibration(prev => ({ ...prev, pacing: +e.target.value }))}
                          style={{ flex: 1, accentColor: "var(--accent)" }} />
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 50, textAlign: "right" }}>慢热</span>
                      </div>
                    </div>

                    {/* C) 修辞力度 slider */}
                    <div style={{ marginBottom: 18 }}>
                      <div className="flex items-center justify-between mb-4">
                        <span className="label" style={{ marginBottom: 0 }}>修辞力度</span>
                        <span className="text-xs font-mono" style={{ color: "var(--text-tertiary)" }}>
                          {calibration.rhetoric < 30 ? "白描直接" : calibration.rhetoric > 70 ? "华丽修辞" : "适度"}
                        </span>
                      </div>
                      <div className="flex items-center gap-8">
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 50 }}>白描</span>
                        <input type="range" min={0} max={100} value={calibration.rhetoric}
                          onChange={e => setCalibration(prev => ({ ...prev, rhetoric: +e.target.value }))}
                          style={{ flex: 1, accentColor: "var(--accent)" }} />
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 50, textAlign: "right" }}>华丽</span>
                      </div>
                    </div>

                    {/* D) 叙事视角 */}
                    <div style={{ marginBottom: 18 }}>
                      <span className="label" style={{ marginBottom: 6, display: "block" }}>叙事视角</span>
                      <div className="flex gap-6">
                        {([["first", "第一人称"], ["third", "第三人称"]] as const).map(([val, label]) => (
                          <button key={val} className={calibration.perspective === val ? "btn-primary" : "btn"}
                            style={{ flex: 1, fontSize: 12, padding: "6px 0", borderRadius: 20, textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center" }}
                            onClick={() => setCalibration(prev => ({ ...prev, perspective: val }))}>{label}</button>
                        ))}
                      </div>
                    </div>

                    {/* E) 网站风格 */}
                    <div style={{ marginBottom: 18 }}>
                      <span className="label" style={{ marginBottom: 6, display: "block" }}>网站风格</span>
                      <div className="flex gap-6">
                        {([["qidian", "起点"], ["fanqie", "番茄"], ["literary", "出版文学"]] as const).map(([val, label]) => (
                          <button key={val} className={calibration.websiteStyle === val ? "btn-primary" : "btn"}
                            style={{ flex: 1, fontSize: 12, padding: "6px 0", borderRadius: 20, textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center" }}
                            onClick={() => setCalibration(prev => ({ ...prev, websiteStyle: val }))}>{label}</button>
                        ))}
                      </div>
                    </div>

                    {/* F) 目标受众 */}
                    <div>
                      <span className="label" style={{ marginBottom: 6, display: "block" }}>目标受众</span>
                      <div className="flex gap-6">
                        {([["male", "男频"], ["female", "女频"], ["general", "大众"]] as const).map(([val, label]) => (
                          <button key={val} className={calibration.audience === val ? "btn-primary" : "btn"}
                            style={{ flex: 1, fontSize: 12, padding: "6px 0", borderRadius: 20, textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center" }}
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
                    <div className="flex gap-6 mb-12" style={{ flexWrap: "wrap" }}>
                      {SAMPLE_TYPES.map(t => (
                        <button key={t.key} className={sampleType === t.key ? "btn-primary" : "btn"}
                          style={{ flex: 1, fontSize: 11, padding: "5px 0", borderRadius: 16, minWidth: 70, textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center", position: "relative" }}
                          onClick={() => setSampleType(t.key)}>
                          {t.label}
                          {lockedSamples[t.key] && <span style={{ marginLeft: 3, fontSize: 9 }} title="已锁定参考样本">🔒</span>}
                        </button>
                      ))}
                    </div>
                    <button className="btn-primary" style={{ width: "100%", marginBottom: 8 }}
                      onClick={generateCalibrationSample} disabled={sampleLoading}>
                      {sampleLoading ? "生成中..." : (calibrationSample && sampleGeneratedType === sampleType) ? "重新生成" : "生成样本段落"}
                    </button>

                    {calibrationSample && sampleGeneratedType === sampleType && (
                      <div>
                        <div style={{
                          marginTop: 8, padding: "12px 14px", background: "var(--bg-surface-2)", borderRadius: 8,
                          borderLeft: "3px solid var(--jade)", fontSize: 13, lineHeight: 1.8, color: "var(--text-primary)",
                          whiteSpace: "pre-wrap", maxHeight: 300, overflowY: "auto", fontFamily: "var(--font-serif)",
                          userSelect: "text", cursor: "text",
                        }}>
                          {calibrationSample}
                        </div>

                        {calibrationAnalysis && (
                          <div style={{
                            marginTop: 8, padding: "8px 12px", background: "var(--accent-subtle)", borderRadius: 6,
                            borderLeft: "3px solid var(--accent)", fontSize: 12, lineHeight: 1.6, color: "var(--text-secondary)",
                          }}>
                            <span style={{ fontWeight: 600, color: "var(--accent)" }}>AI 分析：</span>{calibrationAnalysis}
                          </div>
                        )}

                        {/* Star Rating */}
                        <div style={{ marginTop: 12, display: "flex", alignItems: "center", gap: 8 }}>
                          <span className="label" style={{ marginBottom: 0, fontSize: 11 }}>评分：</span>
                          <div style={{ display: "flex", gap: 2 }}>
                            {[1, 2, 3, 4, 5].map(star => (
                              <button key={star}
                                onClick={() => setSampleFeedback(prev => ({ ...prev, score: star }))}
                                style={{
                                  fontSize: 20, background: "none", border: "none", cursor: "pointer",
                                  color: star <= sampleFeedback.score ? "var(--gold)" : "var(--border)",
                                  transition: "color 0.15s, transform 0.15s",
                                }}
                                onMouseEnter={e => { e.currentTarget.style.transform = "scale(1.2)"; }}
                                onMouseLeave={e => { e.currentTarget.style.transform = "scale(1)"; }}
                              >&#9733;</button>
                            ))}
                          </div>
                          {sampleFeedback.score > 0 && (
                            <span className="text-xs text-muted">
                              {sampleFeedback.score <= 2 ? "不满意" : sampleFeedback.score <= 3 ? "一般" : sampleFeedback.score <= 4 ? "满意" : "非常好"}
                            </span>
                          )}
                        </div>

                        <div style={{ marginTop: 8 }}>
                          <textarea className="input" value={sampleFeedback.comment}
                            onChange={e => setSampleFeedback(prev => ({ ...prev, comment: e.target.value }))}
                            rows={2} placeholder="告诉AI你喜欢/不喜欢什么..."
                            style={{ fontSize: 12, lineHeight: 1.6, width: "100%", boxSizing: "border-box" }} />
                        </div>

                        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                          <button className="btn" style={{ flex: 1, fontSize: 11 }}
                            onClick={submitCalibrationFeedback}
                            disabled={sampleFeedback.score === 0 || sampleLoading}>
                            提交反馈 & 生成改进样本
                          </button>
                          <button className={lockedSamples[sampleType] ? "btn" : "btn-primary"}
                            style={{ flex: 1, fontSize: 11,
                              background: lockedSamples[sampleType] ? undefined : "var(--gold)",
                              borderColor: lockedSamples[sampleType] ? "var(--gold)" : "var(--gold)",
                              color: lockedSamples[sampleType] ? "var(--gold)" : "#fff",
                              border: `1px solid var(--gold)`,
                            }}
                            onClick={toggleLockSample}>
                            {lockedSamples[sampleType] ? "取消锁定" : "锁定参考"}
                          </button>
                        </div>

                        {/* Feedback history */}
                        {calibrationHistory.length > 0 && (
                          <div style={{ marginTop: 12 }}>
                            <div className="label" style={{ fontSize: 10, color: "var(--text-tertiary)", marginBottom: 4 }}>
                              校准历史 ({calibrationHistory.length} 轮)
                            </div>
                            {calibrationHistory.map((h, hi) => (
                              <div key={hi} style={{
                                padding: "6px 10px", background: "var(--bg-surface-2)", borderRadius: 6, marginBottom: 4,
                                fontSize: 11, color: "var(--text-tertiary)", display: "flex", alignItems: "center", gap: 8,
                              }}>
                                <span style={{ color: "var(--gold)" }}>{"★".repeat(h.feedback.score)}{"☆".repeat(5 - h.feedback.score)}</span>
                                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {h.feedback.comment || "无评论"}
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}

                    {/* Removed old confirmStyle banner — locking is now per-sample-type via "锁定参考" button */}
                  </div>
                </div>

                {/* Locked reference samples */}
                {Object.keys(lockedSamples).length > 0 && (
                  <div className="card">
                    <div className="card-body">
                      <div className="label mb-8">🔒 锁定参考样本</div>
                      <div className="text-xs text-muted" style={{ marginBottom: 8 }}>
                        已锁定 {Object.keys(lockedSamples).length}/4 种类型，生成时将作为风格参考。
                      </div>
                      {SAMPLE_TYPES.filter(t => lockedSamples[t.key]).map(t => (
                        <div key={t.key} style={{ marginBottom: 8 }}>
                          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
                            <span style={{ fontSize: 11, fontWeight: 600, color: "var(--gold)" }}>🔒 {t.label}</span>
                            <button className="btn" style={{ fontSize: 10, padding: "1px 8px", color: "var(--text-tertiary)" }}
                              onClick={() => {
                                setLockedSamples(prev => {
                                  const next = { ...prev };
                                  delete next[t.key];
                                  const pid = activeProject || "default";
                                  apiPut(`/api/data/calibration/${pid}`, {
                                    history: calibrationHistory, style_params: calibration, confirmed: styleConfirmed, locked_samples: next,
                                  }).catch(() => {});
                                  return next;
                                });
                              }}>
                              解锁
                            </button>
                          </div>
                          <div style={{
                            padding: "8px 10px", background: "var(--bg-surface-2)", borderRadius: 6,
                            borderLeft: "2px solid var(--gold)", fontSize: 12, lineHeight: 1.6,
                            color: "var(--text-secondary)", whiteSpace: "pre-wrap", maxHeight: 120, overflowY: "auto",
                          }}>
                            {lockedSamples[t.key].length > 200 ? lockedSamples[t.key].slice(0, 200) + "..." : lockedSamples[t.key]}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              /* ── 偏好记忆 (EditAnalyzer) ── */
              <div style={{ padding: 16, overflowY: "auto", flex: 1 }}>
                <div style={{ padding: "12px 14px", background: "var(--purple-subtle)", borderRadius: 8, marginBottom: 16, borderLeft: "3px solid var(--purple)" }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--purple)", marginBottom: 4 }}>EditAnalyzer Agent</div>
                  <div className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
                    根据你在本项目内所做的所有请求及编辑，总结创作偏好并改进各AI能力。你可以增加、更改或删除条目。
                  </div>
                </div>

                <button className="btn-primary mb-16" style={{ width: "100%" }}
                  onClick={analyzePrefences} disabled={prefLoading}>
                  {prefLoading ? "分析中..." : prefEntries.length > 0 ? "刷新交互记录" : "收集交互记录"}
                </button>

                {/* Summary */}
                {prefSummary && (
                  <div className="card mb-16">
                    <div className="card-header"><h3>交互总结</h3></div>
                    <div className="card-body" style={{ fontSize: 13, lineHeight: 1.7, color: "var(--text-secondary)", userSelect: "text" }}>
                      {prefSummary}
                    </div>
                  </div>
                )}

                {/* Extracted interaction memories (like project memory) */}
                {extractedMemories.length > 0 && (
                  <div className="card mb-16">
                    <div className="card-header">
                      <h3>提取的创作偏好</h3>
                      <span className="text-xs text-muted">{extractedMemories.length} 条 · 从交互记录中自动提取</span>
                    </div>
                    <div className="card-body" style={{ padding: 0 }}>
                      {extractedMemories.map((mem) => (
                        <div key={mem.id} style={{
                          padding: "10px 16px", borderBottom: "1px solid var(--border-subtle)",
                          display: "flex", alignItems: "flex-start", gap: 10,
                          borderLeft: "3px solid var(--indigo)",
                        }}>
                          <div style={{
                            width: 22, height: 22, borderRadius: "50%", flexShrink: 0, marginTop: 2,
                            background: "var(--indigo-subtle)", border: "1.5px solid var(--indigo)",
                            display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10,
                            color: "var(--indigo)",
                          }}>M</div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div className="flex items-center gap-8 mb-4">
                              <span className="text-xs font-mono" style={{ color: "var(--text-disabled)" }}>{mem.timestamp}</span>
                              <span className="tag category" style={{ fontSize: 10, background: "var(--indigo-subtle)", color: "var(--indigo)" }}>{mem.source}</span>
                            </div>
                            {editingMemoryId === mem.id ? (
                              <div>
                                <textarea className="input" value={editingMemoryText}
                                  onChange={e => setEditingMemoryText(e.target.value)}
                                  rows={2} style={{ fontSize: 12, width: "100%", boxSizing: "border-box", marginBottom: 6 }} />
                                <div className="flex gap-4">
                                  <button className="btn-primary" style={{ fontSize: 10, padding: "3px 10px" }}
                                    onClick={() => updateExtractedMemory(mem.id, editingMemoryText)}>保存</button>
                                  <button className="btn" style={{ fontSize: 10, padding: "3px 10px" }}
                                    onClick={() => setEditingMemoryId(null)}>取消</button>
                                </div>
                              </div>
                            ) : (
                              <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                                {mem.content}
                              </div>
                            )}
                          </div>
                          <div className="flex gap-2" style={{ flexShrink: 0 }}>
                            <button className="btn-icon" style={{ fontSize: 11 }}
                              onClick={() => { setEditingMemoryId(mem.id); setEditingMemoryText(mem.content); }}
                              title="编辑">&#9998;</button>
                            <button className="btn-icon" style={{ fontSize: 12 }}
                              onClick={() => removeExtractedMemory(mem.id)}
                              title="删除">&times;</button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Entries list (user inputs only) */}
                {prefEntries.length > 0 && (
                  <div className="card">
                    <div className="card-header">
                      <h3>用户交互记录</h3>
                      <span className="text-xs text-muted">{prefEntries.length} 条</span>
                    </div>
                    <div className="card-body" style={{ padding: 0, maxHeight: 400, overflowY: "auto" }}>
                      {prefEntries.map((entry) => (
                        <div key={entry.id} style={{
                          padding: "10px 16px", borderBottom: "1px solid var(--border-subtle)",
                          display: "flex", alignItems: "flex-start", gap: 10,
                          borderLeft: "3px solid var(--purple)",
                        }}>
                          <div style={{
                            width: 22, height: 22, borderRadius: "50%", flexShrink: 0, marginTop: 2,
                            background: "var(--purple-subtle)", border: "1.5px solid var(--purple)",
                            display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10,
                          }}>U</div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div className="flex items-center gap-8 mb-4">
                              <span className="text-xs font-mono" style={{ color: "var(--text-disabled)" }}>{entry.timestamp}</span>
                              <span className="tag category" style={{ fontSize: 10 }}>{entry.action}</span>
                            </div>
                            <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                              {entry.detail.length > 300 ? entry.detail.slice(0, 300) + "..." : entry.detail}
                            </div>
                          </div>
                          <button className="btn-icon" style={{ fontSize: 12, flexShrink: 0 }}
                            onClick={() => removePrefEntry(entry.id)}>&times;</button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {prefEntries.length === 0 && !prefSummary && (
                  <div className="empty-state" style={{ padding: 32 }}>
                    <p>点击上方按钮收集所有AI对话交互记录</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
