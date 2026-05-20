/* Shared merge logic for reference-work extraction.
 *
 * The unified 特征提取 tab runs ONE extraction per chunk and then
 * distributes the result into four work-level JSON blobs (chronicle,
 * characters, settings, style). The merge rules used to live inside
 * three separate per-tab panels; they're consolidated here so the
 * unified panel is the single caller and the rules stay in one place.
 */
import type {
  PlotOutline, CharacterItem, CharacterListItem,
  SettingItem, SettingUpdate, RhythmJson,
} from "./AnalysisEditors";
import { CHAPTER_TYPE_VALUES } from "./AnalysisEditors";
import type { SegmentPlan } from "./segmentationCache";

/* ─────────────────── segment grouping ─────────────────── */

/** Parse a chapter number out of "第 12 章" / "第12章" / "12". */
export function chapterNumOf(s?: string): number {
  const m = (s || "").match(/(\d+)/);
  return m ? parseInt(m[1], 10) : NaN;
}

/** Group items by which volume/segment their chapter falls into — used
 *  by the 分段视图 of the characters / settings tabs. Items whose
 *  chapter is unparseable or outside every segment land in a trailing
 *  "未分段" bucket. Empty segment buckets are dropped. */
export function groupBySegment<T>(
  items: T[],
  plan: SegmentPlan | null,
  getChapter: (item: T) => string | undefined,
): { index: number; title: string; items: T[] }[] {
  const segs = plan?.segments || [];
  const buckets = segs.map(s => ({ index: s.index, title: s.title, items: [] as T[] }));
  const unassigned: T[] = [];
  for (const it of items || []) {
    const ch = chapterNumOf(getChapter(it));
    let placed = false;
    if (Number.isFinite(ch)) {
      for (let i = 0; i < segs.length; i++) {
        if (ch >= segs[i].start_chapter && ch <= segs[i].end_chapter) {
          buckets[i].items.push(it);
          placed = true;
          break;
        }
      }
    }
    if (!placed) unassigned.push(it);
  }
  const out = buckets.filter(b => b.items.length > 0);
  if (unassigned.length > 0) {
    out.push({ index: -1, title: "未分段", items: unassigned });
  }
  return out;
}

/** One extraction 分段 (chunk) — the unit the 特征提取 tab works in.
 *  A volume is split into one or more chunks of ≈32k chars each. */
export interface ChunkLoc {
  /** "segIdx:chunkIndex" — matches the style ledger keys. */
  key: string;
  segIdx: number;
  chunkIndex: number;
  /** 1-based index across the whole book — the「第 N 段」number. */
  globalIndex: number;
  volumeTitle: string;
  startChapter: number;
  endChapter: number;
}

/** Human label for a chunk: 「第 3 段 · 第 21–30 章（第二卷）」. */
export function chunkLabel(c: ChunkLoc): string {
  return `第 ${c.globalIndex} 段 · 第 ${c.startChapter}–${c.endChapter} 章（${c.volumeTitle}）`;
}

/** Group items by which extraction 分段 (chunk) their chapter falls in.
 *  This is the 分段视图 grouping — finer than groupBySegment (which is
 *  per-volume). Items outside every chunk land in a trailing bucket. */
export function groupByChunk<T>(
  items: T[],
  chunkList: ChunkLoc[],
  getChapter: (item: T) => string | undefined,
): { loc: ChunkLoc | null; items: T[] }[] {
  const buckets = chunkList.map(c => ({ loc: c as ChunkLoc | null, items: [] as T[] }));
  const unassigned: T[] = [];
  for (const it of items || []) {
    const ch = chapterNumOf(getChapter(it));
    let placed = false;
    if (Number.isFinite(ch)) {
      for (const b of buckets) {
        const c = b.loc!;
        if (ch >= c.startChapter && ch <= c.endChapter) {
          b.items.push(it);
          placed = true;
          break;
        }
      }
    }
    if (!placed) unassigned.push(it);
  }
  const out = buckets.filter(b => b.items.length > 0);
  if (unassigned.length > 0) out.push({ loc: null, items: unassigned });
  return out;
}

