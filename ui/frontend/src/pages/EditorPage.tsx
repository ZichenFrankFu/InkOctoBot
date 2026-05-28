import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useToast } from "../components/shared/Toast";
import { useResizable } from "../hooks/useResizable";
import { useDialog } from "../components/shared/Dialog";
import useDebounce from "../hooks/useDebounce";
import { computeDiff, groupIntoHunks, assembleFromHunks } from "../utils/simpleDiff";
import type { DiffHunk } from "../utils/simpleDiff";
import type { Volume, ChapterOutline, PipelineStatus, EvalResult, FollowUpQuestion, TextVersion } from "../api/types";
import EvalReport from "../components/editor/EvalReport";
import type { EvalReportData } from "../components/editor/EvalReport";
import FollowUpQuestions from "../components/shared/FollowUpQuestions";
import WebLLMPromptPanel from "../components/shared/WebLLMPromptPanel";
import EditorRightDrawer from "../components/shared/EditorRightDrawer";

const vuid = () => `v_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;

const uid = () => `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
const wc = (t: string) => (t ? t.replace(/[\s\p{P}]/gu, "").length : 0);

const PIPELINE_STEPS: PipelineStatus[] = [
  { step: "Scene Director", status: "pending", detail: "将大纲拆为场景并注入导演指令" },
  { step: "Actor Agents", status: "pending", detail: "角色扮演生成原始对话与内心" },
  { step: "Editor-Writer", status: "pending", detail: "剪辑+文学风格化，~600字/段输出" },
  { step: "Evaluator", status: "pending", detail: "一致性检查 & 质量评估" },
];

interface LocalVolume extends Volume { collapsed?: boolean; }

const AGENT_COLORS: Record<string, { bg: string; border: string; name: string }> = {
  "Scene Director": { bg: "var(--indigo-subtle)", border: "var(--indigo)", name: "Scene Director" },
  "Actor Agents": { bg: "var(--gold-subtle)", border: "var(--gold)", name: "Actor Agents" },
  "Editor-Writer": { bg: "var(--jade-subtle)", border: "var(--jade)", name: "Editor-Writer" },
  "Evaluator": { bg: "var(--accent-subtle)", border: "var(--accent)", name: "Evaluator" },
  "旁白": { bg: "var(--bg-surface-2)", border: "var(--text-secondary)", name: "旁白" },
  "User": { bg: "var(--purple-subtle)", border: "var(--purple)", name: "用户" },
  "System": { bg: "var(--bg-surface-2)", border: "var(--text-tertiary)", name: "系统" },
};

// Character-specific avatar colors for Actor agent group chat
const CHAR_COLORS = [
  { bg: "rgba(255,160,60,0.12)", border: "#e8a040" },
  { bg: "rgba(100,180,255,0.12)", border: "#64b4ff" },
  { bg: "rgba(255,100,130,0.12)", border: "#ff6482" },
  { bg: "rgba(130,220,120,0.12)", border: "#82dc78" },
  { bg: "rgba(200,140,255,0.12)", border: "#c88cff" },
  { bg: "rgba(255,210,80,0.12)", border: "#ffd250" },
];
const getCharColor = (name: string) => {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) | 0;
  return CHAR_COLORS[Math.abs(hash) % CHAR_COLORS.length];
};

interface ChatMessage {
  agent: string;
  content: string;
  status?: "thinking" | "speaking" | "done" | "waiting_confirm";
  timestamp: number;
  isQuestion?: boolean;
  isWarning?: boolean;
  isCoT?: boolean;
  promptSent?: string;
  followUpOptions?: string[];
  warningOptions?: string[];
  agentDisplayName?: string;
}

/** Format Scene Director JSON output as human-readable screenplay format */
function formatSceneDirectorOutput(result: any): string {
  if (!result?.scenes) {
    if (typeof result === "string") {
      try { return formatSceneDirectorOutput(JSON.parse(result)); } catch { return result; }
    }
    // If it has a summary or raw text, show that
    if (result?.summary) return result.summary;
    if (result?.raw) return result.raw;
    return JSON.stringify(result, null, 2);
  }
  const parts = result.scenes.map((scene: any, i: number) => {
    const lines: string[] = [];
    lines.push(`━━━ 场景 ${i + 1}${scene.location ? `: ${scene.location}` : ""} ━━━`);
    if (scene.time) lines.push(`时间：${scene.time}`);
    if (scene.characters?.length) lines.push(`出场：${scene.characters.join("、")}`);
    if (scene.summary) { lines.push(""); lines.push(`【概要】${scene.summary}`); }
    if (scene.beats?.length) {
      lines.push(""); lines.push("【节拍】");
      scene.beats.forEach((b: string, bi: number) => lines.push(`  ${bi + 1}. ${b}`));
    }
    if (scene.character_instructions) {
      lines.push(""); lines.push("【角色指令】");
      for (const [name, inst] of Object.entries(scene.character_instructions as Record<string, any>)) {
        lines.push(`  ${name}:`);
        if (inst.emotional_state) lines.push(`    情绪: ${inst.emotional_state}`);
        if (inst.secret_goal) lines.push(`    暗线: ${inst.secret_goal}`);
        if (inst.must?.length) lines.push(`    必须: ${inst.must.join("; ")}`);
        if (inst.must_not?.length) lines.push(`    禁止: ${inst.must_not.join("; ")}`);
      }
    }
    if (scene.narrator_instructions) { lines.push(""); lines.push(`【旁白指引】${scene.narrator_instructions}`); }
    return lines.join("\n");
  });
  let output = parts.join("\n\n");
  if (result.chapter_arc) output += `\n\n【本章情绪弧线】${result.chapter_arc}`;
  return output;
}

// MIN_SAVE_CHARS — physical guard against the "saved 0-char text_versions"
// disaster the user hit (see docs/UI_REDESIGN_SPEC.md §3). If the editor
// content is below this, save-paths confirm before writing. 50 chars is
// well below any real chapter and well above any conceivable empty paste.
const MIN_SAVE_CHARS = 50;

/** Returns true if the user wants to save anyway, false to abort. */
function confirmShortSave(chars: number): boolean {
  return window.confirm(
    `⚠️ 内容只有 ${chars} 字 (< ${MIN_SAVE_CHARS} 字推荐下限)。\n\n` +
    `这是过去几次"text_versions 0 字符"灾难的根因。常见原因：\n` +
    `  • LLM 调用其实没返回内容\n` +
    `  • 粘贴到了错误的输入框\n` +
    `  • 编辑器内容被清空后误点了保存\n\n` +
    `仍然要保存吗？`
  );
}

