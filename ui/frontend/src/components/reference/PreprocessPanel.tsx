import React, { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPost, apiPut, apiPatch } from "../../api/client";
import { useToast } from "../shared/Toast";
import { useConfirm } from "../shared/Confirm";

interface ChapterPattern {
  name: string;
  /** User-friendly template (e.g. "第N章", "N、", "卷N"). N = chapter number. */
  format?: string;
  /** Raw regex (advanced; only used when format is empty). */
  regex?: string;
  enabled: boolean;
}

interface Chapter {
  number: number;
  parsed_number?: number | null;
  title: string;
  title_only?: string;
  raw_marker?: string;
  pattern?: string;
  volume?: string | null;
  char_count: number;
  is_author_note?: boolean;
  author_note_score?: number;
  author_note_reasons?: string[];
  is_length_outlier?: boolean;
  outlier_kind?: "短" | "长" | null;
  is_split_piece?: boolean;
  is_edited?: boolean;
  had_asides_removed?: boolean;
  is_garbled?: boolean;
  garbled_reasons?: string[];
  preview_head?: string;
  preview_tail?: string;
}

interface LogEntry { ts: number; message: string; chapter?: number | null; }

interface GapEntry {
  after_number: number;
  before_number: number;
  expected_next: number;
  missing_numbers: number[];
  missing_count: number;
  pattern: string;
}

interface PreprocessStatus {
  state: "idle" | "running" | "paused" | "done" | "error" | "cancelled";
  phase?: "" | "loading" | "matching" | "tagging" | "finalizing";
  current_chapter: number;
  total_chapters: number;
  detected_pattern?: string | null;
  flagged_count: number;
  log: LogEntry[];
  chapters?: Chapter[];
  gaps?: GapEntry[];
  candidates?: { name: string; count: number; score: number }[];
  fallback_used?: boolean;
  error?: string | null;
  persisted?: boolean;
  can_undo?: boolean;
  last_removed_chapters?: number[];
}

interface SegmentInfo {
  index: number;
  title: string;
  start_chapter: number;
  end_chapter: number;
  chapter_count?: number;
  char_count: number;
}

interface SegmentPlan {
  type: "volumes" | "chunks" | "custom";
  segments: SegmentInfo[];
  completed: number[];
  total_chapters: number;
  is_custom?: boolean;
}

/** Prefer the title-only text (just the chapter title without the
 *  number prefix). Falls back to the raw marker, then a default. */
function displayTitle(c: { title_only?: string; title?: string; number?: number }): string {
  const t = (c.title_only || "").trim();
  if (t) return t;
  return (c.title || "").trim() || `第 ${c.number ?? "?"} 章`;
}

function fmtChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`;
  return `${n.toLocaleString()} 字`;
}

interface Props {
  refId: string;
  hasFullText: boolean;
  onUpload: () => void;
  onAfterApplyExclusions?: () => void | Promise<void>;
}

export default function PreprocessPanel({ refId, hasFullText, onUpload, onAfterApplyExclusions }: Props) {
  const { toast } = useToast();
  const { confirm: confirmDialog, ConfirmHost } = useConfirm();
  const [status, setStatus] = useState<PreprocessStatus | null>(null);
  // ``excluded`` is purely user-driven. We do NOT auto-seed from the
  // is_author_note flag — that previously made the checkboxes feel
  // stuck (auto-seed re-applied on every poll). A "勾选全部疑似题外话"
  // button below the filter bar lets the user bulk-select manually.
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState<"all" | "flagged" | "outlier" | "garbled" | "kept">("all");
  const [applying, setApplying] = useState(false);
  const [cleaningGarbled, setCleaningGarbled] = useState(false);
  const [repairingEncoding, setRepairingEncoding] = useState(false);
  // Volume plan editor (moved here from PlotOutlinePanel)
  const [plan, setPlan] = useState<SegmentPlan | null>(null);
  const [planDraft, setPlanDraft] = useState<{ title: string; start_chapter: number; end_chapter: number }[] | null>(null);
  const [planSaving, setPlanSaving] = useState(false);
  // Custom chapter patterns
  const [patterns, setPatterns] = useState<ChapterPattern[]>([]);
  const [patternsOpen, setPatternsOpen] = useState(false);
  const [patternTesting, setPatternTesting] = useState<{ idx: number; count: number; preview: any[] } | null>(null);
  // Author-note keywords (user-managed). active = the in-use list, may
  // be either the user's customization or the built-in defaults.
  const [keywords, setKeywords] = useState<{ user: string[]; defaults: string[]; active: string[] }>({ user: [], defaults: [], active: [] });
  const [keywordsOpen, setKeywordsOpen] = useState(false);
  // Garbled-pattern CRUD
  const [garbledOpen, setGarbledOpen] = useState(false);
  const [garbledData, setGarbledData] = useState<{ builtin: { name: string; regex: string }[]; user: { name: string; regex: string; enabled: boolean }[] }>({ builtin: [], user: [] });
  const [newGarbled, setNewGarbled] = useState<{ name: string; regex: string }>({ name: "", regex: "" });
  const [keywordInput, setKeywordInput] = useState("");
  // Persisted-chapters summary (for the 保存全部章节 button label)
  const [savedSummary, setSavedSummary] = useState<{ saved_count: number; saved_at: string | null }>({ saved_count: 0, saved_at: null });
  const [savingAll, setSavingAll] = useState(false);
  // Multi-file upload (append mode)
  const appendFileInputRef = useRef<HTMLInputElement | null>(null);
  const [appending, setAppending] = useState(false);
  // Format-matching (async with progress). guessProgress tracks the
  // currently-scanning pattern; UI shows a progress bar while running.
  // Single-select: only one chapter format may be primary at a time.
  // combine (e.g., 第N章 + 作者说章节 fired together).
  const [guessCandidates, setGuessCandidates] = useState<{ name: string; count: number; score: number; custom: boolean }[] | null>(null);
  // Multi-select: a single work can be analyzed under multiple chapter
  // formats simultaneously. Overlap dedup at the parser level ensures
  // any given chapter heading is claimed by only ONE format, so the
  // results never double-count.
  const [chosenFormats, setChosenFormats] = useState<Set<string>>(new Set());
  const [guessing, setGuessing] = useState(false);
  const [guessProgress, setGuessProgress] = useState<{ current: number; total: number } | null>(null);
  const guessPollRef = useRef<number | null>(null);
  // Inline test results per format in the confirm panel
  const [candidateTests, setCandidateTests] = useState<Record<string, { count: number; preview: any[]; truncated: boolean; loading?: boolean }>>({});
  // Per-chapter content edit modal
  const [editingChapter, setEditingChapter] = useState<{ number: number; title: string; content: string } | null>(null);
  const [editLoading, setEditLoading] = useState(false);
  const [editSaving, setEditSaving] = useState(false);
  // New-chapter modal (CRUD: add)
  const [newChapter, setNewChapter] = useState<{ afterNumber: number | null; heading: string; content: string } | null>(null);
  const [newChapterSaving, setNewChapterSaving] = useState(false);
  // Per-chapter rename modal (CRUD: update title only)
  const [renamingChapter, setRenamingChapter] = useState<{ number: number; heading: string } | null>(null);
  const [renameSaving, setRenameSaving] = useState(false);
  // Bulk-clean modals — one for paragraph-level asides, one for
  // whole-chapter (作者说章节) asides.
  const [paraCleanOpen, setParaCleanOpen] = useState(false);
  const [paraCleanLoading, setParaCleanLoading] = useState(false);
  const [paraCleanList, setParaCleanList] = useState<any[]>([]);
  const [paraCleanSelected, setParaCleanSelected] = useState<Set<string>>(new Set());
  const [bulkOpen, setBulkOpen] = useState(false);

  const pollTimerRef = useRef<number | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await apiGet<PreprocessStatus>(`/api/references/works/${refId}/preprocess/status`);
      setStatus(r);
      return r;
    } catch (e) {
      return null;
    }
  }, [refId]);

  const fetchPlan = useCallback(async () => {
    if (!hasFullText) { setPlan(null); return; }
    try {
      const p = await apiGet<SegmentPlan>(`/api/references/works/${refId}/segments/plan`);
      setPlan(p);
    } catch { /* silent */ }
  }, [refId, hasFullText]);

  const fetchPatterns = useCallback(async () => {
    try {
      const r = await apiGet<{ patterns: ChapterPattern[] }>("/api/references/chapter_patterns");
      setPatterns(r.patterns || []);
    } catch { /* silent */ }
  }, []);

  const fetchKeywords = useCallback(async () => {
    try {
      const r = await apiGet<{ user: string[]; defaults: string[]; active: string[] }>(
        "/api/references/author_note_keywords",
      );
      setKeywords(r);
    } catch { /* silent */ }
  }, []);

  const fetchSavedSummary = useCallback(async () => {
    try {
      const r = await apiGet<{ saved_count: number; saved_at: string | null }>(
        `/api/references/works/${refId}/preprocess/saved_summary`,
      );
      setSavedSummary(r);
    } catch { /* silent */ }
  }, [refId]);

  const fetchGarbledPatterns = useCallback(async () => {
    try {
      const r = await apiGet<{ builtin: { name: string; regex: string }[]; user: { name: string; regex: string; enabled: boolean }[] }>(
        "/api/references/garbled_patterns",
      );
      setGarbledData(r);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    fetchStatus(); fetchPlan(); fetchPatterns(); fetchKeywords(); fetchSavedSummary(); fetchGarbledPatterns();
  }, [fetchStatus, fetchPlan, fetchPatterns, fetchKeywords, fetchSavedSummary, fetchGarbledPatterns]);

  // ── Garbled-pattern CRUD ──

  const saveGarbledPatterns = async (next: { name: string; regex: string; enabled: boolean }[]) => {
    try {
      const r = await apiPut<{ user: { name: string; regex: string; enabled: boolean }[] }>(
        "/api/references/garbled_patterns", { patterns: next },
      );
      setGarbledData(prev => ({ ...prev, user: r.user || [] }));
      toast("已保存乱码模式", "success");
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    }
  };

  const addGarbledPattern = async () => {
    const name = newGarbled.name.trim() || newGarbled.regex.trim().slice(0, 40);
    const regex = newGarbled.regex.trim();
    if (!regex) {
      toast("请填写正则", "info");
      return;
    }
    await saveGarbledPatterns([
      ...garbledData.user,
      { name, regex, enabled: true },
    ]);
    setNewGarbled({ name: "", regex: "" });
  };

  const removeGarbledPattern = async (idx: number) => {
    if (!(await confirmDialog({
      title: "删除乱码模式",
      message: `删除「${garbledData.user[idx]?.name || ""}」？`,
      destructive: true,
    }))) return;
    const next = garbledData.user.filter((_, i) => i !== idx);
    await saveGarbledPatterns(next);
  };

  const toggleGarbledPattern = async (idx: number) => {
    const next = garbledData.user.map((p, i) =>
      i === idx ? { ...p, enabled: !p.enabled } : p,
    );
    await saveGarbledPatterns(next);
  };

  // ── Author-keyword CRUD ──

  const saveKeywords = async (next: string[]) => {
    try {
      const r = await apiPut<{ user: string[]; defaults: string[]; active: string[] }>(
        "/api/references/author_note_keywords", { keywords: next },
      );
      setKeywords(r);
      toast(next.length === 0 ? "已恢复默认题外话关键词" : `已保存 ${next.length} 个关键词`, "success");
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    }
  };

  const addKeyword = async () => {
    const kw = keywordInput.trim();
    if (!kw) return;
    if (keywords.active.includes(kw)) {
      toast(`「${kw}」已在列表中`, "info");
      setKeywordInput("");
      return;
    }
    const base = keywords.user.length > 0 ? keywords.user : keywords.defaults;
    await saveKeywords([...base, kw]);
    setKeywordInput("");
  };

  const removeKeyword = async (kw: string) => {
    const base = keywords.user.length > 0 ? keywords.user : keywords.defaults;
    await saveKeywords(base.filter(k => k !== kw));
  };

  const resetKeywords = async () => {
    if (!(await confirmDialog({
      title: "恢复默认关键词",
      message: "恢复默认题外话关键词列表？您自定义的列表会被清空。",
      destructive: true,
    }))) return;
    await saveKeywords([]);
  };

  // ── Save-all to database ──

  const saveAllChapters = async () => {
    setSavingAll(true);
    try {
      const r = await apiPost<{ saved_count: number }>(
        `/api/references/works/${refId}/preprocess/save_all`, {},
        { timeoutMs: 300_000 },
      );
      toast(`已保存 ${r.saved_count} 章到数据库`, "success");
      await fetchSavedSummary();
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    } finally {
      setSavingAll(false);
    }
  };

  const savePatterns = async (next: ChapterPattern[]) => {
    try {
      const r = await apiPut<{ patterns: ChapterPattern[] }>(
        "/api/references/chapter_patterns", { patterns: next },
      );
      setPatterns(r.patterns || []);
      toast("已保存自定义章节格式", "success");
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    }
  };

  const addPattern = () => {
    const next = [...patterns, { name: `自定义 ${patterns.length + 1}`, format: "", enabled: true }];
    setPatterns(next);
  };

  const removePattern = (idx: number) => {
    const next = patterns.filter((_, i) => i !== idx);
    setPatterns(next);
    savePatterns(next);
  };

  const updatePattern = (idx: number, patch: Partial<ChapterPattern>) => {
    const next = patterns.map((p, i) => i === idx ? { ...p, ...patch } : p);
    setPatterns(next);
  };

  // Quick-test a single format by name (built-in or custom) against
  // the current work. Used by the per-row 测试 button in the format-
  // confirm panel. Caps at 2 MB server-side so it stays snappy.
  const testCandidate = async (name: string) => {
    setCandidateTests(prev => ({ ...prev, [name]: { ...(prev[name] || { count: 0, preview: [], truncated: false }), loading: true } }));
    try {
      const r = await apiPost<{ count: number; preview: any[]; truncated: boolean }>(
        "/api/references/chapter_patterns/test",
        { pattern_name: name, ref_id: refId },
      );
      setCandidateTests(prev => ({ ...prev, [name]: { count: r.count, preview: r.preview || [], truncated: !!r.truncated, loading: false } }));
    } catch (e: any) {
      toast(e?.message || "测试失败", "error");
      setCandidateTests(prev => {
        const n = { ...prev };
        delete n[name];
        return n;
      });
    }
  };

  // Delete a CUSTOM format by name (built-ins can't be deleted).
  // Used by the × button on each custom row in the format-confirm panel.
  const deleteCustomFormat = async (name: string) => {
    if (!(await confirmDialog({
      title: "删除自定义格式",
      message: `删除自定义章节格式「${name}」？此操作会立即生效。`,
      destructive: true,
    }))) return;
    try {
      await fetch(`/api/references/chapter_patterns/${encodeURIComponent(name)}`, {
        method: "DELETE",
      }).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); });
      toast(`已删除「${name}」`, "success");
      // Remove from local candidate list + chosen + tests
      setGuessCandidates(prev => prev ? prev.filter(c => c.name !== name) : prev);
      setChosenFormats(prev => {
        const n = new Set(prev);
        n.delete(name);
        return n;
      });
      setCandidateTests(prev => {
        const n = { ...prev };
        delete n[name];
        return n;
      });
      await fetchPatterns();
    } catch (e: any) {
      toast(e?.message || "删除失败", "error");
    }
  };

  const testPattern = async (idx: number) => {
    const p = patterns[idx];
    const fmt = (p?.format || "").trim();
    const rx = (p?.regex || "").trim();
    if (!fmt && !rx) { toast("请先填写格式或正则", "info"); return; }
    try {
      const r = await apiPost<{ count: number; preview: any[] }>(
        "/api/references/chapter_patterns/test",
        fmt ? { format: fmt, ref_id: refId } : { regex: rx, ref_id: refId },
      );
      setPatternTesting({ idx, count: r.count, preview: r.preview });
    } catch (e: any) {
      toast(e?.message || "测试失败", "error");
    }
  };

  const appendFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".txt")) {
      toast("仅支持 .txt 文件", "error");
      return;
    }
    setAppending(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("append", "true");
      const resp = await fetch(`/api/references/works/${refId}/upload`, {
        method: "POST", body: fd,
      });
      if (!resp.ok) throw new Error(await resp.text());
      toast(`已追加 ${file.name}`, "success");
      await fetchStatus();
      await fetchPlan();
      await onAfterApplyExclusions?.();
    } catch (e: any) {
      toast(e?.message || "追加失败", "error");
    } finally {
      setAppending(false);
      if (appendFileInputRef.current) appendFileInputRef.current.value = "";
    }
  };

  // Poll while a job is running/paused
  useEffect(() => {
    const s = status?.state;
    if (s === "running" || s === "paused") {
      pollTimerRef.current = window.setInterval(() => { fetchStatus(); }, 600);
    }
    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [status?.state, fetchStatus]);

  const guessFormat = useCallback(async () => {
    setGuessing(true);
    setGuessProgress({ current: 0, total: 0 });
    setGuessCandidates(null);
    setCandidateTests({});
    try {
      // Kick off the async match job
      const init = await apiPost<{ state: string; current_pattern: number; total_patterns: number }>(
        `/api/references/works/${refId}/preprocess/guess_start`, {},
      );
      setGuessProgress({ current: init.current_pattern, total: init.total_patterns });
      // Poll until done
      await new Promise<void>((resolve) => {
        const tick = async () => {
          try {
            const s = await apiGet<{
              state: string; current_pattern: number; total_patterns: number;
              candidates: any[]; suggested: string | null;
            }>(`/api/references/works/${refId}/preprocess/guess_status`);
            setGuessProgress({ current: s.current_pattern, total: s.total_patterns });
            if (s.state === "done") {
              // Hide zero-count formats per user request — they add
              // visual noise without offering anything to pick.
              const visible = (s.candidates || []).filter((c: any) => (c.count || 0) > 0);
              setGuessCandidates(visible);
              // Default-select the suggested winner so the user can
              // confirm-with-one-click. Multi-select still allowed.
              const initial = new Set<string>();
              if (s.suggested) initial.add(s.suggested);
              else if (visible.length > 0) initial.add(visible[0].name);
              setChosenFormats(initial);
              resolve();
              return;
            }
            if (s.state === "error") {
              toast("匹配失败", "error");
              resolve();
              return;
            }
            guessPollRef.current = window.setTimeout(tick, 150);
          } catch (e) {
            resolve();
          }
        };
        tick();
      });
    } catch (e: any) {
      toast(e?.message || "匹配失败", "error");
    } finally {
      setGuessing(false);
      if (guessPollRef.current) {
        clearTimeout(guessPollRef.current);
        guessPollRef.current = null;
      }
    }
  }, [refId, toast]);

  const startJob = async (forcePatterns?: string[]) => {
    const fps = forcePatterns ?? Array.from(chosenFormats);
    try {
      // Multi-select: pass force_patterns (plural). Server uses
      // EXACTLY those patterns with overlap dedup, so each chapter
      // heading is claimed by only one format.
      const qs = fps.length > 0
        ? `?force_patterns=${encodeURIComponent(fps.join(","))}`
        : "";
      const r = await apiPost<PreprocessStatus>(
        `/api/references/works/${refId}/preprocess/start${qs}`, {},
      );
      setStatus(r);
      setExcluded(new Set());
      toast(fps.length > 0
        ? `已用「${fps.join(" + ")}」开始识别`
        : "已开始智能识别章节", "info");
    } catch (e: any) {
      toast(e?.message || "启动失败", "error");
    }
  };

  const openChapterEdit = async (number: number, title: string) => {
    setEditingChapter({ number, title, content: "" });
    setEditLoading(true);
    try {
      const r = await apiGet<{ content: string }>(
        `/api/references/works/${refId}/preprocess/chapter/${number}/content`,
      );
      setEditingChapter({ number, title, content: r.content || "" });
    } catch (e: any) {
      toast(e?.message || "加载章节内容失败", "error");
      setEditingChapter(null);
    } finally {
      setEditLoading(false);
    }
  };

  // CRUD: add new chapter
  const openNewChapterModal = (afterNumber: number | null) => {
    setNewChapter({ afterNumber, heading: "", content: "" });
  };
  const saveNewChapter = async () => {
    if (!newChapter || !newChapter.heading.trim()) {
      toast("标题不能为空", "info");
      return;
    }
    setNewChapterSaving(true);
    try {
      const r = await apiPost<{ total_chapters: number }>(
        `/api/references/works/${refId}/preprocess/chapter/new`,
        {
          after_number: newChapter.afterNumber,
          heading: newChapter.heading,
          content: newChapter.content,
        },
        { timeoutMs: 120_000 },
      );
      toast(`已新增章节 · 现有 ${r.total_chapters} 章`, "success");
      setNewChapter(null);
      await fetchStatus();
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "新增失败", "error");
    } finally {
      setNewChapterSaving(false);
    }
  };

  // CRUD: rename chapter (title only)
  const openRenameModal = (number: number, currentTitle: string) => {
    setRenamingChapter({ number, heading: currentTitle });
  };
  const saveRename = async () => {
    if (!renamingChapter || !renamingChapter.heading.trim()) {
      toast("标题不能为空", "info");
      return;
    }
    setRenameSaving(true);
    try {
      await apiPatch(
        `/api/references/works/${refId}/preprocess/chapter/${renamingChapter.number}/title`,
        { heading: renamingChapter.heading },
      );
      toast(`第 ${renamingChapter.number} 章已改名`, "success");
      setRenamingChapter(null);
      await fetchStatus();
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "改名失败", "error");
    } finally {
      setRenameSaving(false);
    }
  };

  // CRUD: delete single chapter
  const deleteOneChapter = async (number: number) => {
    if (!(await confirmDialog({
      title: "删除章节",
      message: `确认从正文中删除第 ${number} 章？此操作可撤销一次。`,
      destructive: true,
    }))) return;
    try {
      await fetch(`/api/references/works/${refId}/preprocess/chapter/${number}`, {
        method: "DELETE",
      }).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); });
      toast(`第 ${number} 章已删除`, "success");
      await fetchStatus();
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "删除失败", "error");
    }
  };

  const saveChapterEdit = async () => {
    if (!editingChapter) return;
    setEditSaving(true);
    try {
      await apiPatch(
        `/api/references/works/${refId}/preprocess/chapter/${editingChapter.number}/content`,
        { content: editingChapter.content },
      );
      toast(`第 ${editingChapter.number} 章已保存`, "success");
      setEditingChapter(null);
      await fetchStatus();
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    } finally {
      setEditSaving(false);
    }
  };

  const undoExclusions = async () => {
    if (!(await confirmDialog({
      title: "撤销清理",
      message: "撤销上一次清理？将从备份恢复正文。",
    }))) return;
    try {
      const r = await apiPost<{ restored_char_count: number; restored_chapters: number[] }>(
        `/api/references/works/${refId}/preprocess/undo_exclusions`, {},
      );
      toast(`已恢复 ${r.restored_chapters.length} 章（${fmtChars(r.restored_char_count)}）`, "success");
      await fetchStatus();
      await fetchPlan();
      await onAfterApplyExclusions?.();
    } catch (e: any) {
      toast(e?.message || "撤销失败", "error");
    }
  };

  const pauseJob = async () => {
    try { await apiPost(`/api/references/works/${refId}/preprocess/pause`, {}); await fetchStatus(); }
    catch (e: any) { toast(e?.message || "暂停失败", "error"); }
  };
  const resumeJob = async () => {
    try { await apiPost(`/api/references/works/${refId}/preprocess/resume`, {}); await fetchStatus(); }
    catch (e: any) { toast(e?.message || "恢复失败", "error"); }
  };
  const cancelJob = async () => {
    if (!(await confirmDialog({
      title: "取消任务",
      message: "确认取消当前预处理任务？",
      destructive: true,
    }))) return;
    try { await apiPost(`/api/references/works/${refId}/preprocess/cancel`, {}); await fetchStatus(); }
    catch (e: any) { toast(e?.message || "取消失败", "error"); }
  };

  const toggleExclude = (n: number) => {
    setExcluded(prev => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  };

  const applyExclusions = async () => {
    if (excluded.size === 0) {
      toast("未选择任何要排除的章节", "info");
      return;
    }
    if (!(await confirmDialog({
      title: "清理章节",
      message: `将从正文中物理删除 ${excluded.size} 个章节，可撤销一次。继续？`,
      destructive: true,
    }))) return;
    setApplying(true);
    try {
      const r = await apiPost<{ ok: boolean; removed_chapters: number[]; new_char_count: number }>(
        `/api/references/works/${refId}/preprocess/apply_exclusions`,
        { excluded_chapters: Array.from(excluded) },
        { timeoutMs: 120_000 },
      );
      toast(`已删除 ${r.removed_chapters.length} 章，现剩 ${fmtChars(r.new_char_count)}`, "success");
      setExcluded(new Set());
      await onAfterApplyExclusions?.();
      await fetchStatus();
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "应用失败", "error");
    } finally {
      setApplying(false);
    }
  };

  // Whole-chapter bulk clean — limited to chapters detected by the
  // 作者说章节 pattern (entries authored as standalone "chapters").
  const cleanGarbled = async () => {
    const garbledCount = chapters.filter(c => c.is_garbled).length;
    if (garbledCount === 0) {
      toast("没有检测到乱码", "info");
      return;
    }
    setCleaningGarbled(true);
    try {
      const r = await apiPost<{ total_chapters: number; new_char_count: number; can_undo: boolean }>(
        `/api/references/works/${refId}/preprocess/clean_garbled`, {},
        { timeoutMs: 120_000 },
      );
      toast(`已清除乱码 · 剩 ${fmtChars(r.new_char_count)} · ${r.total_chapters} 章`, "success");
      await onAfterApplyExclusions?.();
      await fetchStatus();
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "清除失败", "error");
    } finally {
      setCleaningGarbled(false);
    }
  };

  // Encoding mojibake repair — distinct from clean_garbled. This runs
  // the 6 candidate transforms (GBK↔UTF-8, Latin-1 round-trips, etc.)
  // against the whole text and re-encodes whichever recovers the most
  // CJK density. Surfaces a clear toast if no improvement is detected.
  const repairEncoding = async () => {
    if (!(await confirmDialog({
      title: "修复乱码编码",
      message: "尝试自动修复整段文本的编码错乱（如 GBK↔UTF-8 错位、Latin-1 还原）。会备份原文，可撤销一次。继续？",
    }))) return;
    setRepairingEncoding(true);
    try {
      const r = await apiPost<{ total_chapters: number; new_char_count: number; can_undo: boolean }>(
        `/api/references/works/${refId}/preprocess/repair_encoding`, {},
        { timeoutMs: 120_000 },
      );
      toast(`已修复编码 · 剩 ${fmtChars(r.new_char_count)} · ${r.total_chapters} 章`, "success");
      await onAfterApplyExclusions?.();
      await fetchStatus();
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "未检测到可改善的编码问题", "info");
    } finally {
      setRepairingEncoding(false);
    }
  };

  const bulkCleanAuthorChapters = async () => {
    const nums = (status?.chapters || [])
      .filter(c => c.pattern === "作者说章节")
      .map(c => c.number);
    if (nums.length === 0) {
      toast("没有「作者说章节」类型的章节", "info");
      return;
    }
    setApplying(true);
    try {
      const r = await apiPost<{ removed_chapters: number[]; new_char_count: number }>(
        `/api/references/works/${refId}/preprocess/apply_exclusions`,
        { excluded_chapters: nums },
        { timeoutMs: 120_000 },
      );
      toast(`已删除 ${r.removed_chapters.length} 章作者说章节`, "success");
      setBulkOpen(false);
      setExcluded(new Set());
      await onAfterApplyExclusions?.();
      await fetchStatus();
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "清除失败", "error");
    } finally {
      setApplying(false);
    }
  };

  // Paragraph-level cleanup — load detected aside paragraphs and let
  // the user pick which to remove from their parent chapters.
  const openParaCleanModal = async () => {
    setParaCleanOpen(true);
    setParaCleanLoading(true);
    setParaCleanList([]);
    try {
      const r = await apiGet<{ asides: any[] }>(
        `/api/references/works/${refId}/preprocess/aside_paragraphs`,
      );
      setParaCleanList(r.asides || []);
      // Pre-select all by default — user can uncheck individually.
      const keys = new Set<string>();
      (r.asides || []).forEach((a: any) => keys.add(`${a.chapter_number}:${a.para_index}`));
      setParaCleanSelected(keys);
    } catch (e: any) {
      toast(e?.message || "加载失败", "error");
    } finally {
      setParaCleanLoading(false);
    }
  };

  const runParaCleanup = async () => {
    const selected = paraCleanList.filter(a =>
      paraCleanSelected.has(`${a.chapter_number}:${a.para_index}`),
    );
    if (selected.length === 0) {
      toast("未选择任何段落", "info");
      return;
    }
    setApplying(true);
    try {
      const r = await apiPost<{ removed_count: number; new_char_count: number }>(
        `/api/references/works/${refId}/preprocess/clean_aside_paragraphs`,
        { paragraphs: selected.map(a => ({
          chapter_number: a.chapter_number,
          para_index: a.para_index,
          text: a.text,  // robust matching at apply time
        })) },
        { timeoutMs: 120_000 },
      );
      toast(`已删除 ${r.removed_count} 个段落，现剩 ${fmtChars(r.new_char_count)}`, "success");
      setParaCleanOpen(false);
      setParaCleanList([]);
      setParaCleanSelected(new Set());
      await onAfterApplyExclusions?.();
      await fetchStatus();
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "清除失败", "error");
    } finally {
      setApplying(false);
    }
  };

  // ── Volume plan editor (moved from PlotOutlinePanel) ──

  const startPlanEdit = () => {
    if (!plan) return;
    if (plan.segments.length === 0) {
      const total = plan.total_chapters || 1;
      setPlanDraft([{ title: `第 1–${total} 章`, start_chapter: 1, end_chapter: total }]);
    } else {
      setPlanDraft(plan.segments.map(s => ({
        title: s.title || `第 ${s.start_chapter}–${s.end_chapter} 章`,
        start_chapter: s.start_chapter,
        end_chapter: s.end_chapter,
      })));
    }
  };

  const loadAutoSuggest = async () => {
    try {
      const sug = await apiGet<SegmentPlan>(`/api/references/works/${refId}/segments/plan/auto_suggest`);
      if (!sug.segments || sug.segments.length === 0) {
        toast("自动检测未识别到可分卷的结构", "info");
        return;
      }
      setPlanDraft(sug.segments.map(s => ({
        title: s.title || `第 ${s.start_chapter}–${s.end_chapter} 章`,
        start_chapter: s.start_chapter,
        end_chapter: s.end_chapter,
      })));
      toast(`已载入 ${sug.segments.length} 段建议，请检查后保存`, "success");
    } catch (e: any) {
      toast(e?.message || "自动检测失败", "error");
    }
  };

  const addPlanRow = (afterIdx: number) => {
    if (!planDraft) return;
    const total = plan?.total_chapters || 0;
    const prev = afterIdx >= 0 ? planDraft[afterIdx] : null;
    const newStart = prev ? prev.end_chapter + 1 : 1;
    const newEnd = total > 0 ? Math.min(total, newStart) : newStart;
    const next = [...planDraft];
    next.splice(afterIdx + 1, 0, {
      title: `第 ${newStart}–${newEnd} 章`,
      start_chapter: newStart,
      end_chapter: newEnd,
    });
    setPlanDraft(next);
  };

  const removePlanRow = (idx: number) => {
    if (!planDraft) return;
    setPlanDraft(planDraft.filter((_, i) => i !== idx));
  };

  const savePlan = async () => {
    if (!planDraft) return;
    const cleaned = planDraft.map(s => ({
      title: (s.title || "").trim(),
      start_chapter: Math.max(1, Math.floor(s.start_chapter || 1)),
      end_chapter: Math.max(1, Math.floor(s.end_chapter || 1)),
    }));
    if (cleaned.some(s => s.end_chapter < s.start_chapter)) {
      toast("某段的结束章号小于起始章号", "error");
      return;
    }
    if (!(await confirmDialog({
      title: "保存分段",
      message: "保存自定义分段会清空所有已完成的提取结果。继续？",
    }))) return;
    setPlanSaving(true);
    try {
      await apiPut(`/api/references/works/${refId}/segments/plan`,
        { segments: cleaned, plan_type: cleaned.length > 1 ? "volumes" : "custom" },
        { timeoutMs: 60_000 });
      toast("分段计划已保存", "success");
      setPlanDraft(null);
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    } finally { setPlanSaving(false); }
  };

  const cancelPlanEdit = () => setPlanDraft(null);

  // ── Render ──

  if (!hasFullText) {
    return (
      <div className="empty-state" style={{ padding: 32, textAlign: "center" }}>
        <p style={{ marginBottom: 12 }}>本作品尚未上传正文。上传后会自动按「第 N 章」/「1、…」/「Chapter N」等多种格式切分章节。</p>
        <button className="btn-primary" onClick={onUpload}>上传正文</button>
      </div>
    );
  }

  const state = status?.state || "idle";
  // Defensive sort by chapter number so the timeline is always low→high
  // even if the backend response somehow arrives out of order.
  const chapters = [...(status?.chapters || [])].sort(
    (a, b) => (a.number || 0) - (b.number || 0),
  );
  const filteredChapters = chapters.filter(c => {
    if (filter === "flagged") return !!c.is_author_note;
    if (filter === "outlier") return !!c.is_length_outlier;
    if (filter === "garbled") return !!c.is_garbled;
    if (filter === "kept") return !excluded.has(c.number);
    return true;
  });

  const toggleExpand = (n: number) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n); else next.add(n);
      return next;
    });
  };

  const progress = status && status.total_chapters > 0
    ? (status.current_chapter / status.total_chapters) * 100
    : 0;

  return (
    <div className="flex flex-col gap-12">
      <ConfirmHost />
      {/* Section 1: chapter detection + status */}
      <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, background: "var(--bg-surface)" }}>
        <div className="flex items-center justify-between" style={{ marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
              智能章节识别
              {status?.detected_pattern && (
                <span className="tag" style={{
                  marginLeft: 8, fontSize: 10, padding: "1px 6px",
                  color: "var(--accent)", background: "var(--accent-subtle)",
                  border: "1px solid var(--accent)",
                }}>{status.detected_pattern}</span>
              )}
              {status?.fallback_used && (
                <span className="tag" style={{
                  marginLeft: 8, fontSize: 10, padding: "1px 6px",
                  color: "var(--gold)", background: "var(--bg-surface-2)",
                  border: "1px solid var(--gold)",
                }}>未识别结构 · 已按 3000 字切块</span>
              )}
            </div>
            <div className="text-xs text-muted" style={{ marginTop: 2 }}>
              支持「第N章」「第N回」「1、标题」「Chapter N」等多种格式（含阿拉伯/中文数字）；自动标记疑似作者题外话。
            </div>
          </div>
          <div className="flex items-center gap-6" style={{ flexWrap: "wrap" }}>
            {(state === "idle" || state === "done" || state === "cancelled" || state === "error") && !guessCandidates && !guessing && (
              <button className="btn-primary" style={{ fontSize: 12, padding: "5px 14px" }}
                      onClick={guessFormat}
                      title="先扫描全文匹配章节格式，再让你确认后进行识别">
                {state === "idle" ? "匹配章节格式" : "重新识别"}
              </button>
            )}
            {state === "running" && (
              <>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={pauseJob}>暂停</button>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)" }} onClick={cancelJob}>取消</button>
              </>
            )}
            {state === "paused" && (
              <>
                <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }} onClick={resumeJob}>恢复</button>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)" }} onClick={cancelJob}>取消</button>
              </>
            )}
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={onUpload}
                    title="到「文件」tab 管理上传">
              管理文件
            </button>
          </div>
        </div>

        {/* Live match progress — while the format-matching job is running */}
        {guessing && (
          <div style={{
            padding: 10, marginBottom: 10,
            border: "1px solid var(--accent)", borderRadius: 4,
            background: "var(--accent-subtle)",
          }}>
            <div className="text-xs" style={{
              marginBottom: 6, color: "var(--accent)", fontWeight: 600,
              display: "flex", justifyContent: "space-between",
            }}>
              <span>匹配中… 扫描第 {guessProgress?.current || 0} / {guessProgress?.total || "?"} 个格式</span>
              <span>{guessProgress && guessProgress.total > 0
                ? Math.round((guessProgress.current / guessProgress.total) * 100) + "%"
                : ""}</span>
            </div>
            <div style={{ height: 5, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden" }}>
              <div style={{
                height: "100%",
                width: `${guessProgress && guessProgress.total > 0
                  ? (guessProgress.current / guessProgress.total) * 100
                  : 0}%`,
                background: "var(--accent)",
                transition: "width 0.15s",
              }} />
            </div>
          </div>
        )}

        {/* Format-pick step — appears after matching, before detection runs.
            Checkboxes (multi-select) — combine multiple formats e.g.
            第N章 + 作者说章节. No score column per user feedback. */}
        {guessCandidates && !guessing && state !== "running" && state !== "paused" && (
          <div style={{
            padding: 10, marginBottom: 10,
            border: "1px solid var(--accent)", borderRadius: 4,
            background: "var(--accent-subtle)",
          }}>
            <div className="text-xs" style={{ marginBottom: 8, color: "var(--accent)", fontWeight: 600 }}>
              请选择章节格式（可多选）
            </div>
            <div className="text-xs text-muted" style={{ marginBottom: 8, lineHeight: 1.55 }}>
              下方是本作品中匹配到内容的格式（仅显示非零匹配）。可多选 — 不同格式 match 到的章节标题互不重叠。
            </div>
            <div className="flex flex-col gap-4" style={{ marginBottom: 10 }}>
              {guessCandidates.slice(0, 12).map(c => {
                const checked = chosenFormats.has(c.name);
                const t = candidateTests[c.name];
                return (
                  <div key={c.name} style={{
                    border: `1px solid ${checked ? "var(--accent)" : "var(--border)"}`,
                    borderRadius: 3,
                    background: checked ? "var(--bg-card)" : "transparent",
                  }}>
                    <div style={{
                      display: "flex", alignItems: "center", gap: 8,
                      padding: "4px 8px",
                    }}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => {
                          setChosenFormats(prev => {
                            const n = new Set(prev);
                            if (n.has(c.name)) n.delete(c.name);
                            else n.add(c.name);
                            return n;
                          });
                        }}
                        style={{ width: 13, height: 13, cursor: "pointer" }}
                      />
                      <span style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)", flex: 1, minWidth: 0 }}>
                        {c.name}
                      </span>
                      {c.custom && (
                        <span className="tag" style={{
                          fontSize: 10, padding: "1px 6px",
                          color: "var(--purple)", border: "1px solid var(--purple)",
                          background: "transparent",
                        }}>自定义</span>
                      )}
                      <span className="text-xs text-muted" style={{
                        fontFamily: "var(--font-mono)", minWidth: 70, textAlign: "right",
                      }}>
                        匹配 {c.count} 处
                      </span>
                      <button className="btn"
                              style={{ fontSize: 10, padding: "2px 8px" }}
                              onClick={() => testCandidate(c.name)}
                              disabled={t?.loading}
                              title="展示该格式在当前作品上匹配到的章节（截取前 2 MB）">
                        {t?.loading ? "..." : "展示"}
                      </button>
                      {c.custom && (
                        <button className="btn-icon"
                                onClick={() => deleteCustomFormat(c.name)}
                                style={{ fontSize: 14, color: "var(--error)" }}
                                title="删除此自定义格式">&times;</button>
                      )}
                    </div>
                    {t && !t.loading && (
                      <div className="text-xs" style={{
                        margin: "0 8px 6px 8px", padding: 6,
                        background: "var(--bg-surface)", borderRadius: 3,
                        color: "var(--text-secondary)", lineHeight: 1.55,
                      }}>
                        <div>
                          <span className="text-muted">匹配结果：</span>
                          <span style={{
                            fontWeight: 600,
                            color: t.count > 5 ? "var(--jade)" : t.count > 0 ? "var(--gold)" : "var(--text-tertiary)",
                          }}>匹配 {t.count} 处</span>
                          {t.truncated && <span className="text-muted" style={{ marginLeft: 6 }}>（截取前 2 MB）</span>}
                        </div>
                        {t.preview.length > 0 && (
                          <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                            {t.preview.slice(0, 5).map((m: any, k: number) => (
                              <li key={k} style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                                {m.match}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
            <div className="flex gap-6" style={{ justifyContent: "flex-end" }}>
              <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                      onClick={() => { setGuessCandidates(null); setChosenFormats(new Set()); }}>
                取消
              </button>
              <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }}
                      onClick={() => { startJob(Array.from(chosenFormats)); setGuessCandidates(null); }}
                      disabled={chosenFormats.size === 0}>
                {chosenFormats.size <= 1
                  ? `使用「${Array.from(chosenFormats)[0] || "?"}」开始识别`
                  : `使用所选 ${chosenFormats.size} 个格式开始识别`}
              </button>
            </div>
          </div>
        )}

        {/* Progress bar */}
        {(state === "running" || state === "paused") && (
          <div style={{ marginBottom: 8 }}>
            <div className="text-xs" style={{
              color: state === "paused" ? "var(--gold)" : "var(--accent)",
              marginBottom: 4,
              display: "flex", justifyContent: "space-between",
            }}>
              <span>{state === "paused" ? "已暂停" : (
                status?.phase === "loading" ? "正在读取正文文件…" :
                status?.phase === "matching" ? "正在匹配章节格式…" :
                status?.phase === "finalizing" ? "正在生成摘要…" :
                (status?.total_chapters || 0) > 0
                  ? `处理中 · 第 ${status?.current_chapter || 0} / ${status?.total_chapters || 0} 章`
                  : "准备中…"
              )}</span>
              <span>{progress.toFixed(0)}%</span>
            </div>
            <div style={{ height: 5, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden" }}>
              <div style={{
                height: "100%",
                width: `${progress}%`,
                background: state === "paused" ? "var(--gold)" : "var(--accent)",
                borderRadius: 3, transition: "width 0.2s",
              }} />
            </div>
          </div>
        )}

        {/* Status summary when done */}
        {state === "done" && status && (
          <div className="text-xs text-muted" style={{ marginBottom: 8 }}>
            共识别 {status.total_chapters} 章 · 疑似作者题外话 <span style={{ color: "var(--gold)", fontWeight: 600 }}>{status.flagged_count}</span> 章
            {status.persisted && " · （上次保存的结果）"}
          </div>
        )}

        {/* Live log tail */}
        {status && status.log && status.log.length > 0 && (state === "running" || state === "paused") && (
          <div style={{
            maxHeight: 120, overflowY: "auto",
            border: "1px solid var(--border)", borderRadius: 3,
            background: "var(--bg-card)", padding: 6,
            fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.5,
            color: "var(--text-secondary)",
          }}>
            {status.log.slice(-30).map((e, i) => (
              <div key={i} style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                <span style={{ color: "var(--text-tertiary)" }}>
                  {new Date(e.ts * 1000).toLocaleTimeString()}
                </span>{" "}{e.message}
              </div>
            ))}
          </div>
        )}

        {status?.error && (
          <div className="text-xs" style={{ color: "var(--error)", marginTop: 6 }}>{status.error}</div>
        )}

        {/* Custom chapter patterns (collapsible) */}
        <div style={{ marginTop: 10, borderTop: "1px dashed var(--border)", paddingTop: 8 }}>
          <button className="btn-ghost w-full"
                  onClick={() => setPatternsOpen(o => !o)}
                  style={{
                    justifyContent: "space-between", padding: "4px 0",
                    fontSize: 11, fontWeight: 600, color: "var(--text-secondary)",
                    borderRadius: 0,
                  }}>
            <span>自定义章节格式（{patterns.length}）</span>
            <span className="text-xs text-muted" style={{
              transition: "transform 0.15s",
              transform: patternsOpen ? "rotate(180deg)" : "none",
              display: "inline-block",
            }}>&#x25BC;</span>
          </button>
          {patternsOpen && (
            <div style={{ marginTop: 6 }}>
              {status?.detected_pattern && (
                <div className="text-xs" style={{
                  marginBottom: 8, padding: "5px 8px",
                  background: "var(--accent-subtle)", color: "var(--accent)",
                  border: "1px solid var(--accent)", borderRadius: 4,
                }}>
                  当前作品识别到的章节格式：
                  <span style={{ fontWeight: 700, marginLeft: 6 }}>{status.detected_pattern}</span>
                  <span className="text-muted" style={{ marginLeft: 8 }}>
                    （内置格式之一；如需自定义可在下方添加）
                  </span>
                </div>
              )}
              <div className="text-xs text-muted" style={{ marginBottom: 8, lineHeight: 1.6 }}>
                内置「第N章」「第N回」「1、标题」「1.标题」「Chapter N」等格式。
                若你的小说用了不同的章节标记，可在这里添加。
                <br />
                <strong style={{ color: "var(--text-secondary)" }}>写法：</strong>
                直接写出章节标题的样子，把章节号的位置写成
                <code style={{
                  background: "var(--bg-card)", padding: "1px 6px",
                  borderRadius: 2, color: "var(--accent)", fontFamily: "var(--font-mono)",
                }}>N</code>
                （大写 N，代表数字或中文数字）。
                <br />
                <strong style={{ color: "var(--text-secondary)" }}>示例：</strong>
                <code style={{ background: "var(--bg-card)", padding: "1px 6px", marginLeft: 4, borderRadius: 2 }}>第N章</code>
                <code style={{ background: "var(--bg-card)", padding: "1px 6px", marginLeft: 4, borderRadius: 2 }}>第N回</code>
                <code style={{ background: "var(--bg-card)", padding: "1px 6px", marginLeft: 4, borderRadius: 2 }}>N、</code>
                <code style={{ background: "var(--bg-card)", padding: "1px 6px", marginLeft: 4, borderRadius: 2 }}>卷N</code>
                <code style={{ background: "var(--bg-card)", padding: "1px 6px", marginLeft: 4, borderRadius: 2 }}>Chapter N</code>
              </div>
              {patterns.length > 0 && (
                <div className="flex flex-col gap-6" style={{ marginBottom: 8 }}>
                  {patterns.map((p, i) => {
                    const isWinner = status?.detected_pattern && p.name === status.detected_pattern;
                    return (
                    <div key={i} style={{
                      padding: 6,
                      border: `1px solid ${isWinner ? "var(--accent)" : "var(--border)"}`,
                      borderRadius: 4,
                      background: isWinner ? "var(--accent-subtle)" : "transparent",
                    }}>
                      <div className="flex gap-6 items-center" style={{ marginBottom: 4 }}>
                        <input
                          type="checkbox" checked={p.enabled}
                          onChange={e => updatePattern(i, { enabled: e.target.checked })}
                          style={{ flexShrink: 0, width: 13, height: 13 }}
                          title="启用此格式"
                        />
                        <input
                          className="input"
                          placeholder="格式（如：第N章、N、、卷N）— 把章节号位置写成 N"
                          value={p.format || ""}
                          onChange={e => updatePattern(i, { format: e.target.value, regex: "", name: e.target.value })}
                          style={{ flex: 1, fontSize: 12 }}
                          title="格式即名字 — 把章节号位置写成 N，其它字符照写"
                        />
                        {isWinner && (
                          <span className="tag" style={{
                            fontSize: 10, padding: "1px 6px", flexShrink: 0,
                            color: "var(--accent)", background: "var(--bg-card)",
                            border: "1px solid var(--accent)",
                          }}>已识别</span>
                        )}
                        <button className="btn" style={{ fontSize: 11, padding: "3px 8px" }}
                                onClick={() => testPattern(i)}
                                title="展示该格式在当前作品上匹配到的章节">
                          测试
                        </button>
                        <button className="btn-icon"
                                onClick={() => removePattern(i)}
                                style={{ fontSize: 14, color: "var(--error)" }}
                                title="删除">&times;</button>
                      </div>
                      {patternTesting?.idx === i && (
                        <div className="text-xs" style={{
                          padding: 6, marginTop: 4,
                          background: "var(--bg-card)", borderRadius: 3,
                          color: "var(--text-secondary)", lineHeight: 1.55,
                        }}>
                          匹配 <span style={{
                            color: patternTesting.count > 5 ? "var(--jade)" : "var(--gold)",
                            fontWeight: 600,
                          }}>{patternTesting.count}</span> 处。
                          {patternTesting.preview.length > 0 && (
                            <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                              {patternTesting.preview.map((m, k) => (
                                <li key={k} style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                                  {m.match}
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      )}
                    </div>
                    );
                  })}
                </div>
              )}
              <div className="flex gap-6" style={{ justifyContent: "space-between" }}>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={addPattern}>
                  + 添加格式
                </button>
                <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() => savePatterns(patterns)}>
                  保存格式
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Author-note keywords (collapsible CRUD) */}
        <div style={{ marginTop: 10, borderTop: "1px dashed var(--border)", paddingTop: 8 }}>
          <button className="btn-ghost w-full"
                  onClick={() => setKeywordsOpen(o => !o)}
                  style={{
                    justifyContent: "space-between", padding: "4px 0",
                    fontSize: 11, fontWeight: 600, color: "var(--text-secondary)",
                    borderRadius: 0,
                  }}>
            <span>题外话关键词（{keywords.active.length}{keywords.user.length > 0 ? " · 自定义" : " · 默认"}）</span>
            <span className="text-xs text-muted" style={{
              transition: "transform 0.15s",
              transform: keywordsOpen ? "rotate(180deg)" : "none",
              display: "inline-block",
            }}>&#x25BC;</span>
          </button>
          {keywordsOpen && (
            <div style={{ marginTop: 6 }}>
              <div className="text-xs text-muted" style={{ marginBottom: 8, lineHeight: 1.55 }}>
                这些关键词用于识别题外话章节和段落（如「求月票」「盟主」）。可以增删任意条目；
                {keywords.user.length > 0
                  ? "目前使用的是您的自定义列表。"
                  : "目前使用的是内置默认列表 — 一旦您增删任何条目即转为自定义。"}
              </div>
              <div className="flex" style={{ flexWrap: "wrap", gap: 4, marginBottom: 8 }}>
                {keywords.active.map(kw => (
                  <span key={kw} className="tag" style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    fontSize: 11, padding: "2px 4px 2px 8px",
                    background: "var(--bg-card)",
                    border: "1px solid var(--border)",
                    color: "var(--text-secondary)",
                  }}>
                    {kw}
                    <button type="button"
                            onClick={() => removeKeyword(kw)}
                            title={`删除「${kw}」`}
                            style={{
                              padding: "0 4px", margin: 0, border: "none",
                              background: "transparent",
                              color: "var(--text-tertiary)", cursor: "pointer",
                              fontSize: 13,
                            }}>&times;</button>
                  </span>
                ))}
              </div>
              <div className="flex gap-6" style={{ alignItems: "center" }}>
                <input
                  className="input"
                  placeholder="新增关键词（按 Enter 或点添加）"
                  value={keywordInput}
                  onChange={e => setKeywordInput(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addKeyword(); } }}
                  style={{ flex: 1, fontSize: 12 }}
                />
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={addKeyword}
                        disabled={!keywordInput.trim()}>
                  + 添加
                </button>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--text-tertiary)" }}
                        onClick={resetKeywords}
                        disabled={keywords.user.length === 0}
                        title="恢复内置默认关键词">
                  恢复默认
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Garbled-pattern CRUD */}
        <div style={{ marginTop: 10, borderTop: "1px dashed var(--border)", paddingTop: 8 }}>
          <button className="btn-ghost w-full"
                  onClick={() => setGarbledOpen(o => !o)}
                  style={{
                    justifyContent: "space-between", padding: "4px 0",
                    fontSize: 11, fontWeight: 600, color: "var(--text-secondary)",
                    borderRadius: 0,
                  }}>
            <span>乱码识别模式（内置 {garbledData.builtin.length} · 自定义 {garbledData.user.length}）</span>
            <span className="text-xs text-muted" style={{
              transition: "transform 0.15s",
              transform: garbledOpen ? "rotate(180deg)" : "none",
              display: "inline-block",
            }}>&#x25BC;</span>
          </button>
          {garbledOpen && (
            <div style={{ marginTop: 6 }}>
              <div className="text-xs text-muted" style={{ marginBottom: 8, lineHeight: 1.55 }}>
                这些正则用于识别和清除乱码（HTML 转义、随机 ID、BBCode 等）。
                内置模式始终启用；下方可添加自定义模式，或临时禁用某条。
                清除乱码时还会自动尝试 6 种常见编码修复（GBK↔UTF-8 / Latin-1↔GBK / 双重转换）。
              </div>
              {garbledData.builtin.length > 0 && (
                <div style={{ marginBottom: 8 }}>
                  <div className="text-xs text-muted" style={{ marginBottom: 4 }}>内置：</div>
                  <div className="flex" style={{ flexWrap: "wrap", gap: 4 }}>
                    {garbledData.builtin.map((b, i) => (
                      <span key={i} className="tag" style={{
                        fontSize: 11, padding: "2px 6px",
                        background: "var(--bg-card)", border: "1px solid var(--border)",
                        color: "var(--text-secondary)",
                      }} title={b.regex}>{b.name}</span>
                    ))}
                  </div>
                </div>
              )}
              {garbledData.user.length > 0 && (
                <div className="flex flex-col gap-4" style={{ marginBottom: 8 }}>
                  {garbledData.user.map((p, i) => (
                    <div key={i} className="flex gap-6 items-center" style={{
                      padding: "4px 6px",
                      border: "1px solid var(--border)", borderRadius: 4,
                      opacity: p.enabled ? 1 : 0.5,
                    }}>
                      <input
                        type="checkbox" checked={p.enabled}
                        onChange={() => toggleGarbledPattern(i)}
                        style={{ flexShrink: 0, width: 13, height: 13 }}
                        title={p.enabled ? "禁用此模式" : "启用此模式"}
                      />
                      <span style={{ fontSize: 11, minWidth: 100, color: "var(--text-secondary)" }}>{p.name}</span>
                      <code style={{
                        flex: 1, minWidth: 0, fontSize: 11,
                        fontFamily: "var(--font-mono)",
                        color: "var(--text-secondary)",
                        background: "var(--bg-card)", padding: "1px 6px", borderRadius: 2,
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      }}>{p.regex}</code>
                      <button className="btn-icon"
                              onClick={() => removeGarbledPattern(i)}
                              style={{ fontSize: 14, color: "var(--error)" }}
                              title="删除">&times;</button>
                    </div>
                  ))}
                </div>
              )}
              <div className="flex gap-6" style={{ alignItems: "center" }}>
                <input
                  className="input"
                  placeholder="名称（可选）"
                  value={newGarbled.name}
                  onChange={e => setNewGarbled({ ...newGarbled, name: e.target.value })}
                  style={{ width: 120, fontSize: 12 }}
                />
                <input
                  className="input font-mono"
                  placeholder="正则（例：&lt;[^&gt;]+&gt; 或 [A-Z0-9]{12,}）"
                  value={newGarbled.regex}
                  onChange={e => setNewGarbled({ ...newGarbled, regex: e.target.value })}
                  onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addGarbledPattern(); } }}
                  style={{ flex: 1, fontSize: 11 }}
                />
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={addGarbledPattern}
                        disabled={!newGarbled.regex.trim()}>
                  + 添加
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Empty-state CTA: when detection has been run but no chapters
          exist (or never run on a fresh upload). */}
      {chapters.length === 0 && (state === "idle" || state === "done") && hasFullText && (
        <div style={{
          padding: 20, textAlign: "center",
          border: "1px dashed var(--border)", borderRadius: 4,
          background: "var(--bg-surface)",
        }}>
          <div className="text-xs text-muted" style={{ marginBottom: 12, lineHeight: 1.6 }}>
            {state === "done" ? "未识别到任何章节。" : "还没有章节。"}
            <br />
            可以点击上方「匹配章节格式」自动识别，或手动新建第一章。
          </div>
          <button className="btn-primary"
                  style={{ fontSize: 12, padding: "5px 14px" }}
                  onClick={() => openNewChapterModal(null)}>
            新建第一章
          </button>
        </div>
      )}

      {/* Section 2: chapter list + author-note flags + outlier flags */}
      {chapters.length > 0 && (
        <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, background: "var(--bg-surface)" }}>
          <div className="flex items-center justify-between" style={{ marginBottom: 8, gap: 8, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                章节清理（共 {chapters.length} 章 · 已勾选排除 {excluded.size}）
              </div>
              <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                勾选要排除的章节 → 点「清理章节」会从正文中<span style={{ color: "var(--error)" }}>物理删除</span>（可撤销一次）。
              </div>
            </div>
            <div className="flex items-center gap-6">
              {status?.can_undo && (
                <button className="btn"
                        style={{ fontSize: 11, padding: "4px 12px", color: "var(--gold)" }}
                        onClick={undoExclusions}
                        title={`撤销上一次清理（${status.last_removed_chapters?.length || 0} 章）`}>
                  撤销清理
                </button>
              )}
              {/* Cleanup actions use FILLED color so they're visually
                  distinct from the filter chips at the bottom — they're
                  destructive commits, not view toggles. */}
              <button
                      style={{
                        fontSize: 11, padding: "4px 12px",
                        background: "var(--accent)", color: "#fff",
                        border: "1px solid var(--accent)", borderRadius: 3,
                        cursor: "pointer",
                      }}
                      onClick={openParaCleanModal}
                      title="扫描所有正文章节内的题外话段落（如末尾的求月票），逐条预览后批量删除">
                清除题外话段落
              </button>
              {chapters.some(c => c.is_garbled) && (
                <button
                        style={{
                          fontSize: 11, padding: "4px 12px",
                          background: "var(--error)", color: "#fff",
                          border: "1px solid var(--error)", borderRadius: 3,
                          cursor: cleaningGarbled ? "wait" : "pointer",
                          opacity: cleaningGarbled ? 0.7 : 1,
                        }}
                        onClick={cleanGarbled}
                        disabled={cleaningGarbled}
                        title="一次性删除所有 HTML 注释 / 转义 / BBCode 等乱码">
                  {cleaningGarbled ? "清除中…" : `一键清除乱码（${chapters.filter(c => c.is_garbled).length}）`}
                </button>
              )}
              <button
                      style={{
                        fontSize: 11, padding: "4px 12px",
                        background: "var(--purple)", color: "#fff",
                        border: "1px solid var(--purple)", borderRadius: 3,
                        cursor: repairingEncoding ? "wait" : "pointer",
                        opacity: repairingEncoding ? 0.7 : 1,
                      }}
                      onClick={repairEncoding}
                      disabled={repairingEncoding}
                      title="尝试自动还原 GBK↔UTF-8、Latin-1 等编码错乱（与「清除乱码」不同：这里是恢复字符，而不是删除）">
                {repairingEncoding ? "修复中…" : "一键修复乱码编码"}
              </button>
              {(chapters.some(c => c.pattern === "作者说章节")) && (
                <button
                        style={{
                          fontSize: 11, padding: "4px 12px",
                          background: "var(--gold)", color: "#fff",
                          border: "1px solid var(--gold)", borderRadius: 3,
                          cursor: "pointer",
                        }}
                        onClick={() => setBulkOpen(true)}
                        title="预览所有识别为「作者说章节」的整章并批量删除">
                  清除作者说章节（{chapters.filter(c => c.pattern === "作者说章节").length}）
                </button>
              )}
              <button
                      style={{
                        fontSize: 11, padding: "4px 12px",
                        background: excluded.size > 0 ? "var(--purple)" : "var(--bg-surface-2)",
                        color: excluded.size > 0 ? "#fff" : "var(--text-tertiary)",
                        border: `1px solid ${excluded.size > 0 ? "var(--purple)" : "var(--border)"}`,
                        borderRadius: 3,
                        cursor: (applying || excluded.size === 0) ? "not-allowed" : "pointer",
                        opacity: applying ? 0.7 : 1,
                      }}
                      onClick={applyExclusions}
                      disabled={applying || excluded.size === 0}
                      title={excluded.size === 0 ? "未选择任何章节" : `物理删除 ${excluded.size} 章（会备份原文）`}>
                {applying ? "清理中…" : `清理章节（${excluded.size}）`}
              </button>
            </div>
          </div>

          {/* Filter bar — separate row so the buttons are obviously clickable */}
          <div className="flex items-center" style={{ marginBottom: 8, gap: 6, flexWrap: "wrap" }}>
            <span className="text-xs text-muted" style={{ marginRight: 4 }}>筛选：</span>
            {([
              { k: "all", label: "全部", count: chapters.length },
              { k: "flagged", label: "疑似题外话", count: chapters.filter(c => c.is_author_note).length },
              { k: "outlier", label: "长度异常", count: chapters.filter(c => c.is_length_outlier).length },
              { k: "garbled", label: "乱码", count: chapters.filter(c => c.is_garbled).length },
              { k: "kept", label: "正文章节", count: chapters.length - excluded.size },
            ] as const).map(o => (
              <button key={o.k}
                      type="button"
                      onClick={() => setFilter(o.k)}
                      className="btn"
                      style={{
                        fontSize: 11, padding: "3px 10px",
                        background: filter === o.k ? "var(--accent-subtle)" : "var(--bg-card)",
                        color: filter === o.k ? "var(--accent)" : "var(--text-secondary)",
                        border: `1px solid ${filter === o.k ? "var(--accent)" : "var(--border)"}`,
                      }}>
                {o.label} <span style={{ opacity: 0.7, marginLeft: 4 }}>{o.count}</span>
              </button>
            ))}
            <div style={{ flex: 1 }} />
            {/* Single 全选 toggle: first click selects all in current
                view; second click clears them. Derived from whether
                every visible chapter is currently excluded. */}
            {(() => {
              const allSelected = filteredChapters.length > 0
                && filteredChapters.every(c => excluded.has(c.number));
              return (
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() => {
                          const visible = filteredChapters.map(c => c.number);
                          setExcluded(prev => {
                            const next = new Set(prev);
                            if (allSelected) visible.forEach(n => next.delete(n));
                            else visible.forEach(n => next.add(n));
                            return next;
                          });
                        }}
                        disabled={filteredChapters.length === 0}
                        title={allSelected ? "取消当前视图的全部勾选" : "勾选当前视图的所有章节"}>
                  {allSelected ? "全部取消" : "全选"}
                </button>
              );
            })()}
          </div>

          <div className="flex flex-col gap-4" style={{
            maxHeight: 540, overflowY: "auto",
            padding: 4, border: "1px solid var(--border)", borderRadius: 4,
          }}>
            {filteredChapters.length === 0 && (
              <div className="text-xs text-muted" style={{ padding: 12, textAlign: "center" }}>
                当前筛选下没有章节。
              </div>
            )}
            {/* Build a fast lookup of gap entries keyed by after_number
                so we can render a "缺失 N 章" marker between
                consecutive chapter rows in the timeline. */}
            {filteredChapters.flatMap((c, fIdx) => {
              const isOpen = expanded.has(c.number);
              const isFlagged = !!c.is_author_note;
              const isOutlier = !!c.is_length_outlier;
              const isExcluded = excluded.has(c.number);
              const borderColor = isExcluded ? "var(--error)"
                                  : isFlagged ? "var(--gold)"
                                  : isOutlier ? "var(--purple)"
                                  : "var(--border)";
              const bgColor = isExcluded ? "rgba(220,38,38,0.06)"
                              : isFlagged ? "rgba(250,204,21,0.06)"
                              : isOutlier ? "rgba(168,85,247,0.06)"
                              : "transparent";
              // Look up a gap that starts after THIS chapter — rendered
              // immediately below the chapter row to flag missing
              // parsed_numbers (e.g. "1、" → "3、" with 2 missing).
              const gap = (status?.gaps || []).find(g => g.after_number === c.number);
              const chapterEl = (
                <div key={c.number}
                  onClick={() => toggleExclude(c.number)}
                  title={isExcluded ? "已选择删除（点击取消）" : "点击以勾选删除此章节"}
                  style={{
                    border: `1px solid ${borderColor}`,
                    borderRadius: 4,
                    padding: "5px 8px",
                    background: bgColor,
                    cursor: "pointer",
                    boxShadow: isExcluded ? "inset 0 0 0 1px var(--error)" : "none",
                    transition: "background 0.1s",
                  }}>
                  <div className="flex items-center gap-8" style={{ minWidth: 0 }}>
                    <span className="tag" style={{
                      fontSize: 10, minWidth: 42, textAlign: "center", flexShrink: 0,
                      color: isExcluded ? "var(--error)" : "var(--text-secondary)",
                      background: "transparent",
                      border: `1px solid ${isExcluded ? "var(--error)" : "var(--border)"}`,
                      fontFamily: "var(--font-mono)",
                    }}>#{c.number}</span>
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={e => { e.stopPropagation(); toggleExpand(c.number); }}
                      style={{
                        flex: 1, minWidth: 0,
                        padding: "2px 0", borderRadius: 0,
                        justifyContent: "flex-start",
                      }}>
                      <div className="truncate" style={{
                        fontSize: 12, fontWeight: 500,
                        color: isExcluded ? "var(--text-tertiary)" : "var(--text-primary)",
                        textDecoration: isExcluded ? "line-through" : "none",
                        textAlign: "left",
                      }}>{displayTitle(c)}</div>
                    </button>
                    {isFlagged && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px", flexShrink: 0,
                        color: "var(--gold)", background: "var(--bg-surface-2)",
                        border: "1px solid var(--gold)",
                      }} title={(c.author_note_reasons || []).join(" · ")}>题外话?</span>
                    )}
                    {isOutlier && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px", flexShrink: 0,
                        color: "var(--purple)", background: "var(--bg-surface-2)",
                        border: "1px solid var(--purple)",
                      }} title={`本章字数与全文中位数差异较大（${c.outlier_kind || ""}）；切分可能不准`}>
                        长度异常·{c.outlier_kind || ""}
                      </span>
                    )}
                    {c.is_edited && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px", flexShrink: 0,
                        color: "var(--accent)", background: "var(--bg-surface-2)",
                        border: "1px solid var(--accent)",
                      }} title="此章节内容已被手动编辑过">已编辑</span>
                    )}
                    {c.had_asides_removed && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px", flexShrink: 0,
                        color: "var(--jade)", background: "var(--bg-surface-2)",
                        border: "1px solid var(--jade)",
                      }} title="此章节有题外话段落被清理过">已清题外话</span>
                    )}
                    {c.is_garbled && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px", flexShrink: 0,
                        color: "var(--error)", background: "var(--bg-surface-2)",
                        border: "1px solid var(--error)",
                      }} title={(c.garbled_reasons || []).join(" · ")}>乱码</span>
                    )}
                    {c.is_split_piece && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px", flexShrink: 0,
                        color: "var(--text-tertiary)", background: "var(--bg-surface-2)",
                        border: "1px dashed var(--text-tertiary)",
                      }} title="此章节由长度异常章节自动拆分而来">拆分</span>
                    )}
                    <span className="text-xs text-muted" style={{ flexShrink: 0, fontFamily: "var(--font-mono)", minWidth: 64, textAlign: "right" }}>
                      {fmtChars(c.char_count)}
                    </span>
                    <button className="btn"
                            style={{ fontSize: 10, padding: "2px 6px", flexShrink: 0 }}
                            onClick={e => { e.stopPropagation(); openChapterEdit(c.number, c.title); }}
                            title="编辑本章内容（如删除末尾「求月票」）">
                      编辑
                    </button>
                    <button className="btn"
                            style={{ fontSize: 10, padding: "2px 6px", flexShrink: 0 }}
                            onClick={e => { e.stopPropagation(); openRenameModal(c.number, c.title); }}
                            title="只改本章标题（不改内容）">
                      改名
                    </button>
                    <button className="btn"
                            style={{ fontSize: 10, padding: "2px 6px", flexShrink: 0, color: "var(--text-tertiary)" }}
                            onClick={e => { e.stopPropagation(); openNewChapterModal(c.number); }}
                            title="在本章后新建一章">
                      在本章后新建章节
                    </button>
                    <button className="btn"
                            style={{ fontSize: 10, padding: "2px 6px", flexShrink: 0, color: "var(--error)" }}
                            onClick={e => { e.stopPropagation(); deleteOneChapter(c.number); }}
                            title="删除本章">
                      ×
                    </button>
                  </div>
                  {isOpen && (
                    <div style={{
                      marginTop: 6, padding: 8,
                      background: "var(--bg-card)", borderRadius: 3,
                      color: "var(--text-secondary)", lineHeight: 1.6, fontSize: 11,
                    }}>
                      {c.preview_head && (
                        <div style={{ marginBottom: c.preview_tail ? 6 : 0 }}>
                          <span className="text-muted" style={{ marginRight: 6, fontSize: 10 }}>开头</span>
                          <span>{c.preview_head}</span>
                        </div>
                      )}
                      {c.preview_tail && (
                        <div>
                          <span className="text-muted" style={{ marginRight: 6, fontSize: 10 }}>结尾</span>
                          <span>{c.preview_tail}</span>
                        </div>
                      )}
                      {(!c.preview_head && !c.preview_tail) && (
                        <div className="text-muted">（无预览内容）</div>
                      )}
                      {c.author_note_reasons && c.author_note_reasons.length > 0 && (
                        <div style={{ marginTop: 6, paddingTop: 6, borderTop: "1px dashed var(--border)" }}>
                          <span className="text-muted" style={{ marginRight: 6, fontSize: 10 }}>题外话信号</span>
                          {c.author_note_reasons.map((r, i) => (
                            <span key={i} className="tag" style={{
                              marginLeft: 4, fontSize: 10, padding: "1px 6px",
                              background: "var(--bg-surface-2)", color: "var(--text-secondary)",
                              border: "1px solid var(--border)",
                            }}>{r}</span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
              if (!gap) return [chapterEl];
              const previewNums = (gap.missing_numbers || []).slice(0, 8);
              const missingPreview = previewNums.join("、")
                                      + (gap.missing_count > previewNums.length ? "…" : "");
              const gapEl = (
                <div key={`gap-${c.number}`} style={{
                  // Distinct cyan dashed marker so the gap row is
                  // visually separate from the gold "题外话" chapter
                  // chips and the purple "长度异常" chapters.
                  border: "1px dashed #06b6d4",
                  borderRadius: 4,
                  padding: "4px 8px",
                  background: "rgba(6,182,212,0.08)",
                  color: "#06b6d4",
                  fontSize: 11,
                  display: "flex", alignItems: "center", gap: 8,
                }}
                  title={`从第 ${gap.expected_next} 章开始 缺失 ${gap.missing_count} 章`}>
                  <span>⚠</span>
                  <span style={{ flex: 1 }}>
                    缺失 <strong>{gap.missing_count}</strong> 章（{gap.pattern}）：
                    <span style={{ fontFamily: "var(--font-mono)" }}>{missingPreview}</span>
                  </span>
                  <button className="btn"
                          style={{ fontSize: 10, padding: "2px 8px", color: "var(--text-secondary)" }}
                          onClick={e => {
                            e.stopPropagation();
                            openNewChapterModal(c.number);
                          }}
                          title="在此处插入缺失章节">
                    手动补充
                  </button>
                </div>
              );
              return [chapterEl, gapEl];
            })}
          </div>

          {/* Save-all-to-database action — placed BELOW the chapter
              list as the final step the user takes after reviewing
              and cleaning. Filled primary styling matches the rest
              of the "commit" actions. */}
          <div style={{
            marginTop: 12, padding: 12,
            border: "1px solid var(--border)", borderRadius: 4,
            background: "var(--bg-surface)",
            display: "flex", alignItems: "center", justifyContent: "space-between",
            gap: 12, flexWrap: "wrap",
          }}>
            <div className="text-xs text-muted" style={{ flex: 1, minWidth: 200 }}>
              确认章节列表无误后，将完整章节结构写入数据库供后续提取使用。
              {savedSummary.saved_count > 0 && (
                <> · <strong style={{ color: "var(--text-secondary)" }}>已存 {savedSummary.saved_count} 章</strong>，再次点击会覆盖。</>
              )}
            </div>
            <button className="btn-primary"
                    style={{ fontSize: 12, padding: "5px 14px" }}
                    onClick={saveAllChapters}
                    disabled={savingAll || chapters.length === 0}>
              {savingAll
                ? "保存中…"
                : savedSummary.saved_count > 0
                  ? `重新写入数据库（${chapters.length} 章）`
                  : `将全部章节保存写入数据库（${chapters.length}）`}
            </button>
          </div>
        </div>
      )}

      {/* Paragraph-level aside cleanup modal */}
      {paraCleanOpen && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 1000,
            background: "rgba(0,0,0,0.55)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 20,
          }}
        >
          <div style={{
            width: "min(860px, 100%)", maxHeight: "90vh",
            display: "flex", flexDirection: "column",
            background: "var(--bg-app)",
            border: "1px solid var(--border)", borderRadius: 6,
          }}>
            <div style={{
              padding: "10px 14px", borderBottom: "1px solid var(--border)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                  清除题外话段落
                </div>
                <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                  扫描正文章节内嵌的题外话段落（求月票/求订阅/感谢支持/PS 等）。逐条预览，取消勾选不想删除的段落，再点确认。
                </div>
              </div>
              <button className="btn" onClick={() => setParaCleanOpen(false)} disabled={applying}>关闭</button>
            </div>
            <div style={{ padding: 14, flex: 1, overflow: "auto" }}>
              {paraCleanLoading ? (
                <div className="text-xs text-muted">扫描中…</div>
              ) : paraCleanList.length === 0 ? (
                <div className="text-xs text-muted">未在章节内嵌中找到题外话段落。</div>
              ) : (
                <div className="flex flex-col gap-6">
                  {paraCleanList.map(a => {
                    const key = `${a.chapter_number}:${a.para_index}`;
                    const sel = paraCleanSelected.has(key);
                    return (
                      <div key={key}
                        onClick={() => {
                          setParaCleanSelected(prev => {
                            const n = new Set(prev);
                            if (n.has(key)) n.delete(key);
                            else n.add(key);
                            return n;
                          });
                        }}
                        title={sel ? "已选择删除（点击取消）" : "点击勾选此段落"}
                        style={{
                          border: `1px solid ${sel ? "var(--accent)" : "var(--border)"}`,
                          borderRadius: 4, padding: 8,
                          background: sel ? "var(--accent-subtle)" : "transparent",
                          cursor: "pointer",
                          transition: "background 0.1s",
                        }}>
                        <div className="flex items-center gap-8" style={{ marginBottom: 6 }}>
                          <span className="tag" style={{
                            fontSize: 10, padding: "1px 6px", fontFamily: "var(--font-mono)",
                            color: sel ? "var(--accent)" : "var(--text-secondary)",
                            background: "transparent",
                            border: `1px solid ${sel ? "var(--accent)" : "var(--border)"}`,
                          }}>第 {a.chapter_number} 章</span>
                          <div className="truncate" style={{
                            fontSize: 12, fontWeight: 500, color: "var(--text-primary)",
                            flex: 1, minWidth: 0,
                          }}>{a.chapter_title}</div>
                          <span className="text-xs text-muted">
                            段落 {a.para_index + 1}/{a.para_total}
                          </span>
                        </div>
                        {a.reasons && a.reasons.length > 0 && (
                          <div style={{ marginBottom: 6 }}>
                            {a.reasons.map((r: string, i: number) => (
                              <span key={i} className="tag" style={{
                                marginRight: 4, fontSize: 10, padding: "1px 6px",
                                background: "var(--bg-surface-2)", color: "var(--text-secondary)",
                                border: "1px solid var(--border)",
                              }}>{r}</span>
                            ))}
                          </div>
                        )}
                        <div className="text-xs" style={{
                          padding: 6, background: "var(--bg-card)", borderRadius: 3,
                          color: "var(--text-secondary)", lineHeight: 1.65,
                          whiteSpace: "pre-wrap",
                        }}>{a.text}</div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
            <div style={{
              padding: "10px 14px", borderTop: "1px solid var(--border)",
              display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center",
            }}>
              <div className="flex gap-6">
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() => setParaCleanSelected(new Set(paraCleanList.map(a => `${a.chapter_number}:${a.para_index}`)))}>
                  全选
                </button>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() => setParaCleanSelected(new Set())}>
                  全不选
                </button>
              </div>
              <div className="flex gap-6">
                <button className="btn" onClick={() => setParaCleanOpen(false)} disabled={applying}>取消</button>
                <button className="btn-primary" onClick={runParaCleanup}
                        disabled={applying || paraCleanSelected.size === 0}>
                  {applying ? "清除中…" : `确认删除 ${paraCleanSelected.size} 个段落`}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Bulk clean-flagged modal */}
      {bulkOpen && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 1000,
            background: "rgba(0,0,0,0.55)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 20,
          }}
        >
          <div style={{
            width: "min(820px, 100%)", maxHeight: "90vh",
            display: "flex", flexDirection: "column",
            background: "var(--bg-app)",
            border: "1px solid var(--border)", borderRadius: 6,
          }}>
            <div style={{
              padding: "10px 14px", borderBottom: "1px solid var(--border)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                  清除作者说章节
                </div>
                <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                  下方列出所有由「作者说章节」格式识别的整章内容（如上架感言/请假说明/老书友请进等）。确认后将整章从正文中删除，可撤销一次。
                </div>
              </div>
              <button className="btn" onClick={() => setBulkOpen(false)} disabled={applying}>关闭</button>
            </div>
            <div style={{ padding: 14, flex: 1, overflow: "auto" }}>
              {(status?.chapters || []).filter(c => c.pattern === "作者说章节").length === 0 ? (
                <div className="text-xs text-muted">未识别到「作者说章节」类型的章节。</div>
              ) : (
                <div className="flex flex-col gap-8">
                  {(status?.chapters || []).filter(c => c.pattern === "作者说章节").map(c => (
                    <div key={c.number} style={{
                      border: "1px solid var(--gold)", borderRadius: 4,
                      padding: 8, background: "rgba(250,204,21,0.06)",
                    }}>
                      <div className="flex items-center gap-8" style={{ marginBottom: 6 }}>
                        <span className="tag" style={{
                          fontSize: 10, padding: "1px 6px",
                          color: "var(--text-secondary)", border: "1px solid var(--border)",
                          background: "transparent", fontFamily: "var(--font-mono)",
                        }}>#{c.number}</span>
                        <div className="truncate" style={{
                          fontSize: 12, fontWeight: 500, color: "var(--text-primary)", flex: 1, minWidth: 0,
                        }}>{displayTitle(c)}</div>
                        <span className="text-xs text-muted" style={{ fontFamily: "var(--font-mono)" }}>
                          {fmtChars(c.char_count)}
                        </span>
                      </div>
                      {c.author_note_reasons && c.author_note_reasons.length > 0 && (
                        <div style={{ marginBottom: 6 }}>
                          {c.author_note_reasons.map((r, i) => (
                            <span key={i} className="tag" style={{
                              marginRight: 4, fontSize: 10, padding: "1px 6px",
                              background: "var(--bg-surface-2)", color: "var(--text-secondary)",
                              border: "1px solid var(--border)",
                            }}>{r}</span>
                          ))}
                        </div>
                      )}
                      <div className="text-xs" style={{
                        padding: 6, background: "var(--bg-card)", borderRadius: 3,
                        color: "var(--text-secondary)", lineHeight: 1.65,
                        whiteSpace: "pre-wrap",
                      }}>
                        {c.preview_head && <div><span className="text-muted">开头 </span>{c.preview_head}</div>}
                        {c.preview_tail && <div style={{ marginTop: 4 }}><span className="text-muted">结尾 </span>{c.preview_tail}</div>}
                        {(!c.preview_head && !c.preview_tail) && <div className="text-muted">（无内容预览 — 标题本身即为题外话）</div>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div style={{
              padding: "10px 14px", borderTop: "1px solid var(--border)",
              display: "flex", justifyContent: "flex-end", gap: 8,
            }}>
              <button className="btn" onClick={() => setBulkOpen(false)} disabled={applying}>取消</button>
              <button className="btn-primary" onClick={bulkCleanAuthorChapters}
                      disabled={applying || (status?.chapters || []).filter(c => c.pattern === "作者说章节").length === 0}>
                {applying ? "清除中…" : `确认清除 ${(status?.chapters || []).filter(c => c.pattern === "作者说章节").length} 章`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* New-chapter modal */}
      {newChapter && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 1000,
          background: "rgba(0,0,0,0.55)",
          display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
        }}
        >
          <div style={{
            width: "min(720px, 100%)", maxHeight: "90vh",
            display: "flex", flexDirection: "column",
            background: "var(--bg-app)",
            border: "1px solid var(--border)", borderRadius: 6,
          }}>
            <div style={{
              padding: "10px 14px", borderBottom: "1px solid var(--border)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                  新建章节
                </div>
                <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                  {newChapter.afterNumber === null
                    ? "插入到正文开头"
                    : `插入到第 ${newChapter.afterNumber} 章之后`}
                </div>
              </div>
              <button className="btn" onClick={() => setNewChapter(null)} disabled={newChapterSaving}>关闭</button>
            </div>
            <div style={{ padding: 14, flex: 1, overflow: "auto" }}>
              <div className="text-xs" style={{ marginBottom: 4, color: "var(--text-secondary)" }}>章节标题（与其他章节使用相同格式，如「6、新章节」「第六章 新章节」）</div>
              <input className="input" value={newChapter.heading}
                      placeholder="例：147、新章节"
                      onChange={e => setNewChapter({ ...newChapter, heading: e.target.value })}
                      style={{ width: "100%", fontSize: 12, marginBottom: 12 }}
                      autoFocus />
              <div className="text-xs" style={{ marginBottom: 4, color: "var(--text-secondary)" }}>章节内容</div>
              <textarea className="input font-mono"
                        value={newChapter.content}
                        onChange={e => setNewChapter({ ...newChapter, content: e.target.value })}
                        style={{ width: "100%", minHeight: 240, fontSize: 12, lineHeight: 1.7,
                                  resize: "vertical", whiteSpace: "pre-wrap" }}
                        disabled={newChapterSaving} />
            </div>
            <div style={{
              padding: "10px 14px", borderTop: "1px solid var(--border)",
              display: "flex", justifyContent: "flex-end", gap: 8,
            }}>
              <button className="btn" onClick={() => setNewChapter(null)} disabled={newChapterSaving}>取消</button>
              <button className="btn-primary" onClick={saveNewChapter}
                      disabled={newChapterSaving || !newChapter.heading.trim()}>
                {newChapterSaving ? "保存中…" : "保存新章节"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Rename-chapter modal */}
      {renamingChapter && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 1000,
          background: "rgba(0,0,0,0.55)",
          display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
        }}
        >
          <div style={{
            width: "min(560px, 100%)",
            background: "var(--bg-app)",
            border: "1px solid var(--border)", borderRadius: 6,
          }}>
            <div style={{
              padding: "10px 14px", borderBottom: "1px solid var(--border)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                重命名第 {renamingChapter.number} 章
              </div>
              <button className="btn" onClick={() => setRenamingChapter(null)} disabled={renameSaving}>关闭</button>
            </div>
            <div style={{ padding: 14 }}>
              <div className="text-xs text-muted" style={{ marginBottom: 6 }}>
                只改本章的标题行，本章内容保留。整行格式应与其他章节相同。
              </div>
              <input className="input" value={renamingChapter.heading}
                      onChange={e => setRenamingChapter({ ...renamingChapter, heading: e.target.value })}
                      onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); saveRename(); } }}
                      style={{ width: "100%", fontSize: 12 }}
                      autoFocus />
            </div>
            <div style={{
              padding: "10px 14px", borderTop: "1px solid var(--border)",
              display: "flex", justifyContent: "flex-end", gap: 8,
            }}>
              <button className="btn" onClick={() => setRenamingChapter(null)} disabled={renameSaving}>取消</button>
              <button className="btn-primary" onClick={saveRename}
                      disabled={renameSaving || !renamingChapter.heading.trim()}>
                {renameSaving ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Chapter-content edit modal */}
      {editingChapter && (
        <div
          style={{
            position: "fixed", inset: 0, zIndex: 1000,
            background: "rgba(0,0,0,0.55)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 20,
          }}
        >
          <div style={{
            width: "min(900px, 100%)", maxHeight: "90vh",
            display: "flex", flexDirection: "column",
            background: "var(--bg-app)",
            border: "1px solid var(--border)", borderRadius: 6,
            boxShadow: "0 10px 40px rgba(0,0,0,0.4)",
          }}>
            <div style={{
              padding: "10px 14px", borderBottom: "1px solid var(--border)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
              gap: 8,
            }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                  编辑第 {editingChapter.number} 章 · {editingChapter.title}
                </div>
                <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                  保存后会更新正文文件并备份原文（可在「清理章节」处撤销）。
                </div>
              </div>
              <button className="btn" onClick={() => setEditingChapter(null)} disabled={editSaving}>
                关闭
              </button>
            </div>
            <div style={{ padding: 14, flex: 1, overflow: "auto" }}>
              {editLoading ? (
                <div className="text-xs text-muted">加载中…</div>
              ) : (
                <textarea
                  className="input font-mono"
                  value={editingChapter.content}
                  onChange={e => setEditingChapter({ ...editingChapter, content: e.target.value })}
                  style={{
                    width: "100%", minHeight: 500, fontSize: 12, lineHeight: 1.7,
                    resize: "vertical", whiteSpace: "pre-wrap",
                  }}
                  disabled={editSaving}
                />
              )}
              <div className="text-xs text-muted" style={{ marginTop: 6, textAlign: "right" }}>
                {editingChapter.content.replace(/\s/g, "").length} 字（不含空白）
              </div>
            </div>
            <div style={{
              padding: "10px 14px", borderTop: "1px solid var(--border)",
              display: "flex", justifyContent: "flex-end", gap: 8,
            }}>
              <button className="btn" onClick={() => setEditingChapter(null)} disabled={editSaving}>
                取消
              </button>
              <button className="btn-primary" onClick={saveChapterEdit}
                      disabled={editSaving || editLoading || !editingChapter.content.trim()}>
                {editSaving ? "保存中…" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Section 3: volume editor (moved here from 剧情大纲) */}
      <VolumeEditor
        plan={plan}
        planDraft={planDraft}
        planSaving={planSaving}
        startPlanEdit={startPlanEdit}
        loadAutoSuggest={loadAutoSuggest}
        addPlanRow={addPlanRow}
        removePlanRow={removePlanRow}
        savePlan={savePlan}
        cancelPlanEdit={cancelPlanEdit}
        setPlanDraft={setPlanDraft}
      />
    </div>
  );
}

interface VolumeEditorProps {
  plan: SegmentPlan | null;
  planDraft: { title: string; start_chapter: number; end_chapter: number }[] | null;
  planSaving: boolean;
  startPlanEdit: () => void;
  loadAutoSuggest: () => Promise<void>;
  addPlanRow: (afterIdx: number) => void;
  removePlanRow: (idx: number) => void;
  savePlan: () => Promise<void>;
  cancelPlanEdit: () => void;
  setPlanDraft: (d: { title: string; start_chapter: number; end_chapter: number }[] | null) => void;
}

function VolumeEditor(p: VolumeEditorProps) {
  const { plan, planDraft, planSaving } = p;
  if (!plan) return null;

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, background: "var(--bg-surface)" }}>
      <div className="flex items-center justify-between" style={{ marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
            分卷与分段
          </div>
          <div className="text-xs text-muted" style={{ marginTop: 2 }}>
            {plan.segments.length === 0
              ? `共 ${plan.total_chapters} 章 · 尚未划分卷`
              : `${plan.is_custom ? "自定义" : (plan.type === "volumes" ? "按卷处理" : "按 ~10 万字分块")} · ${plan.segments.length} 段`}
          </div>
        </div>
        {!planDraft && plan.segments.length > 0 && (
          <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                  onClick={p.startPlanEdit}
                  title="编辑卷的标题和章节范围">
            编辑分卷
          </button>
        )}
      </div>

      {/* Empty state */}
      {plan.segments.length === 0 && !planDraft && (
        <div style={{
          padding: 16, textAlign: "center",
          border: "1px dashed var(--border)", borderRadius: 4,
        }}>
          <div className="text-xs text-muted" style={{ marginBottom: 12, lineHeight: 1.6 }}>
            还没有分卷。卷标题代表故事中的时间（如「1954 年」），无明确时间时填写章节范围。
          </div>
          <div className="flex gap-8" style={{ justifyContent: "center", flexWrap: "wrap" }}>
            <button className="btn-primary" style={{ fontSize: 12, padding: "5px 14px" }} onClick={p.startPlanEdit}>
              新建卷
            </button>
            <button className="btn" style={{ fontSize: 12, padding: "5px 14px" }} onClick={p.loadAutoSuggest}
                    title="按文中「第 X 卷」标记或 ~10 万字切块自动建议分卷">
              自动检测分卷
            </button>
          </div>
        </div>
      )}

      {/* Edit mode */}
      {planDraft && (
        <div>
          <div className="flex items-center justify-between" style={{ marginBottom: 8, gap: 8, flexWrap: "wrap" }}>
            <div className="text-xs text-muted" style={{ lineHeight: 1.55, flex: 1, minWidth: 220 }}>
              卷标题代表故事中的时间（如「1954 年」），无明确时间时填写章节范围。共 {plan.total_chapters} 章。
              {plan.segments.length > 0 && <><br />保存后会清空已有的提取结果。</>}
            </div>
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                    onClick={p.loadAutoSuggest}
                    disabled={planSaving}>
              自动检测分卷
            </button>
          </div>

          {planDraft.length > 0 && (
            <div className="flex flex-col gap-6" style={{ marginBottom: 10 }}>
              {planDraft.map((s, i) => (
                <div key={i} className="flex gap-6 items-center" style={{
                  padding: 6, border: "1px solid var(--border)", borderRadius: 4,
                }}>
                  <span className="text-xs text-muted" style={{
                    minWidth: 30, textAlign: "center", fontFamily: "var(--font-mono)",
                  }}>#{i + 1}</span>
                  <input
                    className="input"
                    placeholder='故事时间（如 "1954 年"，无则填 "第 1–8 章"）'
                    value={s.title}
                    onChange={e => {
                      const next = [...planDraft];
                      next[i] = { ...s, title: e.target.value };
                      p.setPlanDraft(next);
                    }}
                    style={{ flex: 1, fontSize: 12 }}
                  />
                  <input
                    className="input" type="number" min={1} max={plan.total_chapters || undefined}
                    value={s.start_chapter}
                    onChange={e => {
                      const next = [...planDraft];
                      next[i] = { ...s, start_chapter: parseInt(e.target.value, 10) || 1 };
                      p.setPlanDraft(next);
                    }}
                    style={{ width: 70, fontSize: 12 }}
                    title="起始章号"
                  />
                  <span className="text-xs text-muted">–</span>
                  <input
                    className="input" type="number" min={1} max={plan.total_chapters || undefined}
                    value={s.end_chapter}
                    onChange={e => {
                      const next = [...planDraft];
                      next[i] = { ...s, end_chapter: parseInt(e.target.value, 10) || 1 };
                      p.setPlanDraft(next);
                    }}
                    style={{ width: 70, fontSize: 12 }}
                    title="结束章号"
                  />
                  <span className="text-xs text-muted"
                        style={{ minWidth: 56, textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    {Math.max(0, (s.end_chapter || 0) - (s.start_chapter || 0) + 1)} 章
                  </span>
                  <button className="btn" style={{ fontSize: 11, padding: "3px 8px" }}
                          onClick={() => p.addPlanRow(i)}
                          title="在该段后新建一个分卷">新建卷</button>
                  <button className="btn-icon"
                          onClick={() => p.removePlanRow(i)}
                          style={{ fontSize: 14 }}
                          title="删除该段">&times;</button>
                </div>
              ))}
            </div>
          )}
          {planDraft.length === 0 && (
            <div className="text-xs text-muted" style={{
              padding: 12, marginBottom: 10, textAlign: "center",
              border: "1px dashed var(--border)", borderRadius: 4,
            }}>
              当前没有分卷。点击下方「新建卷」开始添加。
            </div>
          )}
          <div className="flex gap-6" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                    onClick={() => p.addPlanRow(planDraft.length - 1)}
                    disabled={planSaving}>+ 新建卷</button>
            <div className="flex gap-6">
              <button className="btn" onClick={p.cancelPlanEdit} disabled={planSaving}>取消</button>
              <button className="btn-primary" onClick={p.savePlan}
                      disabled={planSaving || planDraft.length === 0}>
                {planSaving ? "保存中..." : "保存分段计划"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* List mode */}
      {!planDraft && plan.segments.length > 0 && (
        <div className="flex flex-col gap-4">
          {plan.segments.map(s => (
            <div key={s.index} style={{
              padding: "6px 10px", border: "1px solid var(--border)", borderRadius: 4,
            }}>
              <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)" }}>
                #{s.index + 1} · {s.title}
              </div>
              <div className="text-xs text-muted">
                第 {s.start_chapter}–{s.end_chapter} 章 · 共 {s.chapter_count ?? (s.end_chapter - s.start_chapter + 1)} 章 · {fmtChars(s.char_count)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