/* ─────────────────── chronicle events ─────────────────── */

/** Detect an "absolute" story-time marker (4-digit year, 公元/纪元
 *  era, full date pattern). */
function isAbsoluteTime(s: string): boolean {
  if (!s) return false;
  return /\d{4}\s*年|公元|纪元|世纪|\d{4}[-/]\d{1,2}/.test(s);
}

const TIME_MARKER_SEP = /\s*[·／/|｜＋+；;]\s*/;

/** Post-process an events list so each event gets a `time_markers`
 *  array with every distinct timestamp. Relative-only events inherit
 *  the most recent absolute timestamp seen earlier in the list. */
export function resolveEventTimeMarkers<
  T extends { time_marker?: string; time_markers?: string[] }
>(rawEvents: T[]): T[] {
  let lastAbsolute: string | null = null;
  return rawEvents.map(ev => {
    const next: any = { ...ev };
    const incoming = typeof ev.time_marker === "string" ? ev.time_marker.trim() : "";
    const incomingList = Array.isArray(ev.time_markers)
      ? ev.time_markers.map(s => (typeof s === "string" ? s.trim() : "")).filter(Boolean)
      : [];
    const pieces: string[] = [];
    const seen = new Set<string>();
    const add = (s: string) => {
      const v = s.trim();
      if (v && !seen.has(v)) { seen.add(v); pieces.push(v); }
    };
    for (const t of incomingList) add(t);
    if (incoming) {
      if (TIME_MARKER_SEP.test(incoming)) {
        for (const part of incoming.split(TIME_MARKER_SEP)) add(part);
      } else {
        add(incoming);
      }
    }
    const hasOwnAbsolute = pieces.some(isAbsoluteTime);
    if (!hasOwnAbsolute && lastAbsolute && !seen.has(lastAbsolute)) {
      pieces.unshift(lastAbsolute);
      seen.add(lastAbsolute);
    }
    for (const p of pieces) {
      if (isAbsoluteTime(p)) lastAbsolute = p;
    }
    if (pieces.length > 0) {
      next.time_markers = pieces;
      next.time_marker = pieces[0];
    }
    return next as T;
  });
}

/** Merge a chunk's events into the chronicle: one epoch per volume
 *  (matched by title), one period per first_chapter. When
 *  `replaceRange` is supplied, periods whose 第N章 label falls inside
 *  [startCh, endCh] are dropped first, so re-extracting a chunk cleanly
 *  overwrites its prior events instead of doubling them. */
export function mergeEventsIntoChronicle(
  base: PlotOutline | null,
  newEvents: any[],
  volumeTitle: string,
  replaceRange?: { startCh: number; endCh: number },
): PlotOutline {
  const next: PlotOutline = base
    ? {
        ...base,
        epochs: (base.epochs || []).map(e => ({
          ...e,
          periods: (e.periods || []).map(p => ({
            ...p, events: [...(p.events || [])],
          })),
        })),
      }
    : { logline: "", epochs: [] };
  let epoch = (next.epochs || []).find(e => e.title === volumeTitle);
  if (!epoch) {
    epoch = { title: volumeTitle, periods: [] };
    next.epochs = [...(next.epochs || []), epoch];
  }
  if (replaceRange) {
    epoch.periods = (epoch.periods || []).filter(p => {
      const m = (p.time || "").match(/第\s*(\d+)\s*章/);
      if (!m) return true;
      const n = parseInt(m[1], 10);
      return !(n >= replaceRange.startCh && n <= replaceRange.endCh);
    });
  }
  for (const ev of newEvents) {
    const key = ((ev.first_chapter || "") + "").trim() || "(未指定章节)";
    let period = (epoch.periods || []).find(p => (p.time || "") === key);
    if (!period) {
      period = { time: key, events: [] };
      epoch.periods = [...(epoch.periods || []), period];
    }
    period.events = [...(period.events || []), ev];
  }
  return next;
}

