import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  // chapter_id: stable per-detection unique key. ALL state (excluded,
  // collapsed, editing, etc.) and ALL API calls use this — `number`
  // alone can collide when two patterns produce the same parsed numeral.
  // Older persisted states may not have chapter_id; we fall back to
  // String(number) for backward compat.
  chapter_id?: string;
  number: number;
  // display_number is what to render as "第N章" — the original parsed
  // numeral. Same as number EXCEPT when a collision-bump rewrote it.
  display_number?: number;
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
  cannot_repair?: boolean;
  preview_head?: string;
  preview_tail?: string;
}

interface LogEntry { ts: number; message: string; chapter?: number | null; }

/** A chapter committed to the reference_chapters DB table. */
interface SavedChapter {
  number: number;
  title: string;
  volume: string;
  char_count: number;
  is_author_note: boolean;
}

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
  parsed_at?: string | null;
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

/** Stable key for a chapter — chapter_id when present, else
 *  String(number) as a legacy fallback for stale persisted state. */
function cid(c: Chapter): string {
  return c.chapter_id || String(c.number);
}

/** The numeral to show in "#N" / "第N章" labels. Prefer
 *  display_number (which preserves the original parsed numeral even
 *  after a collision-bump). */
function displayNum(c: Chapter): number {
  return c.display_number ?? c.number;
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

/** Live status log tail.
 *
 * Split out + memoized because the parent re-renders on every status
 * poll (600ms), and `toLocaleTimeString()` is locale-formatted (slow)
 * being called once per visible entry. The memo keys on the log
 * entries' timestamps so only new entries trigger reformatting. */
const LogTail = React.memo(function LogTail({
  status, state,
}: {
  status: { log?: { ts: number; message: string }[] } | null | undefined;
  state: string;
}) {
  const log = status?.log;
  const formatted = useMemo(() => {
    if (!log || log.length === 0) return [] as { time: string; message: string }[];
    return log.slice(-30).map(e => ({
      time: new Date(e.ts * 1000).toLocaleTimeString(),
      message: e.message,
    }));
  }, [log]);
  if (formatted.length === 0 || (state !== "running" && state !== "paused")) return null;
  return (
    <div style={{
      maxHeight: 120, overflowY: "auto",
      border: "1px solid var(--border)", borderRadius: 3,
      background: "var(--bg-card)", padding: 6,
      fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.5,
      color: "var(--text-secondary)",
    }}>
      {formatted.map((e, i) => (
        <div key={i} style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          <span style={{ color: "var(--text-tertiary)" }}>{e.time}</span>{" "}{e.message}
        </div>
      ))}
    </div>
  );
});

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
  // Track whether the first /preprocess/status call has returned, so
  // we can show a 「加载中」 placeholder instead of the empty-state
  // CTA ("还没有章节") during the initial load. Prevents the user
  // from misclicking 重新检测 on a tab whose history hasn't loaded
  // yet, which would clobber the persisted chapter list.
  const [statusLoaded, setStatusLoaded] = useState(false);
  // ``excluded`` is purely user-driven. We do NOT auto-seed from the
  // is_author_note flag — that previously made the checkboxes feel
  // stuck (auto-seed re-applied on every poll). A "勾选全部疑似题外话"
  // button below the filter bar lets the user bulk-select manually.
  // ALL chapter-keyed sets now hold chapter_id strings (not numbers)
  // so collisions like "1.1" / "1、" — which both could resolve to
  // number=1 — stay distinguishable. Use cid(c) to derive the key.
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<"all" | "flagged" | "outlier" | "garbled" | "kept">("all");
  const [applying, setApplying] = useState(false);
  const [cleaningGarbled, setCleaningGarbled] = useState(false);
  // Synchronous click-feedback flag for startJob. Toggled true the
  // microsecond the user clicks "使用…开始识别" so the button label /
  // disabled state updates before the network round-trip starts.
  const [starting, setStarting] = useState(false);
  // Volume plan editor (moved here from PlotOutlinePanel)
  const [plan, setPlan] = useState<SegmentPlan | null>(null);
  const [planDraft, setPlanDraft] = useState<{ title: string; start_chapter: number; end_chapter: number }[] | null>(null);
  const [planSaving, setPlanSaving] = useState(false);
  // AI-driven volume detection state (separate from the per-segment
  // AI extraction state — this one targets the volume-boundary detector).
  const [aiDetecting, setAiDetecting] = useState(false);
  const [aiPromptUsed, setAiPromptUsed] = useState("");
  const [aiPromptOpen, setAiPromptOpen] = useState(false);
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
  // The actual chapter list committed to the reference_chapters DB
  // table — the source of truth for the read-only「已存储章节」view.
  const [savedChapters, setSavedChapters] = useState<SavedChapter[]>([]);
  const [savingAll, setSavingAll] = useState(false);
  // Once chapters are written to the database the "章节清理" editing
  // section collapses to a compact read-only summary; the user clicks
  // 「修改数据库中章节」 to re-open the full editor. This stays false
  // until the user explicitly asks to edit, and flips back to false
  // after a fresh save.
  const [chapterEditMode, setChapterEditMode] = useState(false);
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
  // Per-candidate expand state for the format-pick list. Independent
  // of `candidateTests` so the user can collapse a row WITHOUT losing
  // the already-loaded match results.
  const [candidateExpanded, setCandidateExpanded] = useState<Set<string>>(new Set());
  // When the user picks the 题外话 filter, lazy-fetch aside paragraphs
  // for this work once and key by chapter number; the chapter preview
  // then renders ONLY the matched paragraphs instead of head + tail
  // (per user feedback — head/tail is useless when filtering to asides).
  const [filterAsides, setFilterAsides] = useState<any[] | null>(null);
  const [filterAsidesLoading, setFilterAsidesLoading] = useState(false);
  // Per-chapter content edit modal
  const [editingChapter, setEditingChapter] = useState<{ chapter_id: string; display_number: number; title: string; content: string } | null>(null);
  const [editLoading, setEditLoading] = useState(false);
  const [editSaving, setEditSaving] = useState(false);
  // New-chapter modal (CRUD: add)
  const [newChapter, setNewChapter] = useState<{ afterNumber: number | null; heading: string; content: string } | null>(null);
  const [newChapterSaving, setNewChapterSaving] = useState(false);
  // Per-chapter rename modal (CRUD: update title only)
  const [renamingChapter, setRenamingChapter] = useState<{ chapter_id: string; display_number: number; heading: string } | null>(null);
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
    } finally {
      setStatusLoaded(true);
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
    // The read-only「已存储章节」view reads the committed DB chapters
    // here — independent of the transient detection state.
    try {
      const r = await apiGet<{ chapters: SavedChapter[] }>(
        `/api/references/works/${refId}/preprocess/saved_chapters`,
      );
      setSavedChapters(r.chapters || []);
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

  // Lazy-load aside paragraphs the first time the 题外话 filter is
  // active. The list is scoped to the work; re-fetch when refId or
  // the chapter list (status) changes since paragraph indexes can
  // shift after edits.
  useEffect(() => {
    if (filter !== "flagged") return;
    let cancelled = false;
    (async () => {
      setFilterAsidesLoading(true);
      try {
        const r = await apiGet<{ asides: any[] }>(
          `/api/references/works/${refId}/preprocess/aside_paragraphs`,
        );
        if (!cancelled) setFilterAsides(r.asides || []);
      } catch {
        if (!cancelled) setFilterAsides([]);
      } finally {
        if (!cancelled) setFilterAsidesLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [filter, refId, status?.chapters?.length]);
  // Invalidate the cached asides whenever the user leaves the 题外话
  // filter so re-entering triggers a fresh fetch.
  useEffect(() => {
    if (filter !== "flagged") setFilterAsides(null);
  }, [filter]);

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
      // Collapse back to the read-only summary now that the DB is current.
      setChapterEditMode(false);
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
    setCandidateExpanded(new Set());
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
    setStarting(true);
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
      setCollapsed(new Set());  // re-expand all on fresh detection
      toast(fps.length > 0
        ? `已用「${fps.join(" + ")}」开始识别`
        : "已开始智能识别章节", "info");
    } catch (e: any) {
      toast(e?.message || "启动失败", "error");
    } finally {
      setStarting(false);
    }
  };

  const openChapterEdit = async (c: Chapter) => {
    const chapter_id = cid(c);
    const display_number = displayNum(c);
    setEditingChapter({ chapter_id, display_number, title: c.title, content: "" });
    setEditLoading(true);
    try {
      const r = await apiGet<{ content: string }>(
        `/api/references/works/${refId}/preprocess/chapter/${encodeURIComponent(chapter_id)}/content`,
      );
      setEditingChapter({ chapter_id, display_number, title: c.title, content: r.content || "" });
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
  const openRenameModal = (c: Chapter) => {
    setRenamingChapter({
      chapter_id: cid(c),
      display_number: displayNum(c),
      heading: c.title,
    });
  };
  const saveRename = async () => {
    if (!renamingChapter || !renamingChapter.heading.trim()) {
      toast("标题不能为空", "info");
      return;
    }
    setRenameSaving(true);
    try {
      await apiPatch(
        `/api/references/works/${refId}/preprocess/chapter/${encodeURIComponent(renamingChapter.chapter_id)}/title`,
        { heading: renamingChapter.heading },
      );
      toast(`第 ${renamingChapter.display_number} 章已改名`, "success");
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
  const deleteOneChapter = async (c: Chapter) => {
    if (!(await confirmDialog({
      title: "删除章节",
      message: `确认从正文中删除第 ${displayNum(c)} 章？此操作可撤销一次。`,
      destructive: true,
    }))) return;
    try {
      await fetch(`/api/references/works/${refId}/preprocess/chapter/${encodeURIComponent(cid(c))}`, {
        method: "DELETE",
      }).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); });
      toast(`第 ${displayNum(c)} 章已删除`, "success");
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
        `/api/references/works/${refId}/preprocess/chapter/${encodeURIComponent(editingChapter.chapter_id)}/content`,
        { content: editingChapter.content },
      );
      toast(`第 ${editingChapter.display_number} 章已保存`, "success");
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

  const toggleExclude = (n: string) => {
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
      const r = await apiPost<{ ok: boolean; removed_chapters: string[]; new_char_count: number }>(
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

  // 一键修复乱码: whole-file encoding mojibake repair + per-chapter
  // fallback. Chapters that resist repair get marked `cannot_repair`
  // by the backend so the UI can surface a 「无法修复」 label.
  const repairGarbled = async () => {
    const garbledCount = chapters.filter(c => c.is_garbled).length;
    if (garbledCount === 0) {
      toast("没有检测到乱码", "info");
      return;
    }
    setCleaningGarbled(true);
    try {
      const r = await apiPost<{ total_chapters: number; new_char_count: number }>(
        `/api/references/works/${refId}/preprocess/repair_garbled`, {},
        { timeoutMs: 120_000 },
      );
      toast(`已修复 · 剩 ${fmtChars(r.new_char_count)} · ${r.total_chapters} 章`, "success");
      await onAfterApplyExclusions?.();
      await fetchStatus();
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "修复失败", "error");
    } finally {
      setCleaningGarbled(false);
    }
  };

  const bulkCleanAuthorChapters = async () => {
    const ids = (status?.chapters || [])
      .filter(c => c.pattern === "作者说章节")
      .map(c => cid(c));
    if (ids.length === 0) {
      toast("没有「作者说章节」类型的章节", "info");
      return;
    }
    setApplying(true);
    try {
      const r = await apiPost<{ removed_chapters: string[]; new_char_count: number }>(
        `/api/references/works/${refId}/preprocess/apply_exclusions`,
        { excluded_chapters: ids },
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
    setAiDetecting(true);
    try {
      // The AI endpoint internally walks: web-search LLM → local LLM →
      // text scan → parser tags. It always returns the prompt it used
      // so we can show it in the modal even when AI succeeded.
      const sug = await apiPost<{
        segments: SegmentInfo[];
        used_method: string;
        prompt_used: string;
        warning?: string;
      }>(
        `/api/references/works/${refId}/segments/plan/ai_detect`,
        {},
        { timeoutMs: 300_000 },
      );
      setAiPromptUsed(sug.prompt_used || "");
      if (!sug.segments || sug.segments.length === 0) {
        // Show the prompt so user can copy it into a web LLM.
        setAiPromptOpen(true);
        toast(sug.warning || "自动检测未识别到可分卷的结构，可复制 prompt 手动尝试", "info");
        return;
      }
      setPlanDraft(sug.segments.map(s => ({
        title: s.title || `第 ${s.start_chapter}–${s.end_chapter} 章`,
        start_chapter: s.start_chapter,
        end_chapter: s.end_chapter,
      })));
      const methodLabel = ({
        ai_web_search: "AI 联网搜索",
        ai_local: "AI 本地推理",
        text_scan: "正文卷标记扫描",
        parser_tags: "章节解析器标记",
      } as Record<string, string>)[sug.used_method] || "自动检测";
      toast(`已载入 ${sug.segments.length} 段建议（${methodLabel}），请检查后保存`, "success");
    } catch (e: any) {
      // On AI failure, still surface the prompt so the user has a path
      // forward (copy to ChatGPT / Claude.ai → paste results back).
      try {
        const r = await apiGet<{ prompt: string }>(
          `/api/references/works/${refId}/segments/plan/ai_detect_prompt`,
        );
        setAiPromptUsed(r.prompt || "");
        setAiPromptOpen(true);
      } catch { /* keep the original toast below */ }
      toast(e?.message || "自动检测失败", "error");
    } finally { setAiDetecting(false); }
  };

  const showAiPrompt = async () => {
    if (aiPromptUsed) { setAiPromptOpen(true); return; }
    try {
      const r = await apiGet<{ prompt: string }>(
        `/api/references/works/${refId}/segments/plan/ai_detect_prompt`,
      );
      setAiPromptUsed(r.prompt || "");
      setAiPromptOpen(true);
    } catch (e: any) {
      toast(e?.message || "获取 prompt 失败", "error");
    }
  };

  const copyAiPrompt = async () => {
    if (!aiPromptUsed) return;
    try {
      await navigator.clipboard.writeText(aiPromptUsed);
      toast("已复制 prompt 到剪贴板", "success");
    } catch {
      toast("复制失败：请手动选中文本", "error");
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
      title: "保存分段计划",
      message: "更改卷的章节范围后，原来按旧范围抽取的剧情大纲会与新范围对不上，所以需要清空一次重新提取。继续保存？",
    }))) return;
    setPlanSaving(true);
    try {
      // The PUT returns the saved plan — use it directly instead of a
      // second GET round-trip.
      const saved = await apiPut<SegmentPlan>(
        `/api/references/works/${refId}/segments/plan`,
        { segments: cleaned, plan_type: cleaned.length > 1 ? "volumes" : "custom" },
        { timeoutMs: 60_000 });
      toast("分段计划已保存", "success");
      setPlanDraft(null);
      setPlan(saved);
      // The chronicle / characters / settings tabs share a segmentation
      // cache keyed by refId — invalidate it so the next tab they
      // open sees the new plan, not the cached old one.
      try {
        const mod = await import("./segmentationCache");
        mod.invalidateSegmentation(refId);
      } catch { /* shared cache module is optional */ }
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
    if (filter === "kept") return !excluded.has(cid(c));
    return true;
  });

  const toggleExpand = (n: string) => {
    // INVERTED: toggle COLLAPSED membership. Default = expanded.
    setCollapsed(prev => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n); else next.add(n);
      return next;
    });
  };
  const allCollapsed = (chs: Chapter[]): boolean =>
    chs.length > 0 && chs.every(c => collapsed.has(cid(c)));
  const toggleAllCollapse = () => {
    if (chapters.length === 0) return;
    if (allCollapsed(chapters)) {
      setCollapsed(new Set());
    } else {
      setCollapsed(new Set(chapters.map(c => cid(c))));
    }
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
            {status?.parsed_at && (
              <div className="text-xs text-muted" style={{
                marginTop: 4,
                color: "var(--text-tertiary)",
                fontFamily: "var(--font-mono)",
              }}>
                上次识别：{status.parsed_at}
              </div>
            )}
          </div>
          <div className="flex items-center gap-6" style={{ flexWrap: "wrap" }}>
            {/* Keep the action button MOUNTED while guessing so the
                user sees the click register instantly — swap label to
                "匹配中…" + disable instead of unmounting the button
                and rendering a separate progress panel below (which
                was the source of the perceived lag on click). */}
            {(state === "idle" || state === "done" || state === "cancelled" || state === "error") && !guessCandidates && (
              <button className="btn-primary"
                      style={{
                        fontSize: 12, padding: "5px 14px",
                        cursor: (guessing || !statusLoaded) ? "wait" : "pointer",
                        background: guessing ? "var(--gold)" : undefined,
                        color: guessing ? "#000" : undefined,
                        borderColor: guessing ? "var(--gold)" : undefined,
                        opacity: !statusLoaded ? 0.5 : 1,
                      }}
                      onClick={() => { setChapterEditMode(true); void guessFormat(); }}
                      disabled={guessing || !statusLoaded}
                      title={!statusLoaded ? "正在加载历史，请稍候…" : "先扫描全文匹配章节格式，再让你确认后进行识别"}>
                {guessing
                  ? <><span className="spinner-inline" style={{
                        display: "inline-block",
                        width: 10, height: 10, marginRight: 6,
                        border: "2px solid #000",
                        borderTopColor: "transparent",
                        borderRadius: "50%",
                        animation: "spin 0.7s linear infinite",
                        verticalAlign: "middle",
                      }} />
                      匹配中… {guessProgress?.total
                        ? Math.round(((guessProgress.current || 0) / guessProgress.total) * 100) + "%"
                        : ""}
                    </>
                  : (state === "idle" ? "匹配章节格式" : "重新识别")}
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
                const isExpanded = candidateExpanded.has(c.name);
                const toggleExpand = () => {
                  setCandidateExpanded(prev => {
                    const n = new Set(prev);
                    if (n.has(c.name)) {
                      n.delete(c.name);
                    } else {
                      n.add(c.name);
                      // Lazy-load matches the first time the row expands.
                      if (!candidateTests[c.name]) testCandidate(c.name);
                    }
                    return n;
                  });
                };
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
                        匹配 {(t?.count ?? c.count)} 处
                      </span>
                      <button className="btn"
                              style={{ fontSize: 10, padding: "2px 8px", minWidth: 50 }}
                              onClick={toggleExpand}
                              disabled={t?.loading}
                              title="展开/收起该格式匹配到的章节">
                        {t?.loading ? "…" : isExpanded ? "收起" : "展开"}
                      </button>
                      {c.custom && (
                        <button className="btn-icon"
                                onClick={() => deleteCustomFormat(c.name)}
                                style={{ fontSize: 14, color: "var(--error)" }}
                                title="删除此自定义格式">&times;</button>
                      )}
                    </div>
                    {isExpanded && t && !t.loading && (
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
                          }}>共 {t.count} 处</span>
                          {t.preview.length < t.count && (
                            <span className="text-muted" style={{ marginLeft: 6 }}>
                              （仅显示前 {t.preview.length} 处）
                            </span>
                          )}
                        </div>
                        {t.preview.length > 0 && (
                          <ul style={{
                            margin: "4px 0 0 16px", padding: 0,
                            maxHeight: 240, overflowY: "auto",
                          }}>
                            {t.preview.map((m: any, k: number) => (
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
              <button className="btn-primary"
                      style={{
                        fontSize: 11, padding: "3px 10px",
                        cursor: starting ? "wait" : "pointer",
                        opacity: starting ? 0.7 : 1,
                      }}
                      onClick={() => { startJob(Array.from(chosenFormats)); setGuessCandidates(null); }}
                      disabled={starting || chosenFormats.size === 0}>
                {starting
                  ? "启动中…"
                  : (chosenFormats.size <= 1
                      ? `使用「${Array.from(chosenFormats)[0] || "?"}」开始识别`
                      : `使用所选 ${chosenFormats.size} 个格式开始识别`)}
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
        <LogTail status={status} state={state} />


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

      {/* Initial-load placeholder — shown until the first
          /preprocess/status call resolves. Prevents the empty-state
          CTA below from flashing on tab switches and prevents the
          user from clicking 重新检测 before the persisted chapter
          history has loaded. */}
      {!statusLoaded && chapters.length === 0 && hasFullText && (
        <div style={{
          padding: 20, textAlign: "center",
          border: "1px dashed var(--border)", borderRadius: 4,
          background: "var(--bg-surface)",
          color: "var(--text-tertiary)", fontSize: 12,
        }}>
          加载中…
        </div>
      )}

      {/* Empty-state CTA: when detection has been run but no chapters
          exist (or never run on a fresh upload). Hidden while the
          read-only「已存储章节」view is showing. */}
      {statusLoaded && chapters.length === 0 && (state === "idle" || state === "done") && hasFullText
        && (savedSummary.saved_count === 0 || chapterEditMode) && (
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

      {/* Section 2 (read-only): when chapters are committed to the DB,
          show them by default — read straight from reference_chapters
          so the view survives reloads even when the transient detection
          state is empty. 「重新识别章节」 re-opens the cleaning editor. */}
      {savedSummary.saved_count > 0 && !chapterEditMode && (
        <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, background: "var(--bg-surface)" }}>
          <div className="flex items-center justify-between" style={{ marginBottom: 8, gap: 8, flexWrap: "wrap" }}>
            <div>
              <div className="flex items-center" style={{ gap: 8 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                  已存储章节
                </span>
                <span className="tag" style={{
                  fontSize: 10, padding: "1px 8px",
                  color: "var(--jade)", border: "1px solid var(--jade)", borderRadius: 3,
                }}>已写入数据库</span>
              </div>
              <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                数据库中共 {savedSummary.saved_count} 章
                {savedSummary.saved_at && ` · 保存于 ${new Date(savedSummary.saved_at).toLocaleString("zh-CN")}`}
                {" "}· 如需重新识别 / 清理，点上方「智能章节识别」的「重新识别」
              </div>
            </div>
          </div>
          <div className="flex flex-col gap-2" style={{
            maxHeight: 360, overflowY: "auto",
            padding: 6, border: "1px solid var(--border)", borderRadius: 4,
          }}>
            {savedChapters.map(c => (
              <div key={c.number} className="flex items-center" style={{
                gap: 8, fontSize: 12, padding: "3px 6px",
                borderBottom: "1px dashed var(--border)",
              }}>
                <span style={{
                  fontFamily: "var(--font-mono)", color: "var(--accent)",
                  minWidth: 56,
                }}>第 {c.number} 章</span>
                <span className="truncate" style={{ flex: 1, color: "var(--text-secondary)" }}>
                  {c.title || "(无标题)"}
                </span>
                <span className="text-xs text-muted" style={{ fontFamily: "var(--font-mono)" }}>
                  {c.char_count.toLocaleString()} 字
                </span>
              </div>
            ))}
            {savedChapters.length === 0 && (
              <div className="text-xs text-muted" style={{ padding: 8, textAlign: "center" }}>
                正在加载已存储章节…
              </div>
            )}
          </div>
        </div>
      )}

      {/* Section 2 (editor): chapter list + author-note flags + outlier flags */}
      {chapters.length > 0 && (savedSummary.saved_count === 0 || chapterEditMode) && (
        <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, background: "var(--bg-surface)" }}>
          <div className="flex items-center justify-between" style={{ marginBottom: 8, gap: 8, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                章节清理（共 {chapters.length} 章 · 已勾选排除 {excluded.size}）
              </div>
            </div>
            <div className="flex items-center gap-6">
              {savedSummary.saved_count > 0 && chapterEditMode && (
                <button className="btn"
                        style={{ fontSize: 11, padding: "4px 12px", color: "var(--text-tertiary)" }}
                        onClick={() => setChapterEditMode(false)}
                        title="收起编辑器，回到只读的已存储章节视图">
                  收起编辑
                </button>
              )}
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
                          background: "var(--purple)", color: "#fff",
                          border: "1px solid var(--purple)", borderRadius: 3,
                          cursor: cleaningGarbled ? "wait" : "pointer",
                          opacity: cleaningGarbled ? 0.7 : 1,
                        }}
                        onClick={repairGarbled}
                        disabled={cleaningGarbled}
                        title="先尝试整本编码还原（GBK↔UTF-8、Latin-1 等），再逐章兜底修复；剩余无法修复的章节会被标注「无法修复」">
                  {cleaningGarbled ? "修复中…" : `一键修复乱码（${chapters.filter(c => c.is_garbled).length}）`}
                </button>
              )}
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
            {chapters.length > 0 && (
              <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                      onClick={toggleAllCollapse}
                      title="一键收起或展开所有章节预览">
                {allCollapsed(chapters) ? "全部展开" : "全部收起"}
              </button>
            )}
            {/* Single 全选 toggle: first click selects all in current
                view; second click clears them. Derived from whether
                every visible chapter is currently excluded. */}
            {(() => {
              const allSelected = filteredChapters.length > 0
                && filteredChapters.every(c => excluded.has(cid(c)));
              return (
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() => {
                          const visible = filteredChapters.map(c => cid(c));
                          setExcluded(prev => {
                            const next = new Set(prev);
                            if (allSelected) visible.forEach(k => next.delete(k));
                            else visible.forEach(k => next.add(k));
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
              const key = cid(c);
              const dnum = displayNum(c);
              // Default-expanded: open UNLESS user explicitly collapsed.
              const isOpen = !collapsed.has(key);
              const isFlagged = !!c.is_author_note;
              const isOutlier = !!c.is_length_outlier;
              const isExcluded = excluded.has(key);
              const borderColor = isExcluded ? "var(--error)"
                                  : isFlagged ? "var(--gold)"
                                  : isOutlier ? "var(--purple)"
                                  : "var(--border)";
              const bgColor = isExcluded ? "rgba(220,38,38,0.06)"
                              : isFlagged ? "rgba(250,204,21,0.06)"
                              : isOutlier ? "rgba(168,85,247,0.06)"
                              : "transparent";
              // Gap lookup uses the DISPLAY numeral so missing chapters
              // line up with what the user sees (e.g. "1、" → "3、"
              // with 2 missing displays correctly under #1).
              const gap = (status?.gaps || []).find(g => g.after_number === dnum);
              const chapterEl = (
                <div key={key}
                  onClick={() => toggleExclude(key)}
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
                    }}>#{dnum}</span>
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={e => { e.stopPropagation(); toggleExpand(key); }}
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
                    {c.cannot_repair && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px", flexShrink: 0,
                        color: "#fff", background: "var(--error)",
                        border: "1px solid var(--error)",
                      }} title="一键修复后仍残留无法识别的乱码，建议手动编辑该章">无法修复</span>
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
                            onClick={e => { e.stopPropagation(); openChapterEdit(c); }}
                            title="编辑本章内容（如删除末尾「求月票」）">
                      编辑
                    </button>
                    <button className="btn"
                            style={{ fontSize: 10, padding: "2px 6px", flexShrink: 0 }}
                            onClick={e => { e.stopPropagation(); openRenameModal(c); }}
                            title="只改本章标题（不改内容）">
                      改名
                    </button>
                    <button className="btn"
                            style={{ fontSize: 10, padding: "2px 6px", flexShrink: 0, color: "var(--text-tertiary)" }}
                            onClick={e => { e.stopPropagation(); openNewChapterModal(dnum); }}
                            title="在本章后新建一章">
                      在本章后新建章节
                    </button>
                    <button className="btn"
                            style={{ fontSize: 10, padding: "2px 6px", flexShrink: 0, color: "var(--error)" }}
                            onClick={e => { e.stopPropagation(); deleteOneChapter(c); }}
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
                      {/* Filter-aware preview: in 题外话/乱码 mode show
                          ONLY the matched paragraphs / signals so the
                          user doesn't have to scan the head+tail to
                          find the offending text. */}
                      {filter === "flagged" ? (
                        (() => {
                          // Backend keys asides by `chapter_number` —
                          // the (post-bump) unique `number` field, not
                          // the visible `display_number`. Match on c.number.
                          const matched = (filterAsides || []).filter(
                            a => a.chapter_number === c.number,
                          );
                          if (filterAsidesLoading && !filterAsides) {
                            return <div className="text-muted">加载题外话段落中…</div>;
                          }
                          if (matched.length === 0) {
                            return <div className="text-muted">（本章无单独段落级题外话；整章为疑似题外话）</div>;
                          }
                          return (
                            <div className="flex flex-col gap-4">
                              {matched.map((a: any, k: number) => (
                                <div key={k} style={{
                                  padding: 6,
                                  background: "rgba(250,204,21,0.08)",
                                  borderLeft: "2px solid var(--gold)",
                                  borderRadius: 2,
                                }}>
                                  <div className="text-muted" style={{ fontSize: 10, marginBottom: 2 }}>
                                    段落 {a.para_index + 1}/{a.para_total} · {(a.reasons || []).join(" · ")}
                                  </div>
                                  <div>{a.text}</div>
                                </div>
                              ))}
                            </div>
                          );
                        })()
                      ) : filter === "garbled" ? (
                        (c.garbled_reasons && c.garbled_reasons.length > 0) ? (
                          <div className="flex flex-col gap-4">
                            {c.garbled_reasons.map((r: string, k: number) => (
                              <div key={k} style={{
                                padding: 6,
                                background: "rgba(220,38,38,0.06)",
                                borderLeft: "2px solid var(--error)",
                                borderRadius: 2,
                                fontFamily: "var(--font-mono)",
                                whiteSpace: "pre-wrap",
                              }}>{r}</div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-muted">（本章无具体乱码样本）</div>
                        )
                      ) : (
                        <>
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
                        </>
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
                <div key={`gap-${key}`} style={{
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
                  <span>[注意]</span>
                  <span style={{ flex: 1 }}>
                    缺失 <strong>{gap.missing_count}</strong> 章（{gap.pattern}）：
                    <span style={{ fontFamily: "var(--font-mono)" }}>{missingPreview}</span>
                  </span>
                  <button className="btn"
                          style={{ fontSize: 10, padding: "2px 8px", color: "var(--text-secondary)" }}
                          onClick={e => {
                            e.stopPropagation();
                            openNewChapterModal(dnum);
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
              {savedSummary.saved_count > 0
                ? <>已存 {savedSummary.saved_count} 章</>
                : null}
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
                    <div key={cid(c)} style={{
                      border: "1px solid var(--gold)", borderRadius: 4,
                      padding: 8, background: "rgba(250,204,21,0.06)",
                    }}>
                      <div className="flex items-center gap-8" style={{ marginBottom: 6 }}>
                        <span className="tag" style={{
                          fontSize: 10, padding: "1px 6px",
                          color: "var(--text-secondary)", border: "1px solid var(--border)",
                          background: "transparent", fontFamily: "var(--font-mono)",
                        }}>#{displayNum(c)}</span>
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
                重命名第 {renamingChapter.display_number} 章
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
            }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                编辑第 {editingChapter.display_number} 章 · {editingChapter.title}
              </div>
              <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                保存后会更新正文文件并备份原文（可在「清理章节」处撤销）。
              </div>
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
        aiDetecting={aiDetecting}
        startPlanEdit={startPlanEdit}
        loadAutoSuggest={loadAutoSuggest}
        showAiPrompt={showAiPrompt}
        addPlanRow={addPlanRow}
        removePlanRow={removePlanRow}
        savePlan={savePlan}
        cancelPlanEdit={cancelPlanEdit}
        setPlanDraft={setPlanDraft}
      />
      {aiPromptOpen && (
        <AiPromptModal
          prompt={aiPromptUsed}
          onClose={() => setAiPromptOpen(false)}
          onCopy={copyAiPrompt}
        />
      )}
    </div>
  );
}

function AiPromptModal({ prompt, onClose, onCopy }: {
  prompt: string;
  onClose: () => void;
  onCopy: () => void;
}) {
  // Modal showing the exact prompt that would be / was sent to the LLM
  // for volume detection. Use case: configured model fails or returns
  // unparseable output → user copies prompt to ChatGPT / Claude.ai →
  // pastes the JSON back into the chapter range inputs manually.
  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 1000,
      }}>
      <div onClick={e => e.stopPropagation()} style={{
        background: "var(--bg-app)", border: "1px solid var(--border)",
        borderRadius: 6, padding: 16, width: "min(720px, 92vw)",
        maxHeight: "85vh", display: "flex", flexDirection: "column",
      }}>
        <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
          <h4 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>
            自动检测分卷的 prompt
          </h4>
          <button className="btn-icon" onClick={onClose}
                  style={{ fontSize: 16 }} title="关闭">&times;</button>
        </div>
        <div className="text-xs text-muted" style={{ marginBottom: 8, lineHeight: 1.55 }}>
          复制这段 prompt 到网页版 LLM（ChatGPT / Claude.ai / 元宝 / 通义 等）。
          模型返回 JSON 后，把里面的 <code>volumes</code> 数组里的每一项手动填入下方的章节范围。
        </div>
        <pre className="font-mono" style={{
          flex: 1, margin: 0, padding: 10,
          background: "var(--bg-card)", border: "1px solid var(--border)",
          borderRadius: 4, fontSize: 11, lineHeight: 1.55,
          overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word",
          color: "var(--text-secondary)",
        }}>{prompt || "（prompt 为空）"}</pre>
        <div className="flex gap-6" style={{ justifyContent: "flex-end", marginTop: 10 }}>
          <button className="btn" onClick={onClose}>关闭</button>
          <button className="btn-primary" onClick={onCopy} disabled={!prompt}>
            复制到剪贴板
          </button>
        </div>
      </div>
    </div>
  );
}

interface VolumeEditorProps {
  plan: SegmentPlan | null;
  planDraft: { title: string; start_chapter: number; end_chapter: number }[] | null;
  planSaving: boolean;
  aiDetecting: boolean;
  startPlanEdit: () => void;
  loadAutoSuggest: () => Promise<void>;
  showAiPrompt: () => void;
  addPlanRow: (afterIdx: number) => void;
  removePlanRow: (idx: number) => void;
  savePlan: () => Promise<void>;
  cancelPlanEdit: () => void;
  setPlanDraft: (d: { title: string; start_chapter: number; end_chapter: number }[] | null) => void;
}

function VolumeEditor(p: VolumeEditorProps) {
  const { plan, planDraft, planSaving } = p;

  return (
    <div style={{
      border: "2px solid var(--accent)", borderRadius: "var(--radius-sm)",
      padding: 12, background: "var(--bg-surface)",
    }}>
      <div className="flex items-center justify-between" style={{ marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
        <div>
          <div className="flex items-center" style={{ gap: 8 }}>
            <span style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
              分卷与分段
            </span>
            <span className="tag" style={{
              fontSize: 10, padding: "1px 7px",
              color: "var(--accent)", border: "1px solid var(--accent)", borderRadius: 3,
            }}>新建 / 编辑 / 删除卷</span>
          </div>
          <div className="text-xs text-muted" style={{ marginTop: 2 }}>
            {!plan
              ? "加载分卷信息中…"
              : plan.segments.length === 0
                ? `共 ${plan.total_chapters} 章 · 尚未划分卷`
                : `${plan.is_custom ? "自定义" : (plan.type === "volumes" ? "按卷处理" : "按 ~10 万字分块")} · ${plan.segments.length} 段`}
          </div>
        </div>
        {plan && !planDraft && plan.segments.length > 0 && (
          <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                  onClick={p.startPlanEdit}
                  title="编辑卷的标题和章节范围">
            编辑分卷
          </button>
        )}
      </div>

      {!plan && (
        <div className="text-xs text-muted" style={{
          padding: 16, textAlign: "center",
          border: "1px dashed var(--border)", borderRadius: 4,
        }}>
          正在加载分卷信息……如长时间未加载，请确认正文已上传并完成章节识别。
        </div>
      )}

      {/* Empty state */}
      {plan && plan.segments.length === 0 && !planDraft && (
        <div style={{
          padding: 16, textAlign: "center",
          border: "1px dashed var(--border)", borderRadius: 4,
        }}>
          <div className="text-xs text-muted" style={{ marginBottom: 12 }}>
            还没有分卷。
          </div>
          <div className="flex gap-8" style={{ justifyContent: "center", flexWrap: "wrap" }}>
            <button className="btn-primary" style={{ fontSize: 12, padding: "5px 14px" }} onClick={p.startPlanEdit}>
              新建卷
            </button>
            <button className="btn" style={{ fontSize: 12, padding: "5px 14px" }} onClick={p.loadAutoSuggest}
                    disabled={p.aiDetecting}
                    title="优先用联网 AI 检索官方分卷信息；联网模型不可用时回退到正文卷标记扫描">
              {p.aiDetecting ? "检测中…" : "自动检测分卷"}
            </button>
            <button className="btn-ghost" style={{ fontSize: 11, padding: "5px 10px" }}
                    onClick={p.showAiPrompt}
                    title="查看 / 复制发给 LLM 的 prompt（可粘到 ChatGPT、Claude.ai 等手动检测）">
              复制 prompt
            </button>
          </div>
        </div>
      )}

      {/* Edit mode */}
      {plan && planDraft && (
        <div>
          <div className="flex items-center justify-between" style={{ marginBottom: 8, gap: 8, flexWrap: "wrap" }}>
            <div className="text-xs text-muted" style={{ flex: 1, minWidth: 180 }}>
              共 {plan.total_chapters} 章
            </div>
            <div className="flex gap-6" style={{ flexShrink: 0 }}>
              <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                      onClick={p.loadAutoSuggest}
                      disabled={planSaving || p.aiDetecting}
                      title="优先用联网 AI 检索官方分卷信息；联网模型不可用时回退到正文卷标记扫描">
                {p.aiDetecting ? "检测中…" : "自动检测分卷"}
              </button>
              <button className="btn-ghost" style={{ fontSize: 11, padding: "3px 8px" }}
                      onClick={p.showAiPrompt}
                      title="查看 / 复制发给 LLM 的 prompt（可粘到 ChatGPT、Claude.ai 等手动检测）">
                复制 prompt
              </button>
            </div>
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
                  <button className="btn-icon" style={{ fontSize: 14, width: 22, height: 22 }}
                          onClick={() => p.addPlanRow(i)}
                          title="在该段后插入一个新的分卷">+</button>
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
              <div style={{ marginBottom: 8 }}>当前没有分卷。</div>
              <button className="btn-primary" style={{ fontSize: 12, padding: "4px 14px" }}
                      onClick={() => p.addPlanRow(-1)}
                      disabled={planSaving}>新建卷</button>
            </div>
          )}
          <div className="flex gap-6" style={{ justifyContent: "flex-end", flexWrap: "wrap" }}>
            <button className="btn" onClick={p.cancelPlanEdit} disabled={planSaving}>取消</button>
            <button className="btn-primary" onClick={p.savePlan}
                    disabled={planSaving || planDraft.length === 0}>
              {planSaving ? "保存中..." : "保存分段计划"}
            </button>
          </div>
        </div>
      )}

      {/* List mode */}
      {plan && !planDraft && plan.segments.length > 0 && (
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
