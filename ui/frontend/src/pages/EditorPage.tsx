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
import UniversalLLMDialog from "../components/shared/UniversalLLMDialog";

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
  // Writer = 网页大模型粘贴回写的「作家智能体」入口；视觉跟 Editor-Writer
  // 共用 jade，但保留独立 key, 避免 pipeline 进度条改名.
  "Writer": { bg: "var(--jade-subtle)", border: "var(--jade)", name: "作家智能体" },
  "Evaluator": { bg: "var(--accent-subtle)", border: "var(--accent)", name: "Evaluator" },
  "旁白": { bg: "var(--bg-surface-2)", border: "var(--text-secondary)", name: "旁白" },
  "User": { bg: "var(--purple-subtle)", border: "var(--purple)", name: "用户" },
  "System": { bg: "var(--bg-surface-2)", border: "var(--text-tertiary)", name: "系统" },
};

/** Normalize a paste from a web LLM into prose:
 *  - strip outer markdown code fences (```json ... ``` / ``` ... ```)
 *  - if the body parses as JSON, pull a text-bearing field
 *    (`text` / `content` / `chapter` / `body` / first long string value)
 *  - trim and collapse trailing whitespace
 *  Returns the original input verbatim if none of the above apply. */
function normalizeWebLLMReply(raw: string): string {
  let s = (raw || "").trim();
  if (!s) return "";
  // Peel one layer of triple-backtick fence if present.
  const fence = s.match(/^```(?:[a-zA-Z]+)?\s*\n([\s\S]*?)\n```\s*$/);
  if (fence) s = fence[1].trim();
  // If it parses as JSON, try to find prose inside.
  if (s.startsWith("{") || s.startsWith("[")) {
    try {
      const obj = JSON.parse(s);
      const pickProse = (o: any): string | null => {
        if (typeof o === "string") return o.length > 60 ? o : null;
        if (!o || typeof o !== "object") return null;
        for (const k of ["text", "content", "chapter", "body", "result", "output"]) {
          if (typeof o[k] === "string" && o[k].trim()) return o[k];
        }
        // Fall through: longest string value wins
        let best = "";
        for (const v of Object.values(o)) {
          if (typeof v === "string" && v.length > best.length) best = v;
        }
        return best || null;
      };
      const prose = pickProse(obj);
      if (prose) s = prose.trim();
    } catch { /* not JSON — keep raw */ }
  }
  return s.replace(/\s+$/g, "");
}

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
  /** Per-message token / cost snapshot — captured at send time on User
   *  messages, rendered as a small chip on the bubble. */
  tokenEstimate?: { inputK: number; llmCalls: number; usd: number };
  /** Progress payload for System status messages while a request is in
   *  flight: ETA in seconds + started-at epoch. The renderer draws an
   *  animated bar and a live "剩余 N 秒" countdown. */
  progress?: { etaSec: number; startedAt: number };
  /** Manual-mode paste-back card payload. When user sends an instruction
   *  while 手动模式 is ON, we push two messages: the User instruction
   *  and a System bubble carrying this payload — its renderer shows the
   *  snapshot prompt (copy button) + a textarea for the web-LLM reply +
   *  an "应用" button. Once applied, the textarea is replaced with a
   *  "✓ 已应用" summary and a Writer message is appended. */
  manualPaste?: { prompt: string; applied?: boolean; pastedLen?: number };
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

// Chapter-content save / existing_content loader 没有字数下限 — 用户
// 显式禁用了之前的 MIN_SAVE_CHARS 守卫，所以即使 0 字内容也会写入
// text_versions / chapter row。