/* ─────────────────── characters ─────────────────── */

export const ROLE_RANK: Record<string, number> = {
  "主角": 0, "女主角": 1, "反派": 2,
  "男配": 3, "女配": 3, "师长": 3,
  "重要配角": 4, "路人": 8, "其他": 9, "": 9,
};

/** Merge new characters into the existing list by name. Sums mentions,
 *  unions the rich fact lists (dedupe by chapter+text), keeps the
 *  highest-rank role_tag, tracks the earliest first_chapter. */
export function mergeCharacters(
  existing: CharacterItem[], incoming: CharacterItem[],
): CharacterItem[] {
  const byName = new Map<string, CharacterItem>();
  for (const c of existing || []) {
    const n = (c.name || "").trim();
    if (!n) continue;
    byName.set(n, { ...c });
  }
  const unionList = (a: CharacterListItem[] = [], b: CharacterListItem[] = []): CharacterListItem[] => {
    const seen = new Set<string>();
    const out: CharacterListItem[] = [];
    for (const x of [...a, ...b]) {
      if (!x || !x.text) continue;
      const key = `${x.chapter || ""}|${x.text}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ chapter: x.chapter || "", text: x.text });
    }
    return out;
  };
  const parseCh = (s?: string) => {
    const m = (s || "").match(/(\d+)/);
    return m ? parseInt(m[1], 10) : Number.MAX_SAFE_INTEGER;
  };
  for (const c of incoming || []) {
    const n = (c.name || "").trim();
    if (!n) continue;
    const prior = byName.get(n);
    if (!prior) {
      byName.set(n, { ...c });
      continue;
    }
    const merged: CharacterItem = { ...prior };
    merged.mentions = (prior.mentions || 0) + (c.mentions || 0);
    if (c.intro && (!prior.intro || c.intro.length > prior.intro.length)) {
      merged.intro = c.intro;
    }
    const pr = ROLE_RANK[prior.role_tag || ""] ?? 9;
    const cr = ROLE_RANK[c.role_tag || ""] ?? 9;
    if (cr < pr) merged.role_tag = c.role_tag;
    if (parseCh(c.first_chapter) < parseCh(prior.first_chapter)) {
      merged.first_chapter = c.first_chapter;
    }
    if (c.first_seen_at && !prior.first_seen_at) {
      merged.first_seen_at = c.first_seen_at;
    }
    merged.appearance  = unionList(prior.appearance,  c.appearance);
    merged.personality = unionList(prior.personality, c.personality);
    merged.experiences = unionList(prior.experiences, c.experiences);
    byName.set(n, merged);
  }
  const list = Array.from(byName.values());
  list.sort((a, b) => {
    const ra = ROLE_RANK[a.role_tag || ""] ?? 9;
    const rb = ROLE_RANK[b.role_tag || ""] ?? 9;
    if (ra !== rb) return ra - rb;
    const ap = (a.appearance_chapters || 0) || (a.mentions || 0);
    const bp = (b.appearance_chapters || 0) || (b.mentions || 0);
    return bp - ap;
  });
  return list;
}

/* ─────────────────── settings ─────────────────── */

/** Merge new settings into the existing list by category+title. Unions
 *  the per-chapter updates, keeps the longest content, earliest
 *  first_chapter. */
export function mergeSettings(
  existing: SettingItem[], incoming: SettingItem[],
): SettingItem[] {
  const key = (s: SettingItem) => `${s.category || "other"}|${(s.title || "").trim()}`;
  const byKey = new Map<string, SettingItem>();
  for (const s of existing || []) {
    if (!s.title) continue;
    byKey.set(key(s), { ...s });
  }
  const unionUpdates = (a: SettingUpdate[] = [], b: SettingUpdate[] = []): SettingUpdate[] => {
    const seen = new Set<string>();
    const out: SettingUpdate[] = [];
    for (const x of [...a, ...b]) {
      if (!x || !x.text) continue;
      const k = `${x.chapter || ""}|${x.text}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ chapter: x.chapter || "", text: x.text });
    }
    return out;
  };
  const parseCh = (str?: string) => {
    const m = (str || "").match(/(\d+)/);
    return m ? parseInt(m[1], 10) : Number.MAX_SAFE_INTEGER;
  };
  for (const s of incoming || []) {
    if (!s.title) continue;
    const k = key(s);
    const prior = byKey.get(k);
    if (!prior) {
      byKey.set(k, { ...s, updates: s.updates || [] });
      continue;
    }
    const merged: SettingItem = { ...prior };
    merged.updates = unionUpdates(prior.updates, s.updates);
    if (s.content && (!prior.content || s.content.length > prior.content.length)) {
      merged.content = s.content;
    }
    if (s.first_introduced_at && !prior.first_introduced_at) {
      merged.first_introduced_at = s.first_introduced_at;
    }
    if (parseCh(s.first_chapter) < parseCh(prior.first_chapter)) {
      merged.first_chapter = s.first_chapter;
    }
    byKey.set(k, merged);
  }
  return Array.from(byKey.values());
}