export default function EditorPage({ projectId, onNavigate }: { projectId: string; onNavigate?: (tab: string) => void }) {
  const { toast } = useToast();
  const [volumes, setVolumes] = useState<LocalVolume[]>([]);
  const [activeChId, setActiveChId] = useState<string>("");
  const [content, setContent] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"saved" | "saving" | "unsaved">("saved");
  // Right-side inspector drawer (spec §5).
  const [drawerOpen, setDrawerOpen] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Last successfully-persisted snapshot per chapter — lets the
  // auto-save skip no-op writes (e.g. right after a chapter switch).
  const persistedRef = useRef<{ chId: string; content: string; title: string }>({ chId: "", content: "", title: "" });
  const [startTime] = useState(Date.now());
  const [elapsed, setElapsed] = useState(0);
  const [searchTerm, setSearchTerm] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState("");
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleVal, setTitleVal] = useState("");
  // Version history state
  const [versionHistory, setVersionHistory] = useState<TextVersion[]>([]);
  const [showVersionHistory, setShowVersionHistory] = useState(false);
  const [maxBackupVersions, setMaxBackupVersions] = useState(10);
  const autoVersionTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastAutoVersionContent = useRef<string>("");

  // Restore aiTab from session if pipeline was running
  const _savedEditorState = (() => {
    try { const raw = sessionStorage.getItem(`inkocto_editor_chat_${projectId}`); return raw ? JSON.parse(raw) : null; } catch { return null; }
  })();
  const [chatLoaded, setChatLoaded] = useState(false);
  const [aiTab, setAiTab] = useState<"outline" | "single" | "cluster" | "rewrite" | "eval">(
    () => normalizeAiTab(_savedEditorState?.aiTab));
  const [selection, setSelection] = useState<{ start: number; end: number; text: string } | null>(null);
  const [rewritePrompt, setRewritePrompt] = useState("");
  const [rewriteModel, setRewriteModel] = useState("default");
  const [pipelineSteps, setPipelineSteps] = useState<PipelineStatus[]>(PIPELINE_STEPS);
  const [generating, setGenerating] = useState(false);
  const [evalResult, setEvalResult] = useState<EvalResult | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>(_savedEditorState?.chatMessages || []);
  const [chatInput, setChatInput] = useState(_savedEditorState?.chatInput || "");
  const [currentAgent, setCurrentAgent] = useState<string | null>(null);
  const [waitingForConfirm, setWaitingForConfirm] = useState(false);
  const [mergePreview, setMergePreview] = useState<{ original: string; generated: string } | null>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const leftPanel = useResizable({ direction: "horizontal", initialSize: 240, minSize: 160, maxSize: 400 });
  const rightPanel = useResizable({ direction: "horizontal", initialSize: 400, minSize: 260, maxSize: 680, invert: true });
  const [rightPanelOpen, setRightPanelOpen] = useState(true);
  const [leftPanelOpen, setLeftPanelOpen] = useState(true);
  const { confirm } = useDialog();

  // Persist editor chat state to sessionStorage + backend (per chapter)
  const EDITOR_CHAT_KEY = `inkocto_editor_chat_${projectId}_${activeChId}`;
  const chatSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const persistable = chatMessages.filter(m => m.status === "done");
    sessionStorage.setItem(EDITOR_CHAT_KEY, JSON.stringify({ aiTab, chatMessages: persistable, chatInput }));
    // Debounced save to backend for permanent persistence
    if (chatLoaded && persistable.length > 0) {
      if (chatSaveTimer.current) clearTimeout(chatSaveTimer.current);
      chatSaveTimer.current = setTimeout(() => {
        apiPut("/api/data/chat_history", {
          project_id: projectId || "default", scope: `pipeline_${activeChId}`,
          messages: persistable.slice(-200),  // keep last 200 messages
        }).catch((err) => { console.warn("Chat save failed:", err.message); });
      }, 2000);
    }
  }, [aiTab, chatMessages, chatInput, EDITOR_CHAT_KEY, chatLoaded, projectId, activeChId]);

  // Load chat history from backend on chapter change
  useEffect(() => {
    if (!activeChId) return;
    const pid = projectId || "default";
    setChatMessages([]);
    setChatLoaded(false);
    // Try to restore from sessionStorage first
    try {
      const raw = sessionStorage.getItem(`inkocto_editor_chat_${pid}_${activeChId}`);
      if (raw) {
        const saved = JSON.parse(raw);
        if (saved?.chatMessages?.length > 0) {
          setChatMessages(saved.chatMessages);
          if (saved.aiTab) setAiTab(normalizeAiTab(saved.aiTab));
          setChatLoaded(true);
          return;
        }
      }
    } catch { /* ignore */ }
    apiGet<{ messages: ChatMessage[] }>(`/api/data/chat_history?project_id=${pid}&scope=pipeline_${activeChId}`)
      .then(r => {
        if (r.messages && r.messages.length > 0) {
          setChatMessages(r.messages);
        }
        setChatLoaded(true);
      })
      .catch(() => setChatLoaded(true));
  }, [projectId, activeChId]);

  // Auto-switch to the active generation tab when a run is in progress.
  useEffect(() => {
    if (generating && aiTab !== "single" && aiTab !== "cluster") {
      setAiTab(genModeRef.current);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generating]);

  // Load version history on mount
  useEffect(() => {
    if (!activeChId) return;
    apiGet<{ versions: TextVersion[] }>(`/api/data/versions?project_id=${projectId || "default"}&chapter_id=${activeChId}`)
      .then(r => { if (r.versions?.length > 0) setVersionHistory(r.versions); })
      .catch((err) => { console.warn("Version history load failed:", err.message); });
  }, [projectId, activeChId]);

  useEffect(() => {
    const pid = projectId || "default";
    apiGet<{ volumes: LocalVolume[] }>(`/api/data/editor?project_id=${pid}`)
      .catch(() => ({ volumes: [] as LocalVolume[] }))
      .then((data) => {
        let vols = data.volumes || [];
        if (vols.length === 0) {
          const ch: ChapterOutline = { id: uid(), volume_id: "v1", title: "第一章", order: 1, synopsis: "", content: "", word_count: 0 };
          vols = [{ id: "v1", project_id: pid, title: "第一卷", order: 1, chapters: [ch] }];
        }
        setVolumes(vols);
        const firstCh = vols[0]?.chapters?.[0];
        if (firstCh) { setActiveChId(firstCh.id); setContent(firstCh.content || ""); setTitleVal(firstCh.title); }
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, [projectId]);

  useEffect(() => {
    const iv = setInterval(() => setElapsed(Math.floor((Date.now() - startTime) / 60000)), 30000);
    return () => clearInterval(iv);
  }, [startTime]);

  const activeCh = useMemo(() => { for (const v of volumes) { const c = v.chapters.find(c => c.id === activeChId); if (c) return c; } return null; }, [volumes, activeChId]);
  const activeVol = useMemo(() => volumes.find(v => v.chapters.some(c => c.id === activeChId)) || null, [volumes, activeChId]);

  useEffect(() => {
    if (activeCh && loaded) {
      setContent(activeCh.content || "");
      setTitleVal(activeCh.title);
      persistedRef.current = { chId: activeChId, content: activeCh.content || "", title: activeCh.title };
    }
    setEditingTitle(false); setSelection(null);
  }, [activeChId, loaded]);

  useEffect(() => {
    if (saveStatus !== "unsaved") return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [saveStatus]);

  useEffect(() => {
    if (!loaded) return;
    // Skip the auto-save entirely when nothing actually changed — e.g.
    // a chapter switch just loaded identical content. Saving here would
    // be a no-op network write + a spurious version bump.
    const p = persistedRef.current;
    if (p.chId === activeChId && p.content === content && p.title === titleVal) {
      return;
    }
    setSaveStatus("unsaved");
    if (saveTimer.current) clearTimeout(saveTimer.current);
    const chId = activeChId;
    saveTimer.current = setTimeout(async () => {
      setSaveStatus("saving");
      const updatedVolumes = volumes.map(v => ({ ...v, chapters: v.chapters.map(c => c.id === chId ? { ...c, content, title: titleVal || c.title, word_count: wc(content) } : c) }));
      setVolumes(updatedVolumes);
      try {
        await apiPut("/api/data/editor", { project_id: projectId || "default", volumes: updatedVolumes });
        persistedRef.current = { chId, content, title: titleVal };
        setSaveStatus("saved");
      } catch (e: any) { setSaveStatus("unsaved"); console.warn("自动保存失败:", e.message); }
    }, 1500);
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current); };
  }, [content, titleVal]);

  // Load max_backup_versions setting
  useEffect(() => {
    apiGet<{ max_backup_versions?: number }>("/api/data/settings")
      .then(r => { if (r.max_backup_versions) setMaxBackupVersions(r.max_backup_versions); })
      .catch((err) => { console.warn("Settings load failed:", err.message); });
  }, []);

  // Auto-save version backup (every 60s if content changed)
  useEffect(() => {
    if (!loaded || !activeChId || !content) return;
    if (autoVersionTimer.current) clearTimeout(autoVersionTimer.current);
    autoVersionTimer.current = setTimeout(() => {
      if (content === lastAutoVersionContent.current) return;
      // Skip when content is below the save-disaster floor — auto-save
      // runs silently in the background so it can't even prompt the
      // user; just never write below MIN_SAVE_CHARS.
      if (content.trim().length < MIN_SAVE_CHARS) return;
      lastAutoVersionContent.current = content;
      const newVersion: TextVersion = {
        version_id: vuid(), chapter_id: activeChId,
        version: versionHistory.filter(v => v.chapter_id === activeChId).length + 1,
        source: "auto_saved", text: content,
        synopsis: activeCh?.synopsis || "",
        created_at: new Date().toISOString(),
      };
      setVersionHistory(prev => {
        const updated = [...prev, newVersion];
        // Trim auto_saved versions per chapter to maxBackupVersions
        const byChapter: Record<string, typeof updated> = {};
        for (const v of updated) {
          (byChapter[v.chapter_id] ||= []).push(v);
        }
        const trimmed: typeof updated = [];
        for (const [, chVersions] of Object.entries(byChapter)) {
          const autoVersions = chVersions.filter(v => v.source === "auto_saved");
          const otherVersions = chVersions.filter(v => v.source !== "auto_saved");
          const keptAuto = autoVersions.slice(-maxBackupVersions);
          trimmed.push(...otherVersions, ...keptAuto);
        }
        return trimmed;
      });
      apiPost("/api/data/versions", { project_id: projectId || "default", version: newVersion }).catch(() => {});
    }, 60000); // 60 seconds
    return () => { if (autoVersionTimer.current) clearTimeout(autoVersionTimer.current); };
  }, [content, loaded, activeChId, maxBackupVersions]);

  // Reset last auto-version content when switching chapters
  useEffect(() => {
    lastAutoVersionContent.current = "";
  }, [activeChId]);

  const handleSaveOutline = useCallback(async () => {
    setSaveStatus("saving");
    const uv = volumes.map(v => ({ ...v, chapters: v.chapters.map(c => c.id === activeChId ? { ...c, content, title: titleVal || c.title, word_count: wc(content) } : c) }));
    try { await apiPut("/api/data/editor", { project_id: projectId || "default", volumes: uv }); setSaveStatus("saved"); toast("已保存", "success"); } catch (e: any) { setSaveStatus("unsaved"); toast(e.message || "保存失败", "error"); }
  }, [volumes, activeChId, content, titleVal, projectId, toast]);

  // Ctrl+S / Cmd+S triggers manual save with toast
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        handleSaveOutline();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handleSaveOutline]);

  const handleMouseUp = () => {
    const el = textRef.current; if (!el) return;
    setTimeout(() => {
      const s = el.selectionStart, e = el.selectionEnd;
      if (s !== undefined && e !== undefined && e > s) { const txt = content.substring(s, e); if (txt.trim().length > 0) { setSelection({ start: s, end: e, text: txt }); return; } }
      setSelection(null);
    }, 10);
  };

  const addVolume = () => { setVolumes([...volumes, { id: uid(), project_id: projectId, title: `第${volumes.length + 1}卷`, order: volumes.length + 1, chapters: [], collapsed: false }]); };
  const addChapter = (volId: string) => { const vol = volumes.find(v => v.id === volId); if (!vol) return; const ch: ChapterOutline = { id: uid(), volume_id: volId, title: `第${vol.chapters.length + 1}章`, order: vol.chapters.length + 1, synopsis: "", content: "", word_count: 0 }; setVolumes(volumes.map(v => v.id === volId ? { ...v, chapters: [...v.chapters, ch] } : v)); };
  const addChapterToFirstVolume = () => { if (volumes.length > 0) addChapter(volumes[0].id); };
  const deleteChapter = (chId: string) => { const allChs = volumes.flatMap(v => v.chapters); if (allChs.length <= 1) return; setVolumes(volumes.map(v => ({ ...v, chapters: v.chapters.filter(c => c.id !== chId) }))); if (activeChId === chId) { const r = allChs.filter(c => c.id !== chId); if (r.length) setActiveChId(r[0].id); } };
  const toggleVolume = (volId: string) => { setVolumes(volumes.map(v => v.id === volId ? { ...v, collapsed: !v.collapsed } : v)); };
  const startRename = (id: string, title: string) => { setRenamingId(id); setRenameVal(title); };
  const commitRename = () => { if (!renamingId || !renameVal.trim()) { setRenamingId(null); return; } setVolumes(volumes.map(v => { if (v.id === renamingId) return { ...v, title: renameVal.trim() }; return { ...v, chapters: v.chapters.map(c => c.id === renamingId ? { ...c, title: renameVal.trim() } : c) }; })); setRenamingId(null); };
  const updateSynopsis = (val: string) => { setVolumes(volumes.map(v => ({ ...v, chapters: v.chapters.map(c => c.id === activeChId ? { ...c, synopsis: val } : c) }))); };

  // Debounce the search term used for filtering: typing fires character
  // events but the filter pass scans every chapter's full text (can be
  // 100k+ chars/chapter), so unthrottled filtering blocks the input.
  const debouncedSearch = useDebounce(searchTerm, 200);
  const searchLower = useMemo(
    () => debouncedSearch.trim().toLowerCase(),
    [debouncedSearch],
  );
  const filteredVolumes = useMemo(() => {
    if (!searchLower) return volumes;
    return volumes
      .map(v => ({
        ...v,
        chapters: v.chapters.filter(c =>
          c.title.toLowerCase().includes(searchLower)
          || (c.content || "").toLowerCase().includes(searchLower)
          || (c.synopsis || "").toLowerCase().includes(searchLower)
        ),
      }))
      .filter(v => v.chapters.length > 0 || v.title.toLowerCase().includes(searchLower));
  }, [volumes, searchLower]);

  // Save text via the browser save-as dialog (lets the user pick the path);
  // falls back to a plain download for browsers without showSaveFilePicker.
  const saveTextFile = async (text: string, suggestedName: string): Promise<boolean> => {
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const picker = (window as any).showSaveFilePicker;
    if (typeof picker === "function") {
      let handle: any;
      try {
        handle = await picker.call(window, {
          suggestedName,
          types: [{ description: "文本文件", accept: { "text/plain": [".txt"] } }],
        });
      } catch (e: any) {
        if (e?.name === "AbortError") return false;  // user cancelled the dialog
        throw e;
      }
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      return true;
    }
    // Fallback for browsers without the save-picker API.
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = suggestedName;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
    return true;
  };

  const handleBundleExport = async () => {
    try {
      const pid = projectId || "default";
      // Fetch characters + worldbook in parallel
      const [charResp, wbResp] = await Promise.all([
        apiGet<{ items: any[] }>(`/api/data/characters?project_id=${pid}`),
        apiGet<{ items: any[] }>(`/api/data/worldbook?project_id=${pid}`),
      ]);
      const characters = charResp.items || [];
      const worldbook = wbResp.items || [];

      const lines: string[] = [];
      lines.push("╔══════════════════════════════════════╗");
      lines.push("║      角色 + 世界书 + 章节大纲 导出     ║");
      lines.push("╚══════════════════════════════════════╝");
      lines.push("");

      // Characters section
      lines.push("═".repeat(50));
      lines.push("【角色卡】");
      lines.push("═".repeat(50));
      if (characters.length === 0) {
        lines.push("（暂无角色数据）\n");
      } else {
        for (const c of characters) {
          lines.push(`\n▸ ${c.name}${c.role ? ` (${c.role})` : ""}`);
          if (c.personality) lines.push(`  性格：${c.personality}`);
          if (c.background) lines.push(`  背景：${c.background}`);
          if (c.speech_style) lines.push(`  说话风格：${c.speech_style}`);
          if (c.appearance) lines.push(`  外貌：${c.appearance}`);
          if (c.tags?.length) lines.push(`  标签：${c.tags.join("、")}`);
          if (c.relationships?.length) {
            lines.push("  关系：");
            for (const r of c.relationships) {
              lines.push(`    - ${r.target_name}${r.label ? ` [${r.label}]` : ""}: 亲密度 ${r.affinity ?? "N/A"}${r.notes ? ` (${r.notes})` : ""}`);
            }
          }
        }
        lines.push("");
      }

      // World book section
      lines.push("═".repeat(50));
      lines.push("【世界书】");
      lines.push("═".repeat(50));
      if (worldbook.length === 0) {
        lines.push("（暂无世界书数据）\n");
      } else {
        const categoryNames: Record<string, string> = {
          power_system: "力量体系", factions: "势力", geography: "地理",
          social_rules: "社会规则", history: "历史", hard_rules: "世界观规则", other: "其他",
        };
        // Group by category
        const grouped: Record<string, any[]> = {};
        for (const wb of worldbook) {
          const cat = wb.category || "other";
          (grouped[cat] ||= []).push(wb);
        }
        for (const [cat, entries] of Object.entries(grouped)) {
          lines.push(`\n── ${categoryNames[cat] || cat} ──`);
          for (const e of entries) {
            lines.push(`\n▸ ${e.title}`);
            if (e.content) lines.push(`  ${e.content}`);
            if (e.tags?.length) lines.push(`  标签：${e.tags.join("、")}`);
          }
        }
        lines.push("");
      }

      // Chapter outlines section
      lines.push("═".repeat(50));
      lines.push("【章节大纲】");
      lines.push("═".repeat(50));
      for (const v of volumes) {
        lines.push(`\n━━━ ${v.title} ━━━`);
        for (const c of v.chapters) {
          lines.push(`\n▸ ${c.title}${c.characters?.length ? ` [${c.characters.join("、")}]` : ""}`);
          if (c.time) lines.push(`  时间：${c.time}`);
          if (c.location) lines.push(`  地点：${c.location}`);
          if (c.synopsis) lines.push(`  大纲：${c.synopsis}`);
          lines.push(`  字数：${c.word_count || wc(c.content || "")} 字`);
        }
      }

      const text = lines.join("\n");
      const ok = await saveTextFile(text, `导出_${Date.now()}.txt`);
      if (ok) toast("已导出角色+世界书+章节大纲", "success");
    } catch (e: any) {
      toast(e.message || "导出失败", "error");
    }
  };

  // Batch multi-select mode: clicking "批量" enters a chapter-picking mode in
  // the tree, then the action bar bulk-deletes or bulk-exports the selection.
  const [batchMode, setBatchMode] = useState(false);
  const [selectedChIds, setSelectedChIds] = useState<Set<string>>(new Set());

  const toggleChSelected = (chId: string) => {
    setSelectedChIds(prev => {
      const next = new Set(prev);
      if (next.has(chId)) next.delete(chId); else next.add(chId);
      return next;
    });
  };

  const exitBatchMode = () => { setBatchMode(false); setSelectedChIds(new Set()); };

  const selectAllChapters = () => {
    setSelectedChIds(new Set(volumes.flatMap(v => v.chapters).map(c => c.id)));
  };

  const deleteSelectedChapters = async () => {
    if (selectedChIds.size === 0) return;
    const allChs = volumes.flatMap(v => v.chapters);
    const remaining = allChs.filter(c => !selectedChIds.has(c.id));
    if (remaining.length === 0) { toast("至少需保留一个章节", "error"); return; }
    if (!(await confirm({ message: `确认删除选中的 ${selectedChIds.size} 个章节？此操作不可撤销。`, destructive: true }))) return;
    setVolumes(volumes.map(v => ({ ...v, chapters: v.chapters.filter(c => !selectedChIds.has(c.id)) })));
    if (activeChId && selectedChIds.has(activeChId)) setActiveChId(remaining[0].id);
    exitBatchMode();
    toast("已删除选中章节", "success");
  };

  const exportSelectedChapters = async () => {
    if (selectedChIds.size === 0) { toast("请先选择章节", "error"); return; }
    const lines: string[] = [];
    for (const v of volumes) {
      const picked = v.chapters.filter(c => selectedChIds.has(c.id));
      if (picked.length === 0) continue;
      lines.push(`===== ${v.title} =====\n`);
      for (const c of picked) {
        lines.push(`--- ${c.title} ---\n`);
        lines.push((c.content || "") + "\n\n");
      }
    }
    try {
      const ok = await saveTextFile(lines.join("\n"), `章节导出_${Date.now()}.txt`);
      if (ok) toast(`已导出 ${selectedChIds.size} 个章节`, "success");
    } catch (e: any) {
      toast(e?.message || "导出失败", "error");
    }
  };

  const generatedTextRef = useRef<string>("");
  const stepTextRef = useRef<string>("");  // text for current step only
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const eventCursorRef = useRef(0);
  const sessionIdRef = useRef<string | null>(null);
  const genModeRef = useRef<"single" | "cluster">("single");
  const [manualPrompt, setManualPrompt] = useState<{ step: string; prompt: string } | null>(null);
  const [manifest, setManifest] = useState<ContextManifest | null>(null);
  const [skillSelection, setSkillSelection] = useState<Record<string, boolean>>({});
  const [ragExcludes, setRagExcludes] = useState<Set<string>>(new Set());
  const [manifestNonce, setManifestNonce] = useState(0);
  const modelSnapshotRef = useRef<{ provider: string; model: string } | null>(null);
  const [modelChanged, setModelChanged] = useState(false);

  // Persist active session across page navigation
  const SESS_KEY = `inkocto_pipeline_${projectId}_${activeChId}`;

  // On mount: check for a running pipeline session to resume
  useEffect(() => {
    const saved = sessionStorage.getItem(SESS_KEY);
    if (saved) {
      try {
        const { sessionId, cursor } = JSON.parse(saved);
        if (sessionId) {
          // Resume polling from saved cursor position to avoid duplicate events
          sessionIdRef.current = sessionId;
          setGenerating(true);
          eventCursorRef.current = cursor || 0;
          startPolling(sessionId);
        }
      } catch { /* ignore */ }
    }
    return () => stopPolling();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopPolling = () => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  };

  const handleEvent = useCallback((data: any) => {
    const stepToLabel = (s: string) =>
      s === "scene_director" ? "Scene Director"
      : s === "actor_agents" ? "Actor Agents"
      : s === "editor_writer" ? "Editor-Writer"
      : s === "evaluator" ? "Evaluator" : "System";

    switch (data.type) {
      case "manual_prompt":
        setManualPrompt({ step: data.step || "", prompt: data.prompt || "" });
        break;
      case "skills_used":
        setChatMessages(prev => [...prev, {
          agent: "System", content: formatSkillsUsed(data.skills),
          status: "done", timestamp: Date.now(),
        }]);
        break;
      case "pipeline_start":
        setChatMessages(prev => {
          if (prev.some(m => m.content.includes("Pipeline") && m.content.includes("开始"))) return prev;
          return [...prev, { agent: "System", content: "Pipeline 开始生成...", status: "done", timestamp: Date.now() }];
        });
        break;
      case "step_start":
        setPipelineSteps(prev => prev.map(s =>
          s.step === data.label ? { ...s, status: "running" as const, detail: data.detail } : s
        ));
        setCurrentAgent(data.label);
        stepTextRef.current = "";
        setChatMessages(prev => [...prev, {
          agent: data.label, content: data.detail || "正在处理中...",
          status: "thinking", timestamp: Date.now(),
        }]);
        break;
      case "token":
        generatedTextRef.current += data.content;
        stepTextRef.current += data.content;
        setChatMessages(prev => {
          const last = prev[prev.length - 1];
          if (last && (last.status === "thinking" || last.status === "speaking")) {
            const updated = [...prev];
            // Show last 600 chars of current step text as preview
            const preview = stepTextRef.current.length > 600
              ? "...\n" + stepTextRef.current.slice(-600)
              : stepTextRef.current;
            updated[updated.length - 1] = {
              ...last,
              content: preview,
              status: "speaking",
            };
            return updated;
          }
          return prev;
        });
        break;
      case "handoff":
        // Show agent I/O transitions as system messages
        setChatMessages(prev => [...prev, {
          agent: "System",
          content: `[${data.from} → ${data.to}] ${data.content}`,
          status: "done", timestamp: Date.now(),
        }]);
        break;
      case "step_done": {
        const label = data.agent_display_name || stepToLabel(data.step);
        const stepLabel = stepToLabel(data.step);
        setPipelineSteps(prev => prev.map(s =>
          s.step === stepLabel ? { ...s, status: "done" as const, detail: "已完成", progress: 100 } : s
        ));
        // Build result content
        let resultContent: string;
        if (data.step === "scene_director" && data.result && !data.result?.error) {
          resultContent = data.result?.text || formatSceneDirectorOutput(data.result);
        } else if (data.result?.text && !data.result?.error) {
          resultContent = data.result.text;
        } else if (data.result?.error) {
          resultContent = `生成出错：${data.result.error}`;
        } else if (data.result?.score !== undefined) {
          resultContent = data.result.summary_text || `评估完成。得分：${data.result.score}/100`;
          if (!data.result.summary_text && data.result.issues?.length) {
            resultContent += "\n\n问题：\n" + data.result.issues.map((i: any) =>
              `- [${i.severity}] ${i.type}: ${i.description}`
            ).join("\n");
          }
        } else if (data.result?.summary) {
          resultContent = data.result.summary;
        } else {
          resultContent = JSON.stringify(data.result || {}, null, 2).slice(0, 1000);
        }
        const msg: ChatMessage = {
          agent: data.agent_display_name ? "Actor Agents" : label,
          content: resultContent, status: "done", timestamp: Date.now(),
          agentDisplayName: data.agent_display_name || undefined,
        };
        if (data.result?.prompt_sent) {
          msg.promptSent = typeof data.result.prompt_sent === "string"
            ? data.result.prompt_sent
            : JSON.stringify(data.result.prompt_sent, null, 2);
        }
        // For group-chat style (actor per-character messages), append without filtering
        // For other steps, replace thinking/speaking message from the SAME agent only
        setChatMessages(prev => {
          if (data.agent_display_name) {
            // Group-chat: first per-character message clears actor thinking/speaking, rest append
            const hasActorThinking = prev.some(m =>
              (m.status === "thinking" || m.status === "speaking") && m.agent === stepLabel
            );
            if (hasActorThinking) {
              return [...prev.filter(m =>
                !((m.status === "thinking" || m.status === "speaking") && m.agent === stepLabel)
              ), msg];
            }
            return [...prev, msg];
          }
          // For overall actor_agents step_done: skip if we already have per-character messages
          if (data.step === "actor_agents") {
            const hasCharMsgs = prev.some(m => m.agent === "Actor Agents" && m.agentDisplayName && m.status === "done");
            if (hasCharMsgs) {
              // Only clear leftover actor thinking/speaking, don't add duplicate content
              const cleaned = prev.filter(m =>
                !((m.status === "thinking" || m.status === "speaking") && m.agent === "Actor Agents")
              );
              return cleaned;
            }
          }
          // Default: replace only thinking/speaking from the same agent
          const filtered = prev.filter(m =>
            !((m.status === "thinking" || m.status === "speaking") && m.agent === stepLabel)
          );
          return [...filtered, msg];
        });
        if (data.step === "evaluator" && data.result) {
          setEvalResult({
            chapter_id: activeChId,
            passed: data.result.passed ?? true,
            score: data.result.score ?? 80,
            issues: (data.result.issues || []).map((i: any) => ({
              type: i.type || "unknown", severity: i.severity || "low",
              description: i.description || "", suggestion: i.suggestion,
            })),
            process: data.result.process || [],
            strengths: data.result.strengths || [],
            summary: data.result.summary || "",
            summary_text: data.result.summary_text || "",
            dimension_scores: data.result.dimension_scores || {},
            process_log: data.result.process_log || [],
          });
        }
        break;
      }
      case "need_confirm": {
        const agent = stepToLabel(data.step);
        setChatMessages(prev => [...prev, {
          agent, content: data.message || "是否继续？",
          status: "waiting_confirm", timestamp: Date.now(), isQuestion: true,
        }]);
        setWaitingForConfirm(true);
        break;
      }
      case "complete":
        setGenerating(false);
        setCurrentAgent(null);
        stopPolling();
        sessionStorage.removeItem(SESS_KEY);
        if (data.text) generatedTextRef.current = data.text;
        setChatMessages(prev => [...prev, {
          agent: "System",
          content: `Pipeline 全部完成！生成了 ${(data.text || "").length} 字。可点击「写入编辑器」。`,
          status: "done", timestamp: Date.now(),
        }]);
        if (data.evaluation) {
          setEvalResult({
            chapter_id: activeChId,
            passed: data.evaluation.passed ?? true,
            score: data.evaluation.score ?? 80,
            issues: (data.evaluation.issues || []).map((i: any) => ({
              type: i.type || "unknown", severity: i.severity || "low", description: i.description || "",
            })),
          });
        }
        break;
      case "agent_warning":
        setChatMessages(prev => [...prev, {
          agent: data.agent || stepToLabel(data.step),
          content: data.message,
          status: "waiting_confirm",
          isWarning: true,
          warningOptions: data.options || ["这是故意的，记录原因", "修改指令"],
          timestamp: Date.now(),
        }]);
        setWaitingForConfirm(true);
        break;
      case "follow_up":
        setChatMessages(prev => [...prev, {
          agent: stepToLabel(data.step),
          content: data.message,
          status: "waiting_confirm",
          isQuestion: true,
          followUpOptions: data.options || [],
          timestamp: Date.now(),
        }]);
        setWaitingForConfirm(true);
        break;
      case "thinking_start":
        setChatMessages(prev => [...prev, {
          agent: stepToLabel(data.step),
          content: "",
          status: "thinking",
          isCoT: true,
          timestamp: Date.now(),
        }]);
        break;
      case "thinking_token":
        setChatMessages(prev => {
          const last = prev[prev.length - 1];
          if (last?.isCoT && last.status === "thinking") {
            const updated = [...prev];
            updated[updated.length - 1] = { ...last, content: last.content + (data.content || "") };
            return updated;
          }
          return prev;
        });
        break;
      case "thinking_end":
        setChatMessages(prev => {
          const last = prev[prev.length - 1];
          if (last?.isCoT && last.status === "thinking") {
            const updated = [...prev];
            updated[updated.length - 1] = { ...last, status: "done" };
            return updated;
          }
          return prev;
        });
        break;
      case "progress_update":
        setPipelineSteps(prev => prev.map(s =>
          s.step === stepToLabel(data.step)
            ? { ...s, progress: data.progress, detail: data.detail || s.detail }
            : s
        ));
        break;
      case "navigate":
        if (data.target === "settings" && onNavigate) {
          onNavigate("settings");
        }
        break;
      case "error":
        setGenerating(false);
        setCurrentAgent(null);
        stopPolling();
        sessionStorage.removeItem(SESS_KEY);
        setChatMessages(prev => [...prev, {
          agent: "System", content: `错误: ${data.message}`, status: "done", timestamp: Date.now(),
        }]);
        break;
    }
  }, [activeChId, SESS_KEY, onNavigate]);

  const startPolling = useCallback((sessionId: string) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const resp = await apiGet<{ status: string; events: any[]; total: number }>(
          `/api/generation/events/${sessionId}?after=${eventCursorRef.current}`
        );
        for (const evt of resp.events) {
          handleEvent(evt);
          eventCursorRef.current += 1;
        }
        // Save cursor so tab-switching resumes from correct position
        if (resp.events.length > 0) {
          try { sessionStorage.setItem(SESS_KEY, JSON.stringify({ sessionId, cursor: eventCursorRef.current })); } catch {}
        }
        if (resp.status === "complete" || resp.status === "error") {
          stopPolling();
        }
      } catch {
        // Session gone — stop polling
        stopPolling();
        sessionStorage.removeItem(SESS_KEY);
      }
    }, 500);
  }, [handleEvent, SESS_KEY]);

  /** Global 1-based chapter number for the active chapter. */
  const chapterNum = useMemo(() => {
    const all = volumes.flatMap(v => v.chapters);
    const idx = all.findIndex(c => c.id === activeChId);
    return idx >= 0 ? idx + 1 : 1;
  }, [volumes, activeChId]);

  /** Assemble the structured chapter-generation payload. The backend
   *  (/quick-generate) assembles the RAG context — character cards /
   *  worldbook / platform / references / writing-knowledge — from these
   *  fields, so the editor only sends chapter-local inputs. */
  // Creation context manifest — RAG / default skills / learned skills /
  // writing knowledge for the creation tab's transparency panel.
  useEffect(() => {
    if (!activeChId) { setManifest(null); return; }
    const mode = aiTab === "cluster" ? "cluster" : aiTab === "eval" ? "eval" : "single";
    apiGet<ContextManifest>(`/api/generation/context-manifest?project_id=${encodeURIComponent(projectId || "default")}&chapter_id=${encodeURIComponent(activeChId)}&chapter_num=${chapterNum}&mode=${mode}`)
      .then(m => {
        setManifest(m);
        setSkillSelection(prev => {
          const next = { ...prev };
          for (const s of (m.learned_skills || [])) {
            if (!(s.name in next)) next[s.name] = true;
          }
          return next;
        });
      })
      .catch(() => setManifest(null));
  }, [projectId, activeChId, chapterNum, aiTab, manifestNonce]);

  const refreshManifest = useCallback(() => setManifestNonce(n => n + 1), []);

  const selectedSkillNames = useMemo(
    () => (manifest?.learned_skills || [])
      .filter(s => skillSelection[s.name] !== false).map(s => s.name),
    [manifest, skillSelection],
  );

  const toggleSkill = useCallback((name: string) => {
    setSkillSelection(prev => ({ ...prev, [name]: prev[name] === false }));
  }, []);

  const toggleRagItem = useCallback((key: string) => {
    setRagExcludes(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);

  const buildGenPayload = useCallback((): Record<string, any> => ({
    project_id: projectId || "default",
    chapter_id: activeChId,
    synopsis: activeCh?.synopsis || "",
    time_setting: activeCh?.time || "",
    location: activeCh?.location || "",
    characters: activeCh?.characters || [],
    character_aliases: activeCh?.character_aliases || {},
    existing_content: content || "",
    chapter_num: chapterNum,
    references: activeCh?.references || [],
    referenced_events: (activeCh?.referenced_events || []).filter((e: any) =>
      !ragExcludes.has(`referenced_materials::event:${e.id || e.name || e.description || ""}`)),
    referenced_inspirations: (activeCh?.referenced_inspirations || []).filter((x: any) =>
      !ragExcludes.has(`referenced_materials::insp:${x.id || x.title || x.content || ""}`)),
    skills: selectedSkillNames,
    rag_excludes: Array.from(ragExcludes),
  }), [activeCh, projectId, activeChId, content, chapterNum, selectedSkillNames, ragExcludes]);

  const fetchGenPrompt = useCallback(async (): Promise<string> => {
    const r = await apiPost<{ prompt: string }>("/api/generation/quick-generate", {
      ...buildGenPayload(), prompt_only: true,
    });
    return r.prompt || "";
  }, [buildGenPayload]);

  const runQuickGenerate = useCallback(async () => {
    if (!activeCh) return;
    setGenerating(true);
    setPipelineSteps(prev => prev.map((s, i) =>
      i === 0 ? { ...s, status: "running" as const } : s
    ));
    setChatMessages(prev => [...prev, {
      agent: "Actor Agents", content: "正在生成章节内容（快速模式）...",
      status: "thinking", timestamp: Date.now(),
    }]);

    try {
      const resp = await apiPost<{ text: string; model: string; tokens?: any; skills_used?: string[] }>("/api/generation/quick-generate", {
        ...buildGenPayload(),
      });

      generatedTextRef.current = resp.text;
      setPipelineSteps(prev => prev.map(s => ({ ...s, status: "done" as const, detail: "已完成" })));
      setChatMessages(prev => {
        const filtered = prev.filter(m => m.status !== "thinking");
        return [...filtered,
          { agent: "Actor Agents", content: `生成完成！共 ${resp.text.length} 字。使用模型: ${resp.model}\n${formatSkillsUsed(resp.skills_used)}`, status: "done" as const, timestamp: Date.now() },
          { agent: "System", content: "快速生成完成！点击「写入编辑器」将内容插入。", status: "done" as const, timestamp: Date.now() },
        ];
      });
    } catch (e: any) {
      setChatMessages(prev => {
        const filtered = prev.filter(m => m.status !== "thinking");
        return [...filtered, {
          agent: "System",
          content: `生成失败: ${e?.message || "未知错误"}。请检查模型连接设置。`,
          status: "done", timestamp: Date.now(),
        }];
      });
    }
    setGenerating(false);
    setCurrentAgent(null);
  }, [activeCh, buildGenPayload]);

  const runPlainAgent = useCallback(async () => {
    if (!activeCh) return;
    genModeRef.current = "single";
    setAiTab("single");
    setGenerating(true);
    setPipelineSteps([{ step: "Plain Agent", status: "running", detail: "单Agent直接生成中..." }]);
    setChatMessages([{
      agent: "System",
      content: `单Agent模式启动。基于大纲「${(activeCh.synopsis || "").slice(0, 50)}...」直接生成全文。`,
      status: "done", timestamp: Date.now(),
    }]);
    generatedTextRef.current = "";
    try {
      const resp = await apiPost<{ text: string; model: string; tokens?: any; skills_used?: string[] }>("/api/generation/quick-generate", {
        ...buildGenPayload(),
      });
      generatedTextRef.current = resp.text;
      setPipelineSteps([{ step: "Plain Agent", status: "done", detail: "已完成", progress: 100 }]);
      setChatMessages(prev => {
        const filtered = prev.filter(m => m.status !== "thinking");
        return [...filtered,
          { agent: "Editor-Writer", content: resp.text, status: "done" as const, timestamp: Date.now() },
          { agent: "System", content: `生成完成！共 ${resp.text.length} 字。模型: ${resp.model}${resp.tokens ? ` (${resp.tokens.input}+${resp.tokens.output} tokens)` : ""}\n${formatSkillsUsed(resp.skills_used)}`, status: "done" as const, timestamp: Date.now() },
        ];
      });
    } catch (e: any) {
      setPipelineSteps([{ step: "Plain Agent", status: "done", detail: "出错" }]);
      setChatMessages(prev => [...prev, {
        agent: "System", content: `生成出错：${e?.message || "请检查模型连接"}`,
        status: "done", timestamp: Date.now(),
      }]);
    }
    setGenerating(false);
    setCurrentAgent(null);
  }, [activeCh, projectId, activeChId, buildGenPayload]);

  /** Apply a web-LLM-generated chapter the user pasted back. */
  const applyPlainPaste = useCallback((text: string) => {
    const t = (text || "").trim();
    if (!t) return;
    generatedTextRef.current = t;
    setPipelineSteps([{ step: "Plain Agent", status: "done", detail: "已解析网页结果", progress: 100 }]);
    setChatMessages([
      { agent: "System", content: "已解析网页 LLM 返回的正文，可点「写入编辑器」。", status: "done", timestamp: Date.now() },
      { agent: "Editor-Writer", content: t, status: "done", timestamp: Date.now() },
    ]);
    setGenerating(false);
  }, []);

  const startGeneration = useCallback(async (manual = false) => {
    if (!activeCh) return;
    genModeRef.current = "cluster";
    setAiTab("cluster");
    setGenerating(true);
    setModelChanged(false);
    setPipelineSteps(PIPELINE_STEPS.map(s => ({ ...s, status: "pending" })));
    setChatMessages([]); setWaitingForConfirm(false);
    generatedTextRef.current = "";
    eventCursorRef.current = 0;

    // Snapshot current model settings for change detection
    try {
      const settings = await apiGet<any>("/api/data/settings");
      const pc = settings?.pipeline_config || {};
      modelSnapshotRef.current = { provider: pc.provider || "", model: pc.model || "" };
    } catch { modelSnapshotRef.current = null; }

    const synopsis = activeCh.synopsis || "";
    const chapterCharacters = activeCh.characters || [];
    const chapterReferences = activeCh.references || [];
    const chapterRefEvents = (activeCh.referenced_events || []).filter((e: any) =>
      !ragExcludes.has(`referenced_materials::event:${e.id || e.name || e.description || ""}`));
    const chapterRefInsps = (activeCh.referenced_inspirations || []).filter((x: any) =>
      !ragExcludes.has(`referenced_materials::insp:${x.id || x.title || x.content || ""}`));
    setChatMessages([{
      agent: "System",
      content: `Pipeline 启动！基于大纲「${synopsis.slice(0, 50)}${synopsis.length > 50 ? "..." : ""}」开始生成。${chapterCharacters.length > 0 ? `\n关联角色：${chapterCharacters.join("、")}` : ""}${chapterReferences.length > 0 ? `\n参考作品：${chapterReferences.length}部` : ""}${chapterRefEvents.length > 0 ? `\n关联事件：${chapterRefEvents.length}个` : ""}${chapterRefInsps.length > 0 ? `\n关联灵感：${chapterRefInsps.length}条` : ""}`,
      status: "done", timestamp: Date.now(),
    }]);

    try {
      const resp = await apiPost<{ session_id: string }>("/api/generation/start", {
        project_id: projectId,
        chapter_id: activeChId,
        synopsis,
        characters: chapterCharacters,
        references: chapterReferences,
        referenced_events: chapterRefEvents,
        referenced_inspirations: chapterRefInsps,
        time_setting: activeCh.time || "",
        location: activeCh.location || "",
        existing_content: content || "",
        character_aliases: activeCh.character_aliases || {},
        manual,
        skills: selectedSkillNames,
        rag_excludes: Array.from(ragExcludes),
      });
      sessionIdRef.current = resp.session_id;
      // Persist session so it survives page navigation
      sessionStorage.setItem(SESS_KEY, JSON.stringify({ sessionId: resp.session_id, cursor: 0 }));
      startPolling(resp.session_id);
    } catch {
      runQuickGenerate();
    }
  }, [activeCh, projectId, activeChId, startPolling, runQuickGenerate, SESS_KEY, selectedSkillNames, ragExcludes]);

  const submitManualResult = useCallback(async (text: string) => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    try {
      await apiPost(`/api/generation/manual-result/${sid}`, { result: text });
      setManualPrompt(null);
    } catch (e: any) {
      toast(e?.message || "提交失败", "error");
    }
  }, [toast]);

  const handleConfirmContinue = () => {
    setWaitingForConfirm(false);
    setChatMessages(prev => [...prev, { agent: "User", content: "确认满意，继续下一步。", status: "done", timestamp: Date.now() }]);
    if (sessionIdRef.current) {
      apiPost(`/api/generation/confirm/${sessionIdRef.current}`, { action: "continue" }).catch((e) => toast(e.message || "操作失败", "error"));
    }
  };

  const handleRollback = useCallback((stepIndex: number) => {
    const agentName = PIPELINE_STEPS[stepIndex].step;
    setPipelineSteps(prev => prev.map((s, i) => i >= stepIndex ? { ...s, status: "pending", detail: PIPELINE_STEPS[i].detail } : s));
    const firstMsgIdx = chatMessages.findIndex(m => m.agent === agentName);
    if (firstMsgIdx >= 0) {
      setChatMessages([...chatMessages.slice(0, firstMsgIdx), {
        agent: "System",
        content: `已回退到「${agentName}」阶段，正在重新生成...`,
        status: "done", timestamp: Date.now(),
      }]);
    }
    // Abort the current pipeline
    if (sessionIdRef.current) {
      apiPost(`/api/generation/confirm/${sessionIdRef.current}`, { action: "abort" }).catch((e) => toast(e.message || "操作失败", "error"));
    }
    stopPolling();
    sessionStorage.removeItem(SESS_KEY);
    setWaitingForConfirm(false);
    setGenerating(false);
    setCurrentAgent(agentName);
    // Auto-restart generation from this step
    setTimeout(() => startGeneration(), 500);
  }, [chatMessages, SESS_KEY, startGeneration]);

  const handleStopPipeline = useCallback(() => {
    if (sessionIdRef.current) {
      apiPost(`/api/generation/stop/${sessionIdRef.current}`, {}).catch((e) => toast(e.message || "操作失败", "error"));
    }
    stopPolling();
    sessionStorage.removeItem(SESS_KEY);
    setGenerating(false);
    setWaitingForConfirm(false);
    setCurrentAgent(null);
    setChatMessages(prev => [...prev, { agent: "System", content: "Pipeline 已被手动终止。", status: "done", timestamp: Date.now(), _stopped: true } as any]);
  }, [SESS_KEY]);

  const handleWriteToEditor = useCallback(() => {
    const text = generatedTextRef.current;
    if (text && text.length > 10) {
      // Show merge preview instead of directly inserting
      setMergePreview({ original: content, generated: text });
      setChatMessages(prev => [...prev, { agent: "System", content: `已准备 ${text.length} 字生成内容，请在编辑器中查看差异并确认写入方式。`, status: "done", timestamp: Date.now() }]);
    } else {
      setChatMessages(prev => [...prev, { agent: "System", content: "没有可写入的生成内容。请先运行 Pipeline。", status: "done", timestamp: Date.now() }]);
    }
  }, [projectId, activeChId, content]);

  // A4: Track last AI-written text for edit feedback analysis
  const lastAiTextRef = useRef<string>("");

  const handleDiffAccept = useCallback((finalText: string) => {
    lastAiTextRef.current = finalText;  // A4: remember AI version for later comparison
    // Auto-save version before overwriting
    if (content && content.length > 10) {
      const prevVersion: TextVersion = {
        version_id: vuid(), chapter_id: activeChId,
        version: versionHistory.filter(v => v.chapter_id === activeChId).length + 1,
        source: "user_edited", text: content, created_at: new Date().toISOString(),
      };
      setVersionHistory(prev => [...prev, prevVersion]);
    }
    // Char-count guard before persisting an "AI version" — the
    // recurring "saved 0 chars" path. Empty/short LLM output should
    // not silently produce a text_versions row.
    const finalChars = (finalText || "").trim().length;
    if (finalChars < MIN_SAVE_CHARS) {
      if (!confirmShortSave(finalChars)) {
        toast("已取消保存（内容过短）", "error");
        setMergePreview(null);
        return;
      }
    }
    setContent(finalText);
    // Save AI version
    const aiVersion: TextVersion = {
      version_id: vuid(), chapter_id: activeChId,
      version: versionHistory.filter(v => v.chapter_id === activeChId).length + 2,
      source: "ai_generated", text: finalText, created_at: new Date().toISOString(),
    };
    setVersionHistory(prev => [...prev, aiVersion]);
    setChatMessages(prev => [...prev, { agent: "System", content: `已合并写入 ${finalText.length} 字到编辑器！`, status: "done", timestamp: Date.now() }]);
    apiPost("/api/data/versions", {
      project_id: projectId || "default", version: aiVersion,
    }).catch((e) => toast(e.message || "操作失败", "error"));
    setMergePreview(null);
    setAiTab("eval");
  }, [projectId, activeChId, content, versionHistory, toast]);

  // A4: EditAnalyzer feedback — when user edits AI-generated text and saves
  const editFeedbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const aiText = lastAiTextRef.current;
    if (!aiText || !content || content === aiText || content.length < 50) return;
    // Only trigger if meaningful change (>5% diff)
    const diffRatio = Math.abs(content.length - aiText.length) / Math.max(aiText.length, 1);
    if (diffRatio < 0.02 && content.slice(0, 100) === aiText.slice(0, 100)) return;
    if (editFeedbackTimer.current) clearTimeout(editFeedbackTimer.current);
    editFeedbackTimer.current = setTimeout(() => {
      apiPost("/api/generation/edit-feedback", {
        project_id: projectId || "default",
        chapter_num: volumes.flatMap(v => v.chapters).find(c => c.id === activeChId)?.order || 0,
        original_text: aiText.slice(0, 5000),
        edited_text: content.slice(0, 5000),
      }).then(() => {
        lastAiTextRef.current = "";  // Only analyze once per AI write
      }).catch(() => {});
    }, 10000);  // Wait 10s of no typing before analyzing
    return () => { if (editFeedbackTimer.current) clearTimeout(editFeedbackTimer.current); };
  }, [content, projectId, activeChId, volumes]);

  const sendChatMessage = () => {
    if (!chatInput.trim()) return;
    const msg = chatInput.trim();
    setChatMessages(prev => [...prev, { agent: "User", content: msg, status: "done", timestamp: Date.now() }]);
    setChatInput("");
    if (waitingForConfirm && sessionIdRef.current) {
      setWaitingForConfirm(false);
      apiPost(`/api/generation/confirm/${sessionIdRef.current}`, { action: "continue", message: msg }).catch((e) => toast(e.message || "操作失败", "error"));
    }
  };

  const words = useMemo(() => wc(content), [content]);
  const totalW = useMemo(() => volumes.reduce((s, v) => s + v.chapters.reduce((s2, c) => s2 + wc(c.content || ""), 0), 0), [volumes]);
  const totalCh = useMemo(() => volumes.reduce((s, v) => s + v.chapters.length, 0), [volumes]);

  if (!loaded) return (
    <div style={{ display: "flex", height: "100vh", gap: 1 }}>
      <div style={{ width: 220, background: "var(--bg-surface)", padding: 16 }}>
        {[1,2,3,4,5].map(i => <div key={i} className="skeleton-line" style={{ height: 28, marginBottom: 8, borderRadius: 6, background: "var(--bg-surface-2)", animation: "pulse 1.5s ease-in-out infinite" }} />)}
      </div>
      <div style={{ flex: 1, background: "var(--bg-surface)", padding: 24 }}>
        <div className="skeleton-line" style={{ height: 32, width: "40%", marginBottom: 16, borderRadius: 6, background: "var(--bg-surface-2)", animation: "pulse 1.5s ease-in-out infinite" }} />
        {[1,2,3,4,5,6].map(i => <div key={i} className="skeleton-line" style={{ height: 16, marginBottom: 12, borderRadius: 4, background: "var(--bg-surface-2)", width: `${70 + Math.random() * 30}%`, animation: "pulse 1.5s ease-in-out infinite" }} />)}
      </div>
      <div style={{ width: 280, background: "var(--bg-surface)", padding: 16 }}>
        {[1,2,3].map(i => <div key={i} className="skeleton-line" style={{ height: 60, marginBottom: 12, borderRadius: 8, background: "var(--bg-surface-2)", animation: "pulse 1.5s ease-in-out infinite" }} />)}
      </div>
    </div>
  );

  return (
    <div className="page-full">
      {/* Floating right-drawer trigger (spec §5: PromptInspector +
          NotificationFeed + deep-links to the learning surfaces) */}
      <button
        onClick={() => setDrawerOpen(true)}
        title="打开右侧 Inspector (Prompt 透视 / 通知 / 导航)"
        style={{
          position: "fixed", right: 16, top: 60, zIndex: 800,
          width: 40, height: 40, borderRadius: "50%",
          background: "var(--bg-surface)",
          border: "1px solid var(--border)",
          boxShadow: "0 2px 6px rgba(0,0,0,0.12)",
          fontSize: 18, cursor: "pointer",
        }}
        aria-label="Open inspector drawer"
      >
        🔍
      </button>

      <EditorRightDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        projectId={projectId}
        chapterId={activeChId}
        chapterNum={(activeCh as any)?.order || (activeCh as any)?.chapter_num || 1}
        synopsis={activeCh?.synopsis || ""}
        characters={activeCh?.characters || []}
        onNavigate={(page) => {
          setDrawerOpen(false);
          onNavigate?.(page);
        }}
      />

      <div className="editor-layout">
        {/* LEFT PANEL */}
        {leftPanelOpen ? (
        <div className="panel" style={{ width: leftPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderRight: "1px solid var(--border)" }}>
          <div className="panel-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <h3>章节</h3>
            <button onClick={() => setLeftPanelOpen(false)} style={{ background: "none", border: "none", color: "var(--text-tertiary)", cursor: "pointer", fontSize: 14, padding: "2px 6px" }} title="收起章节列表">&#9664;</button>
          </div>
          <div style={{ padding: "8px 14px 4px" }}>
            <input className="input" type="text" value={searchTerm} onChange={e => setSearchTerm(e.target.value)} placeholder="搜索章节..." style={{ fontSize: 12, padding: "5px 10px", width: "100%", boxSizing: "border-box" }} />
          </div>
          <div style={{ padding: "4px 14px 6px", display: "flex", gap: 6 }}>
            <button className="btn-icon" onClick={addVolume} style={{ fontSize: 12, flex: 1, padding: "4px 0", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}>+卷</button>
            <button className="btn-icon" onClick={addChapterToFirstVolume} style={{ fontSize: 12, flex: 1, padding: "4px 0", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}>+章</button>
            <button className="btn-icon" onClick={handleBundleExport} style={{ fontSize: 12, flex: 1, padding: "4px 0", border: "1px solid var(--indigo, var(--border))", borderRadius: "var(--radius-sm)", color: "var(--indigo, var(--text-secondary))" }} title="导出角色+世界书+章节大纲，可自选保存位置">导出</button>
            <button className="btn-icon" onClick={() => (batchMode ? exitBatchMode() : setBatchMode(true))} style={{ fontSize: 12, flex: 1, padding: "4px 0", border: "1px solid var(--jade)", borderRadius: "var(--radius-sm)", color: batchMode ? "#fff" : "var(--jade)", background: batchMode ? "var(--jade)" : undefined }} title="批量选择章节后删除 / 导出">批量</button>
          </div>
          {batchMode && (
            <div style={{ padding: "6px 10px", background: "var(--jade-subtle)", borderBottom: "1px solid var(--border)", fontSize: 11 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <span>已选 {selectedChIds.size} 章</span>
                <div style={{ display: "flex", gap: 4 }}>
                  <button className="btn-icon" style={{ fontSize: 10, padding: "2px 6px" }} onClick={selectAllChapters}>全选</button>
                  <button className="btn-icon" style={{ fontSize: 10, padding: "2px 6px" }} onClick={() => setSelectedChIds(new Set())}>清空</button>
                  <button className="btn-icon" style={{ fontSize: 10, padding: "2px 6px" }} onClick={exitBatchMode}>退出</button>
                </div>
              </div>
              <div style={{ display: "flex", gap: 4 }}>
                <button className="btn-icon" style={{ fontSize: 11, flex: 1, padding: "3px 0", border: "1px solid var(--indigo, var(--border))", borderRadius: "var(--radius-sm)", color: "var(--indigo, var(--text-secondary))" }} disabled={selectedChIds.size === 0} onClick={exportSelectedChapters}>导出选中</button>
                <button className="btn-icon" style={{ fontSize: 11, flex: 1, padding: "3px 0", border: "1px solid var(--error)", borderRadius: "var(--radius-sm)", color: "var(--error)" }} disabled={selectedChIds.size === 0} onClick={deleteSelectedChapters}>删除选中</button>
              </div>
            </div>
          )}
          <div className="panel-body" style={{ padding: "8px 6px" }}>
            {filteredVolumes.map(v => (
              <div key={v.id}>
                <div className="chapter-tree-item" style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                  <span style={{ cursor: "pointer", fontSize: 10, width: 14, flexShrink: 0 }} onClick={() => toggleVolume(v.id)}>{v.collapsed ? "\u25B6" : "\u25BC"}</span>
                  {renamingId === v.id ? <input className="input" value={renameVal} onChange={e => setRenameVal(e.target.value)} onBlur={commitRename} onKeyDown={e => e.key === "Enter" && commitRename()} autoFocus style={{ padding: "2px 6px", fontSize: 12, flex: 1 }} />
                    : <span className="truncate" style={{ flex: 1, cursor: "pointer" }} onDoubleClick={() => startRename(v.id, v.title)}>{v.title}</span>}
                  <button className="btn-icon" style={{ width: 22, height: 22, fontSize: 13 }} onClick={() => addChapter(v.id)}>+</button>
                </div>
                {!v.collapsed && v.chapters.map(c => (
                  <div key={c.id} className={`chapter-tree-item indent ${(batchMode ? selectedChIds.has(c.id) : c.id === activeChId) ? "active" : ""}`}
                    onClick={() => (batchMode ? toggleChSelected(c.id) : setActiveChId(c.id))}
                    style={searchLower && (c.content || "").toLowerCase().includes(searchLower) ? { background: "var(--accent-subtle, rgba(255,200,0,0.15))" } : undefined}>
                    {batchMode && <input type="checkbox" checked={selectedChIds.has(c.id)} onChange={() => toggleChSelected(c.id)} onClick={e => e.stopPropagation()} style={{ flexShrink: 0, margin: 0, cursor: "pointer" }} />}
                    {renamingId === c.id ? <input className="input" value={renameVal} onChange={e => setRenameVal(e.target.value)} onBlur={commitRename} onKeyDown={e => e.key === "Enter" && commitRename()} autoFocus style={{ padding: "2px 6px", fontSize: 12, flex: 1 }} onClick={e => e.stopPropagation()} />
                      : <><span className="truncate" style={{ flex: 1 }} onDoubleClick={() => startRename(c.id, c.title)}>{c.title}</span><span className="font-mono text-xs text-muted">{wc(c.content || "")}字</span></>}
                    {!batchMode && totalCh > 1 && <button className="btn-icon" style={{ width: 18, height: 18, fontSize: 11 }} onClick={e => { e.stopPropagation(); deleteChapter(c.id); }}>&times;</button>}
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div style={{ padding: "8px 10px", borderTop: "1px solid var(--border)", flexShrink: 0 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
              <div className="label" style={{ fontSize: 10, marginBottom: 0 }}>版本记录</div>
              <button className="btn-ghost" style={{ fontSize: 10, padding: "1px 6px" }}
                onClick={() => {
                  // Save current state as a version
                  if (!activeChId || !content) return;
                  // Char-count guard — prevents the 0-char text_versions
                  // disaster (see MIN_SAVE_CHARS docstring above).
                  const chars = content.trim().length;
                  if (chars < MIN_SAVE_CHARS && !confirmShortSave(chars)) return;
                  const newVersion: TextVersion = {
                    version_id: vuid(), chapter_id: activeChId,
                    version: versionHistory.filter(v => v.chapter_id === activeChId).length + 1,
                    source: "user_edited", text: content,
                    synopsis: activeCh?.synopsis || "",
                    created_at: new Date().toISOString(),
                  };
                  setVersionHistory(prev => [...prev, newVersion]);
                  // Persist
                  apiPost("/api/data/versions", { project_id: projectId || "default", version: newVersion }).catch((e) => toast(e.message || "操作失败", "error"));
                }}>
                + 保存版本
              </button>
            </div>
            <div style={{ maxHeight: 120, overflowY: "auto" }}>
              <div className="text-xs text-muted" style={{ padding: "4px 6px", borderRadius: 4, fontWeight: 600 }}>
                当前版本 · {new Date().toLocaleDateString("zh-CN")}
              </div>
              {versionHistory.filter(v => v.chapter_id === activeChId).reverse().map(v => (
                <div key={v.version_id} className="text-xs text-muted" style={{ padding: "4px 6px", borderRadius: 4, display: "flex", justifyContent: "space-between", alignItems: "center", gap: 4 }}
                  onMouseEnter={e => e.currentTarget.style.background = "var(--bg-surface-hover)"}
                  onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                >
                  <span style={{ cursor: "pointer", flex: 1 }} onClick={async () => {
                    if (await confirm(`回滚到版本 ${v.version}？当前内容将被替换。`)) {
                      setContent(v.text);
                      if (v.synopsis) {
                        setVolumes(prev => prev.map(vol => ({ ...vol, chapters: vol.chapters.map(c => c.id === activeChId ? { ...c, synopsis: v.synopsis || c.synopsis } : c) })));
                      }
                    }
                  }}>
                    v{v.version} · {v.source === "ai_generated" ? "AI" : v.source === "auto_saved" ? "自动" : "手动"}
                  </span>
                  <span style={{ fontSize: 9, flexShrink: 0 }}>{new Date(v.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "numeric", minute: "numeric" })}</span>
                  <button
                    onClick={async (e) => {
                      e.stopPropagation();
                      if (await confirm({ message: `删除版本 v${v.version}？此操作不可撤销。`, destructive: true })) {
                        setVersionHistory(prev => prev.filter(x => x.version_id !== v.version_id));
                        apiDelete(`/api/data/versions/${v.version_id}?project_id=${projectId || "default"}`).catch((e) => toast(e.message || "操作失败", "error"));
                      }
                    }}
                    style={{ fontSize: 9, padding: "0 4px", background: "none", border: "none", color: "var(--text-disabled)", cursor: "pointer", flexShrink: 0, lineHeight: 1 }}
                    onMouseEnter={e => e.currentTarget.style.color = "var(--error)"}
                    onMouseLeave={e => e.currentTarget.style.color = "var(--text-disabled)"}
                    title="删除此版本"
                  >
                    ×
                  </button>
                </div>
              ))}
              {versionHistory.filter(v => v.chapter_id === activeChId).length === 0 && (
                <div className="text-xs text-muted" style={{ padding: "4px 6px", opacity: 0.6 }}>
                  点击「保存版本」创建版本快照
                </div>
              )}
            </div>
          </div>
          <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)", fontSize: 11, color: "var(--text-tertiary)", flexShrink: 0 }}>{totalCh} 章 &middot; {totalW.toLocaleString()} 字</div>
        </div>
        ) : (
        <div style={{ width: 36, flexShrink: 0, background: "var(--bg-surface)", borderRight: "1px solid var(--border)", display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 12 }}>
          <button onClick={() => setLeftPanelOpen(true)} style={{ background: "none", border: "none", color: "var(--text-tertiary)", cursor: "pointer", fontSize: 14, padding: "4px", writingMode: "vertical-rl", letterSpacing: 2 }} title="展开章节列表">
            章节 &#9654;
          </button>
        </div>
        )}
        {leftPanelOpen && <div className="panel-resize-h" {...leftPanel.handleProps} />}

        {/* CENTER PANEL */}
        <div className="panel flex-1" style={{ background: "var(--bg-app)" }}>
          <div style={{ padding: "14px 28px 10px", borderBottom: "1px solid var(--border)", background: "var(--bg-surface)", flexShrink: 0 }}>
            {editingTitle ? <input className="input" value={titleVal} onChange={e => setTitleVal(e.target.value)} onBlur={() => setEditingTitle(false)} onKeyDown={e => { if (e.key === "Enter") setEditingTitle(false); }} autoFocus style={{ fontFamily: "var(--font-serif)", fontSize: 18, fontWeight: 700, background: "transparent", borderBottom: "2px solid var(--accent)", borderRadius: 0, padding: "2px 0" }} />
              : <h3 className="font-serif" style={{ fontSize: 18, fontWeight: 700, cursor: "text", color: "var(--text-primary)" }} onClick={() => { setEditingTitle(true); setTitleVal(activeCh?.title || ""); }} title="点击编辑章节名">{activeCh?.title || "选择章节"}</h3>}
            <div className="text-xs text-muted mt-4">{activeVol?.title} &middot; {words.toLocaleString()} 字 &middot; 写作 {elapsed} 分钟</div>
          </div>
          <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
            {mergePreview ? (
              <DiffView
                oldText={mergePreview.original}
                newText={mergePreview.generated}
                onAccept={handleDiffAccept}
                onCancel={() => setMergePreview(null)}
              />
            ) : (
              <>
                <textarea ref={textRef} className="text-editor-area" value={content} onChange={e => { setContent(e.target.value); setSelection(null); }} onMouseUp={handleMouseUp} onKeyUp={handleMouseUp}
                  placeholder={"在这里开始写作...\n\n提示：\n  双击左侧章节名可重命名\n  选中文本可触发「AI重写」\n  内容会自动保存"} spellCheck={false} style={{ maxWidth: 800, margin: "0 auto", display: "block" }} />
                {selection && <div style={{ position: "absolute", top: 16, right: 16, zIndex: 50 }}><button className="btn-primary" style={{ fontSize: 11, padding: "5px 14px", borderRadius: 16 }} onClick={() => setAiTab("rewrite")}>AI 重写 ({selection.text.length}字)</button></div>}
              </>
            )}
          </div>
          <div className="flex items-center justify-between" style={{ padding: "6px 28px", borderTop: "1px solid var(--border)", background: "var(--bg-surface)", flexShrink: 0 }}>
            <div className="flex items-center gap-12 text-xs text-muted"><span>{words.toLocaleString()} 字</span><span>写作 {elapsed} 分钟</span></div>
            <div className="flex items-center gap-8 text-xs"><span style={{ color: saveStatus === "saved" ? "var(--jade)" : saveStatus === "saving" ? "var(--gold)" : "var(--text-tertiary)" }}>{saveStatus === "saved" ? "已保存" : saveStatus === "saving" ? "保存中..." : "未保存"}</span></div>
          </div>
        </div>
        {rightPanelOpen && <div className="panel-resize-h" {...rightPanel.handleProps} />}

        {/* RIGHT PANEL */}
        {rightPanelOpen ? (
        <div className="panel" style={{ width: rightPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderLeft: "1px solid var(--border)" }}>
          <div className="panel-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <h3>AI 助手</h3>
            <button onClick={() => setRightPanelOpen(false)} style={{ background: "none", border: "none", color: "var(--text-tertiary)", cursor: "pointer", fontSize: 14, padding: "2px 6px" }} title="收起 AI 面板">&#9654;</button>
          </div>
          <div className="tab-bar-underline" style={{ flexShrink: 0 }}>
            {([["outline", "大纲"], ["single", "单智能体创作"], ["cluster", "集群式智能体创作"], ["rewrite", "重写"], ["eval", "评估"]] as const).map(([key, label]) => (
              <button key={key} className={`tab-item ${aiTab === key ? "active" : ""}`} onClick={() => setAiTab(key)}>{label}</button>
            ))}
          </div>
          <div className="panel-body" style={{ padding: "14px 16px" }}>
            {aiTab === "outline" && <OutlineTab synopsis={activeCh?.synopsis || ""} onChange={updateSynopsis} onSave={handleSaveOutline}
              onStartGeneration={() => { setAiTab("single"); setTimeout(() => { if (!generating) runPlainAgent(); }, 300); }} projectId={projectId}
              chapter={activeCh}
              allChapters={volumes.flatMap(v => (v.chapters || []).map(c => ({ id: c.id, title: c.title })))}
              onUpdateChapter={(field, value) => {
                setVolumes(prev => prev.map(v => ({ ...v, chapters: v.chapters.map(c => c.id === activeChId ? { ...c, [field]: value } : c) })));
              }} />}
            {(aiTab === "single" || aiTab === "cluster") && <InspireTab mode={aiTab} steps={pipelineSteps} generating={generating} onStart={startGeneration} onStartPlain={runPlainAgent} chatMessages={chatMessages} chatInput={chatInput}
              onChatInputChange={setChatInput} onSendMessage={sendChatMessage} waitingForConfirm={waitingForConfirm} onConfirmContinue={handleConfirmContinue} onRollback={handleRollback} onWriteToEditor={handleWriteToEditor} onStopPipeline={handleStopPipeline}
              modelChanged={modelChanged} onDismissModelChange={() => setModelChanged(false)} onRestartWithNewModel={() => { setModelChanged(false); handleStopPipeline(); setTimeout(() => startGeneration(), 500); }}
              onFetchPrompt={fetchGenPrompt}
              onApplyPaste={applyPlainPaste}
              manualPrompt={manualPrompt} onSubmitManual={submitManualResult}
              manifest={manifest} skillSelection={skillSelection} onToggleSkill={toggleSkill}
              ragExcludes={ragExcludes} onToggleRagItem={toggleRagItem} onRefreshManifest={refreshManifest}
              onDeleteMessage={(idx) => setChatMessages(prev => prev.filter((_, i) => i !== idx))} />}
            {aiTab === "rewrite" && <RewriteTab selection={selection} prompt={rewritePrompt} onPromptChange={setRewritePrompt} model={rewriteModel} onModelChange={setRewriteModel} />}
            {aiTab === "eval" && <EvalTab result={evalResult} chapterContent={content} projectId={projectId}
              chapterId={activeChId} manifest={manifest} skillSelection={skillSelection} ragExcludes={ragExcludes}
              onToggleSkill={toggleSkill} onToggleRagItem={toggleRagItem} onRefreshManifest={refreshManifest} />}
          </div>
        </div>
        ) : (
        <div style={{ width: 36, flexShrink: 0, background: "var(--bg-surface)", borderLeft: "1px solid var(--border)", display: "flex", flexDirection: "column", alignItems: "center", paddingTop: 12 }}>
          <button onClick={() => setRightPanelOpen(true)} style={{ background: "none", border: "none", color: "var(--text-tertiary)", cursor: "pointer", fontSize: 14, padding: "4px", writingMode: "vertical-rl", letterSpacing: 2 }} title="展开 AI 面板">
            &#9664; AI
          </button>
        </div>
        )}
      </div>
    </div>
  );
}

/** Flatten a reference work's plot_outline_json into a flat event list. */
function workEvents(plotJson: any): { name: string; description: string; chapter: string }[] {
  let p: any = plotJson;
  if (typeof plotJson === "string") {
    try { p = JSON.parse(plotJson); } catch { return []; }
  }
  const out: { name: string; description: string; chapter: string }[] = [];
  for (const ep of (p?.epochs || [])) {
    for (const per of (ep?.periods || [])) {
      const perMark = String(per?.time_marker || per?.title || "");
      for (const ev of (per?.events || [])) {
        if (ev?.name) out.push({
          name: String(ev.name),
          description: String(ev.description || ""),
          chapter: String(ev.time_marker || perMark || ""),
        });
      }
    }
  }
  return out;
}

/** A selectable chronicle-event row: 章节 tag + 事件名, with click-to-expand
 *  details — keeps each row compact in the narrow AI 助手 column. */
function EventRow({ ev, on, onToggle }: {
  ev: { name: string; description: string; chapter: string };
  on: boolean; onToggle: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 5, padding: "4px 8px", fontSize: 11 }}>
        <span onClick={onToggle} style={{ cursor: "pointer", width: 11, flexShrink: 0, color: "var(--gold)", fontWeight: 700 }}>{on ? "✓" : ""}</span>
        {ev.chapter && (
          <span style={{
            fontSize: 9, padding: "0 5px", flexShrink: 0, borderRadius: 3,
            color: "var(--gold)", border: "1px solid var(--gold)", fontFamily: "var(--font-mono)",
          }}>{ev.chapter}</span>
        )}
        <span onClick={onToggle} style={{
          flex: 1, minWidth: 0, cursor: "pointer",
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          fontWeight: on ? 600 : 400, color: on ? "var(--gold)" : "var(--text-secondary)",
        }}>{ev.name}</span>
        {ev.description && (
          <span onClick={() => setExpanded(e => !e)} style={{ cursor: "pointer", fontSize: 9, color: "var(--text-tertiary)", flexShrink: 0 }}>
            {expanded ? "收起 ▲" : "详情 ▼"}
          </span>
        )}
      </div>
      {expanded && ev.description && (
        <div style={{ padding: "0 8px 5px 25px", fontSize: 10, color: "var(--text-tertiary)", lineHeight: 1.5 }}>
          {ev.description}
        </div>
      )}
    </div>
  );
}

/** Toggle-chip style shared by the chapter linker. */
function chipStyle(on: boolean, color: string, bg: string): React.CSSProperties {
  return {
    fontSize: 11, padding: "3px 10px", borderRadius: 14, border: "1px solid",
    borderColor: on ? color : "var(--border)",
    background: on ? bg : "transparent",
    color: on ? color : "var(--text-secondary)",
    cursor: "pointer",
  };
}

/** A selectable row in a searchable pick-list (works / events / inspirations). */
function PickRow({ label, sub, on, color, onClick }: {
  label: string; sub?: string; on: boolean; color: string; onClick: () => void;
}) {
  return (
    <div onClick={onClick} title={label} style={{
      display: "flex", alignItems: "center", gap: 6,
      padding: "4px 8px", cursor: "pointer", fontSize: 11,
      borderBottom: "1px solid var(--border)",
      background: on ? "var(--bg-surface)" : "transparent",
    }}>
      <span style={{ width: 11, flexShrink: 0, color, fontWeight: 700 }}>{on ? "✓" : ""}</span>
      <span style={{
        flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        fontWeight: on ? 600 : 400, color: on ? color : "var(--text-secondary)",
      }}>{label}</span>
      {sub && <span className="text-xs text-muted" style={{ flexShrink: 0 }}>{sub}</span>}
    </div>
  );
}

function OutlineTab({ synopsis, onChange, onSave, onStartGeneration, projectId, chapter, onUpdateChapter, allChapters }: {
  synopsis: string; onChange: (v: string) => void; onSave: () => void; onStartGeneration: () => void; projectId: string;
  chapter?: ChapterOutline | null; onUpdateChapter?: (field: string, value: any) => void;
  allChapters?: { id: string; title: string }[];
}) {
  const { toast } = useToast();
  const [time, setTime] = useState(chapter?.time || "");
  const [location, setLocation] = useState(chapter?.location || "");

  // Sync when chapter changes (user switches active chapter)
  useEffect(() => {
    setTime(chapter?.time || "");
    setLocation(chapter?.location || "");
  }, [chapter?.id]);

  const [characters, setCharacters] = useState<{ id: string; name: string; selected: boolean }[]>([]);
  const [references, setReferences] = useState<{ id: string; title: string; selected: boolean; events: { name: string; description: string; chapter: string }[] }[]>([]);
  const [inspirations, setInspirations] = useState<{ id: string; category: string; title: string; content: string }[]>([]);
  const [showCharLink, setShowCharLink] = useState(false);
  const [showRefLink, setShowRefLink] = useState(false);
  const [showInspLink, setShowInspLink] = useState(false);
  const [showForeshadow, setShowForeshadow] = useState(false);
  const [foreshadow, setForeshadow] = useState<{ id: string; title: string; content: string; chapter_ids: string[] }[]>([]);
  const fsLoadedRef = useRef(false);

  useEffect(() => {
    fsLoadedRef.current = false;
    apiGet<{ items: any[] }>(`/api/data/foreshadowing/${projectId}`)
      .then(r => setForeshadow(Array.isArray(r.items) ? r.items : []))
      .catch(() => setForeshadow([]))
      .finally(() => { fsLoadedRef.current = true; });
  }, [projectId]);

  useEffect(() => {
    if (!fsLoadedRef.current) return;
    const t = setTimeout(() => {
      apiPut(`/api/data/foreshadowing/${projectId}`, { items: foreshadow }).catch(() => {});
    }, 700);
    return () => clearTimeout(t);
  }, [foreshadow, projectId]);

  const addForeshadow = () => setForeshadow(prev => [
    { id: `fs_${Date.now()}`, title: "新伏笔", content: "",
      chapter_ids: chapter?.id ? [chapter.id] : [] },
    ...prev,
  ]);
  const updateForeshadow = (id: string, patch: Partial<{ title: string; content: string }>) =>
    setForeshadow(prev => prev.map(f => f.id === id ? { ...f, ...patch } : f));
  const deleteForeshadow = (id: string) =>
    setForeshadow(prev => prev.filter(f => f.id !== id));
  const toggleFsChapter = (id: string, chId: string) =>
    setForeshadow(prev => prev.map(f => {
      if (f.id !== id) return f;
      const has = f.chapter_ids.includes(chId);
      return { ...f, chapter_ids: has ? f.chapter_ids.filter(x => x !== chId) : [...f.chapter_ids, chId] };
    }));
  const [refSearch, setRefSearch] = useState("");
  const [eventSearch, setEventSearch] = useState("");
  const [inspSearch, setInspSearch] = useState("");

  // Outline chat state (overlay dialog)
  const [showOutlineChat, setShowOutlineChat] = useState(false);
  const [outlineChatMsgs, setOutlineChatMsgs] = useState<{ role: "user" | "assistant"; content: string; ts: number }[]>([]);
  const [outlineChatInput, setOutlineChatInput] = useState("");
  const [outlineChatLoading, setOutlineChatLoading] = useState(false);
  const [pendingOutline, setPendingOutline] = useState<string | null>(null);
  // Web-LLM workflow: copy the prompt out / paste the reply back.
  const outlineChatEndRef = useRef<HTMLDivElement>(null);

  // Load outline chat from backend
  useEffect(() => {
    const pid = projectId || "default";
    const scope = `outline_chat_${chapter?.id || ""}`;
    apiGet<{ messages: any[] }>(`/api/data/chat_history?project_id=${pid}&scope=${scope}`)
      .then(r => { if (r.messages?.length > 0) setOutlineChatMsgs(r.messages); })
      .catch(() => {});
  }, [projectId, chapter?.id]);

  useEffect(() => { outlineChatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [outlineChatMsgs]);

  const sendOutlineChat = async (inputOverride?: string) => {
    const text = (inputOverride || outlineChatInput).trim();
    if (!text || outlineChatLoading) return;
    const userMsg = { role: "user" as const, content: text, ts: Date.now() };
    const updated = [...outlineChatMsgs, userMsg];
    setOutlineChatMsgs(updated);
    setOutlineChatInput("");
    setOutlineChatLoading(true);
    try {
      const res = await apiPost<{ reply: string }>("/api/generation/outline-chat", {
        project_id: projectId, messages: updated.map(m => ({ role: m.role, content: m.content })),
        context: synopsis || "",
      });
      const aiMsg = { role: "assistant" as const, content: res.reply, ts: Date.now() };
      const finalMsgs = [...updated, aiMsg];
      setOutlineChatMsgs(finalMsgs);
      apiPut("/api/data/chat_history", { project_id: projectId, scope: `outline_chat_${chapter?.id || ""}`, messages: finalMsgs.slice(-200) }).catch(() => {});
    } catch (e: any) {
      setOutlineChatMsgs(prev => [...prev, { role: "assistant", content: `[Error] ${e?.message || "请求失败"}`, ts: Date.now() }]);
    }
    setOutlineChatLoading(false);
  };

  const applyOutlineFromChat = (content: string) => {
    setPendingOutline(content);
  };

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
    }
  };

  /** Build the full outline-chat prompt for running in a web LLM. */
  const fetchOutlinePrompt = async (): Promise<string> => {
    const pending = outlineChatInput.trim();
    const msgs = pending
      ? [...outlineChatMsgs, { role: "user" as const, content: pending, ts: Date.now() }]
      : outlineChatMsgs;
    const res = await apiPost<{ prompt: string }>("/api/generation/outline-chat", {
      project_id: projectId,
      messages: msgs.map(m => ({ role: m.role, content: m.content })),
      context: synopsis || "",
      prompt_only: true,
    });
    return res.prompt || "";
  };

  /** Apply a web-LLM reply pasted by the user as the assistant turn. */
  const applyPastedReply = (raw: string) => {
    const text = (raw || "").trim();
    if (!text) return;
    let msgs = outlineChatMsgs;
    const pending = outlineChatInput.trim();
    if (pending) {
      msgs = [...msgs, { role: "user" as const, content: pending, ts: Date.now() }];
      setOutlineChatInput("");
    }
    const finalMsgs = [...msgs, { role: "assistant" as const, content: text, ts: Date.now() }];
    setOutlineChatMsgs(finalMsgs);
    apiPut("/api/data/chat_history", { project_id: projectId, scope: `outline_chat_${chapter?.id || ""}`, messages: finalMsgs.slice(-200) }).catch(() => {});
  };

  const confirmOutline = () => {
    if (pendingOutline) {
      onChange(pendingOutline);
      setPendingOutline(null);
    }
  };

  useEffect(() => {
    const pid = projectId || "default";
    const chapterChars = chapter?.characters || [];
    apiGet<{ items: any[] }>(`/api/data/characters?project_id=${pid}`)
      .then(r => setCharacters((r.items || []).map((c: any) => ({ id: c.id, name: c.name, selected: chapterChars.includes(c.name) }))))
      .catch(() => {});
    const chapterRefs = chapter?.references || [];
    apiGet<{ items: any[] }>("/api/references/works")
      .then(r => setReferences((r.items || []).map((w: any) => ({
        id: w.ref_id || w.id,
        title: w.title || w.name || "未命名",
        selected: chapterRefs.includes(w.ref_id || w.id),
        events: workEvents(w.plot_outline_json),
      }))))
      .catch(() => setReferences([]));
    apiGet<{ items: any[] }>("/api/references/inspirations")
      .then(r => setInspirations((r.items || []).map((it: any) => ({
        id: it.id, category: it.category || "other",
        title: it.title || "", content: it.content || "",
      }))))
      .catch(() => setInspirations([]));
  }, [projectId, chapter?.id]);

  const toggleChar = (id: string) => {
    setCharacters(prev => {
      const next = prev.map(c => c.id === id ? { ...c, selected: !c.selected } : c);
      const selectedNames = next.filter(c => c.selected).map(c => c.name);
      onUpdateChapter?.("characters", selectedNames);
      return next;
    });
  };
  const refEvents = chapter?.referenced_events || [];
  const refInsps = chapter?.referenced_inspirations || [];
  const isEventLinked = (refId: string, name: string) =>
    refEvents.some(e => e.ref_id === refId && e.name === name);

  const toggleRef = (id: string) => {
    setReferences(prev => {
      const next = prev.map(r => r.id === id ? { ...r, selected: !r.selected } : r);
      const selectedIds = next.filter(r => r.selected).map(r => r.id);
      onUpdateChapter?.("references", selectedIds);
      // Unlinking a work drops the chronicle events linked from it.
      const stillSel = new Set(selectedIds);
      const pruned = refEvents.filter(e => stillSel.has(e.ref_id));
      if (pruned.length !== refEvents.length) onUpdateChapter?.("referenced_events", pruned);
      return next;
    });
  };
  const toggleEvent = (refId: string, workTitle: string, ev: { name: string; description: string; chapter: string }) => {
    const next = isEventLinked(refId, ev.name)
      ? refEvents.filter(e => !(e.ref_id === refId && e.name === ev.name))
      : [...refEvents, { ref_id: refId, work_title: workTitle, name: ev.name, description: ev.description, chapter: ev.chapter }];
    onUpdateChapter?.("referenced_events", next);
  };
  const toggleInspiration = (it: { id: string; category: string; title: string; content: string }) => {
    const next = refInsps.some(i => i.id === it.id)
      ? refInsps.filter(i => i.id !== it.id)
      : [...refInsps, it];
    onUpdateChapter?.("referenced_inspirations", next);
  };
  const selectedChars = characters.filter(c => c.selected);
  const selectedRefs = references.filter(r => r.selected);
  const refQ = refSearch.trim().toLowerCase();
  const filteredRefs = references.filter(r => !refQ || r.title.toLowerCase().includes(refQ));
  const inspQ = inspSearch.trim().toLowerCase();
  const filteredInsps = inspirations.filter(
    it => !inspQ || `${it.title} ${it.content}`.toLowerCase().includes(inspQ));

  return (
    <div>
      <div className="label mb-8">章节剧情大纲</div>
      <textarea className="input" value={synopsis} onChange={e => onChange(e.target.value)} rows={6}
        placeholder={"在这里写这一章的剧情要点...\n\n例如：\n  主角初入宗门\n  与师兄发生冲突"} style={{ lineHeight: 1.8, fontFamily: "var(--font-sans)" }} />

      {/* AI Outline Chat Overlay Button */}
      <div style={{ marginTop: 10, display: "flex", justifyContent: "flex-end" }}>
        <button className="btn" onClick={() => setShowOutlineChat(!showOutlineChat)}
          style={{ fontSize: 11, padding: "5px 14px", borderRadius: 16, borderColor: showOutlineChat ? "var(--accent)" : "var(--border)", color: showOutlineChat ? "var(--accent)" : "var(--text-secondary)" }}>
          {showOutlineChat ? "收起 AI 助手" : "AI 大纲助手"}
          {outlineChatMsgs.length > 0 && <span style={{ marginLeft: 4, fontSize: 9, background: "var(--accent)", color: "#fff", borderRadius: 8, padding: "1px 5px" }}>{outlineChatMsgs.length}</span>}
        </button>
      </div>

      {/* AI Outline Chat Overlay Dialog */}
      {showOutlineChat && (
      <div style={{ marginTop: 6, border: "1px solid var(--accent)", borderRadius: "var(--radius-sm)", overflow: "hidden", boxShadow: "0 4px 20px rgba(0,0,0,0.15)" }}>
        <div style={{ padding: "6px 10px", background: "var(--bg-surface-2)", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: "var(--accent)" }}>Story Architect · AI 大纲助手</span>
          <div style={{ display: "flex", gap: 4 }}>
            <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px" }} onClick={() => { setOutlineChatMsgs([]); apiPut("/api/data/chat_history", { project_id: projectId, scope: `outline_chat_${chapter?.id || ""}`, messages: [] }).catch((e) => toast(e.message || "操作失败", "error")); }}>清空</button>
          </div>
        </div>
        <div style={{ maxHeight: 240, overflowY: "auto", padding: "8px 10px" }}>
          {outlineChatMsgs.length === 0 && (
            <div style={{ padding: "16px 8px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 11 }}>
              与 Story Architect 讨论大纲。满意后点击「写入大纲」。
            </div>
          )}
          {outlineChatMsgs.map((msg, i) => (
            <div key={i} style={{ display: "flex", flexDirection: msg.role === "user" ? "row-reverse" : "row", marginBottom: 8, gap: 6 }}>
              <div style={{ width: 24, height: 24, borderRadius: "50%", background: msg.role === "user" ? "var(--purple-subtle)" : "var(--accent-subtle)", border: `1.5px solid ${msg.role === "user" ? "var(--purple)" : "var(--accent)"}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, flexShrink: 0 }}>
                {msg.role === "user" ? "U" : "AI"}
              </div>
              <div style={{ maxWidth: "82%" }}>
                <div style={{ fontSize: 10, fontWeight: 600, color: msg.role === "user" ? "var(--purple)" : "var(--accent)", marginBottom: 2, textAlign: msg.role === "user" ? "right" : "left" }}>
                  {msg.role === "user" ? "你" : "Story Architect"}
                </div>
                <div style={{
                  padding: "6px 10px", borderRadius: 8,
                  background: msg.role === "user" ? "var(--purple-subtle)" : "var(--bg-surface-2)",
                  borderLeft: msg.role === "assistant" ? "2px solid var(--accent)" : "none",
                  borderRight: msg.role === "user" ? "2px solid var(--purple)" : "none",
                  fontSize: 12, lineHeight: 1.5, color: "var(--text-primary)", whiteSpace: "pre-wrap", wordBreak: "break-word",
                  userSelect: "text",
                }}>
                  {msg.content}
                </div>
              </div>
            </div>
          ))}
          {outlineChatLoading && (
            <div style={{ padding: "6px 10px", fontSize: 11, color: "var(--text-tertiary)" }}>AI 思考中...</div>
          )}
          {/* Pending outline confirmation */}
          {pendingOutline && (
            <div style={{ margin: "8px 0", padding: "8px 10px", background: "var(--accent-subtle)", border: "1px solid var(--accent)", borderRadius: 6 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--accent)", marginBottom: 4 }}>确认写入以下大纲？</div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)", maxHeight: 80, overflowY: "auto", whiteSpace: "pre-wrap", marginBottom: 6 }}>{pendingOutline.slice(0, 300)}{pendingOutline.length > 300 ? "..." : ""}</div>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn-primary" style={{ fontSize: 11, padding: "3px 12px" }} onClick={confirmOutline}>确认写入</button>
                <button className="btn" style={{ fontSize: 11, padding: "3px 12px" }} onClick={() => setPendingOutline(null)}>取消</button>
              </div>
            </div>
          )}
          <div ref={outlineChatEndRef} />
        </div>
        {/* 快捷指令 — a bubble floating just above the input box */}
        <div style={{ padding: "6px 10px 0" }}>
          <div style={{
            background: "var(--bg-surface-2)", border: "1px solid var(--border)",
            borderRadius: 10, padding: "5px 8px",
          }}>
            <div style={{ fontSize: 9, fontWeight: 700, color: "var(--text-tertiary)", marginBottom: 3, letterSpacing: 0.5 }}>
              快捷指令 · 点击直接发给本地 AI
            </div>
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
              {[
                { label: "生成大纲", prompt: "根据这一章的定位，帮我生成详细的章节大纲" },
                { label: "冲突设计", prompt: "帮我设计这一章的核心冲突和转折点" },
                { label: "节奏优化", prompt: "分析并优化这章大纲的叙事节奏" },
                { label: "悬念设置", prompt: "帮我在大纲中设计章末悬念和伏笔" },
              ].map(t => (
                <button key={t.label} className="btn" style={{ fontSize: 10, padding: "2px 8px", borderRadius: 12 }}
                  onClick={() => sendOutlineChat(t.prompt)}>
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          {/* downward pointer — makes the bubble read as hovering over the box */}
          <div style={{
            width: 0, height: 0, marginLeft: 22,
            borderLeft: "6px solid transparent", borderRight: "6px solid transparent",
            borderTop: "6px solid var(--bg-surface-2)",
          }} />
        </div>
        {/* Input box + 网页 LLM actions below it */}
        <div style={{ padding: "0 10px 6px" }}>
          <div style={{ display: "flex", gap: 6 }}>
            <textarea className="input" value={outlineChatInput} onChange={e => setOutlineChatInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendOutlineChat(); } }}
              placeholder="描述你想要的大纲..." rows={1} style={{ flex: 1, fontSize: 11, padding: "4px 8px", minHeight: 28, maxHeight: 80, resize: "none" }} />
            <button className="btn-primary" onClick={() => sendOutlineChat()} disabled={!outlineChatInput.trim() || outlineChatLoading}
              style={{ fontSize: 11, padding: "4px 10px" }}>{outlineChatLoading ? "..." : "发送"}</button>
          </div>
          {/* Web-LLM workflow */}
          <div style={{ marginTop: 6 }}>
            <WebLLMPromptPanel fetchPrompt={fetchOutlinePrompt} onApplyResult={applyPastedReply}
              applyLabel="解析并加入对话" resultPlaceholder="把网页 LLM 返回的回复粘贴到这里" />
          </div>
        </div>
        {outlineChatMsgs.length > 0 && outlineChatMsgs[outlineChatMsgs.length - 1].role === "assistant" && !pendingOutline && (
          <div style={{ padding: "4px 10px 8px", display: "flex", gap: 4 }}>
            <button className="btn" style={{ fontSize: 10, padding: "2px 10px", borderColor: "var(--jade)", color: "var(--jade)" }}
              onClick={() => applyOutlineFromChat(outlineChatMsgs[outlineChatMsgs.length - 1].content)}>
              写入大纲
            </button>
            <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
              onClick={() => sendOutlineChat("请继续优化上面的大纲")}>
              继续优化
            </button>
          </div>
        )}
      </div>
      )}

      {/* 关联角色 — cohesive collapsible section (header attached to panel) */}
      <div style={{ marginTop: 10, border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", overflow: "hidden" }}>
        <button className="btn-ghost" onClick={() => setShowCharLink(v => !v)}
          style={{ width: "100%", fontSize: 11, fontWeight: 600, padding: "6px 12px", textAlign: "left", borderRadius: 0,
            background: showCharLink ? "var(--bg-surface-2)" : "transparent" }}>
          {showCharLink ? "▾ " : "▸ "}关联角色{selectedChars.length > 0 ? ` · 已选 ${selectedChars.length}` : ""}
        </button>
        {showCharLink && (
          <div style={{ padding: 10, borderTop: "1px solid var(--border)" }}>
            {characters.length > 0 ? (
              <>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {characters.map(c => (
                    <button key={c.id} onClick={() => toggleChar(c.id)}
                      style={chipStyle(c.selected, "var(--purple)", "var(--purple-subtle)")}>
                      {c.name}
                    </button>
                  ))}
                </div>
                {selectedChars.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    <div className="label" style={{ fontSize: 10, marginBottom: 4, color: "var(--purple)" }}>隐藏身份（可选）</div>
                    {selectedChars.map(c => {
                      const alias = (chapter?.character_aliases || {})[c.name] || "";
                      return (
                        <div key={c.id} style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                          <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 10, background: "var(--purple-subtle)", color: "var(--purple)", whiteSpace: "nowrap" }}>{c.name}</span>
                          <input className="input" value={alias}
                            onChange={e => {
                              const next = { ...(chapter?.character_aliases || {}) };
                              if (e.target.value.trim()) next[c.name] = e.target.value;
                              else delete next[c.name];
                              onUpdateChapter?.("character_aliases", next);
                            }}
                            placeholder="隐藏身份（如：神秘女人）"
                            style={{ flex: 1, fontSize: 10, padding: "2px 8px", height: 22 }} />
                        </div>
                      );
                    })}
                  </div>
                )}
              </>
            ) : (
              <div className="text-xs text-muted">暂无角色，请在「角色管理」中创建</div>
            )}
          </div>
        )}
      </div>

      {/* 关联参考作品 — collapsible; count shown on the collapsed title */}
      <div style={{ marginTop: 8, border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", overflow: "hidden" }}>
        <button className="btn-ghost" onClick={() => setShowRefLink(v => !v)}
          style={{ width: "100%", fontSize: 11, fontWeight: 600, padding: "6px 12px", textAlign: "left", borderRadius: 0,
            background: showRefLink ? "var(--bg-surface-2)" : "transparent" }}>
          {showRefLink ? "▾ " : "▸ "}关联参考作品{references.length > 0 ? ` · 已选 ${selectedRefs.length}/${references.length}` : ""}
        </button>
        {showRefLink && (
          <div style={{ padding: 10, borderTop: "1px solid var(--border)" }}>
            {references.length > 0 ? (
              <>
                <input className="input" value={refSearch} onChange={e => setRefSearch(e.target.value)}
                  placeholder="搜索参考作品标题..." style={{ fontSize: 11, padding: "3px 8px", marginBottom: 4 }} />
                <div style={{ maxHeight: 150, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
                  {filteredRefs.map(r => (
                    <PickRow key={r.id} label={r.title}
                      sub={r.events.length > 0 ? `${r.events.length} 事件` : "无事件"}
                      on={r.selected} color="var(--jade)" onClick={() => toggleRef(r.id)} />
                  ))}
                  {filteredRefs.length === 0 && (
                    <div className="text-xs text-muted" style={{ padding: 8 }}>无匹配作品</div>
                  )}
                </div>
              </>
            ) : (
              <div className="text-xs text-muted">暂无参考作品，请在「参考作品详情」中导入</div>
            )}

            {/* 编年史事件 — per selected work; each row = 章节 tag + 事件名,
                click「详情」to expand details (the column is narrow). */}
            {selectedRefs.map(r => {
              const eq = eventSearch.trim().toLowerCase();
              const evs = r.events.filter(ev => !eq
                || ev.name.toLowerCase().includes(eq)
                || ev.description.toLowerCase().includes(eq)
                || ev.chapter.toLowerCase().includes(eq));
              const linkedN = refEvents.filter(e => e.ref_id === r.id).length;
              return (
                <div key={r.id} style={{ marginTop: 10 }}>
                  <div className="label" style={{ fontSize: 10, marginBottom: 4, color: "var(--gold)" }}>
                    「{r.title}」编年史事件{linkedN > 0 ? ` · 已选 ${linkedN}` : ""}
                  </div>
                  {r.events.length > 0 ? (
                    <>
                      <input className="input" value={eventSearch} onChange={e => setEventSearch(e.target.value)}
                        placeholder="搜索章节 / 事件名..." style={{ fontSize: 11, padding: "3px 8px", marginBottom: 4 }} />
                      <div style={{ maxHeight: 180, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
                        {evs.map((ev, i) => (
                          <EventRow key={i} ev={ev}
                            on={isEventLinked(r.id, ev.name)}
                            onToggle={() => toggleEvent(r.id, r.title, ev)} />
                        ))}
                        {evs.length === 0 && (
                          <div className="text-xs text-muted" style={{ padding: 8 }}>无匹配事件</div>
                        )}
                      </div>
                    </>
                  ) : (
                    <div className="text-xs text-muted">该作品暂无编年史事件，请先在参考作品详情中提取剧情大纲</div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 关联灵感 — collapsible; count shown on the collapsed title */}
      <div style={{ marginTop: 8, border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", overflow: "hidden" }}>
        <button className="btn-ghost" onClick={() => setShowInspLink(v => !v)}
          style={{ width: "100%", fontSize: 11, fontWeight: 600, padding: "6px 12px", textAlign: "left", borderRadius: 0,
            background: showInspLink ? "var(--bg-surface-2)" : "transparent" }}>
          {showInspLink ? "▾ " : "▸ "}关联灵感{inspirations.length > 0 ? ` · 已选 ${refInsps.length}/${inspirations.length}` : ""}
        </button>
        {showInspLink && (
          <div style={{ padding: 10, borderTop: "1px solid var(--border)" }}>
            {inspirations.length > 0 ? (
              <>
                <input className="input" value={inspSearch} onChange={e => setInspSearch(e.target.value)}
                  placeholder="搜索灵感..." style={{ fontSize: 11, padding: "3px 8px", marginBottom: 4 }} />
                <div style={{ maxHeight: 150, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
                  {filteredInsps.map(it => (
                    <PickRow key={it.id}
                      label={it.title || it.content.slice(0, 16) || "未命名灵感"}
                      sub={it.title ? it.content.slice(0, 14) : undefined}
                      on={refInsps.some(i => i.id === it.id)} color="var(--accent)"
                      onClick={() => toggleInspiration(it)} />
                  ))}
                  {filteredInsps.length === 0 && (
                    <div className="text-xs text-muted" style={{ padding: 8 }}>无匹配灵感</div>
                  )}
                </div>
              </>
            ) : (
              <div className="text-xs text-muted">灵感库为空，请在「灵感搜索 → 灵感库」中添加</div>
            )}
          </div>
        )}
      </div>

      {/* 伏笔 — collapsible CRUD section; each 伏笔 links chapters bidirectionally */}
      <div style={{ marginTop: 8, border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", overflow: "hidden" }}>
        <button className="btn-ghost" onClick={() => setShowForeshadow(v => !v)}
          style={{ width: "100%", fontSize: 11, fontWeight: 600, padding: "6px 12px", textAlign: "left", borderRadius: 0,
            background: showForeshadow ? "var(--bg-surface-2)" : "transparent" }}>
          {showForeshadow ? "▾ " : "▸ "}伏笔{foreshadow.length > 0 ? ` · ${foreshadow.length}` : ""}
        </button>
        {showForeshadow && (
          <div style={{ padding: 10, borderTop: "1px solid var(--border)" }}>
            <button className="btn" style={{ fontSize: 11, padding: "3px 12px", marginBottom: 8 }} onClick={addForeshadow}>
              + 新建伏笔
            </button>
            {foreshadow.length === 0 ? (
              <div className="text-xs text-muted">暂无伏笔。新建后可关联多个章节——任一关联章节生成时都会带上该伏笔。</div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {foreshadow.map(f => (
                  <div key={f.id} style={{ border: "1px solid var(--border)", borderRadius: 4, padding: 8 }}>
                    <div style={{ display: "flex", gap: 6, marginBottom: 4 }}>
                      <input className="input" value={f.title}
                        onChange={e => updateForeshadow(f.id, { title: e.target.value })}
                        placeholder="伏笔标题" style={{ flex: 1, fontSize: 12, padding: "3px 8px" }} />
                      <button onClick={() => deleteForeshadow(f.id)}
                        style={{ background: "none", border: "none", color: "var(--text-disabled)", cursor: "pointer", fontSize: 15 }}
                        title="删除伏笔">&times;</button>
                    </div>
                    <textarea className="input" value={f.content}
                      onChange={e => updateForeshadow(f.id, { content: e.target.value })}
                      placeholder="伏笔内容（埋设了什么、计划如何回收）" rows={2}
                      style={{ width: "100%", fontSize: 11, padding: "4px 8px", resize: "vertical", marginBottom: 4, boxSizing: "border-box" }} />
                    <div className="text-xs text-muted" style={{ marginBottom: 3 }}>关联章节（点击切换；关联为双向）：</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                      {(allChapters || []).map(ch => {
                        const on = f.chapter_ids.includes(ch.id);
                        return (
                          <span key={ch.id} onClick={() => toggleFsChapter(f.id, ch.id)}
                            style={{
                              fontSize: 10, padding: "2px 8px", borderRadius: 10, cursor: "pointer", userSelect: "none",
                              background: on ? "var(--accent-subtle)" : "transparent",
                              color: on ? "var(--accent)" : "var(--text-tertiary)",
                              border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
                            }}>
                            {ch.title || "未命名"}
                          </span>
                        );
                      })}
                      {(allChapters || []).length === 0 && (
                        <span className="text-xs text-muted">暂无章节</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <div className="field" style={{ flex: 1 }}>
          <label className="label">时间</label>
          <input className="input" value={time} onChange={e => { setTime(e.target.value); onUpdateChapter?.("time", e.target.value); }} placeholder="例：第3天·黄昏" style={{ fontSize: 12 }} />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label className="label">地点</label>
          <input className="input" value={location} onChange={e => { setLocation(e.target.value); onUpdateChapter?.("location", e.target.value); }} placeholder="例：云隐山·剑庐" style={{ fontSize: 12 }} />
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button className="btn-primary" style={{ flex: 1 }} onClick={onSave}>保存</button>
        <button className="btn-primary" style={{ flex: 1, background: "var(--jade, #34a853)", border: "none" }} onClick={onStartGeneration}>开始生成</button>
      </div>
      <p className="text-xs text-muted mt-12" style={{ lineHeight: 1.6 }}>
        用上方 AI 大纲助手与 AI 讨论大纲，满意后点击「写入大纲」。关联角色、参考作品的编年史事件与灵感库后，Pipeline 生成时 AI 将参考相关信息。
      </p>
    </div>
  );
}

type RagItem = { id: string; label: string };
type ContextManifest = {
  rag: { key: string; label: string; present: boolean; items: RagItem[] }[];
  default_skills: { name: string; domain: string; step?: string }[];
  learned_skills: { name: string; description?: string; skill_md?: string }[];
  writing_knowledge: { id: string; title: string }[];
};

/** Normalize a persisted aiTab value (migrates the old "inspire" tab). */
function normalizeAiTab(v: any): "outline" | "single" | "cluster" | "rewrite" | "eval" {
  if (v === "inspire" || v === "single") return "single";
  if (v === "cluster" || v === "rewrite" || v === "eval") return v;
  return "outline";
}

/** One-line summary of which learned skills an AI step used. */
function formatSkillsUsed(skills?: string[]): string {
  return skills && skills.length
    ? `本次创作启用技能：${skills.join("、")}`
    : "本次创作未启用自定义技能";
}

/** Transparency panel: skills used + per-item RAG context (de-selectable).
 *  Shared by the creation tabs and the 评估 tab. */
function ContextPanel({ manifest, skillSelection, ragExcludes, onToggleSkill, onToggleRagItem, onRefresh }: {
  manifest: ContextManifest | null;
  skillSelection: Record<string, boolean>;
  ragExcludes: Set<string>;
  onToggleSkill: (name: string) => void;
  onToggleRagItem: (key: string) => void;
  onRefresh?: () => void;
}) {
  const [skillOpen, setSkillOpen] = useState(false);
  const [ragOpen, setRagOpen] = useState(false);
  if (!manifest) return null;

  const sectionHeader = (open: boolean, toggle: () => void, text: string,
                         rightEl?: React.ReactNode) => (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <button onClick={toggle} style={{
        flex: 1, textAlign: "left", background: "none", border: "none", padding: "2px 0",
        cursor: "pointer", fontSize: 11, fontWeight: 700, color: "var(--text-secondary)",
        display: "flex", alignItems: "center", gap: 5,
      }}>
        <span style={{ fontSize: 9, color: "var(--text-tertiary)" }}>{open ? "▾" : "▸"}</span>{text}
      </button>
      {rightEl}
    </div>
  );

  const chip = (key: string, label: string, on: boolean,
                onClick?: () => void, tagText?: string) => (
    <span key={key} onClick={onClick} title={onClick ? "点击启用 / 停用" : "系统自动调用"}
      style={{
        fontSize: 11, padding: "3px 9px", borderRadius: 12, userSelect: "none",
        cursor: onClick ? "pointer" : "default",
        display: "inline-flex", alignItems: "center", gap: 5,
        background: on ? "var(--accent-subtle)" : "transparent",
        color: on ? "var(--accent)" : "var(--text-tertiary)",
        border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
        opacity: on ? 1 : 0.65,
        transition: "background 0.12s, border-color 0.12s, opacity 0.12s",
      }}>
      {tagText && (
        <span style={{
          fontSize: 8, padding: "0 4px", borderRadius: 6, lineHeight: "13px",
          background: "var(--bg-app)", color: "var(--text-tertiary)",
          border: "1px solid var(--border)",
        }}>{tagText}</span>
      )}
      {label}
    </span>
  );

  const allKeys = manifest.rag.flatMap(r => r.items.map(it => `${r.key}::${it.id}`));
  const selCount = allKeys.filter(k => !ragExcludes.has(k)).length;
  const skillCount = manifest.default_skills.length + manifest.learned_skills.length
    + manifest.writing_knowledge.length;

  const downloadSkills = () => {
    const sel = manifest.learned_skills.filter(s => skillSelection[s.name] !== false);
    if (sel.length === 0) return;
    const md = sel.map(s => `# ${s.name}\n\n${s.skill_md || s.description || ""}`)
      .join("\n\n---\n\n");
    const url = URL.createObjectURL(new Blob([md], { type: "text/markdown;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url; a.download = "创作技能.SKILL.md";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <div style={{
      marginBottom: 6, padding: "8px 10px", background: "var(--bg-surface)",
      borderRadius: "var(--radius-sm)", border: "1px solid var(--border)",
    }}>
      {sectionHeader(skillOpen, () => setSkillOpen(o => !o), `调用的 skill（${skillCount}）`,
        manifest.learned_skills.length > 0 ? (
          <button className="btn" style={{ fontSize: 9, padding: "1px 7px" }}
            onClick={downloadSkills}
            title="下载已勾选的自学习技能 SKILL.md（连同复制的 prompt 一起用于网页版大模型）">
            下载 SKILL.md
          </button>
        ) : undefined)}
      {skillOpen && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "6px 0 8px" }}>
          {manifest.default_skills.map(s => chip("d:" + s.name, s.name, true, undefined, s.step || "默认"))}
          {manifest.learned_skills.map(s => chip(
            "l:" + s.name, s.name, skillSelection[s.name] !== false,
            () => onToggleSkill(s.name), "自学习"))}
          {manifest.writing_knowledge.map(k => chip(
            "k:" + k.id, k.title || "（无题）",
            !ragExcludes.has(`writing_knowledge::${k.id}`),
            () => onToggleRagItem(`writing_knowledge::${k.id}`), "写作知识"))}
          {skillCount === 0 && <span className="text-xs text-muted">本次无可调用 skill</span>}
        </div>
      )}
      {sectionHeader(ragOpen, () => setRagOpen(o => !o),
        `引用的 RAG 上下文（已启用 ${selCount}/${allKeys.length} 项）`,
        onRefresh ? (
          <button className="btn" style={{ fontSize: 9, padding: "1px 7px" }}
            onClick={onRefresh}
            title="重新加载 RAG —— 在角色管理 / 世界书 / 大纲等处更新数据后点此同步">
            ↻ 刷新
          </button>
        ) : undefined)}
      {ragOpen && (
        <div style={{ marginTop: 2 }}>
          {manifest.rag.map(cat => {
            const sel = cat.items.filter(it => !ragExcludes.has(`${cat.key}::${it.id}`)).length;
            return (
              <div key={cat.key} style={{ marginTop: 7 }}>
                <div className="text-xs" style={{ color: "var(--text-tertiary)", marginBottom: 3 }}>
                  {cat.label}{cat.items.length > 0 ? ` ·已选 ${sel}/${cat.items.length}` : ""}
                </div>
                {cat.items.length > 0 ? (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {cat.items.map(it => chip(
                      `${cat.key}::${it.id}`, it.label,
                      !ragExcludes.has(`${cat.key}::${it.id}`),
                      () => onToggleRagItem(`${cat.key}::${it.id}`)))}
                  </div>
                ) : <span className="text-xs text-muted">无</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function InspireTab({ mode, steps, generating, onStart, onStartPlain, chatMessages, chatInput, onChatInputChange, onSendMessage, waitingForConfirm, onConfirmContinue, onRollback, onWriteToEditor, onStopPipeline, modelChanged, onDismissModelChange, onRestartWithNewModel, onFetchPrompt, onApplyPaste, manualPrompt, onSubmitManual, manifest, skillSelection, onToggleSkill, ragExcludes, onToggleRagItem, onRefreshManifest, onDeleteMessage }: {
  mode: "single" | "cluster";
  steps: PipelineStatus[]; generating: boolean; onStart: (manual?: boolean) => void; onStartPlain?: () => void; chatMessages: ChatMessage[]; chatInput: string;
  onChatInputChange: (v: string) => void; onSendMessage: () => void; waitingForConfirm: boolean; onConfirmContinue: () => void; onRollback?: (stepIndex: number) => void; onWriteToEditor?: () => void; onStopPipeline?: () => void;
  modelChanged?: boolean; onDismissModelChange?: () => void; onRestartWithNewModel?: () => void;
  onFetchPrompt?: () => Promise<string>; onApplyPaste?: (text: string) => void; onDeleteMessage?: (index: number) => void;
  manualPrompt?: { step: string; prompt: string } | null; onSubmitManual?: (text: string) => void;
  manifest?: ContextManifest | null;
  skillSelection?: Record<string, boolean>; onToggleSkill?: (name: string) => void;
  ragExcludes?: Set<string>; onToggleRagItem?: (key: string) => void; onRefreshManifest?: () => void;
}) {
  const { prompt } = useDialog();
  const chatEndRef = useRef<HTMLDivElement>(null);
  const pipelineMode = mode === "cluster";
  const [expandedPromptIdx, setExpandedPromptIdx] = useState<number | null>(null);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [chatMessages, waitingForConfirm]);

  const getAgentStyle = (agent: string, displayName?: string) => {
    if (displayName && agent === "Actor Agents") {
      // Character-specific colors for group chat
      if (displayName === "旁白") return AGENT_COLORS["旁白"];
      const cc = getCharColor(displayName);
      return { bg: cc.bg, border: cc.border, name: displayName };
    }
    return AGENT_COLORS[agent] || { bg: "var(--gold-subtle)", border: "var(--gold)", name: agent };
  };
  const getAgentAvatar = (agent: string, displayName?: string) => {
    if (displayName && agent === "Actor Agents") {
      if (displayName === "旁白") return "N";
      // Use first character of name as avatar
      return displayName.charAt(0);
    }
    const map: Record<string, string> = { "Scene Director": "SD", "Actor Agents": "AC", "Editor-Writer": "EW", "Evaluator": "EV", "User": "U", "System": "SY" };
    return map[agent] || "AG";
  };
  const [cotExpanded, setCotExpanded] = useState<Record<number, boolean>>({});

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {manualPrompt && generating && (
        <div style={{ marginBottom: 10 }}>
          <div className="label mb-8" style={{ color: "var(--accent)" }}>
            手动模式 · 当前 agent：{manualPrompt.step || "—"}
          </div>
          <WebLLMPromptPanel
            key={`${manualPrompt.step}:${manualPrompt.prompt.length}`}
            title={`Pipeline agent prompt · ${manualPrompt.step}`}
            fetchPrompt={async () => manualPrompt.prompt}
            onApplyResult={(t) => onSubmitManual?.(t)}
            applyLabel="提交结果，继续 Pipeline"
            resultPlaceholder="把网页 LLM 针对该 agent 的返回结果粘贴到这里"
          />
        </div>
      )}
      {pipelineMode && <>
      <div className="label mb-8">集群式智能体创作 · 多 Agent 群聊（导演 → 角色 → 编辑 → 评估）</div>
      {/* Progress bar */}
      <div style={{ display: "flex", gap: 4, marginBottom: 10, padding: "6px 0" }}>
        {steps.map((s, i) => (
          <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4, cursor: s.status === "done" ? "pointer" : "default" }}
            onClick={() => {
              if (s.status !== "done") return;
              if (onRollback) onRollback(i);
            }}
            title={s.status === "done" ? `点击回退到「${s.step}」` : s.detail || undefined}
          >
            <div style={{ width: "100%", height: 6, borderRadius: 3, background: "var(--border)", overflow: "hidden" }}>
              <div style={{
                width: `${s.progress ?? (s.status === "done" ? 100 : s.status === "running" ? 30 : 0)}%`,
                height: "100%", borderRadius: 3,
                background: s.status === "done" ? "var(--jade)" : s.status === "running" ? "var(--gold)" : "var(--border)",
                transition: "width 0.3s, background 0.3s",
              }} />
            </div>
            <span style={{ fontSize: 9, color: s.status === "done" ? "var(--jade)" : s.status === "running" ? "var(--gold)" : "var(--text-disabled)" }}>{s.step.split(" ")[0]}</span>
            {s.status === "running" && s.detail && (
              <span style={{ fontSize: 8, color: "var(--gold)", maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {s.detail} {s.progress ? `${s.progress}%` : ""}
              </span>
            )}
          </div>
        ))}
      </div>
      </>}
      {/* Model change detection banner */}
      {modelChanged && generating && (
        <div style={{ padding: "8px 12px", marginBottom: 8, borderRadius: 6, background: "var(--accent-subtle)", border: "1px solid var(--accent)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <span style={{ fontSize: 12, color: "var(--accent)", fontWeight: 500 }}>检测到模型更换，是否重新生成？</span>
          <div style={{ display: "flex", gap: 6 }}>
            <button className="btn-primary" style={{ fontSize: 11, padding: "3px 12px", background: "var(--accent)", border: "none" }} onClick={onRestartWithNewModel}>是</button>
            <button className="btn" style={{ fontSize: 11, padding: "3px 12px" }} onClick={onDismissModelChange}>忽略</button>
          </div>
        </div>
      )}
      {/* Chat area */}
      <div style={{ flex: 1, overflowY: "auto", border: "1px solid var(--border)", borderRadius: "var(--radius-sm, 6px)", padding: 8, marginBottom: 10, minHeight: 200, maxHeight: 400, background: "var(--bg-app)" }}>
        {chatMessages.length === 0 && !generating && (
          <div style={{ padding: "32px 16px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 13 }}>
            {mode === "cluster" ? "集群式智能体创作" : "单智能体创作"}
          </div>
        )}
        {chatMessages.map((msg, i) => {
          const style = getAgentStyle(msg.agent, msg.agentDisplayName); const isUser = msg.agent === "User";
          const avatar = getAgentAvatar(msg.agent, msg.agentDisplayName);
          const isCharActor = msg.agent === "Actor Agents" && msg.agentDisplayName && msg.agentDisplayName !== "旁白";
          return (
            <div key={i} style={{ display: "flex", flexDirection: isUser ? "row-reverse" : "row", alignItems: "flex-start", marginBottom: 10, gap: 8 }}>
              <div style={{ width: 32, height: 32, borderRadius: "50%", background: style.bg, border: `2px solid ${style.border}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: isCharActor ? 13 : 16, flexShrink: 0, fontWeight: isCharActor ? 700 : 400, color: isCharActor ? style.border : undefined }}>{avatar}</div>
              <div style={{ maxWidth: "80%", minWidth: 0 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: style.border, marginBottom: 2, textAlign: isUser ? "right" : "left" }}>
                  {msg.agentDisplayName || style.name}
                  {isCharActor && <span style={{ fontSize: 9, fontWeight: 400, color: "var(--text-tertiary)", marginLeft: 4 }}>(Actor)</span>}
                  {msg.isWarning && <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 400, color: "var(--gold)" }}>\u26A0</span>}
                  {msg.status === "thinking" && !msg.isCoT && <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 400, color: "#f9ab00" }}>思考中...</span>}
                  {msg.isCoT && msg.status === "thinking" && <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 400, color: "var(--text-tertiary)" }}>思考中</span>}
                </div>
                <div style={{
                  padding: "8px 12px", borderRadius: 10,
                  background: msg.isWarning ? "rgba(255,160,0,0.08)" : style.bg,
                  borderLeft: isUser ? "none" : `3px solid ${msg.isWarning ? "var(--gold)" : style.border}`,
                  borderRight: isUser ? `3px solid ${style.border}` : "none",
                  fontSize: 13, lineHeight: 1.6, color: "var(--text-primary)", wordBreak: "break-word", whiteSpace: "pre-wrap",
                  userSelect: "text", cursor: "text",
                  maxHeight: msg.content.length > 800 ? 300 : undefined, overflowY: msg.content.length > 800 ? "auto" : undefined,
                }}>
                  {/* CoT collapsible */}
                  {msg.isCoT ? (
                    <div>
                      <button
                        onClick={() => setCotExpanded(prev => ({ ...prev, [i]: !prev[i] }))}
                        style={{ fontSize: 11, color: "var(--text-tertiary)", background: "none", border: "none", cursor: "pointer", padding: 0, marginBottom: 4 }}
                      >
                        {msg.status === "thinking" ? "思考中..." : (cotExpanded[i] ? "收起思考过程" : "查看思考过程")}
                        {msg.status === "thinking" && <span style={{ marginLeft: 4, animation: "pulse 1s infinite" }}>...</span>}
                      </button>
                      {(cotExpanded[i] || msg.status === "thinking") && msg.content && (
                        <pre style={{ fontSize: 11, color: "var(--text-tertiary)", whiteSpace: "pre-wrap", lineHeight: 1.5, maxHeight: 200, overflowY: "auto", fontFamily: "var(--font-mono)", padding: "6px 0", margin: 0 }}>
                          {msg.content}
                        </pre>
                      )}
                    </div>
                  ) : msg.isWarning ? (
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                        <span style={{ fontSize: 14 }}>\u26A0</span>
                        <span style={{ fontWeight: 600, color: "var(--gold)" }}>角色/世界观提醒</span>
                      </div>
                      <div>{msg.content}</div>
                      {msg.warningOptions && msg.status === "waiting_confirm" && (
                        <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                          {msg.warningOptions.map((opt, oi) => (
                            <button key={oi} className="btn" style={{ fontSize: 11, padding: "4px 12px", borderRadius: 14 }}
                              onClick={async () => {
                                if (opt.includes("故意")) {
                                  const reason = await prompt({ title: "请说明原因", placeholder: "请输入原因" });
                                  if (reason !== null) {
                                    onChatInputChange(`${opt}：${reason}`);
                                    onSendMessage();
                                  }
                                } else {
                                  onChatInputChange(opt);
                                  onSendMessage();
                                }
                              }}>
                              {opt}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  ) : msg.isQuestion && msg.followUpOptions && msg.followUpOptions.length > 0 ? (
                    <div>
                      <div style={{ marginBottom: 8 }}>{msg.content}</div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                        {msg.followUpOptions.map((opt, oi) => {
                          const isContinue = /满意|继续|进行下一步/.test(opt);
                          return (
                            <button key={oi}
                              onClick={() => {
                                if (isContinue) {
                                  // Auto-confirm and continue pipeline
                                  onConfirmContinue();
                                } else {
                                  onChatInputChange(opt);
                                  onSendMessage();
                                }
                              }}
                              style={{
                                padding: "8px 14px", borderRadius: 8,
                                border: isContinue ? "1px solid var(--jade)" : "1px solid var(--border)",
                                background: isContinue ? "rgba(76,175,80,0.08)" : "var(--bg-surface)",
                                color: "var(--text-primary)",
                                fontSize: 12, textAlign: "left", cursor: "pointer", transition: "all 0.15s",
                                fontWeight: isContinue ? 600 : 400,
                              }}
                              onMouseEnter={e => { e.currentTarget.style.borderColor = isContinue ? "var(--jade)" : "var(--accent)"; e.currentTarget.style.background = isContinue ? "rgba(76,175,80,0.15)" : "var(--accent-subtle)"; }}
                              onMouseLeave={e => { e.currentTarget.style.borderColor = isContinue ? "var(--jade)" : "var(--border)"; e.currentTarget.style.background = isContinue ? "rgba(76,175,80,0.08)" : "var(--bg-surface)"; }}
                            >
                              {isContinue ? `✓ ${opt}` : opt}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ) : msg.isQuestion ? (
                    <QuestionChoices content={msg.content} onChoose={(choice) => {
                      onChatInputChange(choice);
                    }} />
                  ) : msg.content}
                </div>
                {msg.status === "done" && (
                  <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
                    <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px", color: "var(--text-tertiary)" }}
                      onClick={() => { navigator.clipboard.writeText(msg.content); }}>
                      复制
                    </button>
                    {msg.promptSent && (
                      <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px", color: "var(--text-tertiary)" }}
                        onClick={() => setExpandedPromptIdx(expandedPromptIdx === i ? null : i)}>
                        {expandedPromptIdx === i ? "隐藏 Prompt" : "查看 Prompt"}
                      </button>
                    )}
                    {msg.agent !== "User" && msg.agent !== "System" && !generating && (
                      <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px", color: "var(--text-tertiary)" }}
                        onClick={() => {
                          const stepIdx = steps.findIndex(s => s.step === msg.agent);
                          if (stepIdx >= 0 && onRollback) {
                            onRollback(stepIdx);
                          } else {
                            onStart();
                          }
                        }}>
                        ↻ 重新生成
                      </button>
                    )}
                    {!generating && onDeleteMessage && (
                      <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px", color: "var(--text-tertiary)" }}
                        onClick={() => onDeleteMessage(i)}
                        onMouseEnter={e => e.currentTarget.style.color = "var(--error)"}
                        onMouseLeave={e => e.currentTarget.style.color = "var(--text-tertiary)"}
                        title="删除此消息">
                        × 删除
                      </button>
                    )}
                  </div>
                )}
                {expandedPromptIdx === i && msg.promptSent && (
                  <pre style={{
                    marginTop: 6, padding: "8px 10px", borderRadius: 6, fontSize: 11, lineHeight: 1.5,
                    background: "var(--bg-surface-2)", color: "var(--text-secondary)",
                    whiteSpace: "pre-wrap", wordBreak: "break-all", maxHeight: 300, overflowY: "auto",
                    border: "1px solid var(--border)", fontFamily: "var(--font-mono)",
                  }}>
                    {msg.promptSent}
                  </pre>
                )}
              </div>
            </div>
          );
        })}
        {/* Green confirm button removed — follow_up options handle progression */}
        {!generating && chatMessages.length > 0 && chatMessages[chatMessages.length - 1]?.agent === "System" && (() => {
          const lastMsg = chatMessages[chatMessages.length - 1] as any;
          const wasStopped = lastMsg._stopped || lastMsg.content.includes("手动终止");
          const hasError = lastMsg.content.includes("错误");
          const pipelineCompleted = !wasStopped && !hasError && lastMsg.content.includes("完成");
          // Find the last running/done step for "restart current step"
          const lastRunningIdx = [...steps].reverse().findIndex(s => s.status === "done" || s.status === "running");
          const currentStepIdx = lastRunningIdx >= 0 ? steps.length - 1 - lastRunningIdx : 0;
          // Find previous step for "go back"
          const prevStepIdx = Math.max(0, currentStepIdx - 1);
          return (
            <div style={{ display: "flex", justifyContent: "center", gap: 10, padding: "12px 0", borderTop: "1px dashed var(--border)", marginTop: 8 }}>
              {pipelineCompleted && (
                <button className="btn-primary" style={{ padding: "8px 20px", fontSize: 13, borderRadius: 20 }} onClick={() => {
                  if (onWriteToEditor) onWriteToEditor();
                }}>
                  确认完成，写入编辑器
                </button>
              )}
              {(wasStopped || hasError) && (
                <button className="btn-primary" style={{ padding: "8px 16px", fontSize: 12, borderRadius: 20, background: "var(--jade)", border: "none" }} onClick={() => {
                  // Restart current step only (not entire pipeline)
                  if (onRollback) onRollback(currentStepIdx);
                }}>
                  重新生成当前步骤
                </button>
              )}
              {currentStepIdx > 0 && (
                <button className="btn" style={{ padding: "8px 16px", fontSize: 12, borderRadius: 20 }} onClick={() => {
                  // Go back to previous agent and auto-regenerate
                  if (onRollback) onRollback(prevStepIdx);
                }}>
                  回退上一步
                </button>
              )}
            </div>
          );
        })()}
        <div ref={chatEndRef} />
      </div>
      {/* Stop / Control bar */}
      {generating && (
        <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
          <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)", borderColor: "var(--error)", flex: 1 }} onClick={onStopPipeline}>
            ⏹ 终止 Pipeline
          </button>
        </div>
      )}
      {/* Input */}
      <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
        <textarea className="input" value={chatInput} onChange={e => onChatInputChange(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSendMessage(); } }}
          placeholder={waitingForConfirm ? "输入修改意见，或点击确认继续..." : "输入消息与 Agent 对话..."} rows={1} style={{ flex: 1, fontSize: 12, padding: "6px 10px", minHeight: 32, maxHeight: 100, resize: "none" }} />
        <button className="btn-primary" onClick={onSendMessage} disabled={!chatInput.trim()} style={{ fontSize: 12, padding: "6px 12px", flexShrink: 0 }}>发送</button>
      </div>
      {/* Generation controls */}
      {!generating && !waitingForConfirm && (
        <div style={{ marginBottom: 6 }}>
          <ContextPanel
            manifest={manifest || null}
            skillSelection={skillSelection || {}}
            ragExcludes={ragExcludes || new Set()}
            onToggleSkill={(n) => onToggleSkill?.(n)}
            onToggleRagItem={(k) => onToggleRagItem?.(k)}
            onRefresh={onRefreshManifest}
          />
          {mode === "single" ? (
            <>
              {onStartPlain && (
                <button className="btn-primary" style={{ width: "100%" }} onClick={() => onStartPlain()}>
                  {chatMessages.length > 0 ? "单智能体重新创作" : "单智能体创作"}
                </button>
              )}
              {onFetchPrompt && (
                <div style={{ marginTop: 6 }}>
                  <WebLLMPromptPanel fetchPrompt={onFetchPrompt}
                    title="AI大模型网页版"
                    onApplyResult={onApplyPaste} applyLabel="解析并写入编辑器"
                    resultPlaceholder="把网页 LLM 生成的章节正文粘贴到这里" />
                </div>
              )}
            </>
          ) : (
            <>
              <button className="btn-primary" style={{ width: "100%" }} onClick={() => onStart(false)}
                title="多 Agent 协作 Pipeline（导演 → 角色 → 编辑 → 评估），调用 AI 大模型 API">
                {chatMessages.length > 0 ? "重新集群创作" : "集群式智能体创作"}
              </button>
              <div style={{ marginTop: 6 }}>
                <button className="btn" style={{ width: "100%" }}
                  onClick={() => onStart(true)}
                  title="逐 agent 暂停：复制该步 prompt 到 AI大模型网页版、粘贴返回结果再继续">
                  AI大模型网页版（逐 agent 复制 prompt / 粘贴结果）
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function RewriteTab({ selection, prompt, onPromptChange, model, onModelChange }: { selection: { start: number; end: number; text: string } | null; prompt: string; onPromptChange: (v: string) => void; model: string; onModelChange: (v: string) => void; }) {
  const [rewriting, setRewriting] = useState(false);
  const [rewriteResult, setRewriteResult] = useState<string | null>(null);
  const [rewriteError, setRewriteError] = useState<string | null>(null);

  const handleRewrite = async () => {
    if (!selection) return;
    setRewriting(true);
    setRewriteResult(null);
    setRewriteError(null);
    try {
      const resp = await apiPost<{ rewritten: string }>("/api/generation/rewrite", {
        text: selection.text,
        instruction: prompt || "润色并提升文学质量",
        model: model || undefined,
      });
      setRewriteResult(typeof resp.rewritten === "string" ? resp.rewritten : JSON.stringify(resp.rewritten));
    } catch (e: any) {
      setRewriteError(e?.message || "重写失败，请检查模型连接");
    }
    setRewriting(false);
  };

  return (
    <div>
      <div className="label mb-8">AI 重写选中文本</div>
      {selection ? (<>
        <div style={{ padding: "10px 12px", background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)", marginBottom: 12, fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.7, maxHeight: 120, overflowY: "auto", fontFamily: "var(--font-serif)", borderLeft: "3px solid var(--accent)" }}>&ldquo;{selection.text.length > 200 ? selection.text.slice(0, 200) + "..." : selection.text}&rdquo;</div>
        <div className="text-xs text-muted mb-12">选中了 {selection.text.length} 字（位置 {selection.start}-{selection.end}）</div>
        <div className="field mb-12"><label className="label">重写指令（可选）</label><textarea className="input" value={prompt} onChange={e => onPromptChange(e.target.value)} rows={3} placeholder={"告诉 AI 你想怎么改...\n例如：更紧张、加入内心描写、换成第一人称"} /></div>
        <button className="btn-primary w-full" onClick={handleRewrite} disabled={rewriting}>
          {rewriting ? "重写中..." : "AI 重写此段落"}
        </button>
        {rewriteError && <p className="text-xs mt-8" style={{ color: "var(--error)" }}>{rewriteError}</p>}
        {rewriteResult && (<>
          <div style={{ marginTop: 12, padding: "10px 12px", background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)", fontSize: 13, lineHeight: 1.7, fontFamily: "var(--font-serif)", borderLeft: "3px solid var(--jade)", maxHeight: 200, overflowY: "auto", color: "var(--text-primary)" }}>
            {rewriteResult}
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            <button className="btn" style={{ flex: 1, fontSize: 11 }} onClick={() => navigator.clipboard.writeText(rewriteResult)}>
              复制结果
            </button>
          </div>
        </>)}
        <div style={{ marginTop: 10 }}>
          <WebLLMPromptPanel
            fetchPrompt={async () => {
              const r = await apiPost<{ prompt: string }>("/api/generation/rewrite", {
                text: selection.text, instruction: prompt || "润色并提升文学质量", prompt_only: true,
              });
              return r.prompt || "";
            }}
            onApplyResult={(t) => setRewriteResult(t.trim())}
            applyLabel="应用为重写结果"
            resultPlaceholder="把网页 LLM 返回的重写文本粘贴到这里"
          />
        </div>
      </>) : (<div className="empty-state" style={{ padding: "32px 16px" }}><h4>选中文本以重写</h4><p>在编辑器中选中文本，将出现「AI重写」按钮</p></div>)}
    </div>
  );
}

function QuestionChoices({ content, onChoose }: { content: string; onChoose: (choice: string) => void }) {
  const [page, setPage] = useState(0);
  // Split into sections by **方案
  const sections = content.split(/(?=\*\*方案 [A-Z])/);
  const headerText = sections[0] || "";
  const choiceSections = sections.slice(1);
  const ITEMS_PER_PAGE = 2;
  const totalPages = Math.ceil(choiceSections.length / ITEMS_PER_PAGE);
  const pageChoices = choiceSections.slice(page * ITEMS_PER_PAGE, (page + 1) * ITEMS_PER_PAGE);

  return (
    <div>
      <div style={{ marginBottom: 10, whiteSpace: "pre-wrap" }}>{headerText.trim()}</div>
      {pageChoices.map((section, i) => {
        const lines = section.trim().split("\n");
        const title = lines[0].replace(/\*\*/g, "").trim();
        const details = lines.slice(1).join("\n").trim();
        return (
          <button key={i + page * ITEMS_PER_PAGE} onClick={() => onChoose(`选择${title}`)}
            style={{
              display: "block", width: "100%", textAlign: "left", padding: "10px 14px",
              marginBottom: 8, borderRadius: 8, border: "1px solid var(--border-hover)",
              background: "var(--bg-surface)", cursor: "pointer", transition: "all 0.15s",
              color: "var(--text-primary)", fontSize: 13,
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--accent)"; e.currentTarget.style.background = "var(--accent-subtle)"; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = "var(--border-hover)"; e.currentTarget.style.background = "var(--bg-surface)"; }}
          >
            <div style={{ fontWeight: 600, marginBottom: 4, color: "var(--accent)" }}>{title}</div>
            {details && <div style={{ fontSize: 12, color: "var(--text-secondary)", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{details}</div>}
          </button>
        );
      })}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-8" style={{ marginTop: 6 }}>
          <button className="btn-ghost" style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}>← 上一页</button>
          <span className="text-xs text-muted">{page + 1}/{totalPages}</span>
          <button className="btn-ghost" style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1}>下一页 →</button>
        </div>
      )}
      <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--border)" }}>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>或直接输入修改意见</span>
      </div>
    </div>
  );
}

/* ---- DiffView: GitHub-style line diff with per-hunk accept old/new ---- */
function DiffView({ oldText, newText, onAccept, onCancel }: {
  oldText: string; newText: string;
  onAccept: (finalText: string) => void;
  onCancel: () => void;
}) {
  const hunks = useMemo(() => {
    const lines = computeDiff(oldText || "", newText);
    return groupIntoHunks(lines);
  }, [oldText, newText]);

  const [choices, setChoices] = useState<Map<number, "old" | "new">>(() => {
    const m = new Map<number, "old" | "new">();
    hunks.forEach(h => { if (h.hasChanges) m.set(h.id, "new"); });
    return m;
  });

  const setAll = (v: "old" | "new") => {
    const m = new Map<number, "old" | "new">();
    hunks.forEach(h => { if (h.hasChanges) m.set(h.id, v); });
    setChoices(m);
  };

  const toggle = (id: number) => {
    setChoices(prev => {
      const m = new Map(prev);
      m.set(id, m.get(id) === "old" ? "new" : "old");
      return m;
    });
  };

  const handleConfirm = () => {
    onAccept(assembleFromHunks(hunks, choices));
  };

  const changedCount = hunks.filter(h => h.hasChanges).length;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ padding: "8px 28px", background: "var(--bg-surface-2)", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: "var(--accent)" }}>
          逐行对比 — {changedCount} 处变更
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" style={{ fontSize: 11, padding: "4px 12px" }} onClick={() => setAll("new")}>全部使用 AI</button>
          <button className="btn" style={{ fontSize: 11, padding: "4px 12px" }} onClick={() => setAll("old")}>全部保留原文</button>
          <button className="btn-primary" style={{ fontSize: 12, padding: "6px 16px", background: "var(--jade)", border: "none" }} onClick={handleConfirm}>
            确认合并
          </button>
          <button className="btn" style={{ fontSize: 12, padding: "6px 12px" }} onClick={onCancel}>取消</button>
        </div>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: "8px 0", fontFamily: "var(--font-mono)", fontSize: 12, lineHeight: 1.6 }}>
        {hunks.map(hunk => (
          <div key={hunk.id} style={{ position: "relative" }}>
            {hunk.hasChanges && (
              <div style={{ position: "sticky", top: 0, zIndex: 2, padding: "4px 28px", background: "var(--bg-surface)", borderBottom: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 8 }}>
                <button
                  className={choices.get(hunk.id) === "old" ? "btn-primary" : "btn"}
                  style={{ fontSize: 10, padding: "2px 10px", background: choices.get(hunk.id) === "old" ? "var(--error)" : undefined, border: choices.get(hunk.id) === "old" ? "none" : undefined, color: choices.get(hunk.id) === "old" ? "#fff" : undefined }}
                  onClick={() => toggle(hunk.id)}
                >
                  {choices.get(hunk.id) === "old" ? "✓ 保留原文" : "保留原文"}
                </button>
                <button
                  className={choices.get(hunk.id) === "new" ? "btn-primary" : "btn"}
                  style={{ fontSize: 10, padding: "2px 10px", background: choices.get(hunk.id) === "new" ? "var(--jade)" : undefined, border: choices.get(hunk.id) === "new" ? "none" : undefined, color: choices.get(hunk.id) === "new" ? "#fff" : undefined }}
                  onClick={() => toggle(hunk.id)}
                >
                  {choices.get(hunk.id) === "new" ? "✓ 使用 AI" : "使用 AI"}
                </button>
              </div>
            )}
            {hunk.lines.map((line, li) => {
              const dimmed = hunk.hasChanges && (
                (line.type === "removed" && choices.get(hunk.id) === "new") ||
                (line.type === "added" && choices.get(hunk.id) === "old")
              );
              return (
                <div key={li} style={{
                  padding: "1px 28px",
                  background: line.type === "removed" ? "rgba(255,100,100,0.1)"
                    : line.type === "added" ? "rgba(52,168,83,0.1)"
                    : "transparent",
                  opacity: dimmed ? 0.35 : 1,
                  display: "flex", gap: 8,
                  textDecoration: dimmed ? "line-through" : "none",
                }}>
                  <span style={{ width: 20, textAlign: "right", color: "var(--text-disabled)", flexShrink: 0, userSelect: "none" }}>
                    {line.type === "removed" ? line.oldLineNum : line.type === "added" ? "" : line.oldLineNum}
                  </span>
                  <span style={{ width: 20, textAlign: "right", color: "var(--text-disabled)", flexShrink: 0, userSelect: "none" }}>
                    {line.type === "added" ? line.newLineNum : line.type === "removed" ? "" : line.newLineNum}
                  </span>
                  <span style={{
                    width: 16, textAlign: "center", flexShrink: 0, userSelect: "none", fontWeight: 700,
                    color: line.type === "removed" ? "var(--error)" : line.type === "added" ? "var(--jade)" : "transparent",
                  }}>
                    {line.type === "removed" ? "-" : line.type === "added" ? "+" : " "}
                  </span>
                  <span style={{ flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                    {line.text || "\u00A0"}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

interface EvalCategory {
  id: string;
  name: string;
  score: number;
  max_score: number;
  rationale: string;
  findings: string[];
}

const EVAL_CATEGORY_ICONS: Record<string, string> = {
  slop_detection: "\u25C9",
  repetition: "\u21BB",
  narrative_consistency: "\u2261",
  foreshadowing: "\u2234",
  literary_quality: "\u270E",
  llm_evaluation: "\u2605",
};

function ScoreDots({ score, max }: { score: number; max: number }) {
  return (
    <span style={{ letterSpacing: 2, fontSize: 14 }}>
      {Array.from({ length: max }, (_, i) => (
        <span key={i} style={{ color: i < score ? "var(--accent)" : "var(--border)" }}>{i < score ? "●" : "○"}</span>
      ))}
    </span>
  );
}

/** 评估 tab — evaluate the current chapter text on demand (no need to
 *  generate first), or show the result from a pipeline run. */
function EvalTab({ result, chapterContent, projectId, chapterId, manifest, skillSelection, ragExcludes, onToggleSkill, onToggleRagItem, onRefreshManifest }: {
  result: EvalResult | null; chapterContent: string; projectId: string; chapterId: string;
  manifest: ContextManifest | null; skillSelection: Record<string, boolean>; ragExcludes: Set<string>;
  onToggleSkill: (name: string) => void; onToggleRagItem: (key: string) => void;
  onRefreshManifest?: () => void;
}) {
  const { toast } = useToast();
  const [localResult, setLocalResult] = useState<EvalResult | null>(null);
  const [evaluating, setEvaluating] = useState(false);
  const displayResult = localResult || result;
  const evalBody = () => ({
    project_id: projectId, chapter_id: chapterId, rag_excludes: Array.from(ragExcludes),
  });

  const runEval = async () => {
    const text = (chapterContent || "").trim();
    if (!text) { toast("当前章节没有正文可评估", "error"); return; }
    setEvaluating(true);
    try {
      const resp = await apiPost<{ evaluation: EvalResult }>("/api/generation/evaluate", { text, ...evalBody() });
      if (resp.evaluation) setLocalResult(resp.evaluation);
      else toast("评估未返回结果", "error");
    } catch (e: any) {
      toast(e?.message || "评估失败，请检查模型连接", "error");
    } finally { setEvaluating(false); }
  };

  const applyPastedEval = (raw: string) => {
    let s = (raw || "").trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
    const a = s.indexOf("{"), b = s.lastIndexOf("}");
    if (a >= 0 && b > a) s = s.slice(a, b + 1);
    try {
      setLocalResult(JSON.parse(s) as EvalResult);
      toast("已应用网页 LLM 的评估结果", "success");
    } catch {
      toast("无法解析评估 JSON，请检查粘贴的内容", "error");
    }
  };

  return (
    <div>
      <ContextPanel
        manifest={manifest} skillSelection={skillSelection} ragExcludes={ragExcludes}
        onToggleSkill={onToggleSkill} onToggleRagItem={onToggleRagItem}
        onRefresh={onRefreshManifest}
      />
      <div style={{ marginBottom: 12 }}>
        <button className="btn-primary" style={{ width: "100%" }} onClick={runEval} disabled={evaluating}>
          {evaluating ? "评估中..." : "评估当前正文"}
        </button>
      </div>
      <div style={{ marginBottom: 12 }}>
        <WebLLMPromptPanel
          title="AI大模型网页版"
          fetchPrompt={async () => {
            const text = (chapterContent || "").trim();
            if (!text) throw new Error("当前章节没有正文可评估");
            const r = await apiPost<{ prompt: string }>("/api/generation/evaluate", { text, prompt_only: true, ...evalBody() });
            return r.prompt || "";
          }}
          onApplyResult={applyPastedEval}
          applyLabel="应用评估结果"
          resultPlaceholder="把网页 LLM 返回的评估 JSON 粘贴到这里"
        />
      </div>
      {displayResult
        ? <EvalResultView result={displayResult} />
        : (
          <div className="empty-state" style={{ padding: "24px 16px" }}>
            <h4>暂无评估结果</h4>
            <p>点击「评估当前正文」评估编辑器中的章节。</p>
          </div>
        )}
    </div>
  );
}

/** Renders one evaluation result — dimension scores / categories / issues. */
function EvalResultView({ result }: { result: EvalResult }) {
  const [expandedCat, setExpandedCat] = useState<string | null>(null);
  const displayResult = result;

  // Use EvalReport component when dimension_scores are available
  const hasDimensionScores = displayResult.dimension_scores && Object.keys(displayResult.dimension_scores).length > 0;
  if (hasDimensionScores) {
    return (
      <div>
        <EvalReport result={{
          score: displayResult.score,
          passed: displayResult.passed,
          summary_text: displayResult.summary_text || displayResult.summary || "",
          dimension_scores: displayResult.dimension_scores,
          issues: displayResult.issues,
          strengths: displayResult.strengths,
          process_log: displayResult.process_log,
        }} />
      </div>
    );
  }

  const summary = displayResult.summary || "";
  const strengths = displayResult.strengths || [];
  const categories: EvalCategory[] = (displayResult as any).categories || [];

  // Build categories from process steps if backend doesn't provide them
  const displayCategories: EvalCategory[] = categories.length > 0 ? categories : (() => {
    const cats: EvalCategory[] = [];
    const processSteps = displayResult.process || [];
    const issues = displayResult.issues || [];
    // Group issues by type
    const slopIssues = issues.filter(i => i.type === "ai_flavor");
    const repIssues = issues.filter(i => i.type === "repetition");
    const otherIssues = issues.filter(i => i.type !== "ai_flavor" && i.type !== "repetition");

    const slopScore = Math.max(0, 5 - slopIssues.length);
    cats.push({
      id: "slop_detection", name: `AI味检测 (Slop) — AI率 ${Math.round((1 - slopScore / 5) * 100)}%`,
      score: slopScore,
      max_score: 5,
      rationale: slopIssues.length > 0
        ? `检测到 ${slopIssues.length} 处AI常见表达模式，如固定句式、空洞修饰等。这些表达可能让读者感到不够自然。AI率越低越好。`
        : "未发现明显AI痕迹，表达自然流畅。AI率 0%，达标。",
      findings: slopIssues.map(i => i.description),
    });
    cats.push({
      id: "repetition", name: "重复检测",
      score: Math.max(0, 5 - repIssues.length),
      max_score: 5,
      rationale: repIssues.length > 0
        ? `发现 ${repIssues.length} 处重复表达，包括句首重复、短语重复等。建议使用同义词替换增加表达多样性。`
        : "句式和用词变化丰富，未发现明显重复问题。",
      findings: repIssues.map(i => i.description),
    });

    // LLM evaluation from process steps
    const llmStep = processSteps.find(s => s.detector === "LLM Evaluator" && s.status === "done");
    if (llmStep) {
      cats.push({
        id: "narrative_consistency", name: "叙事一致性",
        score: Math.min(5, Math.round((llmStep.llm_score || 70) / 20)),
        max_score: 5,
        rationale: otherIssues.length > 0
          ? `发现 ${otherIssues.length} 处叙事问题：角色行为、情节逻辑或世界观设定方面的不一致。`
          : "角色行为与设定一致，情节逻辑通顺。",
        findings: otherIssues.filter(i => ["consistency", "character", "plot"].includes(i.type)).map(i => i.description),
      });
      cats.push({
        id: "foreshadowing", name: "伏笔一致性",
        score: Math.min(5, Math.round((llmStep.llm_score || 70) / 20)),
        max_score: 5,
        rationale: "伏笔线索与前文保持一致，暂无发现断裂或矛盾的伏笔线。",
        findings: otherIssues.filter(i => i.type === "foreshadowing").map(i => i.description),
      });
      cats.push({
        id: "literary_quality", name: "文学质量",
        score: Math.min(5, Math.round((llmStep.llm_score || 70) / 20)),
        max_score: 5,
        rationale: llmStep.detail || "语言质量评估完成。",
        findings: (llmStep.findings || []).slice(0, 5),
      });
      cats.push({
        id: "llm_evaluation", name: "LLM 深度评估",
        score: Math.min(5, Math.round((llmStep.llm_score || 70) / 20)),
        max_score: 5,
        rationale: summary || llmStep.detail || "LLM 综合评估完成。",
        findings: strengths.map(s => `+ ${s}`),
      });
    }

    return cats;
  })();

  const totalScore = displayResult.score;
  const scoreColor = totalScore >= 80 ? "var(--jade)" : totalScore >= 60 ? "var(--gold)" : "var(--error)";

  // Compute AI率 from slop issues
  const slopCount = displayResult.issues.filter(i => i.type === "ai_flavor").length;
  const aiRate = Math.min(100, slopCount * 20);
  const aiRateColor = aiRate <= 20 ? "var(--jade)" : aiRate <= 50 ? "var(--gold)" : "var(--error)";

  return (
    <div>
      {/* Score header with AI率 */}
      <div style={{ marginBottom: 20 }}>
        <div className="flex items-center gap-12 mb-8">
          <div style={{
            width: 56, height: 56, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 20, fontWeight: 700, fontFamily: "var(--font-mono)",
            background: `${scoreColor}15`, color: scoreColor,
            border: `3px solid ${scoreColor}`,
          }}>{totalScore}</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 600, fontSize: 15, color: "var(--text-primary)", marginBottom: 4 }}>
              {totalScore >= 80 ? "质量优秀" : totalScore >= 60 ? "基本通过" : "需要修改"}
            </div>
            <div style={{ height: 6, borderRadius: 3, background: "var(--border)", overflow: "hidden" }}>
              <div style={{ height: "100%", width: `${totalScore}%`, borderRadius: 3, background: scoreColor, transition: "width 0.5s ease" }} />
            </div>
            <div className="text-xs text-muted" style={{ marginTop: 4 }}>
              {displayResult.issues.length} 个问题 · {strengths.length} 个优点
            </div>
          </div>
        </div>
        {/* AI率 indicator */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", background: "var(--bg-surface-2)", borderRadius: 8, borderLeft: `3px solid ${aiRateColor}` }}>
          <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>AI率</span>
          <div style={{ flex: 1, height: 6, borderRadius: 3, background: "var(--border)", overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${aiRate}%`, borderRadius: 3, background: aiRateColor, transition: "width 0.5s ease" }} />
          </div>
          <span style={{ fontSize: 13, fontWeight: 700, fontFamily: "var(--font-mono)", color: aiRateColor }}>{aiRate}%</span>
          <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{aiRate <= 20 ? "自然" : aiRate <= 50 ? "可接受" : "AI味重"}</span>
        </div>
      </div>

      {/* Summary */}
      {summary && (
        <div style={{ padding: "12px 14px", background: "var(--bg-surface-2)", borderRadius: 8, marginBottom: 16, borderLeft: `3px solid ${scoreColor}` }}>
          <div className="text-sm" style={{ color: "var(--text-secondary)", lineHeight: 1.7 }}>{summary}</div>
        </div>
      )}

      {/* Category cards */}
      <div className="label mb-8" style={{ fontSize: 11, color: "var(--text-tertiary)", letterSpacing: 1 }}>评估维度</div>
      {displayCategories.map(cat => {
        const icon = EVAL_CATEGORY_ICONS[cat.id] || "\u25A3";
        const catColor = cat.score >= 4 ? "var(--jade)" : cat.score >= 3 ? "var(--gold)" : "var(--error)";
        const isExpanded = expandedCat === cat.id;
        return (
          <div key={cat.id} style={{
            padding: "12px 14px", background: "var(--bg-surface-2)", borderRadius: 8, marginBottom: 8,
            borderLeft: `3px solid ${catColor}`, cursor: "pointer", transition: "all 0.15s",
          }}
          onClick={() => setExpandedCat(isExpanded ? null : cat.id)}
          >
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-6">
                <span style={{ fontSize: 16 }}>{icon}</span>
                <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary)" }}>{cat.name}</span>
              </div>
              <div className="flex items-center gap-8">
                <ScoreDots score={cat.score} max={cat.max_score} />
                <span className="font-mono text-xs" style={{ color: catColor, fontWeight: 600 }}>{cat.score}/{cat.max_score}</span>
              </div>
            </div>
            <div className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
              {cat.rationale}
            </div>
            {isExpanded && cat.findings.length > 0 && (
              <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--border)" }}>
                {cat.findings.map((f, fi) => (
                  <div key={fi} className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6, paddingLeft: 8, marginBottom: 2 }}>
                    {f.startsWith("+") ? <span style={{ color: "var(--jade)" }}>{f}</span> : <span>- {f}</span>}
                  </div>
                ))}
              </div>
            )}
            {cat.findings.length > 0 && (
              <div className="text-xs" style={{ color: "var(--text-tertiary)", marginTop: 4 }}>
                {isExpanded ? "点击收起" : `${cat.findings.length} 条详情，点击展开`}
              </div>
            )}
          </div>
        );
      })}

      {/* Strengths */}
      {strengths.length > 0 && (
        <div style={{ padding: "12px 14px", background: "var(--jade-subtle)", borderRadius: 8, marginTop: 12 }}>
          <div className="text-xs mb-6" style={{ fontWeight: 600, color: "var(--jade)", letterSpacing: 1 }}>优点</div>
          {strengths.map((s, i) => (
            <div key={i} className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.6, paddingLeft: 8 }}>+ {s}</div>
          ))}
        </div>
      )}

      {/* Issues summary */}
      {displayResult.issues.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="text-xs mb-6" style={{ fontWeight: 600, color: "var(--text-tertiary)", letterSpacing: 1 }}>问题汇总</div>
          {displayResult.issues.map((issue, i) => (
            <div key={i} style={{ padding: "8px 12px", background: "var(--bg-surface-2)", borderRadius: 6, marginBottom: 6,
              borderLeft: `3px solid ${issue.severity === "high" ? "var(--error)" : issue.severity === "medium" ? "var(--gold)" : "var(--text-tertiary)"}` }}>
              <div className="flex items-center gap-6 mb-2">
                <span style={{
                  fontSize: 10, padding: "1px 6px", borderRadius: 4, fontWeight: 600,
                  background: issue.severity === "high" ? "var(--accent-subtle)" : issue.severity === "medium" ? "var(--gold-subtle)" : "var(--bg-surface)",
                  color: issue.severity === "high" ? "var(--error)" : issue.severity === "medium" ? "var(--gold)" : "var(--text-tertiary)",
                }}>
                  {issue.severity === "high" ? "严重" : issue.severity === "medium" ? "中等" : "轻微"}
                </span>
                <span className="text-xs text-muted">{issue.type}</span>
              </div>
              <div className="text-xs" style={{ color: "var(--text-secondary)", lineHeight: 1.5 }}>{issue.description}</div>
              {issue.suggestion && <div className="text-xs" style={{ color: "var(--jade)", marginTop: 4 }}>建议：{issue.suggestion}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