export default function EditorPage({ projectId, onNavigate }: { projectId: string; onNavigate?: (tab: string) => void }) {
  const { toast } = useToast();
  const [volumes, setVolumes] = useState<LocalVolume[]>([]);
  const [activeChId, setActiveChId] = useState<string>("");
  const [content, setContent] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"saved" | "saving" | "unsaved">("saved");
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
  const [pipelinePaused, setPipelinePaused] = useState(false);
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
    if (!activeChId) {
      toast("没有可保存的章节", "error");
      return;
    }
    setSaveStatus("saving");
    const uv = volumes.map(v => ({ ...v, chapters: v.chapters.map(c => c.id === activeChId ? { ...c, content, title: titleVal || c.title, word_count: wc(content) } : c) }));
    try {
      await apiPut("/api/data/editor", { project_id: projectId || "default", volumes: uv });
      setVolumes(uv);
      persistedRef.current = { chId: activeChId, content, title: titleVal };
      setSaveStatus("saved");
      toast("已保存", "success");
    } catch (e: any) {
      setSaveStatus("unsaved");
      toast(e.message || "保存失败", "error");
    }
  }, [volumes, activeChId, content, titleVal, projectId, toast]);

  // Ctrl/Cmd + S: save the chapter outline AND commit a version snapshot so
  // the user has a stable rollback point per explicit save (not just the
  // 60s auto-version backup). Short content is skipped with a confirm to
  // keep the "saved 0-char text_versions" disaster from re-occurring.
  const handleSaveAndCommit = useCallback(async () => {
    if (!activeChId) {
      toast("没有可保存的章节", "error");
      return;
    }
    const chars = content.trim().length;
    // Persist the editor state first — same path as 自动保存 + manual button.
    await handleSaveOutline();
    if (chars === 0) return;        // nothing to snapshot
    const newVersion: TextVersion = {
      version_id: vuid(), chapter_id: activeChId,
      version: versionHistory.filter(v => v.chapter_id === activeChId).length + 1,
      source: "user_edited", text: content,
      synopsis: activeCh?.synopsis || "",
      created_at: new Date().toISOString(),
    };
    setVersionHistory(prev => [...prev, newVersion]);
    try {
      await apiPost("/api/data/versions", {
        project_id: projectId || "default", version: newVersion,
      });
      toast(`已保存并提交版本 v${newVersion.version}`, "success");
    } catch (e: any) {
      toast(`版本提交失败：${e?.message || ""}`, "error");
    }
  }, [activeChId, content, projectId, versionHistory, activeCh, handleSaveOutline, toast]);

  // Ctrl+S / Cmd+S triggers save + commit (snapshot a version) with toast
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        handleSaveAndCommit();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handleSaveAndCommit]);

  const handleMouseUp = () => {
    const el = textRef.current; if (!el) return;
    setTimeout(() => {
      const s = el.selectionStart, e = el.selectionEnd;
      if (s !== undefined && e !== undefined && e > s) { const txt = content.substring(s, e); if (txt.trim().length > 0) { setSelection({ start: s, end: e, text: txt }); return; } }
      setSelection(null);
    }, 10);
  };

  const addVolume = () => { setVolumes([...volumes, { id: uid(), project_id: projectId, title: `第${volumes.length + 1}卷`, order: volumes.length + 1, chapters: [], collapsed: false }]); };
  const addChapter = (volId: string) => { const vol = volumes.find(v => v.id === volId); if (!vol) return; const ch: ChapterOutline = { id: uid(), volume_id: volId, title: `第${vol.chapters.length + 1}章`, order: vol.chapters.length + 1, synopsis: "", content: "", word_count: 0 }; setVolumes(volumes.map(v => v.id === volId ? { ...v, chapters: [...v.chapters, ch] } : v)); setActiveChId(ch.id); };
  // 顶栏 "+章" 添加到当前章节所在卷（无激活章节时落到最后一卷）。
  // 之前固定写首卷，加上卷头自己又长一个 "+" 按钮 — 现在卷头按钮被移除,
  // 这条路径需要承担「按当前位置加章」的语义。
  const addChapterSmart = () => {
    if (volumes.length === 0) return;
    const target = activeVol ?? volumes[volumes.length - 1];
    addChapter(target.id);
  };
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

  // Read a .txt / .md file via showOpenFilePicker (with hidden input fallback).
  const pickTextFile = async (): Promise<string | null> => {
    const picker = (window as any).showOpenFilePicker;
    if (typeof picker === "function") {
      let handle: any;
      try {
        [handle] = await picker.call(window, {
          types: [{ description: "文本文件", accept: { "text/plain": [".txt", ".md", ".markdown"] } }],
          multiple: false,
        });
      } catch (e: any) {
        if (e?.name === "AbortError") return null;
        throw e;
      }
      const file = await handle.getFile();
      return await file.text();
    }
    return await new Promise<string | null>((resolve, reject) => {
      const input = document.createElement("input");
      input.type = "file";
      input.accept = ".txt,.md,.markdown,text/plain";
      input.onchange = async () => {
        const f = input.files?.[0];
        if (!f) { resolve(null); return; }
        try { resolve(await f.text()); } catch (e) { reject(e); }
      };
      input.click();
    });
  };

  // Split imported text on Chinese-novel chapter markers.
  // Matches "第X章 标题", "第X节 标题", "Chapter N ...", "Prologue/序章/楔子/尾声".
  // Returns null when no marker is found — the caller treats that as a single
  // chapter and dumps the whole file into one new entry.
  const parseImportedText = (text: string): Array<{ title: string; content: string }> | null => {
    const re = /^[ \t]*((?:第[一-鿿0-9零一二三四五六七八九十百千万]+[章节回][^\n]*)|(?:序章|楔子|尾声|后记|番外)[^\n]*|Chapter\s+\d+[^\n]*)\s*$/gmi;
    const matches: Array<{ title: string; index: number; length: number }> = [];
    for (const m of text.matchAll(re)) {
      if (m.index === undefined) continue;
      matches.push({ title: m[1].trim(), index: m.index, length: m[0].length });
    }
    if (matches.length === 0) return null;
    const result: Array<{ title: string; content: string }> = [];
    // Preamble before the first marker — only keep it if it contains content.
    const preamble = text.slice(0, matches[0].index).trim();
    if (preamble) result.push({ title: "导入前言", content: preamble });
    for (let i = 0; i < matches.length; i++) {
      const start = matches[i].index + matches[i].length;
      const end = i + 1 < matches.length ? matches[i + 1].index : text.length;
      result.push({
        title: matches[i].title.slice(0, 80),  // guard against runaway-long titles
        content: text.slice(start, end).trim(),
      });
    }
    return result;
  };

  const handleImport = async () => {
    let text: string | null = null;
    try { text = await pickTextFile(); }
    catch (e: any) { toast(e?.message || "读取文件失败", "error"); return; }
    if (text == null) return;             // user cancelled
    if (!text.trim()) { toast("文件为空", "error"); return; }

    // Land into the active chapter's volume (so importing while viewing
    // 第三卷·序章 appends there), falling back to the last volume.
    let targetVol = activeVol ?? volumes[volumes.length - 1] ?? null;
    if (!targetVol) {
      const nv: LocalVolume = {
        id: uid(), project_id: projectId,
        title: "导入卷", order: 1, chapters: [], collapsed: false,
      };
      setVolumes([nv]);
      targetVol = nv;
    }

    const parsed = parseImportedText(text);
    if (!parsed || parsed.length === 0) {
      const trimmed = text.trim();
      const ok = await confirm({
        message: `未识别到章节标记，将整个文件作为一章导入到「${targetVol.title}」？\n（共 ${wc(trimmed).toLocaleString()} 字）`,
      });
      if (!ok) return;
      const baseOrder = targetVol.chapters.length;
      const ch: ChapterOutline = {
        id: uid(), volume_id: targetVol.id,
        title: `导入章节 ${baseOrder + 1}`,
        order: baseOrder + 1,
        synopsis: "", content: trimmed, word_count: wc(trimmed),
      };
      setVolumes(volumes.map(v => v.id === targetVol!.id ? { ...v, chapters: [...v.chapters, ch] } : v));
      setActiveChId(ch.id);
      toast("已导入 1 章", "success");
      return;
    }

    const totalWords = parsed.reduce((s, p) => s + wc(p.content), 0);
    const ok = await confirm({
      message: `识别到 ${parsed.length} 个章节（共 ${totalWords.toLocaleString()} 字），添加到「${targetVol.title}」末尾？`,
    });
    if (!ok) return;
    const baseOrder = targetVol.chapters.length;
    const newChs: ChapterOutline[] = parsed.map((p, i) => ({
      id: uid(), volume_id: targetVol!.id,
      title: p.title || `导入章节 ${baseOrder + i + 1}`,
      order: baseOrder + i + 1,
      synopsis: "", content: p.content, word_count: wc(p.content),
    }));
    setVolumes(volumes.map(v => v.id === targetVol!.id ? { ...v, chapters: [...v.chapters, ...newChs] } : v));
    if (newChs[0]) setActiveChId(newChs[0].id);
    toast(`已导入 ${newChs.length} 章`, "success");
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
      case "paused":
        setPipelinePaused(true);
        setChatMessages(prev => [...prev, {
          agent: "System", content: "Pipeline 已暂停 — 已生成内容已保留，点击恢复继续。", status: "done", timestamp: Date.now(),
        }]);
        break;
      case "resumed":
        setPipelinePaused(false);
        setChatMessages(prev => [...prev, {
          agent: "System", content: "Pipeline 已恢复。", status: "done", timestamp: Date.now(),
        }]);
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

  /** Toggle a whole loader (all items). Used by the input-above
   *  per-message loader selector. When any item is currently included,
   *  the next press excludes all; otherwise it includes all. */
  const toggleRagLoader = useCallback((loaderKey: string, items: { id: string }[]) => {
    if (!items.length) return;
    setRagExcludes(prev => {
      const next = new Set(prev);
      const keys = items.map(it => `${loaderKey}::${it.id}`);
      const allExcluded = keys.every(k => next.has(k));
      if (allExcluded) keys.forEach(k => next.delete(k));
      else keys.forEach(k => next.add(k));
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

  /** Approximate token count for a CJK-heavy mixed-language prompt.
   *  - CJK (Han/CJK punct/Kana/Hangul): ≈ 1 token / 1.6 chars
   *  - ASCII text: ≈ 1 token / 4 chars
   *  - Other (digits/whitespace/etc.): ≈ 1 token / 3 chars
   *  Empirically within ±10% of OpenAI/Anthropic tokenizers for Chinese
   *  novels with English code/labels. Cheap O(n) scan, no extra dep. */
  const approxTokens = (text: string): number => {
    if (!text) return 0;
    let cjk = 0, ascii = 0, other = 0;
    for (let i = 0; i < text.length; i++) {
      const cp = text.codePointAt(i)!;
      // Skip the low surrogate of a surrogate pair if we counted the pair.
      if (cp > 0xffff) i++;
      if (
        (cp >= 0x4e00 && cp <= 0x9fff)     // CJK Unified
        || (cp >= 0x3000 && cp <= 0x303f)  // CJK Symbols/Punct
        || (cp >= 0xff00 && cp <= 0xffef)  // Fullwidth
        || (cp >= 0x3040 && cp <= 0x30ff)  // Kana
        || (cp >= 0xac00 && cp <= 0xd7af)  // Hangul
      ) {
        cjk++;
      } else if (cp >= 0x20 && cp <= 0x7e) {
        ascii++;
      } else {
        other++;
      }
    }
    return Math.round(cjk / 1.6 + ascii / 4 + other / 3);
  };
  /** Per-1K-input USD cost. Defaults are gpt-4o-mini-ish; we only use it
   *  to put a "$0.0xx" hint on the chip — true accounting still happens
   *  server-side. Override via /api/data/settings if needed (not wired
   *  here to keep the chip honest about how rough it is). */
  const PRICE_PER_1K_INPUT_USD = 0.0008;

  /** Fetch the actual rendered prompt for this chapter (single-agent
   *  mode) and derive a token / cost estimate from its real length. The
   *  rendered prompt is cached on the User message (`promptSent`) so
   *  the per-message "查看 / 修改 Prompt" modal can reuse it without
   *  another round-trip. */
  const fetchTokenEstimate = useCallback(async (): Promise<{
    inputK: number; tokens: number; llmCalls: number; usd: number; etaSec: number; prompt: string;
  } | null> => {
    if (!activeChId) return null;
    try {
      const prompt = await fetchGenPrompt();
      const tokens = approxTokens(prompt);
      // 5K tokens ≈ 1 s wall-clock as a coarse default. Cap so the bar
      // doesn't show "8 分钟" on a 50K prompt or "0 秒" on a tiny one.
      const eta = Math.max(8, Math.min(180, Math.round(tokens / 5000) + 12));
      return {
        inputK: Math.round(tokens / 1000),
        tokens,
        llmCalls: 1,
        usd: (tokens / 1000) * PRICE_PER_1K_INPUT_USD,
        etaSec: eta,
        prompt,
      };
    } catch { return null; }
  }, [activeChId, fetchGenPrompt]);
  /** Single-agent run.
   *  Flow (Claude-style chat):
   *    1. push User message (instruction or "按大纲创作本章") with
   *       token estimate snapshot
   *    2. push System "生成中" message with progress bar + ETA
   *    3. await /quick-generate
   *    4. on success: replace progress with "生成完成" status; append
   *       Writer (作家智能体) message with the chapter text
   *    5. on error: replace progress with "生成出错: …"
   *
   *  `instruction` is the user's typed instruction (becomes the User
   *  message content); empty → default "按大纲创作本章".
   *  `userPreSent` is true when the User message has already been
   *  pushed by sendChatMessage — runPlainAgent then skips re-adding it.
   */
  const runPlainAgent = useCallback(async (instruction = "", userPreSent = false) => {
    if (!activeCh) return;
    genModeRef.current = "single";
    setAiTab("single");
    setGenerating(true);
    setPipelineSteps([{ step: "Plain Agent", status: "running", detail: "单Agent直接生成中..." }]);

    const est = await fetchTokenEstimate();
    const userText = instruction.trim() || "按大纲创作本章";
    const startedAt = Date.now();
    const etaSec = est?.etaSec ?? 30;
    const progressMsg: ChatMessage = {
      agent: "System", content: "生成中",
      status: "done", timestamp: Date.now() + 1,
      progress: { etaSec, startedAt },
    };
    setChatMessages(prev => {
      const next = userPreSent
        ? prev.map((m, i) => {
            // Backfill prompt + token estimate on the User msg that
            // sendChatMessage just pushed (so the message bubble's
            // "查看 Prompt" modal and the token chip both have data).
            if (i !== prev.length - 1 || m.agent !== "User") return m;
            return {
              ...m,
              promptSent: m.promptSent ?? est?.prompt,
              tokenEstimate: m.tokenEstimate ?? (est
                ? { inputK: est.inputK, llmCalls: est.llmCalls, usd: est.usd }
                : undefined),
            };
          })
        : [...prev, {
            agent: "User" as const, content: userText, status: "done" as const,
            timestamp: Date.now(),
            promptSent: est?.prompt,
            tokenEstimate: est ? { inputK: est.inputK, llmCalls: est.llmCalls, usd: est.usd } : undefined,
          }];
      next.push(progressMsg);
      return next;
    });
    generatedTextRef.current = "";

    try {
      const payload: any = { ...buildGenPayload() };
      // Surface the chat-typed 指令 to this 生成请求. Persisted
      // chapter.special_requirements (if any) prefixed; the chat input
      // becomes the "本次补充" tail. We do NOT write back to the chapter row.
      if (instruction.trim()) {
        const base = ((activeCh as any)?.special_requirements || "").trim();
        payload.special_requirements = base
          ? `${base}\n[本次补充] ${instruction.trim()}`
          : instruction.trim();
      }
      const resp = await apiPost<{ text: string; model: string; tokens?: any; skills_used?: string[] }>(
        "/api/generation/quick-generate", payload,
      );
      generatedTextRef.current = resp.text;
      setPipelineSteps([{ step: "Plain Agent", status: "done", detail: "已完成", progress: 100 }]);
      setChatMessages(prev => {
        // Replace the in-flight progress System message with a completion
        // status, then append the Writer agent's chapter text.
        const out: ChatMessage[] = prev.map(m =>
          m.progress
            ? { ...m, progress: undefined, content: `生成完成 · ${resp.text.length} 字 · ${resp.model}${resp.tokens ? ` (${resp.tokens.input}+${resp.tokens.output} tk)` : ""}` }
            : m,
        );
        out.push({ agent: "Writer", content: resp.text, status: "done", timestamp: Date.now() });
        return out;
      });
    } catch (e: any) {
      setPipelineSteps([{ step: "Plain Agent", status: "done", detail: "出错" }]);
      setChatMessages(prev => prev.map(m =>
        m.progress
          ? { ...m, progress: undefined, content: `生成出错：${e?.message || "请检查模型连接"}` }
          : m,
      ));
    }
    setGenerating(false);
    setCurrentAgent(null);
  }, [activeCh, projectId, activeChId, buildGenPayload, fetchTokenEstimate]);

  /** Apply a web-LLM-generated chapter the user pasted back. */
  const applyPlainPaste = useCallback((text: string) => {
    const t = normalizeWebLLMReply(text);
    if (!t) return;
    generatedTextRef.current = t;
    setPipelineSteps([{ step: "Plain Agent", status: "done", detail: "已解析网页结果", progress: 100 }]);
    const wc_ = wc(t);
    setChatMessages(prev => [
      ...prev,
      {
        agent: "System",
        content: `已收到网页大模型的回复（${wc_.toLocaleString()} 字），由作家智能体接管展示。可在下方「写入编辑器」。`,
        status: "done", timestamp: Date.now(),
      },
      { agent: "Writer", content: t, status: "done", timestamp: Date.now() },
    ]);
    setGenerating(false);
  }, []);

  /** Apply a paste from a specific in-chat ManualPaste card. Marks that
   *  card as applied (so its textarea collapses to "✓ 已应用 · N 字"),
   *  then appends the Writer message right after it so the conversation
   *  reads top-down: User → ManualPaste card (applied) → Writer.
   *  Different from applyPlainPaste, which appends to the END regardless
   *  of where the user clicked. */
  const applyInChatManualPaste = useCallback((msgIdx: number, text: string) => {
    const t = normalizeWebLLMReply(text);
    if (!t) return;
    generatedTextRef.current = t;
    setChatMessages(prev => {
      const target = prev[msgIdx];
      if (!target?.manualPaste) return prev;
      const before = prev.slice(0, msgIdx);
      const after = prev.slice(msgIdx + 1);
      const updatedPasteCard: ChatMessage = {
        ...target,
        manualPaste: { ...target.manualPaste, applied: true, pastedLen: t.length },
      };
      const writerMsg: ChatMessage = {
        agent: "Writer", content: t, status: "done", timestamp: Date.now(),
      };
      return [...before, updatedPasteCard, writerMsg, ...after];
    });
  }, []);

  // Web-LLM dialog (mirror of MarketFeatureExtraction's UniversalLLMDialog flow):
  // user clicks "网页大模型创作" → dialog opens with the live single-agent
  // prompt → user copies / pastes / commits → reply lands in the chat as
  // "作家智能体" via applyPlainPaste.
  const [webLLMOpen, setWebLLMOpen] = useState(false);
  const [webLLMPrompt, setWebLLMPrompt] = useState("");
  const openWebLLMDialog = useCallback(async () => {
    try {
      const p = await fetchGenPrompt();
      setWebLLMPrompt(p || "");
      setWebLLMOpen(true);
    } catch (e: any) {
      toast(e?.message || "获取 prompt 失败", "error");
    }
  }, [fetchGenPrompt, toast]);

  // Per-message prompt editor modal — User messages carry the rendered
  // prompt as promptSent; clicking "查看 / 修改 Prompt" opens this with
  // an editable copy. Submitting re-runs single-agent generation but
  // passes system_hint = edited prompt so the backend skips RAG
  // assembly and uses the verbatim string.
  const [promptEditOpen, setPromptEditOpen] = useState(false);
  const [promptEditOriginal, setPromptEditOriginal] = useState("");
  const [promptEditDraft, setPromptEditDraft] = useState("");
  const openPromptEdit = useCallback((msgIdx: number) => {
    const msg = chatMessages[msgIdx];
    if (!msg?.promptSent) {
      toast("此消息没有缓存 prompt（旧消息或来源不同）", "error");
      return;
    }
    setPromptEditOriginal(msg.promptSent);
    setPromptEditDraft(msg.promptSent);
    setPromptEditOpen(true);
  }, [chatMessages, toast]);
  const submitEditedPrompt = useCallback(async () => {
    if (!activeCh) return;
    const editedPrompt = promptEditDraft.trim();
    if (!editedPrompt) { toast("Prompt 不能为空", "error"); return; }
    setPromptEditOpen(false);
    genModeRef.current = "single";
    setAiTab("single");
    setGenerating(true);
    setPipelineSteps([{ step: "Plain Agent", status: "running", detail: "按编辑后的 prompt 重新生成..." }]);
    const tokens = approxTokens(editedPrompt);
    const inputK = Math.round(tokens / 1000);
    const eta = Math.max(8, Math.min(180, Math.round(tokens / 5000) + 12));
    const startedAt = Date.now();
    setChatMessages(prev => [
      ...prev,
      {
        agent: "User", content: "（按编辑后的 prompt 重新创作）", status: "done",
        timestamp: Date.now(),
        promptSent: editedPrompt,
        tokenEstimate: { inputK, llmCalls: 1, usd: (tokens / 1000) * PRICE_PER_1K_INPUT_USD },
      },
      {
        agent: "System", content: "生成中", status: "done",
        timestamp: Date.now() + 1,
        progress: { etaSec: eta, startedAt },
      },
    ]);
    try {
      const payload: any = { ...buildGenPayload() };
      // system_hint = edited prompt -> backend chat-mode branch:
      // system_prompt = edited, user_content = synopsis. Empty synopsis
      // means the LLM only sees our verbatim prompt.
      payload.system_hint = editedPrompt;
      payload.synopsis = "";
      const resp = await apiPost<{ text: string; model: string; tokens?: any }>(
        "/api/generation/quick-generate", payload,
      );
      generatedTextRef.current = resp.text;
      setPipelineSteps([{ step: "Plain Agent", status: "done", detail: "已完成", progress: 100 }]);
      setChatMessages(prev => {
        const out: ChatMessage[] = prev.map(m =>
          m.progress
            ? { ...m, progress: undefined, content: `生成完成 · ${resp.text.length} 字 · ${resp.model}${resp.tokens ? ` (${resp.tokens.input}+${resp.tokens.output} tk)` : ""}` }
            : m,
        );
        out.push({ agent: "Writer", content: resp.text, status: "done", timestamp: Date.now() });
        return out;
      });
    } catch (e: any) {
      setChatMessages(prev => prev.map(m =>
        m.progress
          ? { ...m, progress: undefined, content: `生成出错：${e?.message || "请检查模型连接"}` }
          : m,
      ));
    }
    setGenerating(false);
  }, [activeCh, promptEditDraft, buildGenPayload, toast]);

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
    setPipelinePaused(false);
    setWaitingForConfirm(false);
    setCurrentAgent(null);
    setChatMessages(prev => [...prev, { agent: "System", content: "Pipeline 已被手动终止。", status: "done", timestamp: Date.now(), _stopped: true } as any]);
  }, [SESS_KEY]);

  // LLM交互·机制6: 暂停/恢复 — pipeline 在下一个步骤边界停住，
  // 进行中的 LLM 调用先完成、结果保留。
  const handlePauseResume = useCallback(() => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    const action = pipelinePaused ? "resume" : "pause";
    apiPost(`/api/generation/${action}/${sid}`, {})
      .then(() => setPipelinePaused(!pipelinePaused))
      .catch((e) => toast(e.message || "操作失败", "error"));
  }, [pipelinePaused]);

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

  // Manual-mode toggle: when ON, sendChatMessage opens UniversalLLMDialog
  // (网页大模型) instead of calling /quick-generate.
  const [manualMode, setManualMode] = useState(false);
  const sendChatMessage = async () => {
    const msg = chatInput.trim();
    // single mode allows empty-send → "按大纲创作本章" default;
    // cluster / waitingForConfirm still requires text.
    const singleIdle = aiTab === "single" && !generating && !waitingForConfirm;
    if (!msg && !singleIdle) return;
    setChatInput("");

    if (waitingForConfirm && sessionIdRef.current) {
      // Pipeline confirm flow keeps the old semantics.
      setChatMessages(prev => [...prev, {
        agent: "User", content: msg, status: "done", timestamp: Date.now(),
      }]);
      setWaitingForConfirm(false);
      apiPost(`/api/generation/confirm/${sessionIdRef.current}`, { action: "continue", message: msg }).catch((e) => toast(e.message || "操作失败", "error"));
      return;
    }

    if (singleIdle) {
      const userText = msg || "按大纲创作本章";
      if (manualMode) {
        // 手动模式: push User instruction + 把 paste-back UI 嵌进对话框
        // (作为一条 manualPaste system 消息). 不走 API. 用户在 chat
        // 里复制 prompt → 网页 LLM → 粘贴回复 → 应用 → Writer 消息.
        const est = await fetchTokenEstimate();
        const ts = Date.now();
        setChatMessages(prev => [...prev,
          {
            agent: "User", content: userText, status: "done", timestamp: ts,
            promptSent: est?.prompt,
            tokenEstimate: est
              ? { inputK: est.inputK, llmCalls: est.llmCalls, usd: est.usd }
              : undefined,
          },
          {
            agent: "System", content: "", status: "done", timestamp: ts + 1,
            manualPaste: { prompt: est?.prompt || "", applied: false },
          },
        ]);
      } else {
        // 内置 API 创作: runPlainAgent owns 推 user msg + 进度条 +
        // 结果 — sendChatMessage 只先推一条空 user msg 给 UI 即时反馈,
        // runPlainAgent 收尾时再 backfill prompt / token chip.
        setChatMessages(prev => [...prev, {
          agent: "User", content: userText, status: "done", timestamp: Date.now(),
        }]);
        runPlainAgent(msg, true);
      }
      return;
    }

    // Cluster mode / generating: keep prior behavior — pure chat message,
    // no generation trigger.
    setChatMessages(prev => [...prev, {
      agent: "User", content: msg, status: "done", timestamp: Date.now(),
    }]);
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
          <div style={{ padding: "4px 14px 6px", display: "flex", flexDirection: "column", gap: 6 }}>
            {/* Row 1: 新增 */}
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn-icon" onClick={addVolume} style={{ fontSize: 12, flex: 1, padding: "4px 0", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}>+卷</button>
              <button className="btn-icon" onClick={addChapterSmart} title={activeVol ? `在 ${activeVol.title} 末尾新增章节` : "在最后一卷末尾新增章节"} style={{ fontSize: 12, flex: 1, padding: "4px 0", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}>+章</button>
            </div>
            {/* Row 2: 导入 / 导出 — 同级主操作 */}
            <div style={{ display: "flex", gap: 6 }}>
              <button className="btn-icon" onClick={handleImport} title="从 .txt / .md 文件导入章节，自动按「第X章」切分" style={{ fontSize: 12, flex: 1, padding: "5px 0", border: "1px solid var(--indigo, var(--border))", borderRadius: "var(--radius-sm)", color: "var(--indigo, var(--text-secondary))", fontWeight: 600 }}>导入</button>
              <button className="btn-icon" onClick={handleBundleExport} title="导出角色+世界书+章节大纲，可自选保存位置" style={{ fontSize: 12, flex: 1, padding: "5px 0", border: "1px solid var(--indigo, var(--border))", borderRadius: "var(--radius-sm)", color: "var(--indigo, var(--text-secondary))", fontWeight: 600 }}>导出</button>
            </div>
            {/* Row 3: 批量 — 副入口，比主操作小一号 */}
            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button
                className="btn-icon"
                onClick={() => (batchMode ? exitBatchMode() : setBatchMode(true))}
                title="批量选择章节后删除 / 导出"
                style={{
                  fontSize: 10,
                  padding: "2px 10px",
                  border: `1px solid ${batchMode ? "var(--jade)" : "var(--border)"}`,
                  borderRadius: "var(--radius-sm)",
                  color: batchMode ? "#fff" : "var(--text-tertiary)",
                  background: batchMode ? "var(--jade)" : "transparent",
                  height: "auto",
                  width: "auto",
                }}
              >
                {batchMode ? "退出批量" : "批量操作"}
              </button>
            </div>
          </div>
          {batchMode && (
            <div style={{
              margin: "4px 10px 8px",
              padding: 10,
              background: "var(--bg-surface-2)",
              border: "1px solid var(--border)",
              borderLeft: "3px solid var(--jade)",
              borderRadius: "var(--radius-sm)",
              fontSize: 12,
              display: "flex",
              flexDirection: "column",
              gap: 8,
            }}>
              {/* Status row: count badge + close */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{
                    display: "inline-flex",
                    alignItems: "center",
                    justifyContent: "center",
                    minWidth: 22,
                    height: 20,
                    padding: "0 6px",
                    borderRadius: 10,
                    background: selectedChIds.size > 0 ? "var(--jade)" : "var(--bg-surface)",
                    color: selectedChIds.size > 0 ? "#fff" : "var(--text-tertiary)",
                    fontSize: 11,
                    fontWeight: 700,
                    lineHeight: 1,
                  }}>{selectedChIds.size}</span>
                  <span style={{ color: "var(--text-secondary)", fontSize: 11 }}>已选章节</span>
                </div>
                <button
                  onClick={exitBatchMode}
                  title="退出批量模式"
                  style={{
                    width: 22, height: 22,
                    padding: 0,
                    background: "transparent",
                    border: "none",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--text-tertiary)",
                    fontSize: 16,
                    lineHeight: 1,
                    cursor: "pointer",
                  }}
                >×</button>
              </div>
              {/* Selection chips */}
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  onClick={selectAllChapters}
                  style={{
                    flex: 1,
                    padding: "5px 0",
                    background: "var(--bg-surface)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--text-secondary)",
                    fontSize: 11,
                    cursor: "pointer",
                  }}
                >全选</button>
                <button
                  onClick={() => setSelectedChIds(new Set())}
                  disabled={selectedChIds.size === 0}
                  style={{
                    flex: 1,
                    padding: "5px 0",
                    background: "var(--bg-surface)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-sm)",
                    color: selectedChIds.size === 0 ? "var(--text-disabled)" : "var(--text-secondary)",
                    fontSize: 11,
                    cursor: selectedChIds.size === 0 ? "not-allowed" : "pointer",
                  }}
                >清空</button>
              </div>
              {/* Primary actions */}
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  onClick={exportSelectedChapters}
                  disabled={selectedChIds.size === 0}
                  title="将选中章节导出为 .txt 文件"
                  style={{
                    flex: 1,
                    padding: "7px 0",
                    background: selectedChIds.size === 0 ? "transparent" : "var(--indigo-subtle, transparent)",
                    border: `1px solid ${selectedChIds.size === 0 ? "var(--border)" : "var(--indigo)"}`,
                    borderRadius: "var(--radius-sm)",
                    color: selectedChIds.size === 0 ? "var(--text-disabled)" : "var(--indigo)",
                    fontSize: 12,
                    fontWeight: 600,
                    cursor: selectedChIds.size === 0 ? "not-allowed" : "pointer",
                  }}
                >导出</button>
                <button
                  onClick={deleteSelectedChapters}
                  disabled={selectedChIds.size === 0}
                  title="删除选中章节（不可撤销）"
                  style={{
                    flex: 1,
                    padding: "7px 0",
                    background: selectedChIds.size === 0 ? "transparent" : "var(--error)",
                    border: `1px solid var(--error)`,
                    borderRadius: "var(--radius-sm)",
                    color: selectedChIds.size === 0 ? "var(--text-disabled)" : "#fff",
                    fontSize: 12,
                    fontWeight: 600,
                    cursor: selectedChIds.size === 0 ? "not-allowed" : "pointer",
                  }}
                >删除</button>
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
            <div className="flex items-center gap-8 text-xs">
              <span style={{ color: saveStatus === "saved" ? "var(--jade)" : saveStatus === "saving" ? "var(--gold)" : "var(--text-tertiary)" }}>
                {saveStatus === "saved" ? "已保存" : saveStatus === "saving" ? "保存中..." : "未保存"}
              </span>
              <button
                className="btn-primary"
                style={{ fontSize: 11, padding: "3px 12px" }}
                onClick={handleSaveAndCommit}
                disabled={!activeChId || saveStatus === "saving"}
                title="保存并提交版本快照">
                保存
              </button>
            </div>
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
          <div className="tab-bar-underline" style={{ flexShrink: 0, width: "100%" }}>
            {/* 多智能体（cluster / 导演模式）pipeline 已暂时下线，留给下一阶段
                重做；当前 tab 仅保留：RAG / 单智能体 / 重写 / 评估。
                flex:1 + textAlign:center 让 4 个 tab 平分整行宽度。 */}
            {([["outline", "RAG"], ["single", "智能体创作"], ["rewrite", "重写"], ["eval", "评估"]] as const).map(([key, label]) => (
              <button
                key={key}
                className={`tab-item ${aiTab === key ? "active" : ""}`}
                onClick={() => setAiTab(key)}
                style={{ flex: 1, textAlign: "center", padding: "10px 8px", minWidth: 0 }}
              >{label}</button>
            ))}
          </div>
          <div className="panel-body" style={{ padding: "14px 16px" }}>
            {aiTab === "outline" && <OutlineTab synopsis={activeCh?.synopsis || ""} onChange={updateSynopsis} onSave={handleSaveOutline}
              onStartGeneration={() => { setAiTab("single"); setTimeout(() => { if (!generating) runPlainAgent(); }, 300); }} projectId={projectId}
              chapter={activeCh}
              chapterNum={chapterNum}
              allChapters={volumes.flatMap(v => (v.chapters || []).map(c => ({ id: c.id, title: c.title })))}
              onUpdateChapter={(field, value) => {
                setVolumes(prev => prev.map(v => ({ ...v, chapters: v.chapters.map(c => c.id === activeChId ? { ...c, [field]: value } : c) })));
              }}
              onNavigate={onNavigate} />}
            {(aiTab === "single" || aiTab === "cluster") && <InspireTab mode={aiTab} steps={pipelineSteps} generating={generating} onStart={startGeneration} onStartPlain={runPlainAgent} chatMessages={chatMessages} chatInput={chatInput}
              onChatInputChange={setChatInput} onSendMessage={sendChatMessage} waitingForConfirm={waitingForConfirm} onConfirmContinue={handleConfirmContinue} onRollback={handleRollback} onWriteToEditor={handleWriteToEditor} onStopPipeline={handleStopPipeline}
              paused={pipelinePaused} onPauseResume={handlePauseResume} projectId={projectId} chapterId={activeChId} chapterNum={chapterNum}
              modelChanged={modelChanged} onDismissModelChange={() => setModelChanged(false)} onRestartWithNewModel={() => { setModelChanged(false); handleStopPipeline(); setTimeout(() => startGeneration(), 500); }}
              onFetchPrompt={fetchGenPrompt}
              onApplyPaste={applyPlainPaste}
              onApplyManualResult={applyPlainPaste}
              onApplyInChatManualPaste={applyInChatManualPaste}
              onOpenWebLLM={openWebLLMDialog}
              manualMode={manualMode} onToggleManualMode={() => setManualMode(v => !v)}
              manualPrompt={manualPrompt} onSubmitManual={submitManualResult}
              manifest={manifest} skillSelection={skillSelection} onToggleSkill={toggleSkill}
              ragExcludes={ragExcludes} onToggleRagItem={toggleRagItem} onToggleRagLoader={toggleRagLoader} onRefreshManifest={refreshManifest}
              onSwitchToRagTab={() => setAiTab("outline")}
              onEditPromptForMsg={openPromptEdit}
              onDeleteMessage={(idx) => setChatMessages(prev => prev.filter((_, i) => i !== idx))} />}
            {aiTab === "rewrite" && <RewriteTab selection={selection} prompt={rewritePrompt} onPromptChange={setRewritePrompt} model={rewriteModel} onModelChange={setRewriteModel} />}
            {aiTab === "eval" && <EvalTab result={evalResult} chapterContent={content} projectId={projectId}
              chapterId={activeChId} chapterNum={chapterNum} manifest={manifest} skillSelection={skillSelection} ragExcludes={ragExcludes}
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
      {/* 网页大模型创作 dialog — 与「市场特征提取」复用同一 UniversalLLMDialog。
          manual_only 模式: 用户复制 prompt 到 ChatGPT / Claude / Gemini, 把回复
          粘回 → onCommit 走 applyPlainPaste, 经 normalizeWebLLMReply 后作为
          「作家智能体」消息进入 chat. */}
      <UniversalLLMDialog
        open={webLLMOpen}
        onClose={() => setWebLLMOpen(false)}
        title={`网页大模型创作 · ${activeCh?.title || "本章"}`}
        description="复制 prompt 到网页大模型（ChatGPT / Claude / Gemini …），把回复粘回 → 作家智能体在右侧聊天接管展示。"
        prompt={webLLMPrompt}
        editablePrompt
        onCommit={(p) => applyPlainPaste(p.text)}
        minChars={80}
        initialMode="manual_only"
      />
      {/* 单条 User 消息的「查看 / 修改 Prompt」弹窗. 用户可改 prompt,
          点「按编辑后的 prompt 重新生成」走 system_hint 直发, 跳过
          RAG 重渲染. */}
      {promptEditOpen && (
        <div
          onClick={() => setPromptEditOpen(false)}
          style={{
            position: "fixed", inset: 0, zIndex: 1100,
            background: "rgba(0,0,0,0.45)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              width: "min(900px, 92vw)",
              height: "min(680px, 88vh)",
              background: "var(--bg-surface)",
              borderRadius: 10,
              border: "1px solid var(--border)",
              display: "flex", flexDirection: "column",
              overflow: "hidden",
            }}
          >
            <header style={{
              padding: "12px 16px",
              borderBottom: "1px solid var(--border)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <div>
                <h3 style={{ margin: 0, fontSize: 15 }}>查看 / 修改 Prompt</h3>
                <p style={{ margin: "2px 0 0", fontSize: 11, color: "var(--text-tertiary)" }}>
                  {promptEditDraft.length.toLocaleString()} 字 · 编辑后点「重新生成」会用编辑后的 prompt 直接调 LLM（跳过 RAG 重组装）。
                </p>
              </div>
              <button
                onClick={() => setPromptEditOpen(false)}
                style={{
                  background: "transparent", border: "none",
                  fontSize: 18, color: "var(--text-tertiary)", cursor: "pointer",
                  padding: "2px 8px", borderRadius: 4,
                }}
              >×</button>
            </header>
            <div style={{ flex: 1, padding: 16, overflow: "hidden", display: "flex", flexDirection: "column" }}>
              <textarea
                value={promptEditDraft}
                onChange={e => setPromptEditDraft(e.target.value)}
                spellCheck={false}
                style={{
                  flex: 1, width: "100%", boxSizing: "border-box",
                  padding: "10px 12px",
                  background: "var(--bg-app)", color: "var(--text-primary)",
                  border: "1px solid var(--border)", borderRadius: 6,
                  fontFamily: "var(--font-mono)", fontSize: 12, lineHeight: 1.6,
                  resize: "none",
                }}
              />
              <div style={{
                marginTop: 10, display: "flex", justifyContent: "space-between",
                alignItems: "center", gap: 10,
              }}>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                  {promptEditDraft === promptEditOriginal
                    ? "未修改"
                    : `已修改 ${Math.abs(promptEditDraft.length - promptEditOriginal.length)} 字`}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button
                    onClick={async () => {
                      try { await navigator.clipboard.writeText(promptEditDraft); toast("已复制 prompt", "success"); }
                      catch { toast("复制失败", "error"); }
                    }}
                    style={{
                      padding: "6px 14px", borderRadius: 6, fontSize: 12,
                      background: "transparent", color: "var(--text-secondary)",
                      border: "1px solid var(--border)", cursor: "pointer",
                    }}
                  >复制</button>
                  <button
                    onClick={() => setPromptEditDraft(promptEditOriginal)}
                    disabled={promptEditDraft === promptEditOriginal}
                    style={{
                      padding: "6px 14px", borderRadius: 6, fontSize: 12,
                      background: "transparent",
                      color: promptEditDraft === promptEditOriginal ? "var(--text-disabled)" : "var(--text-secondary)",
                      border: "1px solid var(--border)",
                      cursor: promptEditDraft === promptEditOriginal ? "not-allowed" : "pointer",
                    }}
                  >还原</button>
                  <button
                    onClick={submitEditedPrompt}
                    disabled={!promptEditDraft.trim() || generating}
                    style={{
                      padding: "6px 18px", borderRadius: 6, fontSize: 12, fontWeight: 600,
                      background: promptEditDraft.trim() && !generating ? "var(--accent)" : "transparent",
                      color: promptEditDraft.trim() && !generating ? "#fff" : "var(--text-disabled)",
                      border: `1px solid ${promptEditDraft.trim() && !generating ? "var(--accent)" : "var(--border)"}`,
                      cursor: promptEditDraft.trim() && !generating ? "pointer" : "not-allowed",
                    }}
                  >按编辑后的 prompt 重新生成</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
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

/** Flatten a reference work's settings_json into a flat (label, content) list. */
function workSettings(settingsJson: any): { label: string; content: string }[] {
  let s: any = settingsJson;
  if (typeof settingsJson === "string") {
    try { s = JSON.parse(settingsJson); } catch { return []; }
  }
  const out: { label: string; content: string }[] = [];
  if (s && typeof s === "object" && !Array.isArray(s)) {
    for (const [label, value] of Object.entries(s)) {
      const lab = String(label || "").trim();
      if (!lab) continue;
      let body = "";
      if (typeof value === "string") body = value;
      else if (Array.isArray(value)) body = value.map(v => String(v)).join("；");
      else if (value && typeof value === "object") {
        body = Object.entries(value).map(([k, v]) => `${k}: ${v}`).join("；");
      } else if (value != null) body = String(value);
      body = body.trim();
      if (body) out.push({ label: lab, content: body });
    }
  } else if (Array.isArray(s)) {
    s.forEach((item, i) => {
      if (typeof item === "string" && item.trim()) {
        out.push({ label: `条目${i + 1}`, content: item.trim() });
      } else if (item && typeof item === "object") {
        const lab = String((item as any).label || (item as any).name || (item as any).title || `条目${i + 1}`);
        const body = String((item as any).content || (item as any).description || (item as any).value || "");
        if (body.trim()) out.push({ label: lab, content: body.trim() });
      }
    });
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
        <span onClick={onToggle} style={{ cursor: "pointer", width: 11, flexShrink: 0, color: "var(--gold)", fontWeight: 700 }}>{on ? "✓" : "○"}</span>
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
            {expanded ? "收起 ▴" : "详情 ▾"}
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

/** Parse a reference work's character list. Prefers ``static_characters_json``
 *  (pure-setting works' canonical roster) and falls back to
 *  ``extracted_characters_json`` (narrative works' AI-extracted roster). */
function workReferenceCharacters(w: any): { name: string; description: string }[] {
  const out: { name: string; description: string }[] = [];
  const seen = new Set<string>();
  const push = (name: any, description: any, role: any) => {
    const n = String(name || "").trim();
    if (!n || seen.has(n)) return;
    seen.add(n);
    const d = String(description || "").trim();
    const r = String(role || "").trim();
    out.push({
      name: n,
      description: r ? `（${r}）${d}` : d,
    });
  };
  const parse = (raw: any) => {
    if (raw == null) return null;
    if (typeof raw !== "string") return raw;
    try { return JSON.parse(raw); } catch { return null; }
  };
  // Pure-setting works first (static_characters_json).
  const sc = parse(w.static_characters_json);
  const items1: any[] = Array.isArray(sc)
    ? sc
    : (sc && Array.isArray(sc.characters) ? sc.characters : []);
  items1.forEach(c => { if (c && typeof c === "object") push(c.name, c.description, c.role); });
  // Narrative works' extracted roster.
  const ec = parse(w.extracted_characters_json);
  const items2: any[] = Array.isArray(ec)
    ? ec
    : (ec && Array.isArray(ec.characters) ? ec.characters : []);
  items2.forEach(c => {
    if (!c || typeof c !== "object") return;
    push(c.name, c.intro || c.description, c.role_tag || c.role);
  });
  return out;
}

/** Parse a reference work's setting-feature entries (pure-setting taxonomy:
 *  category / title / description). One row per entry. */
function workEntries(raw: any): { title: string; content: string }[] {
  let s: any = raw;
  if (typeof raw === "string") {
    try { s = JSON.parse(raw); } catch { return []; }
  }
  if (!Array.isArray(s)) return [];
  const out: { title: string; content: string }[] = [];
  for (const f of s) {
    if (!f || typeof f !== "object") continue;
    const title = String(f.title || f.name || "").trim();
    if (!title) continue;
    const cat = String(f.category || "").trim();
    const desc = String(f.description || "").trim();
    out.push({
      title: cat ? `[${cat}] ${title}` : title,
      content: desc,
    });
  }
  return out;
}

/** A selectable setting/worldview row — label tag + content preview, with
 *  click-to-expand details for the full setting description. */
function SettingRow({ s, on, onToggle }: {
  s: { label: string; content: string };
  on: boolean; onToggle: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 5, padding: "4px 8px", fontSize: 11 }}>
        <span onClick={onToggle} style={{ cursor: "pointer", width: 11, flexShrink: 0, color: "var(--indigo)", fontWeight: 700 }}>{on ? "✓" : "○"}</span>
        <span style={{
          fontSize: 9, padding: "0 5px", flexShrink: 0, borderRadius: 3,
          color: "var(--indigo)", border: "1px solid var(--indigo)",
        }}>{s.label}</span>
        <span onClick={onToggle} style={{
          flex: 1, minWidth: 0, cursor: "pointer",
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          fontWeight: on ? 600 : 400, color: on ? "var(--indigo)" : "var(--text-secondary)",
        }}>{s.content.slice(0, 40)}{s.content.length > 40 ? "…" : ""}</span>
        <span onClick={() => setExpanded(e => !e)} style={{ cursor: "pointer", fontSize: 9, color: "var(--text-tertiary)", flexShrink: 0 }}>
          {expanded ? "收起 ▲" : "详情 ▼"}
        </span>
      </div>
      {expanded && (
        <div style={{ padding: "0 8px 5px 25px", fontSize: 10, color: "var(--text-tertiary)", lineHeight: 1.5 }}>
          {s.content}
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
/* ── ReadOnlyEditorSection ──
 * Editor 大纲 tab 下 关联角色 / 参考作品 / 灵感 / 伏笔 / 时间 / 地点
 * 等"已迁至 故事线 page"区块的标准卡片：左侧 vertical 彩条 + 标题
 * + 计数 + 自定义内容。没有 collapsible toggle、没有输入框样式，
 * 保持单纯展示。 */
function ReadOnlyEditorSection({ title, count, color, children }: {
  title: string;
  count?: number;
  color: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{
      marginTop: 8, padding: "10px 12px",
      background: "var(--bg-surface)",
      border: "1px solid var(--border)",
      borderRadius: 8,
      position: "relative",
    }}>
      <div style={{
        position: "absolute", left: 0, top: 0, bottom: 0,
        width: 3, background: color, opacity: 0.8,
        borderRadius: "8px 0 0 8px",
      }} />
      <div style={{
        display: "flex", alignItems: "baseline", gap: 6,
        marginBottom: 8,
      }}>
        <span style={{
          fontSize: 11, fontWeight: 700, color,
          letterSpacing: 0.3,
        }}>
          {title}
        </span>
        {count !== undefined && count > 0 && (
          <span style={{
            fontSize: 10, color: "var(--text-tertiary)", fontWeight: 500,
          }}>
            · {count}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}


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
      <span style={{ width: 11, flexShrink: 0, color, fontWeight: 700 }}>{on ? "✓" : "○"}</span>
      <span style={{
        flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        fontWeight: on ? 600 : 400, color: on ? color : "var(--text-secondary)",
      }}>{label}</span>
      {sub && <span className="text-xs text-muted" style={{ flexShrink: 0 }}>{sub}</span>}
    </div>
  );
}

/** ReferenceLinkSection — 编辑器内可编辑的关联参考作品区域。
 *  上半：作品列表（搜索 + 多选）；下半：每部已选作品分两栏展示「具体
 *  情节」（events）与「具体设定」（settings），用户可勾选具体条目，
 *  选中项会通过 referenced_materials loader 注入到 prompt。 */
type RefWork = {
  id: string; title: string; selected: boolean; structureType: string;
  events: { name: string; description: string; chapter: string }[];
  settings: { label: string; content: string }[];
  characters: { name: string; description: string }[];
  entries: { title: string; content: string }[];
};

function ReferenceLinkSection({
  references, selectedRefs,
  refEvents, refSettings, refCharacters, refEntries,
  toggleRef, toggleEvent, toggleSetting, toggleCharacter, toggleEntry,
  isEventLinked, isSettingLinked, isCharacterLinked, isEntryLinked,
  refSearch, setRefSearch, eventSearch, setEventSearch,
}: {
  references: RefWork[];
  selectedRefs: RefWork[];
  refEvents: { ref_id: string; work_title: string; name: string; description: string; chapter?: string }[];
  refSettings: { ref_id: string; work_title: string; label: string; content: string }[];
  refCharacters: { ref_id: string; work_title: string; name: string; description: string }[];
  refEntries: { ref_id: string; work_title: string; title: string; content: string }[];
  toggleRef: (id: string) => void;
  toggleEvent: (refId: string, workTitle: string,
                 ev: { name: string; description: string; chapter: string }) => void;
  toggleSetting: (refId: string, workTitle: string,
                   s: { label: string; content: string }) => void;
  toggleCharacter: (refId: string, workTitle: string,
                     c: { name: string; description: string }) => void;
  toggleEntry: (refId: string, workTitle: string,
                 e: { title: string; content: string }) => void;
  isEventLinked: (refId: string, name: string) => boolean;
  isSettingLinked: (refId: string, label: string) => boolean;
  isCharacterLinked: (refId: string, name: string) => boolean;
  isEntryLinked: (refId: string, title: string) => boolean;
  refSearch: string; setRefSearch: (v: string) => void;
  eventSearch: string; setEventSearch: (v: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const refQ = refSearch.trim().toLowerCase();
  const filteredRefs = references.filter(r => !refQ || r.title.toLowerCase().includes(refQ));
  // Per-work category counts for the collapsed-state chip subtitle.
  const linkedCount = (ref_id: string) =>
    refEvents.filter(e => e.ref_id === ref_id).length
    + refSettings.filter(s => s.ref_id === ref_id).length
    + refCharacters.filter(c => c.ref_id === ref_id).length
    + refEntries.filter(e => e.ref_id === ref_id).length;
  // 智能识别: a work is "pure-setting" when it has settings/characters/entries
  // but no events. Subtitle in the work picker reflects whichever categories
  // the work actually exposes.
  const workSubtitle = (r: RefWork) => {
    const parts: string[] = [];
    if (r.events.length) parts.push(`${r.events.length} 情节`);
    if (r.settings.length) parts.push(`${r.settings.length} 设定`);
    if (r.characters.length) parts.push(`${r.characters.length} 人物`);
    if (r.entries.length) parts.push(`${r.entries.length} 条目`);
    return parts.length ? parts.join(" · ") : "暂无可挑选数据";
  };
  return (
    <div style={{
      marginTop: 8, padding: "10px 12px",
      background: "var(--bg-surface)",
      border: "1px solid var(--border)",
      borderRadius: 8,
      position: "relative",
    }}>
      <div style={{
        position: "absolute", left: 0, top: 0, bottom: 0,
        width: 3, background: "var(--jade)", opacity: 0.8,
        borderRadius: "8px 0 0 8px",
      }} />
      <button onClick={() => setExpanded(e => !e)} style={{
        all: "unset", cursor: "pointer", width: "100%",
        display: "flex", alignItems: "baseline", gap: 6,
        marginBottom: expanded ? 8 : 0,
      }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: "var(--jade)", letterSpacing: 0.3 }}>
          关联参考作品
        </span>
        <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
          · {selectedRefs.length} 部
          {refEvents.length > 0 ? ` · ${refEvents.length} 情节` : ""}
          {refSettings.length > 0 ? ` · ${refSettings.length} 设定` : ""}
          {refCharacters.length > 0 ? ` · ${refCharacters.length} 人物` : ""}
          {refEntries.length > 0 ? ` · ${refEntries.length} 条目` : ""}
        </span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          {expanded ? "▴" : "▾"}
        </span>
      </button>
      {!expanded ? (
        selectedRefs.length === 0 ? (
          <span className="text-xs text-muted" style={{ fontStyle: "italic", display: "block", marginTop: 4 }}>
            未关联参考作品 — 点击 ▾ 展开后选择作品并挑选具体条目
          </span>
        ) : (
          <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 4 }}>
            {selectedRefs.map(r => {
              const n = linkedCount(r.id);
              return (
                <span key={r.id} style={{
                  fontSize: 11, padding: "3px 10px",
                  background: "var(--jade-subtle)", color: "var(--jade)",
                  border: "1px solid var(--jade)", borderRadius: 12,
                  fontWeight: 500,
                }} title={`${r.title} · ${n} 个具体条目`}>
                  {r.title}
                  {n > 0 && (
                    <span style={{ fontSize: 9, marginLeft: 4, opacity: 0.7 }}>·{n}</span>
                  )}
                </span>
              );
            })}
          </div>
        )
      ) : (
        <div>
          {/* 作品多选 */}
          <div style={{ marginBottom: 10 }}>
            <input className="input" value={refSearch} onChange={e => setRefSearch(e.target.value)}
              placeholder="搜索作品标题..." style={{ fontSize: 11, padding: "4px 8px", marginBottom: 4 }} />
            {references.length === 0 ? (
              <div className="text-xs text-muted" style={{ padding: 8 }}>
                参考库为空，请到「参考作品详情」导入
              </div>
            ) : (
              <div style={{
                maxHeight: 160, overflowY: "auto",
                border: "1px solid var(--border)", borderRadius: 4,
              }}>
                {filteredRefs.map(r => (
                  <PickRow key={r.id} label={r.title}
                    sub={workSubtitle(r)}
                    on={r.selected} color="var(--jade)"
                    onClick={() => toggleRef(r.id)} />
                ))}
                {filteredRefs.length === 0 && (
                  <div className="text-xs text-muted" style={{ padding: 8 }}>无匹配</div>
                )}
              </div>
            )}
          </div>

          {selectedRefs.length > 0 && (
            <input className="input" value={eventSearch} onChange={e => setEventSearch(e.target.value)}
              placeholder="搜索情节 / 设定 / 人物 / 条目..." style={{ fontSize: 11, padding: "4px 8px", marginBottom: 8 }} />
          )}
          {selectedRefs.map(r => {
            const eq = eventSearch.trim().toLowerCase();
            const evs = r.events.filter(ev => !eq
              || ev.name.toLowerCase().includes(eq)
              || ev.description.toLowerCase().includes(eq)
              || ev.chapter.toLowerCase().includes(eq));
            const ses = r.settings.filter(s => !eq
              || s.label.toLowerCase().includes(eq)
              || s.content.toLowerCase().includes(eq));
            const chs = r.characters.filter(c => !eq
              || c.name.toLowerCase().includes(eq)
              || c.description.toLowerCase().includes(eq));
            const ens = r.entries.filter(e => !eq
              || e.title.toLowerCase().includes(eq)
              || e.content.toLowerCase().includes(eq));
            const evLinked = refEvents.filter(e => e.ref_id === r.id).length;
            const seLinked = refSettings.filter(s => s.ref_id === r.id).length;
            const chLinked = refCharacters.filter(c => c.ref_id === r.id).length;
            const enLinked = refEntries.filter(e => e.ref_id === r.id).length;
            const isPureSetting = r.structureType === "setting_collection"
              || (r.events.length === 0 && (r.settings.length + r.characters.length + r.entries.length) > 0);
            return (
              <div key={r.id} style={{
                marginBottom: 10, padding: "6px 8px",
                background: "var(--bg-surface-2)",
                border: "1px solid var(--border)", borderRadius: 6,
              }}>
                <div style={{
                  fontSize: 11, fontWeight: 700, color: "var(--jade)",
                  marginBottom: 6, display: "flex", alignItems: "baseline", gap: 6,
                  flexWrap: "wrap",
                }}>
                  「{r.title}」
                  <span style={{
                    fontSize: 8.5, padding: "1px 5px", borderRadius: 8,
                    color: isPureSetting ? "var(--indigo)" : "var(--gold)",
                    background: isPureSetting ? "var(--indigo-subtle)" : "var(--gold-subtle)",
                    border: `1px solid ${isPureSetting ? "var(--indigo)" : "var(--gold)"}`,
                    fontWeight: 600,
                  }}>
                    {isPureSetting ? "纯设定" : "叙事"}
                  </span>
                  <span style={{ fontSize: 9, color: "var(--text-tertiary)", fontWeight: 400 }}>
                    · 已选
                    {r.events.length > 0 ? ` ${evLinked} 情节` : ""}
                    {r.settings.length > 0 ? ` ${seLinked} 设定` : ""}
                    {r.characters.length > 0 ? ` ${chLinked} 人物` : ""}
                    {r.entries.length > 0 ? ` ${enLinked} 条目` : ""}
                  </span>
                </div>
                {/* 智能识别：只渲染该作品有数据的类别。条目竖向堆叠，不并排。 */}
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {r.events.length > 0 && (
                    <CategoryPanel
                      label="具体情节" color="var(--gold)"
                      empty={evs.length === 0 ? "无匹配" : null}>
                      {evs.map((ev, i) => (
                        <EventRow key={i} ev={ev}
                          on={isEventLinked(r.id, ev.name)}
                          onToggle={() => toggleEvent(r.id, r.title, ev)} />
                      ))}
                    </CategoryPanel>
                  )}
                  {r.settings.length > 0 && (
                    <CategoryPanel
                      label="具体设定" color="var(--indigo)"
                      empty={ses.length === 0 ? "无匹配" : null}>
                      {ses.map((s, i) => (
                        <SettingRow key={i} s={s}
                          on={isSettingLinked(r.id, s.label)}
                          onToggle={() => toggleSetting(r.id, r.title, s)} />
                      ))}
                    </CategoryPanel>
                  )}
                  {r.characters.length > 0 && (
                    <CategoryPanel
                      label="具体人物" color="var(--purple)"
                      empty={chs.length === 0 ? "无匹配" : null}>
                      {chs.map((c, i) => (
                        <CharacterRefRow key={i} c={c}
                          on={isCharacterLinked(r.id, c.name)}
                          onToggle={() => toggleCharacter(r.id, r.title, c)} />
                      ))}
                    </CategoryPanel>
                  )}
                  {r.entries.length > 0 && (
                    <CategoryPanel
                      label="具体条目" color="var(--jade)"
                      empty={ens.length === 0 ? "无匹配" : null}>
                      {ens.map((e, i) => (
                        <EntryRow key={i} e={e}
                          on={isEntryLinked(r.id, e.title)}
                          onToggle={() => toggleEntry(r.id, r.title, e)} />
                      ))}
                    </CategoryPanel>
                  )}
                  {r.events.length === 0 && r.settings.length === 0
                    && r.characters.length === 0 && r.entries.length === 0 && (
                    <div className="text-xs text-muted" style={{ padding: 6, fontSize: 10, fontStyle: "italic" }}>
                      此作品暂未抽取出可挑选的数据 — 请先在「参考作品详情」运行分析流程。
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Scrollable inner panel for one category (情节 / 设定 / 人物 / 条目). */
function CategoryPanel({ label, color, empty, children }: {
  label: string; color: string; empty: string | null; children: React.ReactNode;
}) {
  return (
    <div>
      <div style={{ fontSize: 10, fontWeight: 600, color, marginBottom: 3 }}>
        {label}
      </div>
      {empty != null ? (
        <div className="text-xs text-muted" style={{ padding: 4, fontSize: 10 }}>{empty}</div>
      ) : (
        <div style={{
          maxHeight: 180, overflowY: "auto",
          border: "1px solid var(--border)", borderRadius: 4,
        }}>
          {children}
        </div>
      )}
    </div>
  );
}

/** Selectable character row — name + role + click-to-expand description. */
function CharacterRefRow({ c, on, onToggle }: {
  c: { name: string; description: string }; on: boolean; onToggle: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 5, padding: "4px 8px", fontSize: 11 }}>
        <span onClick={onToggle} style={{ cursor: "pointer", width: 11, flexShrink: 0, color: "var(--purple)", fontWeight: 700 }}>{on ? "✓" : "○"}</span>
        <span onClick={onToggle} style={{
          flex: 1, minWidth: 0, cursor: "pointer",
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          fontWeight: on ? 600 : 400, color: on ? "var(--purple)" : "var(--text-secondary)",
        }}>{c.name}</span>
        {c.description && (
          <span onClick={() => setExpanded(e => !e)} style={{ cursor: "pointer", fontSize: 9, color: "var(--text-tertiary)", flexShrink: 0 }}>
            {expanded ? "收起 ▴" : "详情 ▾"}
          </span>
        )}
      </div>
      {expanded && c.description && (
        <div style={{ padding: "0 8px 5px 25px", fontSize: 10, color: "var(--text-tertiary)", lineHeight: 1.5 }}>
          {c.description}
        </div>
      )}
    </div>
  );
}

/** Selectable entry row (pure-setting taxonomy item) — title + click-to-expand content. */
function EntryRow({ e, on, onToggle }: {
  e: { title: string; content: string }; on: boolean; onToggle: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 5, padding: "4px 8px", fontSize: 11 }}>
        <span onClick={onToggle} style={{ cursor: "pointer", width: 11, flexShrink: 0, color: "var(--jade)", fontWeight: 700 }}>{on ? "✓" : "○"}</span>
        <span onClick={onToggle} style={{
          flex: 1, minWidth: 0, cursor: "pointer",
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          fontWeight: on ? 600 : 400, color: on ? "var(--jade)" : "var(--text-secondary)",
        }}>{e.title}</span>
        {e.content && (
          <span onClick={() => setExpanded(x => !x)} style={{ cursor: "pointer", fontSize: 9, color: "var(--text-tertiary)", flexShrink: 0 }}>
            {expanded ? "收起 ▴" : "详情 ▾"}
          </span>
        )}
      </div>
      {expanded && e.content && (
        <div style={{ padding: "0 8px 5px 25px", fontSize: 10, color: "var(--text-tertiary)", lineHeight: 1.5 }}>
          {e.content}
        </div>
      )}
    </div>
  );
}

function OutlineTab({ synopsis, onChange, onSave, onStartGeneration, projectId, chapter, chapterNum, onUpdateChapter, allChapters, onNavigate: _onNavigate }: {
  synopsis: string; onChange: (v: string) => void; onSave: () => void; onStartGeneration: () => void; projectId: string;
  chapter?: ChapterOutline | null; onUpdateChapter?: (field: string, value: any) => void;
  chapterNum?: number;
  allChapters?: { id: string; title: string }[];
  onNavigate?: (tab: string) => void;
}) {
  const { toast } = useToast();

  // 章节剧情大纲 debounced 自动保存（1.5s 静止后写回 editor → 同步
  // 进 故事线 章节大纲）。
  const lastSavedSynRef = useRef(synopsis);
  const onSaveRef = useRef(onSave);
  useEffect(() => { onSaveRef.current = onSave; }, [onSave]);
  useEffect(() => { lastSavedSynRef.current = synopsis; }, [chapter?.id]);
  useEffect(() => {
    if (synopsis === lastSavedSynRef.current) return;
    const t = setTimeout(() => {
      onSaveRef.current();
      lastSavedSynRef.current = synopsis;
    }, 1500);
    return () => clearTimeout(t);
  }, [synopsis]);

  // Editable references registry — loaded from /api/references/works for
  // the ReferenceLinkSection below. Characters / inspirations / 伏笔 /
  // 时间 / 地点 are no longer displayed in the editor (they surface in
  // the RAG injection panel via their respective loaders).
  const [references, setReferences] = useState<{
    id: string; title: string; selected: boolean; structureType: string;
    events: { name: string; description: string; chapter: string }[];
    settings: { label: string; content: string }[];
    characters: { name: string; description: string }[];
    entries: { title: string; content: string }[];
  }[]>([]);
  const [refSearch, setRefSearch] = useState("");
  const [eventSearch, setEventSearch] = useState("");

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
    const chapterRefs = chapter?.references || [];
    apiGet<{ items: any[] }>("/api/references/works")
      .then(r => setReferences((r.items || []).map((w: any) => ({
        id: w.ref_id || w.id,
        title: w.title || w.name || "未命名",
        selected: chapterRefs.includes(w.ref_id || w.id),
        structureType: (w.structure_type || "narrative") as string,
        events: workEvents(w.plot_outline_json),
        settings: workSettings(w.settings_json),
        characters: workReferenceCharacters(w),
        entries: workEntries(w.setting_features_json),
      }))))
      .catch(() => setReferences([]));
  }, [projectId, chapter?.id]);

  const refEvents = chapter?.referenced_events || [];
  const refSettings = (chapter as any)?.referenced_settings || [];
  const refCharacters = (chapter as any)?.referenced_characters || [];
  const refEntries = (chapter as any)?.referenced_entries || [];
  const isEventLinked = (refId: string, name: string) =>
    refEvents.some(e => e.ref_id === refId && e.name === name);
  const isSettingLinked = (refId: string, label: string) =>
    refSettings.some((s: any) => s.ref_id === refId && s.label === label);
  const isCharacterLinked = (refId: string, name: string) =>
    refCharacters.some((c: any) => c.ref_id === refId && c.name === name);
  const isEntryLinked = (refId: string, title: string) =>
    refEntries.some((e: any) => e.ref_id === refId && e.title === title);

  // 关联参考作品 is editable inline in the editor; specific events / settings
  // / characters / entries per work are picked from the inline panel below.
  // Categories with 0 items in the work are auto-hidden by ReferenceLinkSection.
  const toggleRef = (id: string) => {
    setReferences(prev => {
      const next = prev.map(r => r.id === id ? { ...r, selected: !r.selected } : r);
      const selectedIds = next.filter(r => r.selected).map(r => r.id);
      onUpdateChapter?.("references", selectedIds);
      const stillSel = new Set(selectedIds);
      const prunedEvents = refEvents.filter(e => stillSel.has(e.ref_id));
      if (prunedEvents.length !== refEvents.length) onUpdateChapter?.("referenced_events", prunedEvents);
      const prunedSettings = refSettings.filter((s: any) => stillSel.has(s.ref_id));
      if (prunedSettings.length !== refSettings.length) onUpdateChapter?.("referenced_settings", prunedSettings);
      const prunedCharacters = refCharacters.filter((c: any) => stillSel.has(c.ref_id));
      if (prunedCharacters.length !== refCharacters.length) onUpdateChapter?.("referenced_characters", prunedCharacters);
      const prunedEntries = refEntries.filter((e: any) => stillSel.has(e.ref_id));
      if (prunedEntries.length !== refEntries.length) onUpdateChapter?.("referenced_entries", prunedEntries);
      return next;
    });
  };
  const toggleEvent = (refId: string, workTitle: string, ev: { name: string; description: string; chapter: string }) => {
    const next = isEventLinked(refId, ev.name)
      ? refEvents.filter(e => !(e.ref_id === refId && e.name === ev.name))
      : [...refEvents, { ref_id: refId, work_title: workTitle, name: ev.name, description: ev.description, chapter: ev.chapter }];
    onUpdateChapter?.("referenced_events", next);
  };
  const toggleSetting = (refId: string, workTitle: string, s: { label: string; content: string }) => {
    const next = isSettingLinked(refId, s.label)
      ? refSettings.filter((x: any) => !(x.ref_id === refId && x.label === s.label))
      : [...refSettings, { ref_id: refId, work_title: workTitle, label: s.label, content: s.content }];
    onUpdateChapter?.("referenced_settings", next);
  };
  const toggleCharacter = (refId: string, workTitle: string, c: { name: string; description: string }) => {
    const next = isCharacterLinked(refId, c.name)
      ? refCharacters.filter((x: any) => !(x.ref_id === refId && x.name === c.name))
      : [...refCharacters, { ref_id: refId, work_title: workTitle, name: c.name, description: c.description }];
    onUpdateChapter?.("referenced_characters", next);
  };
  const toggleEntry = (refId: string, workTitle: string, e: { title: string; content: string }) => {
    const next = isEntryLinked(refId, e.title)
      ? refEntries.filter((x: any) => !(x.ref_id === refId && x.title === e.title))
      : [...refEntries, { ref_id: refId, work_title: workTitle, title: e.title, content: e.content }];
    onUpdateChapter?.("referenced_entries", next);
  };
  const selectedRefs = references.filter(r => r.selected);

  return (
    <div style={{ display: "flex", flexDirection: "column", minHeight: "100%" }}>
      {/* 可滚动主体，paddingBottom 给 sticky 底栏让出空间 */}
      <div style={{ flex: 1, paddingBottom: 4 }}>
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

      {/* 创作备注 textarea 已从 RAG tab 撤掉 —— 它的语义现在由
          「智能体创作」聊天输入框承担：每次发送的文本就是「本次创作
          指令 / 备注」。chapter.special_requirements 字段仍保留以供
          loader 在历史数据上工作，但 RAG tab 不再露面。 */}

      {/* 关联参考作品 — 编辑器内可直接编辑。先在顶部选择参考作品，
          再为每部已选作品挑选具体情节 / 设定 / 人物 / 条目（智能识别
          作品类型，自动隐藏空类别），loader 通过 referenced_materials
          块注入到 prompt。其余关联信息（角色 / 灵感 / 伏笔 / 时间地点）
          全部合并到下面的 RAG 注入内容里展示，不再在这里重复。 */}
      <ReferenceLinkSection
        references={references}
        selectedRefs={selectedRefs}
        refEvents={refEvents}
        refSettings={refSettings}
        refCharacters={refCharacters}
        refEntries={refEntries}
        toggleRef={toggleRef}
        toggleEvent={toggleEvent}
        toggleSetting={toggleSetting}
        toggleCharacter={toggleCharacter}
        toggleEntry={toggleEntry}
        isEventLinked={isEventLinked}
        isSettingLinked={isSettingLinked}
        isCharacterLinked={isCharacterLinked}
        isEntryLinked={isEntryLinked}
        refSearch={refSearch} setRefSearch={setRefSearch}
        eventSearch={eventSearch} setEventSearch={setEventSearch}
      />

      {projectId && chapter?.id && (
        <RAGLoaderList projectId={projectId}
          chapterId={chapter.id}
          chapterNum={chapterNum || 1} />
      )}

      </div>
      {/* sticky footer — 保存 / 开始生成 始终钉在面板底部 */}
      <div
        style={{
          position: "sticky",
          bottom: -14,                          // 抵消 panel-body padding 的 14px
          marginLeft: -16, marginRight: -16,    // 让背景顶到面板左右边
          padding: "10px 16px 14px",
          background: "var(--bg-surface)",
          borderTop: "1px solid var(--border)",
          display: "flex",
          gap: 8,
          zIndex: 5,
        }}
      >
        <button className="btn-primary" style={{ flex: 1 }} onClick={onSave}>保存</button>
        <button className="btn-primary" style={{ flex: 1, background: "var(--jade, #34a853)", border: "none" }} onClick={onStartGeneration}>开始生成</button>
      </div>
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

/** Normalize a persisted aiTab value. Cluster / 导演 multi-agent mode
 *  is offline this iteration — any prior session that landed on cluster
 *  falls back to single. */
function normalizeAiTab(v: any): "outline" | "single" | "cluster" | "rewrite" | "eval" {
  if (v === "inspire" || v === "single") return "single";
  if (v === "cluster") return "single";
  if (v === "rewrite" || v === "eval") return v;
  return "outline";
}

/** One-line summary of which learned skills an AI step used. */
function formatSkillsUsed(skills?: string[]): string {
  return skills && skills.length
    ? `本次创作启用技能：${skills.join("、")}`
    : "本次创作未启用自定义技能";
}

/** Look up the actual prompt section whose `## title` *contains* one of
 *  the candidate substrings. The 后端 loaders嵌的标题里常带括号补充
 *  («参考作品综合», «相关灵感（用户灵感库）», «故事舞台 客观状态
 *  （截至第 N 章）» 等），所以严格相等匹配会全部 miss。 */
const sectionMatch = (sections: Map<string, string>, candidates: string[]): string => {
  for (const [title, body] of sections) {
    for (const c of candidates) {
      if (title === c || title.includes(c)) return body;
    }
  }
  return "";
};

/** Sections expected by the prompt template — used by RAG预览 to surface
 *  which loaders actually injected content into the rendered prompt.
 *  hint = 未注入 时给出的诊断提示，帮助用户立刻知道为什么是空。
 *  group = 顶部分组（系统级 / 上下文 / 章节）— 与后端
 *  builder._SECTION_GROUPS 一一对应。 */
const RAG_PREVIEW_SECTIONS: {
  title: string; source: string; matches: string[];
  group: "system" | "context" | "user"; hint: string;
}[] = [
  // —— 章节专属 ——
  { title: "创作备注", source: "user_special_requirements", matches: ["创作备注", "用户特别要求"],
    group: "user", hint: "请在本页上方「创作备注」字段输入内容" },
  { title: "本章大纲",     source: "chapter_outline",           matches: ["本章大纲"],
    group: "user", hint: "请填写「章节剧情大纲」或在故事线为本章添加情节卡" },
  { title: "时间与地点",   source: "time_location",             matches: ["时间与地点"],
    group: "user", hint: "请在故事线情节卡设置时间 / 地点" },
  { title: "本章出场角色", source: "characters_block",          matches: ["本章出场角色", "出场角色"],
    group: "user", hint: "请在故事线情节卡选择「出场角色」" },
  { title: "已有正文",     source: "existing_content / current_chapter_draft", matches: ["已有正文", "正文草稿", "前几章正文"],
    group: "user", hint: "首次创作正常为空；本章已有 10 字以上内容才会注入" },
  // —— 上下文 ——
  { title: "出场角色档案", source: "character_cards",           matches: ["出场角色档案", "角色档案"],
    group: "context", hint: "请在「角色管理」补全本章出场角色的档案" },
  { title: "世界观设定",   source: "worldbook",                 matches: ["世界观设定", "世界书"],
    group: "context", hint: "请在「故事中世界 → 世界书」添加设定条目" },
  { title: "关联参考作品", source: "reference",                 matches: ["参考作品综合", "关联参考", "参考作品"],
    group: "context", hint: "请在「参考作品详情」给本项目关联作品，并在编辑器选择具体情节 / 设定" },
  { title: "关联伏笔",     source: "foreshadowing",             matches: ["关联伏笔", "伏笔"],
    group: "context", hint: "请在故事线情节卡为本章关联未回收的伏笔" },
  { title: "当前涉及的故事线", source: "subplots",              matches: ["当前涉及的故事线", "故事线"],
    group: "context", hint: "请在故事线为本章关联主线 / 支线" },
  { title: "相关灵感",     source: "inspiration",               matches: ["相关灵感", "灵感库"],
    group: "context", hint: "请在「灵感库」添加条目，或在大纲中描述匹配方向" },
  { title: "故事舞台 客观状态", source: "storyland_state",
    // 保留 "Storyland 客观状态" / "storyland" 作为兜底, 老快照里的 prompt 仍然
    // 以原名落盘, 重命名后能继续匹配上.
    matches: ["故事舞台 客观状态", "Storyland 客观状态", "客观状态", "storyland"],
    group: "context", hint: "需 SPO 三元组 / 角色 ledger / 情绪轨迹（待前章完成后由 Truth 系统沉淀）" },
  { title: "读者视角记忆", source: "reader_memory",             matches: ["读者视角记忆"],
    group: "context", hint: "需 章节号 > 1 且已生成前章摘要 / 锚点" },
  // —— 系统级 ——
  { title: "用户写作偏好", source: "user_preferences",          matches: ["用户写作偏好"],
    group: "system", hint: "请在「设置 → 写作偏好」填写禁词 / 风格规则" },
  { title: "创作技能",     source: "skills",                    matches: ["创作技能", "技能"],
    group: "system", hint: "请在「设置 → 自学技能」启用至少一个 SKILL" },
  { title: "平台风格",     source: "platform_directive",        matches: ["平台风格", "平台指令"],
    group: "system", hint: "请在项目设置选择「平台 + 题材」并完成市场画像提取（基础+高级特征已并入此 loader）" },
];

const RAG_GROUP_LABEL: Record<string, string> = {
  system: "系统级", context: "上下文", user: "章节专属",
};
const RAG_GROUP_COLOR: Record<string, string> = {
  system: "var(--purple)", context: "var(--jade)", user: "var(--accent)",
};

/** Parse a rendered prompt into `## title` → body sections. Heuristic-based;
 *  matches the parser used by PromptInspector so RAG预览 surfaces the same view. */
function parsePromptSections(prompt: string): Map<string, string> {
  const out = new Map<string, string>();
  if (!prompt) return out;
  let curTitle = "", curBody = "";
  for (const line of prompt.split("\n")) {
    const m = line.match(/^##\s+(.+?)\s*$/);
    if (m) {
      if (curTitle) out.set(curTitle, curBody);
      curTitle = m[1];
      curBody = "";
    } else if (curTitle) {
      curBody += line + "\n";
    }
  }
  if (curTitle) out.set(curTitle, curBody);
  return out;
}

/** RAGLoaderList — RAG tab 的主要内容块。
 *  · 顶部状态条：已注入 N / 总数 · prompt 长度 · 三组各自统计 · 刷新
 *  · 按 system / context / user 三大分组渲染；每组带左侧彩条、组级 mini-counter
 *  · 每个 loader 一行 details，默认全部展开；summary 末尾用 ^ / ▾ 指引展开收起 */
function RAGLoaderList({ projectId, chapterId, chapterNum }: {
  projectId: string; chapterId: string; chapterNum: number;
}) {
  const { toast } = useToast();
  const [loading, setLoading] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    if (!projectId || !chapterId) return;
    setLoading(true);
    try {
      const res = await apiPost<{ status: string; prompt: string }>(
        "/api/generation/quick-generate",
        { project_id: projectId, chapter_id: chapterId, chapter_num: chapterNum,
          synopsis: "", characters: [], prompt_only: true },
      );
      setPrompt(res.prompt || "");
      setLoaded(true);
    } catch (e: any) {
      toast(`获取 RAG 失败：${e?.message || ""}`, "error");
    } finally { setLoading(false); }
  }, [projectId, chapterId, chapterNum, toast]);

  useEffect(() => {
    setLoaded(false);
    setPrompt("");
    if (projectId && chapterId) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, chapterId, chapterNum]);

  const sections = parsePromptSections(prompt);
  const entries = RAG_PREVIEW_SECTIONS.map(expected => {
    const body = sectionMatch(sections, expected.matches).trim();
    return { expected, body, present: body.length > 0 };
  });
  const filled = entries.filter(e => e.present).length;
  const groups: ("system" | "context" | "user")[] = ["user", "context", "system"];

  return (
    <div style={{
      marginTop: 16, padding: "12px 14px",
      background: "var(--bg-surface)",
      border: "1px solid var(--border)", borderRadius: 10,
    }}>
      <div style={{
        display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap",
        marginBottom: 10, paddingBottom: 8,
        borderBottom: "1px solid var(--border-subtle)",
      }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)" }}>
          RAG 注入内容
        </span>
        <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          · {loaded ? `${filled}/${RAG_PREVIEW_SECTIONS.length} 已注入 · ${prompt.length} 字` : "拉取中…"}
        </span>
        <span style={{ flex: 1, minWidth: 8 }} />
        <button className="btn" style={{ fontSize: 10.5, padding: "2px 10px" }}
          onClick={load} disabled={loading}
          title="重新渲染当前章节的 RAG prompt">
          {loading ? "刷新中..." : "刷新"}
        </button>
      </div>
      {!loaded && loading ? (
        <div className="text-xs text-muted" style={{ padding: "12px 0", textAlign: "center" }}>
          正在渲染 RAG prompt...
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {groups.map(g => {
            const groupEntries = entries.filter(e => e.expected.group === g);
            if (groupEntries.length === 0) return null;
            const groupFilled = groupEntries.filter(e => e.present).length;
            const color = RAG_GROUP_COLOR[g];
            return (
              <div key={g}>
                <div style={{
                  display: "flex", alignItems: "baseline", gap: 6,
                  paddingLeft: 8, marginBottom: 4,
                  borderLeft: `3px solid ${color}`,
                }}>
                  <span style={{
                    fontSize: 11, fontWeight: 700, color, letterSpacing: 0.5,
                  }}>
                    {RAG_GROUP_LABEL[g]}
                  </span>
                  <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
                    · {groupFilled}/{groupEntries.length} 已注入
                  </span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {groupEntries.map(({ expected, body, present }) => (
                    <RAGLoaderRow key={expected.title}
                      title={expected.title}
                      hint={expected.hint}
                      body={body} present={present} color={color} />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Single row in RAGLoaderList — default-expanded; summary footer shows
 *  ▴ when open and ▾ when collapsed. Renders the loader's body when it
 *  injected something, otherwise the hint that explains how to get the
 *  loader to fire. (前期的诊断面板已按用户要求移除.) */
function RAGLoaderRow({ title, hint, body, present, color }: {
  title: string; hint: string; body: string; present: boolean; color: string;
}) {
  const [open, setOpen] = useState(true);
  return (
    <details open={open}
      onToggle={e => setOpen((e.currentTarget as HTMLDetailsElement).open)}
      style={{
        padding: "5px 10px",
        background: present ? "var(--bg-surface-2)" : "transparent",
        borderLeft: `2px solid ${present ? color : "var(--border)"}`,
        borderRadius: 4,
      }}>
      <summary style={{
        cursor: "pointer",
        fontSize: 11.5,
        color: present ? "var(--text-primary)" : "var(--text-tertiary)",
        display: "flex", alignItems: "center", gap: 8,
        listStyle: "none",
      }}>
        <span style={{
          width: 6, height: 6, borderRadius: 3, flexShrink: 0,
          background: present ? color : "var(--text-disabled)",
          opacity: present ? 1 : 0.5,
        }} />
        <strong>{title}</strong>
        <span style={{ flex: 1 }} />
        <span style={{
          fontSize: 10, color: present ? color : "var(--text-disabled)",
          fontWeight: 600, flexShrink: 0,
        }}>
          {present ? `${body.length} 字` : "未注入"}
        </span>
        <span style={{
          fontSize: 11, color: "var(--text-tertiary)", flexShrink: 0,
          width: 12, textAlign: "center",
        }}>
          {open ? "▴" : "▾"}
        </span>
      </summary>
      {present ? (
        <pre style={{
          marginTop: 6, padding: 8, background: "var(--bg-app)",
          fontSize: 10.5, lineHeight: 1.6, fontFamily: "var(--font-mono)",
          color: "var(--text-secondary)",
          maxHeight: 280, overflow: "auto", borderRadius: 4,
          whiteSpace: "pre-wrap", wordBreak: "break-word",
        }}>{body}</pre>
      ) : (
        hint && (
          <div style={{
            marginTop: 4, paddingLeft: 14, fontSize: 10,
            color: "var(--text-tertiary)", lineHeight: 1.5,
            fontStyle: "italic",
          }}>
            ↳ {hint}
          </div>
        )
      )}
    </details>
  );
}


/** RAG预览 loader-injection panel: fetches the rendered prompt on demand
 *  (prompt_only=true, no LLM call) and shows which of the 16 sections were
 *  actually injected — replaces the old floating prompt-inspector ball. */
function LoaderInjectionPreview({ projectId, chapterId, chapterNum }: {
  projectId: string; chapterId: string; chapterNum: number;
}) {
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [prompt, setPrompt] = useState("");
  const [showFull, setShowFull] = useState(false);

  const load = useCallback(async () => {
    if (!projectId || !chapterId) return;
    setLoading(true);
    try {
      const res = await apiPost<{ status: string; prompt: string }>(
        "/api/generation/quick-generate",
        {
          project_id: projectId, chapter_id: chapterId, chapter_num: chapterNum,
          synopsis: "", characters: [], prompt_only: true,
        },
      );
      setPrompt(res.prompt || "");
    } catch (e: any) {
      toast(`获取 prompt 失败：${e?.message || ""}`, "error");
    } finally { setLoading(false); }
  }, [projectId, chapterId, chapterNum, toast]);

  // Lazy-load the rendered prompt the first time the user opens the preview.
  const onToggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !prompt && !loading) load();
  };

  const copyAll = () => {
    if (!prompt) return;
    navigator.clipboard.writeText(prompt).then(
      () => toast("已复制完整 prompt", "success"),
      () => toast("复制失败", "error"),
    );
  };

  const sections = parsePromptSections(prompt);
  const filled = RAG_PREVIEW_SECTIONS.filter(
    s => sectionMatch(sections, s.matches).trim().length > 0,
  ).length;

  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--border)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <button onClick={onToggle} style={{
          flex: 1, textAlign: "left", background: "none", border: "none", padding: "2px 0",
          cursor: "pointer", fontSize: 11, fontWeight: 600, color: "var(--text-secondary)",
        }}>
          {open ? "收起" : "展开"} Prompt 注入预览
          {prompt && <span style={{ marginLeft: 6, color: "var(--text-tertiary)", fontWeight: 400 }}>
            （{filled}/{RAG_PREVIEW_SECTIONS.length} 已注入 · {prompt.length} 字）
          </span>}
        </button>
        {open && (
          <>
            <button className="btn" style={{ fontSize: 9, padding: "1px 7px" }}
              onClick={load} disabled={loading}
              title="重新拉取渲染后的 prompt">
              {loading ? "..." : "刷新"}
            </button>
            <button className="btn" style={{ fontSize: 9, padding: "1px 7px" }}
              onClick={copyAll} disabled={!prompt}
              title="复制完整 prompt（用于网页版大模型）">
              复制
            </button>
            <button className="btn" style={{ fontSize: 9, padding: "1px 7px" }}
              onClick={() => setShowFull(s => !s)} disabled={!prompt}>
              {showFull ? "收起完整" : "看完整"}
            </button>
          </>
        )}
      </div>

      {open && (
        <div style={{ marginTop: 6 }}>
          {loading && !prompt ? (
            <div className="text-xs text-muted" style={{ padding: "8px 0" }}>渲染 prompt 中...</div>
          ) : !prompt ? (
            <div className="text-xs text-muted" style={{ padding: "8px 0" }}>
              点击「刷新」拉取渲染后的 prompt 以查看 loader 注入状态。
            </div>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 3 }}>
              {RAG_PREVIEW_SECTIONS.map(expected => {
                const body = sectionMatch(sections, expected.matches).trim();
                const present = body.length > 0;
                return (
                  <details key={expected.title} style={{
                    padding: "3px 8px",
                    background: present ? "var(--bg-surface-2)" : "transparent",
                    borderLeft: `3px solid ${present ? "var(--accent)" : "var(--border)"}`,
                    borderRadius: 3,
                  }}>
                    <summary style={{
                      cursor: present ? "pointer" : "default", fontSize: 11,
                      color: present ? "var(--text-primary)" : "var(--text-tertiary)",
                    }}>
                      <strong>{expected.title}</strong>
                      <span style={{ marginLeft: 8, color: "var(--text-tertiary)", fontSize: 10 }}>
                        {present ? `${body.length} 字` : "未注入"}
                      </span>
                    </summary>
                    {present && (
                      <pre style={{
                        marginTop: 4, padding: 6, background: "var(--bg-surface)",
                        fontSize: 10, fontFamily: "var(--font-mono)",
                        maxHeight: 160, overflow: "auto", borderRadius: 3,
                        whiteSpace: "pre-wrap", wordBreak: "break-word",
                      }}>{body}</pre>
                    )}
                  </details>
                );
              })}
            </div>
          )}

          {showFull && prompt && (
            <details open style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer", fontSize: 11, fontWeight: 600 }}>完整 prompt</summary>
              <pre style={{
                marginTop: 6, padding: 8, background: "var(--bg-surface-2)",
                fontSize: 10, fontFamily: "var(--font-mono)",
                maxHeight: 320, overflow: "auto", borderRadius: 4,
                whiteSpace: "pre-wrap",
              }}>{prompt}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

/** Transparency panel: skills used + per-item RAG context (de-selectable).
 *  The RAG section doubles as the prompt-loader inspector — the floating ball
 *  trigger was retired in favour of an inline preview here. */
function ContextPanel({ manifest, skillSelection, ragExcludes, onToggleSkill, onToggleRagItem, onRefresh, projectId, chapterId, chapterNum }: {
  manifest: ContextManifest | null;
  skillSelection: Record<string, boolean>;
  ragExcludes: Set<string>;
  onToggleSkill: (name: string) => void;
  onToggleRagItem: (key: string) => void;
  onRefresh?: () => void;
  projectId?: string;
  chapterId?: string;
  chapterNum?: number;
}) {
  const [skillOpen, setSkillOpen] = useState(false);
  const [ragOpen, setRagOpen] = useState(true);
  if (!manifest) return null;

  const sectionHeader = (open: boolean, toggle: () => void, text: string,
                         rightEl?: React.ReactNode) => (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <button onClick={toggle} style={{
        flex: 1, textAlign: "left", background: "none", border: "none", padding: "2px 0",
        cursor: "pointer", fontSize: 11, fontWeight: 700, color: "var(--text-secondary)",
        display: "flex", alignItems: "center", gap: 5,
      }}>
        <span style={{ fontSize: 9, color: "var(--text-tertiary)" }}>{open ? "[-]" : "[+]"}</span>{text}
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
      marginBottom: 8, padding: "10px 12px", background: "var(--bg-surface)",
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
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, margin: "6px 0 10px" }}>
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
      <div style={{ marginTop: 6 }}>
        {sectionHeader(ragOpen, () => setRagOpen(o => !o),
          `RAG 预览（已启用 ${selCount}/${allKeys.length} 项）`,
          onRefresh ? (
            <button className="btn" style={{ fontSize: 9, padding: "1px 7px" }}
              onClick={onRefresh}
              title="重新加载 RAG —— 在角色卡 / 世界书 / 大纲等处更新数据后点此同步">
              刷新
            </button>
          ) : undefined)}
      </div>
      {ragOpen && (
        <div style={{ marginTop: 4 }}>
          {manifest.rag.map(cat => {
            const sel = cat.items.filter(it => !ragExcludes.has(`${cat.key}::${it.id}`)).length;
            return (
              <div key={cat.key} style={{ marginTop: 7 }}>
                <div className="text-xs" style={{ color: "var(--text-tertiary)", marginBottom: 3 }}>
                  {cat.label}{cat.items.length > 0 ? `（已选 ${sel}/${cat.items.length}）` : ""}
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
          {projectId && chapterId && (
            <LoaderInjectionPreview
              projectId={projectId}
              chapterId={chapterId}
              chapterNum={chapterNum || 1}
            />
          )}
        </div>
      )}
    </div>
  );
}

/** 手动模式专用 — 跟在用户的「指令」消息后面的 paste-back 卡片.
 *  state: textarea 文本是 local; 应用后通过 onApply(text) 上报给
 *  EditorPage, 由它给本卡 mark applied + insert Writer message.
 *  Applied 后 textarea 折叠成 "✓ 已应用 · N 字" 摘要, 但消息卡仍留在
 *  对话里, 用户能滚回去看是哪一轮粘的. */
function InChatManualPasteCard({
  prompt, applied, pastedLen, timestamp, onApply,
}: {
  prompt: string;
  applied: boolean;
  pastedLen?: number;
  timestamp: number;
  onApply: (text: string) => void;
}) {
  const { toast } = useToast();
  const [paste, setPaste] = useState("");
  const [copying, setCopying] = useState(false);
  const copyPrompt = async () => {
    if (!prompt) { toast("Prompt 为空", "error"); return; }
    setCopying(true);
    try {
      try { await navigator.clipboard.writeText(prompt); }
      catch {
        const ta = document.createElement("textarea");
        ta.value = prompt; ta.style.position = "fixed"; ta.style.opacity = "0";
        document.body.appendChild(ta); ta.select();
        try { document.execCommand("copy"); } finally { document.body.removeChild(ta); }
      }
      toast(`已复制 ${prompt.length.toLocaleString()} 字 prompt`, "success");
    } finally { setCopying(false); }
  };
  const apply = () => {
    if (!paste.trim()) return;
    onApply(paste);
    setPaste("");
  };
  // Applied state — collapse the textarea into a tiny success badge.
  if (applied) {
    return (
      <div style={{
        display: "inline-flex", alignItems: "center", gap: 8,
        padding: "6px 12px", borderRadius: 999,
        background: "var(--jade-subtle, rgba(52,199,123,0.12))",
        color: "var(--jade)",
        border: "1px solid var(--jade)",
        fontSize: 11, fontWeight: 600,
      }}>
        <span style={{ fontSize: 13 }}>✓</span>
        <span>已应用网页大模型回复</span>
        {pastedLen != null && (
          <span style={{ fontSize: 10, fontWeight: 400, opacity: 0.85 }}>
            · {pastedLen.toLocaleString()} 字
          </span>
        )}
        <span style={{ fontSize: 10, fontWeight: 400, opacity: 0.7 }}>
          · {new Date(timestamp).toLocaleString("zh-CN", { hour: "numeric", minute: "numeric" })}
        </span>
      </div>
    );
  }
  return (
    <div style={{
      border: "1px solid var(--indigo)",
      borderLeft: "3px solid var(--indigo)",
      borderRadius: 10,
      background: "var(--bg-surface)",
      padding: 12,
      display: "flex", flexDirection: "column", gap: 10,
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          width: 22, height: 22, borderRadius: 6,
          background: "var(--indigo)", color: "#fff",
          fontSize: 10, fontWeight: 700, flexShrink: 0,
        }}>WEB</span>
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
          网页大模型 · 粘贴回复
        </span>
        <span style={{
          marginLeft: "auto",
          fontSize: 10, color: "var(--text-tertiary)",
        }}>
          ① 复制 prompt → ② 网页 LLM 跑 → ③ 粘回并应用
        </span>
      </div>
      {/* Step 1 — copy prompt */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <button
          onClick={copyPrompt} disabled={copying || !prompt}
          style={{
            padding: "5px 14px", borderRadius: 6, fontSize: 11, fontWeight: 600,
            background: "var(--indigo)", color: "#fff",
            border: "none", cursor: copying ? "wait" : "pointer",
            opacity: prompt ? 1 : 0.5,
          }}
        >
          {copying ? "复制中…" : "复制 prompt"}
        </button>
        <span style={{ fontSize: 10.5, color: "var(--text-tertiary)" }}>
          {prompt
            ? `${prompt.length.toLocaleString()} 字 · 含本条指令 + 当前 RAG 上下文`
            : "Prompt 不可用"}
        </span>
      </div>
      {/* Step 2 — paste textarea */}
      <textarea
        value={paste}
        onChange={e => setPaste(e.target.value)}
        placeholder="把网页大模型的回复粘到这里…"
        rows={4}
        style={{
          width: "100%", boxSizing: "border-box",
          padding: "8px 10px", fontSize: 12, lineHeight: 1.55,
          background: "var(--bg-app)", color: "var(--text-primary)",
          border: "1px solid var(--border)", borderRadius: 6,
          fontFamily: "var(--font-sans)",
          resize: "vertical", minHeight: 80, maxHeight: 240,
          outline: "none",
        }}
      />
      {/* Step 3 — apply */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
          {paste.trim() ? `${paste.length.toLocaleString()} 字待应用` : "粘贴后启用「应用回复」"}
        </span>
        <button
          onClick={apply} disabled={!paste.trim()}
          style={{
            padding: "6px 18px", borderRadius: 8,
            background: paste.trim() ? "var(--jade)" : "transparent",
            color: paste.trim() ? "#fff" : "var(--text-disabled)",
            border: `1px solid ${paste.trim() ? "var(--jade)" : "var(--border)"}`,
            fontSize: 12, fontWeight: 600,
            cursor: paste.trim() ? "pointer" : "not-allowed",
          }}
        >
          应用回复
        </button>
      </div>
    </div>
  );
}

/** Claude-style 一体化输入卡片. 顶部 chip strip = 本条 RAG 加载;
 *  中部 textarea = 指令 (或 manual 模式下的粘贴框); 底部 action row
 *  = 提示 + 创作 / 应用回复 按钮. 上方再叠一个手动模式 toggle pill.
 *  Auto: textarea→指令 → 创作 → onSendMessage
 *  Manual: textarea→粘贴 → 应用回复 → onApplyManualResult */
function SingleModeComposer({
  chatInput, onChatInputChange, onSendMessage,
  manualMode, onToggleManualMode, onApplyManualResult,
  manifest, ragExcludes, onToggleRagLoader, onSwitchToRagTab,
}: {
  chatInput: string;
  onChatInputChange: (v: string) => void;
  onSendMessage: () => void;
  manualMode: boolean;
  onToggleManualMode?: () => void;
  onApplyManualResult?: (text: string) => void;
  manifest: ContextManifest | null;
  ragExcludes: Set<string>;
  onToggleRagLoader?: (key: string, items: { id: string }[]) => void;
  onSwitchToRagTab?: () => void;
}) {
  const [focused, setFocused] = useState(false);
  const togglable = (manifest?.rag || []).filter(r => r.items && r.items.length > 0);
  const isOn = (r: { key: string; items: { id: string }[] }) =>
    r.items.length > 0 && !r.items.every(it => ragExcludes.has(`${r.key}::${it.id}`));
  const onCount = togglable.filter(isOn).length;

  // Manual 跟 auto 在主输入框语义一致 — textarea 都是「用户的具体
  // 指令」, 按钮都是「创作」, 走同一个 onSendMessage. 走到 sendChatMessage
  // 里再按 manualMode 分流: auto 调 API, manual 推一条 paste-back 卡片
  // 进对话, 让用户在聊天里完成复制 prompt / 粘贴回复 / 应用三步.
  const canSubmit = true;  // 留空也允许 → "按大纲创作本章"
  const handleSubmit = () => {
    onSendMessage();
  };
  const submitLabel = "创作";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 10 }}>
      {/* Manual mode toggle pill — 居中悬浮在输入卡片正上方 */}
      {onToggleManualMode && (
        <div style={{ display: "flex", justifyContent: "center" }}>
          <button
            onClick={onToggleManualMode}
            title={manualMode
              ? "关闭手动模式 — 回到内置 API 创作"
              : "打开手动模式 — 用网页大模型手动跑 prompt + 粘回结果"}
            style={{
              display: "inline-flex", alignItems: "center", gap: 8,
              padding: "5px 14px",
              borderRadius: 999,
              border: `1px solid ${manualMode ? "var(--indigo)" : "var(--border)"}`,
              background: manualMode ? "var(--indigo-subtle, var(--bg-surface-2))" : "var(--bg-surface)",
              color: manualMode ? "var(--indigo)" : "var(--text-secondary)",
              fontSize: 11, fontWeight: 600, cursor: "pointer",
              transition: "all 0.15s",
            }}
          >
            <span style={{
              display: "inline-block", width: 24, height: 12, borderRadius: 6,
              background: manualMode ? "var(--indigo)" : "var(--border)",
              position: "relative", transition: "background 0.15s",
            }}>
              <span style={{
                position: "absolute", top: 1, left: manualMode ? 13 : 1,
                width: 10, height: 10, borderRadius: "50%",
                background: "#fff",
                transition: "left 0.15s",
              }} />
            </span>
            <span>手动模式</span>
            {manualMode && <span style={{ fontSize: 10, opacity: 0.85 }}>· 网页大模型</span>}
          </button>
        </div>
      )}

      {/* Composer card */}
      <div style={{
        border: `1px solid ${focused ? "var(--accent)" : "var(--border)"}`,
        borderRadius: 14,
        background: "var(--bg-surface)",
        boxShadow: focused ? "0 0 0 3px var(--accent-glow, rgba(224,85,69,0.12))" : "none",
        transition: "border-color 0.15s, box-shadow 0.15s",
        overflow: "hidden",
      }}>
        {/* Chip strip — per-message RAG loaders. 文案明确强调"本条";
            一次性展示所有 togglable loader, 不折叠. chip 大小统一,
            grid-like 流式排列保证对齐. */}
        {manifest && onToggleRagLoader && togglable.length > 0 && (
          <div style={{
            padding: "10px 12px",
            background: "var(--bg-surface-2)",
            borderBottom: "1px solid var(--border)",
            display: "flex", flexDirection: "column", gap: 8,
          }}>
            {/* Header row */}
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{
                fontSize: 10, color: "var(--text-tertiary)", fontWeight: 700,
                textTransform: "uppercase", letterSpacing: 0.8,
              }}>本条加载</span>
              <span style={{
                fontSize: 10, color: "var(--text-tertiary)",
                padding: "1px 7px", borderRadius: 8,
                background: "var(--bg-surface)", border: "1px solid var(--border)",
                lineHeight: 1.4, fontWeight: 600,
              }}>{onCount}/{togglable.length}</span>
              <span style={{ flex: 1 }} />
              {onSwitchToRagTab && (
                <button onClick={onSwitchToRagTab} title="到 RAG tab 查看具体注入内容" style={{
                  background: "none", border: "none", padding: 0,
                  color: "var(--accent)", cursor: "pointer", fontSize: 10.5, fontWeight: 500,
                }}>详情 →</button>
              )}
            </div>
            {/* Chip grid — flex wrap with uniform gap; 全部 loader 一次性展示 */}
            <div style={{
              display: "flex", flexWrap: "wrap",
              gap: "6px 6px",
            }}>
              {togglable.map(r => {
                const on = isOn(r);
                return (
                  <button
                    key={r.key}
                    onClick={() => onToggleRagLoader(r.key, r.items)}
                    title={`${on ? "取消" : "启用"} ${r.label}（本条指令）`}
                    style={{
                      display: "inline-flex", alignItems: "center", gap: 5,
                      padding: "4px 10px", borderRadius: 14,
                      border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
                      background: on ? "var(--accent-subtle)" : "var(--bg-surface)",
                      color: on ? "var(--accent)" : "var(--text-secondary)",
                      fontSize: 11, lineHeight: 1.45, cursor: "pointer",
                      fontWeight: on ? 600 : 400, transition: "all 0.12s",
                      whiteSpace: "nowrap",
                    }}
                  >
                    <span style={{
                      display: "inline-block", width: 7, height: 7, borderRadius: "50%",
                      background: on ? "var(--accent)" : "var(--text-disabled)",
                      flexShrink: 0,
                    }} />
                    <span>{r.label}</span>
                    {r.items.length > 1 && (
                      <span style={{
                        fontSize: 9.5, opacity: 0.75, fontWeight: 400,
                        padding: "0 4px", borderRadius: 6,
                        background: on ? "rgba(0,0,0,0.06)" : "var(--bg-surface-2)",
                      }}>{r.items.length}</span>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Textarea — Claude-style: no inner border, soft padding */}
        <textarea
          value={chatInput}
          onChange={e => onChatInputChange(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onKeyDown={e => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit(); }
          }}
          placeholder={manualMode
            ? "本条指令 · Enter 发送后, 复制 prompt 到网页大模型, 把回复粘回对话框中的应答卡片"
            : "回复或指令 ·  Enter 发送 · Shift+Enter 换行"}
          rows={3}
          style={{
            display: "block", width: "100%", boxSizing: "border-box",
            border: "none", outline: "none", background: "transparent",
            color: "var(--text-primary)",
            fontFamily: "var(--font-sans)",
            fontSize: 14, lineHeight: 1.6,
            padding: "14px 16px 8px",
            minHeight: 80, maxHeight: 240, resize: "vertical",
          }}
        />

        {/* Action row — char count + Enter hint + submit */}
        <div style={{
          padding: "6px 10px 8px 16px",
          display: "flex", alignItems: "center", gap: 10,
          borderTop: "1px solid var(--border)",
          background: "var(--bg-surface)",
        }}>
          <div style={{ fontSize: 10.5, color: "var(--text-tertiary)", display: "flex", gap: 8, alignItems: "center" }}>
            <span>{chatInput.trim() ? `${chatInput.length.toLocaleString()} 字` : "留空 = 按大纲创作"}</span>
            {manualMode && (
              <span style={{ color: "var(--indigo)", fontWeight: 600 }}>
                · 手动模式
              </span>
            )}
          </div>
          <span style={{ flex: 1 }} />
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            title={manualMode ? "应用粘贴的回复" : "创作（Enter）"}
            style={{
              padding: "6px 18px", borderRadius: 8,
              background: canSubmit ? "var(--accent)" : "var(--bg-surface-2)",
              color: canSubmit ? "#fff" : "var(--text-disabled)",
              border: "none", cursor: canSubmit ? "pointer" : "not-allowed",
              fontSize: 13, fontWeight: 600,
              transition: "background 0.15s",
              display: "inline-flex", alignItems: "center", gap: 6,
            }}
          >
            {submitLabel}
            <span style={{ fontSize: 11, opacity: 0.85 }}>↵</span>
          </button>
        </div>
      </div>
    </div>
  );
}

/** Inline progress bubble for an in-flight System status message.
 *  Animated bar fills from 0 → ~95% over `etaSec` so the user gets a
 *  realistic completion feel even though /quick-generate is a single
 *  blocking call. Tick driven by setInterval; cleared on unmount.
 *  When ETA elapses, the bar holds at 95% and the label says "收尾中…". */
function ProgressBubble({ label, etaSec, startedAt }: {
  label: string; etaSec: number; startedAt: number;
}) {
  const [, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, []);
  const elapsed = (Date.now() - startedAt) / 1000;
  const pct = Math.min(95, Math.max(2, (elapsed / Math.max(1, etaSec)) * 95));
  const remaining = Math.max(0, Math.ceil(etaSec - elapsed));
  const stillRunning = remaining > 0;
  return (
    <div style={{ minWidth: 220 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6, fontSize: 12, color: "var(--text-secondary)" }}>
        <span>{label}{stillRunning ? "…" : "（收尾中…）"}</span>
        <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
          {stillRunning ? `预计 ${remaining}s` : `已 ${Math.round(elapsed)}s`}
        </span>
      </div>
      <div style={{
        height: 6, borderRadius: 3,
        background: "var(--border)", overflow: "hidden",
      }}>
        <div style={{
          height: "100%", width: `${pct}%`,
          background: "linear-gradient(90deg, var(--accent), var(--gold))",
          borderRadius: 3,
          transition: "width 0.45s ease-out",
        }} />
      </div>
    </div>
  );
}

// 生成前成本预估 (spec LLM调用·机制1): 本章预估 LLM 调用数与
// token / USD，导演模式以场景数为主变量；超出单章成本上限时展示
// 降级建议（导演转单 Agent / 跳过 LLM 评估层）。
function CostEstimateBlock({ mode, projectId, chapterId }: {
  mode: "single" | "cluster"; projectId?: string; chapterId?: string;
}) {
  const [est, setEst] = useState<any>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    let alive = true;
    const apiMode = mode === "cluster" ? "director" : "single";
    apiGet<any>(
      `/api/generation/cost-estimate?mode=${apiMode}` +
      (projectId ? `&project_id=${encodeURIComponent(projectId)}` : "") +
      (chapterId ? `&chapter_id=${encodeURIComponent(chapterId)}` : ""),
    ).then(r => { if (alive) setEst(r); }).catch(() => { if (alive) setEst(null); });
    return () => { alive = false; };
  }, [mode, projectId, chapterId]);

  if (!est?.requested) return null;
  const req = est.requested;
  const eff = est.effective || req;
  const degraded = eff.degraded;
  const overCap = eff.over_cap;
  const stepLabels: Record<string, string> = {
    director_to_single: "导演模式降级为单 Agent",
    skip_llm_eval: "跳过 LLM 评估层（确定性检测仍运行）",
  };
  return (
    <div style={{
      padding: "6px 10px", marginBottom: 6, borderRadius: 6, fontSize: 11,
      background: "var(--bg-secondary)",
      border: `1px solid ${overCap ? "var(--error)" : degraded ? "var(--gold)" : "var(--border-subtle)"}`,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}
        onClick={() => setOpen(!open)}>
        <span style={{ color: "var(--text-secondary)" }}>
          本章预估：{req.llm_calls} 次 LLM 调用
          {req.estimated_usd > 0 ? ` · 约 $${req.estimated_usd.toFixed(3)}` : ""}
          {` · 输入约 ${Math.round(req.input_tokens / 1000)}K tokens`}
        </span>
        {degraded && (
          <span style={{ color: overCap ? "var(--error)" : "var(--gold)", fontWeight: 600 }}>
            {overCap ? "降级后仍超上限" : "已超上限，建议降级"}
          </span>
        )}
        <span style={{ marginLeft: "auto", color: "var(--text-disabled)" }}>{open ? "收起" : "明细"}</span>
      </div>
      {open && (
        <div style={{ marginTop: 6, color: "var(--text-tertiary)" }}>
          {est.cap_usd > 0 && <div>单章成本上限：${est.cap_usd}（设置页 chapter_cost_cap_usd）</div>}
          {(eff.degradation_steps || []).map((s: string) => (
            <div key={s} style={{ color: "var(--gold)" }}>降级建议：{stepLabels[s] || s}</div>
          ))}
          {(req.breakdown || []).map((b: any) => (
            <div key={b.call} style={{ display: "flex", gap: 8 }}>
              <span style={{ width: 120 }}>{b.call}</span>
              <span>{b.input_tokens} in / {b.output_tokens} out</span>
              {b.usd > 0 && <span>${b.usd.toFixed(4)}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function InspireTab({ mode, steps, generating, onStart, onStartPlain, chatMessages, chatInput, onChatInputChange, onSendMessage, waitingForConfirm, onConfirmContinue, onRollback, onWriteToEditor, onStopPipeline, paused, onPauseResume, projectId, chapterId, chapterNum, modelChanged, onDismissModelChange, onRestartWithNewModel, onFetchPrompt, onApplyPaste, onOpenWebLLM, manualMode, onToggleManualMode, manualPrompt, onSubmitManual, manifest, skillSelection, onToggleSkill, ragExcludes, onToggleRagItem, onToggleRagLoader, onSwitchToRagTab, onRefreshManifest, onDeleteMessage, onEditPromptForMsg, onApplyManualResult, onApplyInChatManualPaste }: {
  mode: "single" | "cluster";
  steps: PipelineStatus[]; generating: boolean; onStart: (manual?: boolean) => void; onStartPlain?: () => void; chatMessages: ChatMessage[]; chatInput: string;
  onChatInputChange: (v: string) => void; onSendMessage: () => void; waitingForConfirm: boolean; onConfirmContinue: () => void; onRollback?: (stepIndex: number) => void; onWriteToEditor?: () => void; onStopPipeline?: () => void;
  paused?: boolean; onPauseResume?: () => void; projectId?: string; chapterId?: string; chapterNum?: number;
  modelChanged?: boolean; onDismissModelChange?: () => void; onRestartWithNewModel?: () => void;
  onFetchPrompt?: () => Promise<string>; onApplyPaste?: (text: string) => void; onDeleteMessage?: (index: number) => void;
  onOpenWebLLM?: () => void;
  manualMode?: boolean; onToggleManualMode?: () => void;
  manualPrompt?: { step: string; prompt: string } | null; onSubmitManual?: (text: string) => void;
  manifest?: ContextManifest | null;
  skillSelection?: Record<string, boolean>; onToggleSkill?: (name: string) => void;
  ragExcludes?: Set<string>; onToggleRagItem?: (key: string) => void; onRefreshManifest?: () => void;
  /** Toggle ALL items of one loader at once — used by the per-message
   *  loader selector that lives above the chat input. */
  onToggleRagLoader?: (loaderKey: string, items: { id: string }[]) => void;
  /** Jump to the RAG tab — used by the "详情请到 RAG tab 查看" link. */
  onSwitchToRagTab?: () => void;
  /** Open the per-message prompt edit modal for a given chat message
   *  index. EditorPage owns the modal state. */
  onEditPromptForMsg?: (msgIdx: number) => void;
  /** Apply a manual-mode pasted web-LLM result to the chat. Same path
   *  as the inline manual panel below the input. */
  onApplyManualResult?: (text: string) => void;
  /** Apply a paste from a specific in-chat ManualPaste card (by its
   *  message index). Updates that card to "✓ 已应用" and inserts the
   *  Writer message right after it. */
  onApplyInChatManualPaste?: (msgIdx: number, text: string) => void;
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
    const map: Record<string, string> = { "Scene Director": "SD", "Actor Agents": "AC", "Editor-Writer": "EW", "Writer": "作", "Evaluator": "EV", "User": "U", "System": "SY" };
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
      {/* Chat area — flex:1 真正撑满 panel-body 列;
          单 mode 下手动模式 toggle 开后, 末尾追加 3 步引导卡片 */}
      <div style={{ flex: 1, overflowY: "auto", border: "1px solid var(--border)", borderRadius: "var(--radius-sm, 6px)", padding: 8, marginBottom: 10, minHeight: 0, background: "var(--bg-app)" }}>
        {chatMessages.length === 0 && !generating && (
          <div style={{ padding: "32px 16px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 13 }}>
            {mode === "cluster"
              ? "集群式智能体创作"
              : "在下方输入本次创作的具体指令，点「创作」开始 — 留空即按大纲创作。"}
          </div>
        )}
        {chatMessages.map((msg, i) => {
          const style = getAgentStyle(msg.agent, msg.agentDisplayName); const isUser = msg.agent === "User";
          const avatar = getAgentAvatar(msg.agent, msg.agentDisplayName);
          const isCharActor = msg.agent === "Actor Agents" && msg.agentDisplayName && msg.agentDisplayName !== "旁白";
          return (
            <div key={i} style={{ display: "flex", flexDirection: isUser ? "row-reverse" : "row", alignItems: "flex-start", marginBottom: 10, gap: 8 }}>
              <div style={{ width: 32, height: 32, borderRadius: "50%", background: style.bg, border: `2px solid ${style.border}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: isCharActor ? 13 : 16, flexShrink: 0, fontWeight: isCharActor ? 700 : 400, color: isCharActor ? style.border : undefined }}>{avatar}</div>
              <div style={{ maxWidth: msg.manualPaste ? "95%" : "80%", minWidth: 0, width: msg.manualPaste ? "95%" : undefined }}>
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
                              {isContinue ? ` ${opt}` : opt}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ) : msg.isQuestion ? (
                    <QuestionChoices content={msg.content} onChoose={(choice) => {
                      onChatInputChange(choice);
                    }} />
                  ) : msg.progress ? (
                    <ProgressBubble label={msg.content} etaSec={msg.progress.etaSec} startedAt={msg.progress.startedAt} />
                  ) : msg.manualPaste ? (
                    <InChatManualPasteCard
                      prompt={msg.manualPaste.prompt}
                      applied={!!msg.manualPaste.applied}
                      pastedLen={msg.manualPaste.pastedLen}
                      timestamp={msg.timestamp}
                      onApply={(text) => onApplyInChatManualPaste?.(i, text)}
                    />
                  ) : msg.content}
                </div>
                {msg.tokenEstimate && msg.agent === "User" && (
                  <div style={{
                    marginTop: 4, display: "flex", justifyContent: isUser ? "flex-end" : "flex-start", gap: 4,
                  }}>
                    <span style={{
                      display: "inline-flex", alignItems: "center", gap: 4,
                      padding: "2px 8px", borderRadius: 10,
                      fontSize: 10, lineHeight: 1,
                      background: "var(--bg-surface-2)", color: "var(--text-tertiary)",
                      border: "1px solid var(--border-subtle, var(--border))",
                    }} title="本次请求的 token 估算（cost-estimate）">
                      ~{msg.tokenEstimate.inputK}K tokens · {msg.tokenEstimate.llmCalls} 次调用
                      {msg.tokenEstimate.usd > 0 ? ` · $${msg.tokenEstimate.usd.toFixed(3)}` : ""}
                    </span>
                  </div>
                )}
                {msg.status === "done" && (
                  <div style={{ display: "flex", gap: 4, marginTop: 4 }}>
                    <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px", color: "var(--text-tertiary)" }}
                      onClick={() => { navigator.clipboard.writeText(msg.content); }}>
                      复制
                    </button>
                    {msg.promptSent && msg.agent === "User" && onEditPromptForMsg && (
                      <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px", color: "var(--text-tertiary)" }}
                        onClick={() => onEditPromptForMsg(i)}
                        title="弹窗查看本次实际发给 LLM 的 prompt，编辑后可重新生成">
                        查看 / 修改 Prompt
                      </button>
                    )}
                    {msg.promptSent && msg.agent !== "User" && (
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
                        ×
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
          {onPauseResume && (
            <button className="btn" style={{
              fontSize: 11, padding: "3px 10px", flex: 1,
              color: paused ? "var(--jade)" : "var(--gold)",
              borderColor: paused ? "var(--jade)" : "var(--gold)",
            }} onClick={onPauseResume}>
              {paused ? "恢复" : "暂停"}
            </button>
          )}
          <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)", borderColor: "var(--error)", flex: 1 }} onClick={onStopPipeline}>
            终止 Pipeline
          </button>
        </div>
      )}
      {/* Single mode Claude-style composer (auto / manual):
            [手动模式 toggle pill — 上方居中, 美观]
            ┌────────────────────────────────────────────┐
            │ 本条加载  ☑ 角色档案 · ☑ 世界书 · ☐ 灵感   │  ← 章节 RAG
            │           [▾ 详情→]                        │
            │ ────────────────────────────────────────── │
            │                                            │
            │  [textarea — instruction or paste]         │
            │                                            │
            │ ────────────────────────────────────────── │
            │ ~12K · ⏎ 发送              [创作 ↵]        │
            └────────────────────────────────────────────┘
          Auto = textarea→指令, button→创作, Enter 发. Manual = textarea
          →粘贴回复, button→应用回复, 走 onApplyManualResult. 手动模式
          的 3 步引导卡 (复制 / 粘贴 / 应用) 渲染在聊天底部. */}
      {mode === "single" && !generating && !waitingForConfirm && (
        <SingleModeComposer
          chatInput={chatInput}
          onChatInputChange={onChatInputChange}
          onSendMessage={onSendMessage}
          manualMode={!!manualMode}
          onToggleManualMode={onToggleManualMode}
          onApplyManualResult={onApplyManualResult}
          manifest={manifest || null}
          ragExcludes={ragExcludes || new Set()}
          onToggleRagLoader={onToggleRagLoader}
          onSwitchToRagTab={onSwitchToRagTab}
        />
      )}
      {/* Cluster + waitingForConfirm fall back to a plain textarea. */}
      {(mode !== "single" || generating || waitingForConfirm) && (
        <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
          <textarea className="input" value={chatInput} onChange={e => onChatInputChange(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSendMessage(); } }}
            placeholder={waitingForConfirm ? "输入修改意见，或点击确认继续..." : "输入消息与 Agent 对话..."}
            rows={1} style={{ flex: 1, fontSize: 12, padding: "6px 10px", minHeight: 32, maxHeight: 100, resize: "none" }} />
          <button className="btn-primary"
            onClick={onSendMessage}
            disabled={!chatInput.trim()}
            style={{ fontSize: 12, padding: "6px 12px", flexShrink: 0 }}>
            发送
          </button>
        </div>
      )}
      {/* Cluster mode keeps its two big legacy buttons; single mode UI
          is now fully driven by the chat input above — no extra row. */}
      {mode === "cluster" && !generating && !waitingForConfirm && (
        <div style={{ marginBottom: 6 }}>
          <ContextPanel
            manifest={manifest || null}
            skillSelection={skillSelection || {}}
            ragExcludes={ragExcludes || new Set()}
            onToggleSkill={(n) => onToggleSkill?.(n)}
            onToggleRagItem={(k) => onToggleRagItem?.(k)}
            onRefresh={onRefreshManifest}
            projectId={projectId} chapterId={chapterId} chapterNum={chapterNum}
          />
          <CostEstimateBlock mode={mode} projectId={projectId} chapterId={chapterId} />
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
                  {choices.get(hunk.id) === "old" ? " 保留原文" : "保留原文"}
                </button>
                <button
                  className={choices.get(hunk.id) === "new" ? "btn-primary" : "btn"}
                  style={{ fontSize: 10, padding: "2px 10px", background: choices.get(hunk.id) === "new" ? "var(--jade)" : undefined, border: choices.get(hunk.id) === "new" ? "none" : undefined, color: choices.get(hunk.id) === "new" ? "#fff" : undefined }}
                  onClick={() => toggle(hunk.id)}
                >
                  {choices.get(hunk.id) === "new" ? " 使用 AI" : "使用 AI"}
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
function EvalTab({ result, chapterContent, projectId, chapterId, chapterNum, manifest, skillSelection, ragExcludes, onToggleSkill, onToggleRagItem, onRefreshManifest }: {
  result: EvalResult | null; chapterContent: string; projectId: string; chapterId: string; chapterNum?: number;
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
        projectId={projectId} chapterId={chapterId} chapterNum={chapterNum}
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