/* ─────────────────── style fingerprint ─────────────────── */

export interface ChapterPayoff {
  type: string;
  /** Concrete plot description of this 爽点 (not just the tag). */
  plot?: string;
}
export interface ChapterHook { position: string; content: string }
/** Per-chapter signal from the unified extraction — feeds both the
 *  style fingerprint's aggregates and the 节奏 (rhythm) section. */
export interface ChapterSignal {
  chapter: string;
  info_density?: number;
  chapter_types?: string[];
  summary?: string;
  payoffs?: ChapterPayoff[];
  hooks?: ChapterHook[];
}
export interface StyleFingerprint {
  avg_sentence_length?: number;
  vocab_complexity?: number;
  punctuation_profile?: {
    ellipsis?: number; dash?: number; exclamation?: number;
    question?: number; comma?: number;
  };
  dialogue_ratio?: number;
  rhetoric_frequency?: number;
  description_density?: number;
  payoff_density?: number;
  info_density?: number;
  hook_density?: number;
  pacing_profile?: { fast?: number; medium?: number; slow?: number };
  chapter_signals?: ChapterSignal[];
  /** Per-chunk ledger. Also the source of truth for which chunks the
   *  unified extraction tab has already committed. */
  _chunks?: Record<string, StyleChunkEntry>;
}
export interface StyleChunkEntry {
  chars: number;
  source?: "ai" | "paste";
  /** Present once the 风格/节奏 section is committed for this chunk. */
  fp?: StyleFingerprint;
  /** Which of the four sections have been committed for this chunk.
   *  Lets the unified tab offer a per-section 确认入库. */
  done?: { events?: boolean; characters?: boolean; settings?: boolean; style?: boolean };
  /** Counts of each feature type extracted — for the "已入库" badge. */
  counts?: { events: number; characters: number; settings: number };
}

function r2(v: number): number { return Math.round(v * 100) / 100; }
function r4(v: number): number { return Math.round(v * 10000) / 10000; }

/** Coerce a raw chapter_signals array (from a pasted web-LLM response,
 *  which may not match the schema) into the canonical shape — payoffs
 *  and hooks ALWAYS arrays. The AI path is normalized server-side; this
 *  is the paste-path equivalent and the reason `.map is not a function`
 *  no longer fires. */
