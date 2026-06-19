import React, { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import { useToast } from "../components/shared/Toast";
import { useDialog } from "../components/shared/Dialog";
import WebLLMPromptPanel from "../components/shared/WebLLMPromptPanel";
import ChapterTimeline from "../components/shared/ChapterTimeline";
import SnapshotStageEditor from "../components/characters/SnapshotStageEditor";
import NameGeneratorModal from "../components/characters/NameGeneratorModal";
import type { Character, CharacterLayerB, CharacterRelationship, DynamicPropertySnapshot } from "../api/types";
import { renderPrompt } from "../utils/promptTemplate";

interface CharChatMsg {
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

interface Props {
  projectId: string;
  projects: any[];
}

const ROLES = ["主角", "配角", "反派", "路人"];
const GENDERS = ["男", "女", "其他"];

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
  const { toast } = useToast();
  const { confirm } = useDialog();
  const [items, setItems] = useState<Character[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Character | null>(null);
  const [dirty, setDirty] = useState(false);
  const [search, setSearch] = useState("");
  const [relTarget, setRelTarget] = useState("");
  const [rightView, setRightView] = useState<"detail" | "graph">("detail");
  const [showNamer, setShowNamer] = useState(false);   // 取名弹窗

  // Warn before leaving with unsaved changes
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

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

  // Dynamic properties flashcard state
  const [flashcardIndex, setFlashcardIndex] = useState(0);

  // Per-row edit toggles inside 关系 / 隐藏身份 sections — each row has
  // its own ✎/× icons, so the section header just hosts a 「+ 添加」 button.
  // Reset whenever the user switches snapshots or characters so editing
  // state can't bleed across cards.
  const [editingRelRows, setEditingRelRows] = useState<Set<number>>(new Set());
  const [editingHiddenRows, setEditingHiddenRows] = useState<Set<number>>(new Set());
  // 决策参数 random sample preview — 10 scenarios where the character
  // picks A vs B based on a probability derived from Layer B params.
  const [decisionPreview, setDecisionPreview] = useState<Array<{
    scenario: string; optionA: string; optionB: string; pB: number; reason: string;
  }> | null>(null);
  useEffect(() => {
    setEditingRelRows(new Set());
    setEditingHiddenRows(new Set());
    setDecisionPreview(null);
  }, [flashcardIndex, editing?.id]);
  const toggleRelEdit = (idx: number) => setEditingRelRows(prev => {
    const next = new Set(prev);
    if (next.has(idx)) next.delete(idx); else next.add(idx);
    return next;
  });
  const toggleHiddenEdit = (idx: number) => setEditingHiddenRows(prev => {
    const next = new Set(prev);
    if (next.has(idx)) next.delete(idx); else next.add(idx);
    return next;
  });

  // Dynamic section sub-tab: snapshots, relationships, layerB
  const [dynTab, setDynTab] = useState<"snapshots">("snapshots");

  // Relationship time filter
  const [relTimeFilter, setRelTimeFilter] = useState("");

  // ── Editor-driven chapter range for the global graph timeline.
  // We pull from /api/data/editor so the timeline reflects the project's
  // actual chapter spine (not just chapters that happen to have relationship
  // markers). This means the timeline is ALWAYS visible once the editor
  // has chapters, and the user can drag the window to see relationship
  // states at any point in the story.
  const [editorChapterCount, setEditorChapterCount] = useState(0);
  useEffect(() => {
    const pid = projectId || "default";
    apiGet<{ volumes: any[] }>(`/api/data/editor?project_id=${pid}`)
      .then(r => {
        const count = (r.volumes || []).reduce(
          (sum, v) => sum + ((v.chapters || []).length),
          0,
        );
        setEditorChapterCount(count);
      })
      .catch(() => setEditorChapterCount(0));
  }, [projectId]);

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

  // Shared system_hint construction — used by both "send" and the web-LLM prompt preview
  const buildCharChatSystemHint = useCallback(async () => {
    if (!editing) return "";
    return renderPrompt(
      "assistant.character",
      {
        char_name: editing.name,
        char_role: editing.role || "配角",
        personality: editing.personality || "未设定",
        background: editing.background || "未设定",
        speech_style: editing.speech_style || "未设定",
      },
      `你是一个专业的小说角色设计师。当前正在设计角色「${editing.name}」（定位：${editing.role || "配角"}）。\n已有信息：\n- 性格：${editing.personality || "未设定"}\n- 背景：${editing.background || "未设定"}\n- 说话风格：${editing.speech_style || "未设定"}\n\n请根据用户的需求提供角色设计建议、润色人设、或生成新的角色信息。如果用户要求生成完整人设，请以 JSON 格式输出：{"personality":"...","background":"...","speech_style":"..."}。否则用自然语言回答。`,
    );
  }, [editing]);

  // Web-LLM workflow: render the prompt for preview/copy
  const fetchCharChatPrompt = useCallback(async () => {
    const systemHint = await buildCharChatSystemHint();
    const resp = await apiPost<{ status: string; prompt: string }>("/api/generation/quick-generate", {
      project_id: projectId || "default",
      chapter_id: "char_chat",
      synopsis: charChatInput.trim(),
      system_hint: systemHint,
      prompt_only: true,
    });
    return resp.prompt || "";
  }, [buildCharChatSystemHint, projectId, charChatInput]);

  // Web-LLM workflow: apply a pasted result as an assistant message (same shape as normal replies)
  const applyCharChatResult = useCallback((text: string) => {
    const content = text.trim();
    if (!content) return;
    setCharChatMessages(prev => [...prev, { role: "assistant", content, timestamp: Date.now() }]);
  }, []);

  const sendCharChatMessage = async (inputOverride?: string) => {
    const msg = (inputOverride || charChatInput).trim();
    if (!msg || charChatLoading || !editing) return;
    setCharChatMessages(prev => [...prev, { role: "user", content: msg, timestamp: Date.now() }]);
    setCharChatInput("");
    setCharChatLoading(true);

    const controller = new AbortController();
    charAbortRef.current = controller;

    try {
      const systemHint = await buildCharChatSystemHint();
      const resp = await fetch("/api/generation/quick-generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId || "default",
          chapter_id: "char_chat",
          synopsis: msg,
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

  // Live view of the cast for the 全局图谱 — overlays the in-progress
  // `editing` draft onto the persisted `items` list so relationship edits
  // show up on the graph immediately (without waiting for 保存). The graph
  // also depends on the per-character `relationships` array; using `items`
  // alone leaves it stale until the user clicks 保存.
  const liveCharacters = useMemo(() => {
    if (!editing) return items;
    return items.map(c => c.id === editing.id ? editing : c);
  }, [items, editing]);

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
        dynamic_snapshots: [],
      });
      setItems([...items, c]);
      setEditing(c);
      setDirty(false);
    } catch (e: any) {
      toast(e.message || "操作失败", "error");
    }
  };

  const save = async () => {
    if (!editing) return;
    try {
      await apiPut(`/api/data/characters/${editing.id}`, editing);
      setDirty(false);
      load();
      toast("已保存", "success");
    } catch (e: any) {
      toast(e.message || "操作失败", "error");
    }
  };

  // Guard for actions that would discard the current 角色卡 draft
  // (switching characters, toggling rightView, clicking a node in the
  // global graph). Shows a confirm dialog when there are unsaved edits.
  const confirmDiscardIfDirty = async (action: () => void) => {
    if (!dirty) { action(); return; }
    const ok = await confirm({
      title: "未保存的更改",
      message: "当前角色卡有未保存的修改，继续将丢弃这些内容。建议先点击「保存」。",
      confirmLabel: "丢弃并继续",
      cancelLabel: "取消",
      destructive: true,
    });
    if (ok) action();
  };

  const remove = async (id: string) => {
    if (!(await confirm({ message: "确定删除该角色？", destructive: true }))) return;
    try {
      await apiDelete(`/api/data/characters/${id}`);
      if (editing?.id === id) setEditing(null);
      load();
    } catch (e: any) {
      toast(e.message || "操作失败", "error");
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
      label: "",
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

  // Dynamic snapshots
  const addSnapshot = () => {
    if (!editing) return;
    const snaps = editing.dynamic_snapshots || [];
    const prevSnap = snaps.length > 0 ? snaps[snaps.length - 1] : null;
    const newSnap: DynamicPropertySnapshot = {
      chapter: `第${snaps.length + 1}章`,
      personality: prevSnap?.personality || editing.personality || "",
      background: prevSnap?.background || editing.background || "",
      speech_style: prevSnap?.speech_style || editing.speech_style || "",
      notes: "",
      relationships: prevSnap?.relationships || (editing.relationships || []).map(r => ({ ...r })),
      layer_b: prevSnap?.layer_b || (editing.layer_b ? { ...editing.layer_b } : { ...DEFAULT_LAYER_B }),
    };
    setEditing({ ...editing, dynamic_snapshots: [...snaps, newSnap] });
    setDirty(true);
    setFlashcardIndex(snaps.length);
  };

  const updateSnapshot = (idx: number, key: string, val: string) => {
    if (!editing) return;
    const snaps = [...(editing.dynamic_snapshots || [])];
    snaps[idx] = { ...snaps[idx], [key]: val };
    setEditing({ ...editing, dynamic_snapshots: snaps });
    setDirty(true);
  };

  const removeSnapshot = (idx: number) => {
    if (!editing) return;
    const snaps = [...(editing.dynamic_snapshots || [])];
    snaps.splice(idx, 1);
    setEditing({ ...editing, dynamic_snapshots: snaps });
    setDirty(true);
    if (flashcardIndex >= snaps.length) setFlashcardIndex(Math.max(0, snaps.length - 1));
  };

  // Snapshot-level relationship helpers
  const addSnapshotRel = (snapIdx: number, targetId: string) => {
    if (!editing || !targetId) return;
    const target = items.find(c => c.id === targetId);
    if (!target) return;
    const snaps = [...(editing.dynamic_snapshots || [])];
    const snap = snaps[snapIdx];
    if (!snap) return;
    const rels = snap.relationships || [];
    const newRel: CharacterRelationship = {
      target_id: targetId, target_name: target.name,
      affinity: 50, priority: (rels.length || 0) + 2,
      chapter: snap.chapter, notes: "", label: "",
    };
    snaps[snapIdx] = { ...snap, relationships: [...rels, newRel] };
    setEditing({ ...editing, dynamic_snapshots: snaps });
    setDirty(true);
    setRelTarget("");
  };

  // Add a placeholder row (target not yet picked) — used by the section
  // header「+ 添加」button so the row appears immediately with target /
  // label / affinity / notes inputs all on one card.
  const addEmptySnapshotRel = (snapIdx: number) => {
    if (!editing) return;
    const snaps = [...(editing.dynamic_snapshots || [])];
    const snap = snaps[snapIdx];
    if (!snap) return;
    const rels = snap.relationships || [];
    const newRel: CharacterRelationship = {
      target_id: "", target_name: "",
      affinity: 50, priority: (rels.length || 0) + 2,
      chapter: snap.chapter, notes: "", label: "",
    };
    snaps[snapIdx] = { ...snap, relationships: [...rels, newRel] };
    setEditing({ ...editing, dynamic_snapshots: snaps });
    setDirty(true);
  };

  const updateSnapshotRel = (snapIdx: number, relIdx: number, key: string, val: any) => {
    // Functional setEditing so two onChange calls in the same tick
    // (e.g. picking a target writes both target_id and target_name)
    // compose against the latest queued state instead of stomping each
    // other from a stale closure.
    setEditing(prev => {
      if (!prev) return prev;
      const snaps = [...(prev.dynamic_snapshots || [])];
      const snap = snaps[snapIdx];
      if (!snap) return prev;
      const rels = [...(snap.relationships || [])];
      rels[relIdx] = { ...rels[relIdx], [key]: val };
      snaps[snapIdx] = { ...snap, relationships: rels };
      return { ...prev, dynamic_snapshots: snaps };
    });
    setDirty(true);
  };

  const removeSnapshotRel = (snapIdx: number, relIdx: number) => {
    setEditing(prev => {
      if (!prev) return prev;
      const snaps = [...(prev.dynamic_snapshots || [])];
      const snap = snaps[snapIdx];
      if (!snap) return prev;
      const rels = [...(snap.relationships || [])];
      rels.splice(relIdx, 1);
      snaps[snapIdx] = { ...snap, relationships: rels };
      return { ...prev, dynamic_snapshots: snaps };
    });
    setDirty(true);
  };

  // Snapshot-level hidden identities helpers
  const addSnapshotHidden = (snapIdx: number) => {
    setEditing(prev => {
      if (!prev) return prev;
      const snaps = [...(prev.dynamic_snapshots || [])];
      const snap = snaps[snapIdx];
      if (!snap) return prev;
      const list = snap.hidden_identities || [];
      snaps[snapIdx] = {
        ...snap,
        hidden_identities: [...list, { name: "", revealed_to: [], notes: "" }],
      };
      return { ...prev, dynamic_snapshots: snaps };
    });
    setDirty(true);
  };

  const updateSnapshotHidden = (snapIdx: number, hIdx: number, key: string, val: any) => {
    setEditing(prev => {
      if (!prev) return prev;
      const snaps = [...(prev.dynamic_snapshots || [])];
      const snap = snaps[snapIdx];
      if (!snap) return prev;
      const list = [...(snap.hidden_identities || [])];
      list[hIdx] = { ...list[hIdx], [key]: val };
      snaps[snapIdx] = { ...snap, hidden_identities: list };
      return { ...prev, dynamic_snapshots: snaps };
    });
    setDirty(true);
  };

  const removeSnapshotHidden = (snapIdx: number, hIdx: number) => {
    setEditing(prev => {
      if (!prev) return prev;
      const snaps = [...(prev.dynamic_snapshots || [])];
      const snap = snaps[snapIdx];
      if (!snap) return prev;
      const list = [...(snap.hidden_identities || [])];
      list.splice(hIdx, 1);
      snaps[snapIdx] = { ...snap, hidden_identities: list };
      return { ...prev, dynamic_snapshots: snaps };
    });
    setDirty(true);
  };

  const updateSnapshotLayerB = (snapIdx: number, key: keyof CharacterLayerB, val: number) => {
    setEditing(prev => {
      if (!prev) return prev;
      const snaps = [...(prev.dynamic_snapshots || [])];
      const snap = snaps[snapIdx];
      if (!snap) return prev;
      snaps[snapIdx] = { ...snap, layer_b: { ...(snap.layer_b || DEFAULT_LAYER_B), [key]: val } };
      return { ...prev, dynamic_snapshots: snaps };
    });
    setDirty(true);
  };

  // Compute latest affinity rankings across all snapshots (priority dropped)
  const latestRankings = useMemo(() => {
    type RankEntry = { name: string; value: number; chapter: string };
    if (!editing) return { affinity: [] as RankEntry[] };
    const snaps = editing.dynamic_snapshots || [];
    // Build map: target_id -> latest values
    const latestByTarget: Record<string, { name: string; affinity: number; chapter: string }> = {};
    // Go through snapshots in order (latest wins)
    for (const snap of snaps) {
      for (const rel of (snap.relationships || [])) {
        latestByTarget[rel.target_id] = { name: rel.target_name, affinity: rel.affinity, chapter: snap.chapter };
      }
    }
    // Fallback to top-level relationships if no snapshots
    if (Object.keys(latestByTarget).length === 0) {
      for (const rel of (editing.relationships || [])) {
        latestByTarget[rel.target_id] = { name: rel.target_name, affinity: rel.affinity, chapter: rel.chapter || "" };
      }
    }
    const entries = Object.values(latestByTarget);
    return {
      affinity: [...entries].sort((a, b) => b.affinity - a.affinity).map(e => ({ name: e.name, value: e.affinity, chapter: e.chapter })),
    };
  }, [editing]);

  // Filtered relationships for per-character view (legacy, kept for graph)
  const filteredRels = useMemo(() => {
    if (!editing) return [];
    const rels = editing.relationships || [];
    if (!relTimeFilter) return rels;
    return rels.filter(r => r.chapter && r.chapter.includes(relTimeFilter));
  }, [editing, relTimeFilter]);

  return (
    <div className="page-full">
      {showNamer && <NameGeneratorModal onClose={() => setShowNamer(false)} />}
      <div className="panel-layout">
        {/* ======== LEFT PANEL: Character List ======== */}
        <div className="panel" style={{ width: leftPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderRight: "1px solid var(--border)" }}>
          <div className="panel-header" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
            <div className="flex items-center justify-between">
              <h3>角色卡</h3>
              <div className="flex gap-4">
                <button className="btn" style={{ padding: "5px 10px", fontSize: 11 }}
                  onClick={() => setShowNamer(true)} title="基于人名库取名">
                  取名
                </button>
                <button className="btn-primary" style={{ padding: "5px 12px", fontSize: 12 }} onClick={create}>
                  + 新建
                </button>
              </div>
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

          {/* View toggle — both buttons are gated by confirmDiscardIfDirty
              so a switch with unsaved edits prompts the user first. */}
          <div style={{ padding: "6px 12px", borderBottom: "1px solid var(--border)", display: "flex", gap: 4 }}>
            <button
              className={rightView === "detail" ? "btn-primary" : "btn"}
              style={{ flex: 1, fontSize: 11, padding: "4px 0", borderRadius: 14, textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center" }}
              onClick={() => confirmDiscardIfDirty(() => setRightView("detail"))}
            >
              角色详情
            </button>
            <button
              className={rightView === "graph" ? "btn-primary" : "btn"}
              style={{ flex: 1, fontSize: 11, padding: "4px 0", borderRadius: 14, textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center" }}
              onClick={() => confirmDiscardIfDirty(() => setRightView("graph"))}
            >
              全局图谱
            </button>
          </div>

          {/* List */}
          <div className="panel-body">
            {loading ? (
              <div className="loading"><div className="loading-spinner" /></div>
            ) : filtered.length === 0 ? (
              <div className="empty-state" style={{ padding: 32, textAlign: "center" }}>
                <p>{search ? "没有匹配的角色" : "暂无角色，点击左上角「+」添加"}</p>
              </div>
            ) : (
              filtered.map(c => (
                <div
                  key={c.id}
                  className={`report-list-item ${editing?.id === c.id ? "active" : ""}`}
                  onClick={() => {
                    // Switching to the same character is a no-op (and
                    // shouldn't trigger a discard prompt either).
                    if (editing?.id === c.id) { setRightView("detail"); return; }
                    confirmDiscardIfDirty(() => {
                      setEditing(c);
                      setDirty(false);
                      setRightView("detail");
                    });
                  }}
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
                    </div>
                  </div>
                  <button
                    className="btn-icon"
                    style={{ fontSize: 14 }}
                    title="删除"
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

        {/* ======== RIGHT PANEL ======== */}
        <div className="panel flex-1" style={{ background: "var(--bg-app)", overflowY: rightView === "graph" ? "hidden" : "auto" }}>
          {rightView === "graph" ? (
            /* ======== GLOBAL RELATIONSHIP GRAPH (full column) ======== */
            <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
              <div style={{ padding: "16px 20px 8px", flexShrink: 0 }}>
                <h2 className="font-serif" style={{ fontSize: 22, fontWeight: 700, marginBottom: 8 }}>
                  全局关系图谱
                </h2>
                <div style={{ padding: "6px 12px", background: "var(--bg-surface-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--border)", fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.7 }}>
                  <strong>好感度：</strong>正值=好感, 0=不熟悉, 负值=厌恶。拖动顶部时间轴可查看该章节的好感快照。
                </div>
              </div>
              <div style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                <GlobalRelationshipGraph
                  characters={liveCharacters}
                  editorChapterCount={editorChapterCount}
                  onSelectCharacter={(id) => {
                    // Clicking the currently-edited character should
                    // preserve the in-flight draft (the bug: previously
                    // we re-read from `items` and lost unsaved snapshots).
                    if (editing?.id === id) { setRightView("detail"); return; }
                    confirmDiscardIfDirty(() => {
                      const next = liveCharacters.find(c => c.id === id) || null;
                      setEditing(next);
                      setDirty(false);
                      setRightView("detail");
                    });
                  }}
                  fullHeight
                />
              </div>
            </div>
          ) : !editing ? (
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

              {/* AI Character Chat Panel (3.2.1) - overlay style */}
              {showCharChat && (
                <div className="card mb-20" style={{ overflow: "hidden", border: "2px solid var(--accent)", boxShadow: "0 4px 16px var(--accent-glow)" }}>
                  <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", background: "var(--accent-subtle)" }}>
                    <div>
                      <h3 style={{ color: "var(--accent)" }}>AI 角色助手</h3>
                      <div className="text-xs" style={{ color: "var(--text-tertiary)", marginTop: 2 }}>Agent: Character Designer</div>
                    </div>
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
                            {msg.role === "user" ? "\uD83D\uDC64" : "\uD83E\uDD16"}
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
                          <div style={{ width: 28, height: 28, borderRadius: "50%", background: "var(--accent-subtle)", border: "2px solid var(--accent)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 13 }}>{"\uD83E\uDD16"}</div>
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
                        <textarea className="input" value={charChatInput} onChange={e => setCharChatInput(e.target.value)}
                          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendCharChatMessage(); } }}
                          placeholder="描述你想要的角色特征..." rows={1} style={{ flex: 1, fontSize: 12, minHeight: 32, maxHeight: 100, resize: "none" }} />
                        <button className="btn-primary" onClick={() => sendCharChatMessage()} disabled={!charChatInput.trim() || charChatLoading} style={{ fontSize: 12, padding: "6px 14px" }}>
                          {charChatLoading ? "生成中..." : "发送"}
                        </button>
                      </div>
                      {/* Templates + Quick actions */}
                      <div style={{ display: "flex", gap: 4, marginTop: 6, flexWrap: "wrap" }}>
                        {[
                          { label: "生成完整人设", prompt: "生成完整人设" },
                          { label: "丰富背景故事", prompt: "丰富背景故事" },
                          { label: "设计说话风格", prompt: "设计说话风格" },
                          { label: "增加性格矛盾点", prompt: "增加性格矛盾点" },
                          { label: "设计角色弧光", prompt: "为这个角色设计一条完整的角色弧光（成长/转变路线）" },
                          { label: "关系建议", prompt: "根据这个角色的设定，建议几个有趣的人际关系" },
                        ].map(t => (
                          <button key={t.label} className="btn" style={{ fontSize: 10, padding: "2px 8px", borderRadius: 12 }}
                            onClick={() => sendCharChatMessage(t.prompt)} disabled={charChatLoading}>
                            {t.label}
                          </button>
                        ))}
                      </div>
                      {/* Web LLM prompt workflow */}
                      <div style={{ marginTop: 8 }}>
                        <WebLLMPromptPanel
                          fetchPrompt={fetchCharChatPrompt}
                          onApplyResult={applyCharChatResult}
                          resultPlaceholder="把网页 LLM 返回的角色设计内容粘贴到这里"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              )}

              {/* ═══ FIXED PROPERTIES (3.2.2) ═══ */}
              <div className="card mb-20">
                <div className="card-header"><h3>角色固定属性</h3><span className="text-xs text-muted">不随时间/章节变化</span></div>
                <div className="card-body">
                  {/* A) 姓名, 性别, 年龄 (Not Null) */}
                  <div className="flex gap-12 mb-12" style={{ flexWrap: "wrap" }}>
                    <div className="field" style={{ flex: 2, minWidth: 120 }}>
                      <label className="label">姓名 *</label>
                      <input className="input" value={editing.name} onChange={e => u("name", e.target.value)} />
                    </div>
                    <div className="field" style={{ flex: 1, minWidth: 80 }}>
                      <label className="label">性别 *</label>
                      <div className="flex gap-4">
                        {GENDERS.map(g => (
                          <button key={g} className={(editing as any).gender === g ? "btn-primary" : "btn"}
                            style={{ flex: 1, fontSize: 11, padding: "5px 0", borderRadius: 16, textAlign: "center", display: "flex", alignItems: "center", justifyContent: "center" }}
                            onClick={() => u("gender", g)}>{g}</button>
                        ))}
                      </div>
                    </div>
                    <div className="field" style={{ flex: 1, minWidth: 70 }}>
                      <label className="label">年龄</label>
                      <input className="input" value={(editing as any).age || ""} onChange={e => u("age", e.target.value)}
                        placeholder="例：25" />
                    </div>
                  </div>
                  {/* B) 角色定位 (Not Null) */}
                  <div className="field mb-12">
                    <label className="label">角色定位 *</label>
                    <div className="flex gap-6" style={{ flexWrap: "wrap" }}>
                      {ROLES.map(r => (
                        <button key={r}
                          className={editing.role === r ? "btn-primary" : "btn"}
                          style={{ padding: "5px 14px", fontSize: 12, borderRadius: 20 }}
                          onClick={() => u("role", r)}>{r}</button>
                      ))}
                    </div>
                  </div>
                  {/* C) 外貌核心记忆点 (nullable) */}
                  <div className="field mb-12">
                    <label className="label">外貌核心记忆点</label>
                    <textarea className="input" value={editing.appearance || ""} onChange={e => u("appearance", e.target.value)}
                      rows={2} placeholder="最突出的外貌特征（如：左眼疤痕、银白长发）..." />
                  </div>
                  {/* D) 性格核心记忆点 (nullable) */}
                  <div className="field mb-12">
                    <label className="label">性格核心记忆点</label>
                    <textarea className="input" value={editing.personality || ""} onChange={e => u("personality", e.target.value)}
                      rows={2} placeholder="最核心的性格特质（如：表面冷漠实则重情）..." />
                  </div>
                  {/* E) 背景故事 (nullable) */}
                  <div className="field mb-12">
                    <label className="label">背景故事</label>
                    <textarea className="input" value={editing.background || ""} onChange={e => u("background", e.target.value)}
                      rows={3} placeholder="在小说正文开始前发生在此角色身上的事..."
                      style={{ fontFamily: "var(--font-serif)", lineHeight: 1.8 }} />
                  </div>
                  {/* F) 口癖 (nullable) */}
                  <div className="field">
                    <label className="label">口癖</label>
                    <textarea className="input" value={editing.speech_style || ""} onChange={e => u("speech_style", e.target.value)}
                      rows={2} placeholder="角色的口头禅、语气特点..." />
                  </div>
                </div>
              </div>

              {/* ═══ UNIFIED DYNAMIC PROPERTIES CARD ═══ */}
              <div className="card mb-20">
                <div className="card-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <h3>角色动态属性</h3>
                    <span className="text-xs text-muted">时间快照（含关系 / 隐藏身份 / 决策参数）</span>
                  </div>
                </div>
                <div className="card-body">
                    {/* 转变档位 (角色卡·机制2): 对接生成链路使用的 canonical
                      * 快照表，过渡期各章可设 动摇/试探/倾向 */}
                    {editing.id && (
                      <div style={{ marginBottom: 12 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>
                          转变档位（动摇 / 试探 / 倾向）
                        </div>
                        <SnapshotStageEditor characterId={editing.id} characterName={editing.name} />
                      </div>
                    )}
                    <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 8 }}>
                      <button className="btn" style={{ fontSize: 11, padding: "4px 12px" }} onClick={addSnapshot}>
                        + 添加快照
                      </button>
                    </div>

                    {(editing.dynamic_snapshots || []).length > 0 && (
                      <div>
                        {/* Timeline-style tab strip */}
                        <div style={{ display: "flex", gap: 0, marginBottom: 10, overflowX: "auto", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)" }}>
                          {(editing.dynamic_snapshots || []).map((snap, i) => (
                            <button key={i} onClick={() => setFlashcardIndex(i)}
                              style={{
                                flex: "0 0 auto", padding: "6px 14px", fontSize: 11, fontWeight: i === flashcardIndex ? 600 : 400,
                                color: i === flashcardIndex ? "var(--accent)" : "var(--text-tertiary)",
                                background: i === flashcardIndex ? "var(--accent-subtle)" : "transparent",
                                border: "none", borderRight: "1px solid var(--border)",
                                cursor: "pointer", transition: "all 0.15s", whiteSpace: "nowrap",
                              }}>
                              {snap.chapter || `快照${i + 1}`}
                            </button>
                          ))}
                        </div>

                        {/* Navigation arrows + delete */}
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                            <button className="btn" style={{ fontSize: 12, padding: "5px 14px" }}
                              onClick={() => setFlashcardIndex(Math.max(0, flashcardIndex - 1))}
                              disabled={flashcardIndex === 0}>
                              ← 上一个
                            </button>
                            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", padding: "0 8px" }}>
                              {flashcardIndex + 1} / {(editing.dynamic_snapshots || []).length}
                            </span>
                            <button className="btn" style={{ fontSize: 12, padding: "5px 14px" }}
                              onClick={() => setFlashcardIndex(Math.min((editing.dynamic_snapshots || []).length - 1, flashcardIndex + 1))}
                              disabled={flashcardIndex >= (editing.dynamic_snapshots || []).length - 1}>
                              下一个 →
                            </button>
                          </div>
                          <button className="btn" style={{ fontSize: 11, padding: "4px 10px", color: "var(--error)", borderColor: "var(--error)" }}
                            onClick={() => removeSnapshot(flashcardIndex)}>
                            删除
                          </button>
                        </div>

                        {/* Snapshot card */}
                        {(() => {
                          const snap = (editing.dynamic_snapshots || [])[flashcardIndex];
                          if (!snap) return null;
                          const snapRels = snap.relationships || [];
                          const snapLayerB = snap.layer_b || DEFAULT_LAYER_B;
                          return (
                            <div style={{
                              padding: "12px 14px", background: "var(--bg-surface-2)", borderRadius: "var(--radius-md)",
                              border: "2px solid var(--accent)", position: "relative",
                            }}>
                              <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
                                <div className="field" style={{ flex: 2, marginBottom: 0 }}>
                                  <label className="label">章节/时间点</label>
                                  <input className="input" value={snap.chapter} onChange={e => updateSnapshot(flashcardIndex, "chapter", e.target.value)}
                                    placeholder="例：第5章、三年后" style={{ fontWeight: 600 }} />
                                </div>
                                <div className="field" style={{ flex: 1, marginBottom: 0 }}>
                                  <label className="label">转变档位</label>
                                  <select className="select" value={snap.stage || ""}
                                    onChange={e => updateSnapshot(flashcardIndex, "stage", e.target.value)}
                                    style={{ width: "100%", fontSize: 12 }}>
                                    <option value="">未设定</option>
                                    <option value="动摇">动摇 · 对原状态产生怀疑</option>
                                    <option value="试探">试探 · 开始尝试新状态</option>
                                    <option value="倾向">倾向 · 多数行为已偏向新状态</option>
                                  </select>
                                </div>
                              </div>
                              <div className="field mb-8">
                                <label className="label">性格变化</label>
                                <textarea className="input" value={snap.personality || ""} onChange={e => updateSnapshot(flashcardIndex, "personality", e.target.value)} rows={2} placeholder="此阶段的性格..." />
                              </div>
                              <div className="field mb-8">
                                <label className="label">经历变化</label>
                                <textarea className="input" value={snap.background || ""} onChange={e => updateSnapshot(flashcardIndex, "background", e.target.value)} rows={2} placeholder="此阶段发生了什么..." />
                              </div>
                              <div className="field mb-12">
                                <label className="label">备注</label>
                                <input className="input" value={snap.notes || ""} onChange={e => updateSnapshot(flashcardIndex, "notes", e.target.value)} placeholder="变化原因或事件..." />
                              </div>

                              {/* ── 关系 section ── */}
                              <SnapSection
                                title="关系"
                                count={snapRels.length}
                                headerAction={
                                  others.length > 0 ? (
                                    <button
                                      className="btn"
                                      style={{ fontSize: 11, padding: "3px 12px" }}
                                      onClick={() => {
                                        // One-step add — drop an empty row in edit mode.
                                        // User picks target via dropdown in the row itself.
                                        const newIdx = snapRels.length;
                                        addEmptySnapshotRel(flashcardIndex);
                                        setEditingRelRows(prev => new Set(prev).add(newIdx));
                                      }}
                                    >
                                      + 添加
                                    </button>
                                  ) : null
                                }
                              >
                                {snapRels.length === 0 ? (
                                  <div className="text-xs text-muted" style={{ padding: "8px 4px" }}>
                                    暂无关系。点击右上角「+ 添加」记录角色关系。
                                  </div>
                                ) : (
                                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                    {snapRels.map((rel, relIdx) => {
                                      const isEditing = editingRelRows.has(relIdx);
                                      const rowActions = (
                                        <>
                                          <RowIconButton symbol={isEditing ? "✓" : "✎"}
                                            title={isEditing ? "收起" : "编辑"}
                                            onClick={() => toggleRelEdit(relIdx)} />
                                          <RowIconButton symbol="×"
                                            title="删除"
                                            color="var(--error)"
                                            onClick={() => {
                                              removeSnapshotRel(flashcardIndex, relIdx);
                                              setEditingRelRows(prev => {
                                                const next = new Set<number>();
                                                prev.forEach(i => { if (i < relIdx) next.add(i); else if (i > relIdx) next.add(i - 1); });
                                                return next;
                                              });
                                            }} />
                                        </>
                                      );
                                      if (!isEditing) {
                                        return (
                                          <div key={relIdx}>
                                            <RelationshipRowView rel={rel} actions={rowActions} />
                                          </div>
                                        );
                                      }
                                      // ── Edit mode: target picker (if not chosen yet) + label + affinity + notes — all in one card.
                                      return (
                                        <div key={relIdx} style={{
                                          padding: "12px 14px",
                                          background: "var(--bg-surface-2)",
                                          borderRadius: 8, border: "1px solid var(--accent)",
                                        }}>
                                          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                                            {rel.target_id ? (
                                              <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary)" }}>
                                                &rarr; {rel.target_name}
                                              </span>
                                            ) : (
                                              <select
                                                className="select"
                                                value=""
                                                onChange={e => {
                                                  const id = e.target.value;
                                                  if (!id) return;
                                                  const t = items.find(c => c.id === id);
                                                  if (!t) return;
                                                  // Set both id + name in a single
                                                  // functional update so React can
                                                  // never split them across renders.
                                                  setEditing(prev => {
                                                    if (!prev) return prev;
                                                    const snaps = [...(prev.dynamic_snapshots || [])];
                                                    const snap = snaps[flashcardIndex];
                                                    if (!snap) return prev;
                                                    const rels = [...(snap.relationships || [])];
                                                    rels[relIdx] = { ...rels[relIdx], target_id: id, target_name: t.name };
                                                    snaps[flashcardIndex] = { ...snap, relationships: rels };
                                                    return { ...prev, dynamic_snapshots: snaps };
                                                  });
                                                  setDirty(true);
                                                }}
                                                style={{ flex: 1, fontSize: 12 }}
                                              >
                                                <option value="">选择关系对象...</option>
                                                {others.map(o => (
                                                  <option key={o.id} value={o.id}>{o.name}</option>
                                                ))}
                                              </select>
                                            )}
                                            <span style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                                              {rowActions}
                                            </span>
                                          </div>
                                          <div className="field mb-6">
                                            <label className="label" style={{ fontSize: 10, marginBottom: 4 }}>关系标签</label>
                                            <input className="input" value={rel.label || ""} onChange={e => updateSnapshotRel(flashcardIndex, relIdx, "label", e.target.value)} placeholder="例：师徒、情侣、宿敌..." style={{ fontSize: 11 }} />
                                          </div>
                                          <ParamSlider name={`好感度 (${rel.affinity > 0 ? "+" : ""}${rel.affinity})`} value={rel.affinity} min={-100} max={100} step={5} onChange={v => updateSnapshotRel(flashcardIndex, relIdx, "affinity", v)} />
                                          <div className="field mt-6">
                                            <label className="label" style={{ fontSize: 10, marginBottom: 4 }}>备注</label>
                                            <input className="input" value={rel.notes || ""} onChange={e => updateSnapshotRel(flashcardIndex, relIdx, "notes", e.target.value)} placeholder="关系备注..." style={{ fontSize: 11 }} />
                                          </div>
                                          <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 8, lineHeight: 1.5 }}>
                                            好感度：越高越喜欢（负=厌恶, 0=不熟, 正=好感）
                                          </div>
                                        </div>
                                      );
                                    })}
                                  </div>
                                )}
                              </SnapSection>

                              {/* ── 隐藏身份 section ── */}
                              <SnapSection
                                title="隐藏身份"
                                count={(snap.hidden_identities || []).length}
                                headerAction={
                                  <button
                                    className="btn"
                                    style={{ fontSize: 11, padding: "3px 12px" }}
                                    onClick={() => {
                                      const newIdx = (snap.hidden_identities || []).length;
                                      addSnapshotHidden(flashcardIndex);
                                      // Open the new row in edit mode immediately.
                                      setEditingHiddenRows(prev => new Set(prev).add(newIdx));
                                    }}
                                  >
                                    + 添加
                                  </button>
                                }
                              >
                                {(snap.hidden_identities || []).length === 0 ? (
                                  <div className="text-xs text-muted" style={{ padding: "8px 4px" }}>
                                    暂无隐藏身份。点击右上角「+ 添加」记录化名 / 伪装身份。
                                  </div>
                                ) : (
                                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                                    {(snap.hidden_identities || []).map((h, hIdx) => {
                                      const isEditing = editingHiddenRows.has(hIdx);
                                      const rowActions = (
                                        <>
                                          <RowIconButton symbol={isEditing ? "✓" : "✎"}
                                            title={isEditing ? "收起" : "编辑"}
                                            onClick={() => toggleHiddenEdit(hIdx)} />
                                          <RowIconButton symbol="×"
                                            title="删除"
                                            color="var(--error)"
                                            onClick={() => {
                                              removeSnapshotHidden(flashcardIndex, hIdx);
                                              setEditingHiddenRows(prev => {
                                                const next = new Set<number>();
                                                prev.forEach(i => { if (i < hIdx) next.add(i); else if (i > hIdx) next.add(i - 1); });
                                                return next;
                                              });
                                            }} />
                                        </>
                                      );
                                      if (!isEditing) {
                                        return (
                                          <div key={hIdx}>
                                            <HiddenIdentityRowView hidden={h} actions={rowActions} />
                                          </div>
                                        );
                                      }
                                      return (
                                        <div key={hIdx} style={{
                                          padding: "12px 14px",
                                          background: "var(--bg-surface-2)",
                                          borderRadius: 8, border: "1px solid var(--accent)",
                                        }}>
                                          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                                            <input className="input" value={h.name} onChange={e => updateSnapshotHidden(flashcardIndex, hIdx, "name", e.target.value)}
                                                   placeholder="化名 / 伪装身份名" style={{ fontSize: 12, fontWeight: 600, flex: 1 }} />
                                            <span style={{ display: "flex", gap: 6 }}>{rowActions}</span>
                                          </div>
                                          <div className="field mb-6">
                                            <label className="label" style={{ fontSize: 10, marginBottom: 4 }}>已知真相的角色</label>
                                            <CharNamesMultiSelect
                                              value={h.revealed_to || []}
                                              options={others}
                                              onChange={(v) => updateSnapshotHidden(flashcardIndex, hIdx, "revealed_to", v)}
                                              placeholder="点击选择已知真相的角色…"
                                            />
                                          </div>
                                          <input className="input" value={h.notes || ""}
                                                 onChange={e => updateSnapshotHidden(flashcardIndex, hIdx, "notes", e.target.value)}
                                                 placeholder="伪装动机 / 破绽 / 备注..." style={{ fontSize: 11 }} />
                                          <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 8, lineHeight: 1.5 }}>
                                            化名 / 伪装身份在此阶段有效；&ldquo;已知真相&rdquo;中的角色看穿伪装，其他角色仍以化名认知。
                                          </div>
                                        </div>
                                      );
                                    })}
                                  </div>
                                )}
                              </SnapSection>

                              {/* ── 决策参数 section ── */}
                              <SnapSection
                                title="决策参数"
                                headerAction={
                                  <button
                                    className="btn"
                                    style={{ fontSize: 11, padding: "3px 12px" }}
                                    onClick={() => setDecisionPreview(simulateDecisionActions(snapLayerB))}
                                    title="基于当前参数计算 10 个性格情境下角色的倾向比例"
                                  >
                                    {decisionPreview ? "重新生成" : "效果预览"}
                                  </button>
                                }
                              >
                                <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginBottom: 10, lineHeight: 1.5 }}>
                                  量化参数影响 AI 生成时角色在此阶段的行为倾向。
                                </div>
                                <ParamSlider name="损失厌恶" value={snapLayerB.loss_aversion ?? DEFAULT_LAYER_B.loss_aversion} min={0} max={5} step={0.1} onChange={v => updateSnapshotLayerB(flashcardIndex, "loss_aversion", v)} />
                                <ParamSlider name="风险厌恶" value={snapLayerB.risk_aversion_gain ?? DEFAULT_LAYER_B.risk_aversion_gain} min={0} max={1} step={0.05} onChange={v => updateSnapshotLayerB(flashcardIndex, "risk_aversion_gain", v)} />
                                <ParamSlider name="冲动概率" value={snapLayerB.impulse_probability ?? DEFAULT_LAYER_B.impulse_probability} min={0} max={1} step={0.05} onChange={v => updateSnapshotLayerB(flashcardIndex, "impulse_probability", v)} />
                                <ParamSlider name="社交频率" value={snapLayerB.social_frequency ?? DEFAULT_LAYER_B.social_frequency} min={0} max={10} step={0.5} onChange={v => updateSnapshotLayerB(flashcardIndex, "social_frequency", v)} />

                                {decisionPreview && (
                                  <div style={{
                                    marginTop: 14, padding: "12px 14px",
                                    background: "var(--bg-surface-2)", borderRadius: 8,
                                    border: "1px solid var(--border-subtle)",
                                  }}>
                                    <div style={{
                                      display: "flex", alignItems: "center", justifyContent: "space-between",
                                      marginBottom: 8,
                                    }}>
                                      <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)" }}>
                                        10 个性格情境 · 角色的选择倾向（百分比）
                                      </span>
                                      <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px" }}
                                        onClick={() => setDecisionPreview(null)}>
                                        收起
                                      </button>
                                    </div>
                                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                                      {decisionPreview.map((row, i) => {
                                        const pctB = Math.round(row.pB * 100);
                                        const pctA = 100 - pctB;
                                        const leansB = pctB >= 50;
                                        return (
                                        <div key={i} style={{
                                          display: "flex", alignItems: "flex-start", gap: 8,
                                          padding: "8px 10px",
                                          background: "var(--bg-surface)", borderRadius: 6,
                                          border: "1px solid var(--border-subtle)",
                                        }}>
                                          <span style={{
                                            minWidth: 18, height: 18, borderRadius: "50%",
                                            background: "var(--bg-secondary)", color: "var(--text-tertiary)",
                                            fontSize: 10, fontWeight: 700,
                                            display: "inline-flex", alignItems: "center", justifyContent: "center",
                                            flexShrink: 0, marginTop: 1,
                                          }}>
                                            {i + 1}
                                          </span>
                                          <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{ fontSize: 11.5, color: "var(--text-primary)", lineHeight: 1.5, marginBottom: 6 }}>
                                              {row.scenario}
                                            </div>
                                            {/* Percentage split — two chips with bars under each */}
                                            <div style={{ display: "flex", gap: 8, alignItems: "stretch", marginBottom: 4 }}>
                                              <div style={{ flex: pctA, minWidth: 0, opacity: leansB ? 0.55 : 1 }}>
                                                <div style={{
                                                  display: "flex", justifyContent: "space-between", alignItems: "baseline",
                                                  fontSize: 11, marginBottom: 2,
                                                }}>
                                                  <span style={{
                                                    fontWeight: leansB ? 500 : 700,
                                                    color: leansB ? "var(--text-secondary)" : "var(--text-primary)",
                                                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                                                  }}>{row.optionA}</span>
                                                  <span style={{
                                                    fontSize: 10, fontWeight: 700, marginLeft: 6,
                                                    color: leansB ? "var(--text-tertiary)" : "var(--accent)",
                                                  }}>{pctA}%</span>
                                                </div>
                                                <div style={{
                                                  height: 4, borderRadius: 2, overflow: "hidden",
                                                  background: "var(--bg-secondary)",
                                                }}>
                                                  <div style={{
                                                    height: "100%", width: `${pctA}%`,
                                                    background: leansB ? "var(--text-tertiary)" : "var(--accent)",
                                                  }} />
                                                </div>
                                              </div>
                                              <div style={{ flex: pctB, minWidth: 0, opacity: leansB ? 1 : 0.55 }}>
                                                <div style={{
                                                  display: "flex", justifyContent: "space-between", alignItems: "baseline",
                                                  fontSize: 11, marginBottom: 2,
                                                }}>
                                                  <span style={{
                                                    fontWeight: leansB ? 700 : 500,
                                                    color: leansB ? "var(--text-primary)" : "var(--text-secondary)",
                                                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                                                  }}>{row.optionB}</span>
                                                  <span style={{
                                                    fontSize: 10, fontWeight: 700, marginLeft: 6,
                                                    color: leansB ? "var(--accent)" : "var(--text-tertiary)",
                                                  }}>{pctB}%</span>
                                                </div>
                                                <div style={{
                                                  height: 4, borderRadius: 2, overflow: "hidden",
                                                  background: "var(--bg-secondary)",
                                                }}>
                                                  <div style={{
                                                    height: "100%", width: `${pctB}%`,
                                                    background: leansB ? "var(--accent)" : "var(--text-tertiary)",
                                                  }} />
                                                </div>
                                              </div>
                                            </div>
                                            <div style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
                                              依据：{row.reason}
                                            </div>
                                          </div>
                                        </div>
                                        );
                                      })}
                                    </div>
                                  </div>
                                )}
                              </SnapSection>
                            </div>
                          );
                        })()}
                      </div>
                    )}

                    {(editing.dynamic_snapshots || []).length === 0 && (
                      <div className="text-xs text-muted" style={{ textAlign: "center", padding: 16 }}>
                        点击「添加快照」记录角色在不同章节/时间点的动态变化（含关系和决策参数）
                      </div>
                    )}
                </div>
              </div>

              {/* ═══ AFFINITY RANKINGS (latest values) ═══ */}
              {latestRankings.affinity.length > 0 && (
                <div className="card mb-20">
                  <div className="card-header"><h3>好感度排序</h3><span className="text-xs text-muted">最新快照值</span></div>
                  <div className="card-body">
                    <div className="text-xs text-muted mb-4">高 → 低</div>
                    {latestRankings.affinity.map(r => (
                      <div key={r.name} style={{ fontSize: 11, display: "flex", justifyContent: "space-between", padding: "3px 0" }}>
                        <span style={{ color: "var(--text-secondary)" }}>{r.name}</span>
                        <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                          <span style={{ color: r.value > 0 ? "var(--accent)" : r.value < 0 ? "var(--jade)" : "var(--text-disabled)", fontFamily: "var(--font-mono)", fontSize: 10 }}>
                            {r.value > 0 ? "+" : ""}{r.value}
                          </span>
                          {r.chapter && <span style={{ fontSize: 9, color: "var(--text-disabled)" }}>@{r.chapter}</span>}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* ═══ SINGLE CHARACTER RELATIONSHIP GRAPH (3.2.4) ═══ */}
              {(editing.relationships || []).length > 0 && (
                <div className="card mb-20">
                  <div className="card-header"><h3>{editing.name} 的关系图谱</h3></div>
                  <div className="card-body" style={{ padding: 8 }}>
                    <SingleCharRelGraph
                      character={editing}
                      allCharacters={items}
                      onSelectCharacter={(id) => {
                        const target = items.find(c => c.id === id);
                        if (target) { setEditing(target); setDirty(false); }
                      }}
                    />
                  </div>
                </div>
              )}

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

/* ── SnapSection ──
 * Visually grouped section inside the snapshot card. Each section gets
 * a clear header (title + count badge + optional right-side action) and
 * a subtly bordered body so 关系 / 隐藏身份 / 决策参数 don't bleed
 * into each other the way a thin borderTop divider used to make them. */
function SnapSection({ title, count, headerAction, children }: {
  title: string;
  count?: number;
  headerAction?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div style={{
      marginTop: 14,
      padding: "12px 14px 14px",
      background: "var(--bg-surface)",
      borderRadius: 8,
      border: "1px solid var(--border)",
    }}>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        marginBottom: 10,
        paddingBottom: 8, borderBottom: "1px solid var(--border-subtle)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            width: 3, height: 14, borderRadius: 2,
            background: "var(--accent)",
          }} />
          <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary)" }}>
            {title}
          </span>
          {typeof count === "number" && (
            <span style={{
              fontSize: 10, padding: "1px 7px", borderRadius: 8,
              background: "var(--bg-secondary)", color: "var(--text-tertiary)",
              fontWeight: 600,
            }}>
              {count}
            </span>
          )}
        </div>
        {headerAction}
      </div>
      {children}
    </div>
  );
}

/* ── CharNamesMultiSelect ──
 * Chip-style multi-select bound to a list of character options. Replaces
 * the legacy 「已知真相」 free-text input so the user picks from existing
 * 角色卡 names instead of typing (and risking typos that break the
 * cross-reference). Allows clicking a chip's × to remove. */
function CharNamesMultiSelect({
  value, options, onChange, placeholder,
}: {
  value: string[];
  options: Character[];
  onChange: (next: string[]) => void;
  placeholder?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const wrapRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const toggle = (name: string) => {
    const next = value.includes(name)
      ? value.filter(v => v !== name)
      : [...value, name];
    onChange(next);
  };

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: "flex", flexWrap: "wrap", gap: 4,
          minHeight: 32, padding: "4px 8px",
          background: "var(--bg-surface)",
          border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
          cursor: "pointer",
        }}>
        {value.length === 0 ? (
          <span className="text-xs text-muted" style={{ lineHeight: "22px" }}>
            {placeholder || "点击选择角色…"}
          </span>
        ) : value.map(name => (
          <span key={name} className="tag" style={{
            fontSize: 10, padding: "1px 8px",
            background: "var(--gold-subtle)", color: "var(--gold)",
            border: "1px solid transparent",
          }}>
            {name}
            <span onClick={(e) => { e.stopPropagation(); toggle(name); }}
              style={{ marginLeft: 4, cursor: "pointer", color: "var(--gold)" }}>×</span>
          </span>
        ))}
      </div>
      {open && (
        <div style={{
          position: "absolute", zIndex: 50, left: 0, right: 0, top: "100%",
          marginTop: 4, background: "var(--bg-surface)",
          border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
          boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
          maxHeight: 240, overflowY: "auto",
        }}>
          {options.length === 0 ? (
            <div className="text-xs text-muted" style={{ padding: 10 }}>
              当前没有其它角色可供选择。
            </div>
          ) : (
            options.map(opt => {
              const on = value.includes(opt.name);
              return (
                <div key={opt.id}
                  onClick={() => toggle(opt.name)}
                  style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "5px 10px", cursor: "pointer",
                    background: on ? "var(--gold-subtle)" : undefined,
                  }}>
                  <input type="checkbox" checked={on} readOnly />
                  <span style={{ fontSize: 12, fontWeight: 500 }}>{opt.name}</span>
                  {opt.role && <span className="text-xs text-muted">· {opt.role}</span>}
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

/* ── RowIconButton ──
 * Compact icon button used inside relationship / hidden-identity rows
 * for per-row edit (✎) and delete (×) actions. */
function RowIconButton({ symbol, onClick, title, color }: {
  symbol: string; onClick: () => void; title: string; color?: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      style={{
        width: 22, height: 22, padding: 0,
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        background: "transparent", border: "1px solid var(--border-subtle)",
        borderRadius: 6, cursor: "pointer",
        color: color || "var(--text-tertiary)",
        fontSize: 12, lineHeight: 1,
        transition: "background 0.15s, border-color 0.15s, color 0.15s",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = color
          ? "rgba(220,53,69,0.08)"
          : "var(--bg-secondary)";
        e.currentTarget.style.borderColor = color || "var(--border)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "transparent";
        e.currentTarget.style.borderColor = "var(--border-subtle)";
      }}
    >
      {symbol}
    </button>
  );
}

/* ── RelationshipRowView ──
 * Read-only display of a single relationship row inside the snapshot
 * card. The `actions` slot is rendered at the top-right of the header
 * row so per-row ✎ / × icons sit inline next to the 好感 chip instead
 * of overlapping it. */
function RelationshipRowView({ rel, actions }: {
  rel: CharacterRelationship;
  actions?: React.ReactNode;
}) {
  const aff = rel.affinity ?? 0;
  // Positive 好感 → red, negative → green, zero → gold.
  // Same convention as the 全局关系图谱 so the chip color in detail view
  // matches the arrow color in the graph.
  const affColor = aff > 0 ? "var(--accent)" : aff < 0 ? "var(--jade)" : "var(--gold)";
  const affBg = aff > 0 ? "var(--accent-subtle)" : aff < 0 ? "var(--jade-subtle)" : "var(--gold-subtle)";
  return (
    <div style={{
      padding: "10px 12px",
      background: "var(--bg-surface-2)",
      borderRadius: 8,
      border: "1px solid var(--border-subtle)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 6 }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary)" }}>
          {rel.target_name || <span style={{ color: "var(--text-disabled)", fontStyle: "italic", fontWeight: 400 }}>（未指定对象）</span>}
        </span>
        {rel.label && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 8px",
            background: "var(--bg-secondary)", color: "var(--text-secondary)",
            border: "1px solid var(--border-subtle)",
          }}>
            {rel.label}
          </span>
        )}
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{
            fontSize: 10, padding: "2px 8px", borderRadius: 10,
            background: affBg, color: affColor, fontWeight: 600,
          }}>
            好感 {aff > 0 ? "+" : ""}{aff}
          </span>
          {actions}
        </span>
      </div>
      {/* Affinity bar — centered at 0, extends left for negative, right for positive */}
      <div style={{
        position: "relative", height: 4, background: "var(--bg-secondary)",
        borderRadius: 2, marginBottom: rel.notes ? 6 : 0,
      }}>
        <div style={{
          position: "absolute", top: 0, bottom: 0,
          left: aff < 0 ? `${50 + aff / 2}%` : "50%",
          width: `${Math.abs(aff) / 2}%`,
          background: affColor, borderRadius: 2,
        }} />
        <div style={{
          position: "absolute", top: -2, bottom: -2, left: "50%",
          width: 1, background: "var(--border)",
        }} />
      </div>
      {rel.notes && (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)", lineHeight: 1.5 }}>
          {rel.notes}
        </div>
      )}
    </div>
  );
}

/* ── HiddenIdentityRowView ──
 * Read-only display of one hidden identity / alias row. `actions` slot
 * renders inline at the right of the header so ✎ / × sit cleanly
 * instead of being overlaid via absolute positioning. */
function HiddenIdentityRowView({ hidden, actions }: {
  hidden: { name: string; revealed_to?: string[]; notes?: string };
  actions?: React.ReactNode;
}) {
  const revealedTo = hidden.revealed_to || [];
  return (
    <div style={{
      padding: "10px 12px",
      background: "var(--bg-surface-2)",
      borderRadius: 8,
      border: "1px solid var(--border-subtle)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary)" }}>
          {hidden.name || <span style={{ color: "var(--text-disabled)", fontStyle: "italic", fontWeight: 400 }}>（未命名）</span>}
        </span>
        {actions && (
          <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
            {actions}
          </span>
        )}
      </div>
      {revealedTo.length > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", marginBottom: hidden.notes ? 6 : 0 }}>
          <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>已知真相:</span>
          {revealedTo.map((name, i) => (
            <span key={i} className="tag" style={{
              fontSize: 10, padding: "1px 8px",
              background: "var(--gold-subtle)", color: "var(--gold)",
              border: "1px solid transparent",
            }}>
              {name}
            </span>
          ))}
        </div>
      )}
      {hidden.notes && (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)", lineHeight: 1.5 }}>
          {hidden.notes}
        </div>
      )}
    </div>
  );
}

/* ── 决策参数 预览 ──
 * For each of 10 scenarios we compute the probability that the character
 * picks option B over option A, given Layer B params. The UI then shows
 * 「{option}: {%}」 (as a percentage split, not a random sample) so it's
 * deterministic given the params and easier to read.
 *
 * NOTE: `risk_aversion` here uses the (renamed) `risk_aversion_gain` field
 * — we dropped the separate loss variant from the UI. */
function simulateDecisionActions(layerB: CharacterLayerB): Array<{
  scenario: string; optionA: string; optionB: string; pB: number; reason: string;
}> {
  const lossA = layerB.loss_aversion ?? 2.5;
  const riskA = layerB.risk_aversion_gain ?? 0.5;
  const impulse = layerB.impulse_probability ?? 0.3;
  const social = layerB.social_frequency ?? 5;
  const clamp = (x: number) => Math.max(0.02, Math.min(0.98, x));

  // Scenarios are intentionally abstract — they're 性格 dimensions ("how
  // does this character relate to risk / loss / social signals / impulse")
  // rather than concrete world events. This keeps the preview useful
  // across genres (玄幻 / 都市 / 历史 / etc.) without ever feeling
  // out of place for the project.
  const scenarios: Array<{ txt: string; pB: number; a: string; b: string; reason: string }> = [
    {
      txt: "面对一个充满未知的机会",
      pB: 0.3 + (1 - riskA) * 0.5 + impulse * 0.2,
      a: "谨慎观察", b: "立刻行动",
      reason: "风险厌恶 + 冲动",
    },
    {
      txt: "在群体里是否主动表达观点",
      pB: 0.2 + (social / 10) * 0.7,
      a: "倾听沉默", b: "主动发言",
      reason: "社交频率主导",
    },
    {
      txt: "在长远利益与当下回报之间",
      pB: 0.35 + impulse * 0.4 - (lossA / 5) * 0.1,
      a: "选择长远", b: "选择当下",
      reason: "冲动 vs 损失厌恶",
    },
    {
      txt: "面对一次确定性的损失",
      pB: (lossA / 5) * 0.85,
      a: "接受现实", b: "全力挽回",
      reason: "损失厌恶主导",
    },
    {
      txt: "与陌生人建立联系",
      pB: 0.2 + (social / 10) * 0.6 + impulse * 0.1,
      a: "保持距离", b: "热情靠近",
      reason: "社交频率 + 冲动",
    },
    {
      txt: "权衡高回报但高风险的选择",
      pB: (1 - riskA) * 0.85,
      a: "规避保守", b: "大胆尝试",
      reason: "风险厌恶主导",
    },
    {
      txt: "在冲突或对立面前",
      pB: 0.2 + impulse * 0.5 + (1 - riskA) * 0.15,
      a: "退一步隐忍", b: "正面回应",
      reason: "冲动 + 风险厌恶",
    },
    {
      txt: "看到他人陷入困境",
      pB: 0.25 + (social / 10) * 0.5 + impulse * 0.15,
      a: "默默观望", b: "主动援手",
      reason: "社交频率 + 冲动",
    },
    {
      txt: "面对自身的失败或挫败",
      pB: 0.3 + impulse * 0.4 - (lossA / 5) * 0.2,
      a: "退避反思", b: "立即再来",
      reason: "冲动 vs 损失厌恶",
    },
    {
      txt: "在维持现状与打破现状之间",
      pB: 0.3 + impulse * 0.3 + (1 - riskA) * 0.3,
      a: "维持稳定", b: "主动求变",
      reason: "冲动 + 风险厌恶",
    },
  ];
  return scenarios.map(s => ({
    scenario: s.txt,
    optionA: s.a, optionB: s.b,
    pB: clamp(s.pB),
    reason: s.reason,
  }));
}

/** Parse a chapter marker ("第3章" / "3" / "Ch.4" / "" ) into a chapter
 *  number. Returns null for unparseable values so the timeline can decide
 *  whether to include them in 「全部」or under 「未标注」. */
function parseChapterMarker(raw: string | undefined | null): number | null {
  if (!raw) return null;
  const m = String(raw).match(/(\d+)/);
  return m ? parseInt(m[1], 10) : null;
}

/* ---- Global Relationship Graph (all characters, directed edges with labels) ----
 * Live: takes the parent's already-merged characters array (which folds in
 * the `editing` draft) so relationship edits show up immediately, before
 * 保存. Adds a draggable [from, to] timeline that filters edges + snapshot
 * relationships by the chapter the user set on each relationship. */
function GlobalRelationshipGraph({ characters, editorChapterCount, onSelectCharacter, fullHeight }: { characters: Character[]; editorChapterCount?: number; onSelectCharacter: (id: string) => void; fullHeight?: boolean }) {
  const [zoom, setZoom] = React.useState(1);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [containerSize, setContainerSize] = React.useState({ w: 800, h: 600 });

  // Collect every chapter marker referenced by a relationship (top-level
  // or snapshot) so we can highlight them as ticks on the timeline. The
  // timeline range itself runs 1..editorChapterCount when the editor has
  // content, so dragging always works (not blocked on the user manually
  // tagging chapters on every relationship row).
  const allChapterNums = React.useMemo(() => {
    const set = new Set<number>();
    for (const c of characters) {
      for (const rel of (c.relationships || [])) {
        const n = parseChapterMarker(rel.chapter);
        if (n !== null) set.add(n);
      }
      for (const snap of (c.dynamic_snapshots || [])) {
        const sn = parseChapterMarker(snap.chapter);
        if (sn !== null) set.add(sn);
        for (const rel of (snap.relationships || [])) {
          const rn = parseChapterMarker(rel.chapter);
          if (rn !== null) set.add(rn);
        }
      }
    }
    return Array.from(set).sort((a, b) => a - b);
  }, [characters]);
  const derivedMin = allChapterNums[0] ?? 1;
  const derivedMax = allChapterNums[allChapterNums.length - 1] ?? 1;
  // Editor chapter count expands the range so the user can drag past the
  // last tagged chapter (e.g. visualise relationships up to chapter 20
  // even if only chapters 1, 3, 5 carry markers).
  const chapterMin = 1;
  const chapterMax = Math.max(derivedMax, editorChapterCount || 0, 1);
  const hasTimeline = chapterMax > chapterMin;

  // Single-handle chapter focus — drag to show relationship state AT a
  // specific chapter, not a window. Initial value sits at the latest
  // tracked chapter so the user lands on "current" state.
  const [tlChapter, setTlChapter] = React.useState<number | null>(null);
  React.useEffect(() => {
    setTlChapter(chapterMax);
  }, [chapterMin, chapterMax]);
  const effChapter = tlChapter ?? chapterMax;

  React.useEffect(() => {
    if (!fullHeight || !containerRef.current) return;
    const measure = () => {
      if (containerRef.current) {
        const rect = containerRef.current.getBoundingClientRect();
        // Top control bar (~ 60px) + bottom legend strip (~ 30px) need
        // to be reserved; the SVG fills the remaining flex space.
        const reserved = hasTimeline ? 100 : 50;
        setContainerSize({
          w: Math.max(400, rect.width - 16),
          h: Math.max(300, rect.height - reserved),
        });
      }
    };
    measure();
    const obs = new ResizeObserver(measure);
    obs.observe(containerRef.current);
    return () => obs.disconnect();
  }, [fullHeight, hasTimeline]);

  if (characters.length <= 1) {
    return (
      <div className="card" style={{ padding: 32, textAlign: "center" }}>
        <p className="text-muted">至少需要 2 个角色才能显示关系图谱</p>
      </div>
    );
  }

  const n = characters.length;

  // Dynamic sizing - use container size when fullHeight
  const W = fullHeight ? containerSize.w : Math.max(550, Math.min(1000, n * 140 + 200));
  const H = fullHeight ? containerSize.h : Math.max(400, Math.min(750, n <= 4 ? 420 : 320 + n * 50));
  const cx = W / 2, cy = H / 2;

  // Circular layout
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

  // Show relationships AS OF `effChapter`. A relationship with no
  // chapter marker is treated as "always visible". A relationship tagged
  // with chapter N is shown only if N ≤ effChapter (it's already
  // happened by then). Snapshot relationships contribute the latest
  // snapshot ≤ effChapter.
  const upTo = (chapter?: string | null) => {
    const n2 = parseChapterMarker(chapter);
    if (n2 === null) return true;
    return n2 <= effChapter;
  };
  const allEdges: { fromId: string; toId: string; affinity: number; priority: number; notes?: string; chapter?: string; label?: string; source: "top" | "snapshot" }[] = [];
  characters.forEach(c => {
    // Pick the latest snapshot whose chapter ≤ effChapter. If none, fall
    // back to the top-level relationships.
    const snaps = (c.dynamic_snapshots || []).slice().sort((a, b) => {
      const an = parseChapterMarker(a.chapter) ?? -Infinity;
      const bn = parseChapterMarker(b.chapter) ?? -Infinity;
      return an - bn;
    });
    const activeSnap = snaps.filter(s => {
      const sn = parseChapterMarker(s.chapter);
      return sn !== null && sn <= effChapter;
    }).pop();
    const relsForGraph = activeSnap?.relationships?.length
      ? activeSnap.relationships
      : (c.relationships || []);
    relsForGraph.forEach(rel => {
      if (!positions[rel.target_id]) return;
      if (!upTo(rel.chapter)) return;
      allEdges.push({
        fromId: c.id, toId: rel.target_id,
        affinity: rel.affinity ?? 0, priority: rel.priority ?? 10,
        notes: rel.notes, chapter: rel.chapter, label: rel.label,
        source: activeSnap?.relationships?.length ? "snapshot" : "top",
      });
    });
  });

  // Node radius based on name length
  const nodeR = (name: string) => Math.max(28, name.length * 7 + 8);

  const handleWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setZoom(prev => Math.max(0.5, Math.min(3, prev + (e.deltaY < 0 ? 0.1 : -0.1))));
  };

  // Compute zoomed viewBox
  const vbW = W / zoom, vbH = H / zoom;
  const vbX = (W - vbW) / 2, vbY = (H - vbH) / 2;

  return (
    <div ref={containerRef} className={fullHeight ? "" : "card"} style={fullHeight ? { height: "100%", display: "flex", flexDirection: "column" } : {}}>
      <div className={fullHeight ? "" : "card-body"} style={fullHeight ? { flex: 1, display: "flex", flexDirection: "column", padding: "0 8px 8px", gap: 6 } : { padding: 8 }}>
        {/* Control bar — zoom + chapter timeline live together at the top
            so the timeline doesn't get crushed against the bottom edge. */}
        <div style={{
          display: "flex", gap: 12, alignItems: "center",
          padding: "8px 12px",
          background: "var(--bg-surface)",
          border: "1px solid var(--border-subtle)",
          borderRadius: 8,
          flexShrink: 0,
        }}>
          {hasTimeline ? (
            <div style={{ flex: 1, minWidth: 0 }}>
              <ChapterTimeline
                mode="single"
                min={chapterMin} max={chapterMax}
                from={effChapter} to={effChapter}
                marks={allChapterNums}
                onChange={(f) => { setTlChapter(f); }}
                label={
                  <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                    第 <b style={{ color: "var(--accent)" }}>{effChapter}</b> 章
                  </span>
                }
              />
            </div>
          ) : (
            <span className="text-xs text-muted" style={{ flex: 1 }}>
              编辑器还没有章节，时间轴暂不可用
            </span>
          )}
          <div style={{ display: "flex", gap: 4, flexShrink: 0, alignItems: "center" }}>
            <button className="btn" style={{ fontSize: 10, padding: "2px 8px" }} onClick={() => setZoom(prev => Math.max(0.5, prev - 0.2))}>−</button>
            <span style={{ fontSize: 10, color: "var(--text-secondary)", minWidth: 32, textAlign: "center" }}>{Math.round(zoom * 100)}%</span>
            <button className="btn" style={{ fontSize: 10, padding: "2px 8px" }} onClick={() => setZoom(prev => Math.min(3, prev + 0.2))}>+</button>
            <button className="btn" style={{ fontSize: 10, padding: "2px 8px" }} onClick={() => setZoom(1)}>重置</button>
          </div>
        </div>
        <svg width="100%" height={fullHeight ? "100%" : undefined} viewBox={`${vbX} ${vbY} ${vbW} ${vbH}`} style={fullHeight ? { display: "block", flex: 1, minHeight: 0 } : { display: "block" }} onWheel={handleWheel}>
          <defs>
            {/* Positive 好感 → red. Negative 好感 → green. Zero → gold.
                Stroke width on the line carries the magnitude. */}
            <marker id="rel-arrow-pos" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L8,3 L0,6 Z" fill="var(--accent)" opacity="0.85" />
            </marker>
            <marker id="rel-arrow-neg" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L8,3 L0,6 Z" fill="var(--jade)" opacity="0.85" />
            </marker>
            <marker id="rel-arrow-zero" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L8,3 L0,6 Z" fill="var(--gold)" opacity="0.85" />
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

            // Offset for parallel edges (A->B and B->A). When the pair
            // is bidirectional we widen the gap (14px each side, 28px
            // total) so the two arrows + affinity badges don't crash
            // into each other.
            const hasReverse = allEdges.some(e => e.fromId === edge.toId && e.toId === edge.fromId);
            const perp = hasReverse ? 14 : 0;
            const perpX = -uy * perp;
            const perpY = ux * perp;

            const x1 = from.x + ux * (r1 + 4) + perpX;
            const y1 = from.y + uy * (r1 + 4) + perpY;
            const x2 = to.x - ux * (r2 + 10) + perpX;
            const y2 = to.y - uy * (r2 + 10) + perpY;

            // Edge color: positive 好感 → red (accent), negative → green
            // (jade), zero → gold. Magnitude is read off the stroke
            // width, not the hue, so the binary semantic stays clean.
            const color = edge.affinity > 0 ? "var(--accent)" : edge.affinity < 0 ? "var(--jade)" : "var(--gold)";
            const markerId = edge.affinity > 0 ? "rel-arrow-pos" : edge.affinity < 0 ? "rel-arrow-neg" : "rel-arrow-zero";
            const strokeW = Math.max(1.5, Math.min(3, 1.5 + Math.abs(edge.affinity) / 60));

            // Place the affinity badge CLOSE to the source end (~30% along
            // the edge). For a bidirectional pair this puts A→B's badge
            // near A and B→A's badge near B — they sit ≈ 40% of the edge
            // length apart and can't visually collide.
            const t = 0.32;
            const ax = x1 + (x2 - x1) * t;
            const ay = y1 + (y2 - y1) * t;

            const labelText = edge.label || edge.notes?.slice(0, 6) || "";
            const affLabel = `${edge.affinity > 0 ? "+" : ""}${edge.affinity}`;
            // Edge label sits at the midpoint (and only when present);
            // affinity badge owns the 30% mark so they don't stack.
            const lmx = (x1 + x2) / 2 + perpX * 0.4;
            const lmy = (y1 + y2) / 2 + perpY * 0.4;

            return (
              <g key={`${edge.fromId}-${edge.toId}-${idx}`}>
                <line x1={x1} y1={y1} x2={x2} y2={y2}
                  stroke={color} strokeWidth={strokeW} opacity={0.7}
                  markerEnd={`url(#${markerId})`} />
                {/* Affinity number badge — always shown so the graph
                    carries quantitative info, not just a hue. */}
                <g>
                  <rect
                    x={ax - 17} y={ay - 7}
                    width={34} height={14} rx={7}
                    fill="var(--bg-surface)" stroke={color} strokeWidth={0.8}
                    opacity={0.95}
                  />
                  <text x={ax} y={ay + 3} textAnchor="middle"
                    fontSize={9.5} fill={color} fontWeight={700}>
                    {affLabel}
                  </text>
                </g>
                {labelText && (
                  <text x={lmx} y={lmy - 3} textAnchor="middle" fontSize={9}
                    fill={color} fontWeight={500} opacity={0.9}>
                    {labelText}
                  </text>
                )}
              </g>
            );
          })}

          {/* Character nodes */}
          {characters.map(c => {
            const pos = positions[c.id];
            if (!pos) return null;
            const r = nodeR(c.name);
            const fillColor = c.role === "主角" ? "var(--accent-subtle)" : c.role === "反派" ? "var(--purple-subtle)" : "var(--jade-subtle)";
            return (
              <g key={c.id} style={{ cursor: "pointer" }} onClick={() => onSelectCharacter(c.id)}>
                <circle cx={pos.x} cy={pos.y} r={r} fill={fillColor} stroke="var(--border-hover)" strokeWidth={1.5} />
                <text x={pos.x} y={pos.y + 5} textAnchor="middle" fontSize={12} fontWeight={500} fill="var(--text-primary)">
                  {c.name}
                </text>
              </g>
            );
          })}
        </svg>
        <div style={{ display: "flex", gap: 16, justifyContent: "center", alignItems: "center", flexWrap: "wrap", flexShrink: 0, fontSize: 10, color: "var(--text-tertiary)" }}>
          <span><span style={{ color: "var(--accent)" }}>&#9632;</span> 好感 &gt; 0</span>
          <span><span style={{ color: "var(--gold)" }}>&#9632;</span> 好感 = 0</span>
          <span><span style={{ color: "var(--jade)" }}>&#9632;</span> 好感 &lt; 0</span>
          <span style={{ marginLeft: 16, color: "var(--text-disabled)" }}>
            点击角色节点跳转到详情{hasTimeline ? " · 顶部拖动时间轴定位到某一章查看好感快照" : ""}
          </span>
        </div>
      </div>
    </div>
  );
}


/* ---- Single Character Relationship Graph (3.2.4) ---- */
function SingleCharRelGraph({ character, allCharacters, onSelectCharacter }: {
  character: Character; allCharacters: Character[]; onSelectCharacter: (id: string) => void;
}) {
  const rels = character.relationships || [];
  if (rels.length === 0) return null;

  const connectedIds = rels.map(r => r.target_id);
  const connectedChars = allCharacters.filter(c => connectedIds.includes(c.id));

  const W = 500, H = Math.max(300, connectedChars.length * 40 + 100);
  const centerX = W / 2, centerY = H / 2;
  const radius = Math.min(W, H) * 0.35;

  const nodeR = (name: string) => Math.max(24, name.length * 7 + 6);
  const centerR = nodeR(character.name);

  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: "block" }}>
      <defs>
        <marker id="sc-arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto" markerUnits="strokeWidth">
          <path d="M0,0 L8,3 L0,6 Z" fill="var(--accent)" opacity="0.8" />
        </marker>
      </defs>

      {/* Edges from center to connected nodes */}
      {connectedChars.map((c, i) => {
        const angle = -Math.PI / 2 + (2 * Math.PI / connectedChars.length) * i;
        const tx = centerX + radius * Math.cos(angle);
        const ty = centerY + radius * Math.sin(angle);
        const rel = rels.find(r => r.target_id === c.id);
        if (!rel) return null;

        const dx = tx - centerX, dy = ty - centerY;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const ux = dx / dist, uy = dy / dist;
        const tr = nodeR(c.name);

        const x1 = centerX + ux * (centerR + 4);
        const y1 = centerY + uy * (centerR + 4);
        const x2 = tx - ux * (tr + 10);
        const y2 = ty - uy * (tr + 10);

        // Heatmap color based on affinity
        const aff = rel.affinity || 0;
        const color = aff > 50 ? "var(--jade)" : aff > 0 ? "#6bba6b" : aff > -30 ? "var(--gold)" : "var(--error)";
        const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;

        return (
          <g key={c.id}>
            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={color} strokeWidth={2} opacity={0.7} markerEnd="url(#sc-arrow)" />
            {rel.label && (
              <text x={mx} y={my - 6} textAnchor="middle" fontSize={9} fill={color} fontWeight={500}>{rel.label}</text>
            )}
            <text x={mx} y={my + 8} textAnchor="middle" fontSize={8} fill="var(--text-disabled)">
              {aff > 0 ? "+" : ""}{aff}
            </text>
          </g>
        );
      })}

      {/* Connected nodes */}
      {connectedChars.map((c, i) => {
        const angle = -Math.PI / 2 + (2 * Math.PI / connectedChars.length) * i;
        const tx = centerX + radius * Math.cos(angle);
        const ty = centerY + radius * Math.sin(angle);
        const r = nodeR(c.name);
        return (
          <g key={c.id} style={{ cursor: "pointer" }} onClick={() => onSelectCharacter(c.id)}>
            <circle cx={tx} cy={ty} r={r} fill="var(--bg-surface-2)" stroke="var(--border-hover)" strokeWidth={1.5} />
            <text x={tx} y={ty + 4} textAnchor="middle" fontSize={11} fontWeight={500} fill="var(--text-primary)">{c.name}</text>
          </g>
        );
      })}

      {/* Center node (current character) */}
      <circle cx={centerX} cy={centerY} r={centerR} fill="var(--accent-subtle)" stroke="var(--accent)" strokeWidth={2} />
      <text x={centerX} y={centerY + 5} textAnchor="middle" fontSize={13} fontWeight={700} fill="var(--accent)">{character.name}</text>
    </svg>
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