export function normalizeChapterSignals(raw: any): ChapterSignal[] {
  if (!Array.isArray(raw)) return [];
  const out: ChapterSignal[] = [];
  for (const s of raw) {
    if (!s || typeof s !== "object") continue;
    let payoffs: ChapterPayoff[] = [];
    if (Array.isArray(s.payoffs)) {
      payoffs = s.payoffs
        .map((p: any) => typeof p === "string"
          ? { type: p.trim() || "其他", plot: "" }
          : (p && typeof p === "object"
            ? { type: String(p.type || "其他"), plot: String(p.plot || "") }
            : null))
        .filter(Boolean) as ChapterPayoff[];
    } else if (typeof s.payoffs === "number" && s.payoffs > 0) {
      payoffs = Array.from({ length: Math.floor(s.payoffs) }, () => ({ type: "其他", plot: "" }));
    }
    let hooks: ChapterHook[] = [];
    if (Array.isArray(s.hooks)) {
      hooks = s.hooks
        .map((h: any) => typeof h === "string"
          ? { position: "章末", content: h.trim() }
          : (h && typeof h === "object"
            ? { position: String(h.position || "章末"), content: String(h.content || "") }
            : null))
        .filter(Boolean) as ChapterHook[];
    } else if (typeof s.hooks === "number" && s.hooks > 0) {
      hooks = Array.from({ length: Math.floor(s.hooks) }, () => ({ position: "章末", content: "" }));
    }
    const info = Number(s.info_density);
    out.push({
      chapter: String(s.chapter || ""),
      info_density: Number.isFinite(info) ? info : 0,
      chapter_types: Array.isArray(s.chapter_types)
        ? s.chapter_types.map((t: any) => String(t)).filter(Boolean)
        : [],
      summary: String(s.summary || ""),
      payoffs,
      hooks,
    });
  }
  return out;
}

/** Normalize a pasted unified-extraction `style` block: coerce numbers,
 *  normalize chapter_signals, and DERIVE the chunk-level payoff / hook /
 *  info densities from the signals (single source of truth, same as the
 *  server's _normalize_unified_style). */
export function normalizeUnifiedStyle(
  raw: any, nChars: number, nChapters: number,
): StyleFingerprint {
  const s = (raw && typeof raw === "object") ? raw : {};
  const num = (v: any): number => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };
  const signals = normalizeChapterSignals(s.chapter_signals);
  let payoffs = 0, hooks = 0, infoSum = 0;
  for (const sig of signals) {
    payoffs += (sig.payoffs || []).length;
    hooks += (sig.hooks || []).length;
    infoSum += sig.info_density || 0;
  }
  const pp = (s.pacing_profile && typeof s.pacing_profile === "object") ? s.pacing_profile : {};
  return {
    dialogue_ratio:      r4(num(s.dialogue_ratio)),
    rhetoric_frequency:  r4(num(s.rhetoric_frequency)),
    description_density: r4(num(s.description_density)),
    payoff_density:      r4(payoffs / Math.max(nChars / 10000, 1)),
    hook_density:        r4(hooks / Math.max(nChapters, 1)),
    info_density:        signals.length ? r4(infoSum / signals.length) : 0,
    pacing_profile: {
      fast:   r2(num(pp.fast)),
      medium: r2(num(pp.medium)),
      slow:   r2(num(pp.slow)),
    },
    chapter_signals: signals,
  };
}

/** Char-weighted average of every STYLE-committed chunk's fingerprint,
 *  plus the concatenated per-chapter signals (sorted by chapter). Only
 *  entries that carry an `fp` (i.e. their 风格 section was committed)
 *  count toward the aggregate — a chunk with只入库了事件 doesn't skew
 *  the style numbers. The full `chunks` ledger is carried through so
 *  per-section commit state survives reloads. */
export function aggregateStyleChunks(
  chunks: Record<string, StyleChunkEntry>,
): StyleFingerprint {
  const entries = Object.values(chunks).filter(
    e => e.fp && Object.keys(e.fp).length > 0,
  );
  if (entries.length === 0) return { _chunks: chunks };
  const total = entries.reduce((s, e) => s + (e.chars || 1), 0) || 1;
  const w = (sel: (fp: StyleFingerprint) => number | undefined): number =>
    entries.reduce((s, e) => s + (sel(e.fp!) || 0) * (e.chars || 1), 0) / total;
  const signals: ChapterSignal[] = [];
  for (const e of entries) {
    for (const sig of (e.fp!.chapter_signals || [])) signals.push(sig);
  }
  const chNum = (s: string) => {
    const m = (s || "").match(/(\d+)/);
    return m ? parseInt(m[1], 10) : Number.MAX_SAFE_INTEGER;
  };
  signals.sort((a, b) => chNum(a.chapter) - chNum(b.chapter));
  return {
    avg_sentence_length: r2(w(f => f.avg_sentence_length)),
    vocab_complexity:    r4(w(f => f.vocab_complexity)),
    dialogue_ratio:      r4(w(f => f.dialogue_ratio)),
    rhetoric_frequency:  r4(w(f => f.rhetoric_frequency)),
    description_density: r4(w(f => f.description_density)),
    payoff_density:      r4(w(f => f.payoff_density)),
    info_density:        r4(w(f => f.info_density)),
    hook_density:        r4(w(f => f.hook_density)),
    pacing_profile: {
      fast:   r4(w(f => f.pacing_profile?.fast)),
      medium: r4(w(f => f.pacing_profile?.medium)),
      slow:   r4(w(f => f.pacing_profile?.slow)),
    },
    punctuation_profile: {
      ellipsis:    r4(w(f => f.punctuation_profile?.ellipsis)),
      dash:        r4(w(f => f.punctuation_profile?.dash)),
      exclamation: r4(w(f => f.punctuation_profile?.exclamation)),
      question:    r4(w(f => f.punctuation_profile?.question)),
      comma:       r4(w(f => f.punctuation_profile?.comma)),
    },
    chapter_signals: signals,
    _chunks: chunks,
  };
}

/* ─────────────────── rhythm (节奏) ─────────────────── */

/** Build the 节奏 (rhythm) section from the aggregated per-chapter
 *  signals. The unified extraction's chapter_signals carry per-chapter
 *  info density, types, payoffs (爽点) and hooks (钩子) — exactly the
 *  per-chapter data the rhythm section wants. The rhythm_json is
 *  therefore fully DERIVED from chapter_signals (single source of
 *  truth) rather than extracted separately. */
export function buildRhythmFromSignals(
  signals: ChapterSignal[] | undefined,
): RhythmJson {
  const valid = new Set<string>(CHAPTER_TYPE_VALUES as readonly string[]);
  const chNum = (s: string) => {
    const m = (s || "").match(/(\d+)/);
    return m ? parseInt(m[1], 10) : 0;
  };
  const features = (signals || []).map(s => {
    const types = (Array.isArray(s.chapter_types) ? s.chapter_types : [])
      .map(t => (valid.has(t) ? t : "其他"))
      .filter((t, i, a) => a.indexOf(t) === i);
    const hookList = Array.isArray(s.hooks) ? s.hooks : [];
    return {
      chapter: chNum(s.chapter),
      types: (types.length ? types : ["其他"]) as any,
      info_density: typeof s.info_density === "number" ? s.info_density : 0,
      summary: s.summary || "",
      hooks: hookList.map(h => ({
        position: (h && (h.position === "章首" || h.position === "段中" || h.position === "章末")
          ? h.position : "章末") as "章首" | "段中" | "章末",
        content: (h && h.content) || "",
      })),
    };
  });
  features.sort((a, b) => a.chapter - b.chapter);
  const shuangdian: { chapter: number; type: string; plot?: string }[] = [];
  for (const s of (signals || [])) {
    for (const p of (Array.isArray(s.payoffs) ? s.payoffs : [])) {
      shuangdian.push({
        chapter: chNum(s.chapter),
        type: (p && p.type) || "其他",
        plot: (p && p.plot) || "",
      });
    }
  }
  shuangdian.sort((a, b) => a.chapter - b.chapter);
  return {
    coverage: { chapters: features.length, chars: 0 },
    opening_pattern: "",
    climax_positions: features
      .filter(f => (f.types as string[]).includes("高潮"))
      .map(f => f.chapter),
    shuangdian,
    chapter_features: features,
    info_density_curve: features.map(f => f.info_density),
    pacing_segments: [],
  };
}
