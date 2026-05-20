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
  SettingItem, SettingUpdate,
} from "./AnalysisEditors";

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

export interface ChapterSignal {
  chapter: string;
  info_density?: number;
  payoffs?: number;
  hooks?: number;
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
  source: "ai" | "paste";
  fp: StyleFingerprint;
  /** Counts of the other three feature types committed alongside this
   *  chunk — purely for the "已入库 · N事件 N角色" badge. */
  counts?: { events: number; characters: number; settings: number };
}

function r2(v: number): number { return Math.round(v * 100) / 100; }
function r4(v: number): number { return Math.round(v * 10000) / 10000; }

/** Char-weighted average of every committed chunk's fingerprint, plus
 *  the concatenated per-chapter signals (sorted by chapter number). */
export function aggregateStyleChunks(
  chunks: Record<string, StyleChunkEntry>,
): StyleFingerprint {
  const entries = Object.values(chunks);
  if (entries.length === 0) return { _chunks: chunks };
  const total = entries.reduce((s, e) => s + (e.chars || 1), 0) || 1;
  const w = (sel: (fp: StyleFingerprint) => number | undefined): number =>
    entries.reduce((s, e) => s + (sel(e.fp) || 0) * (e.chars || 1), 0) / total;
  const signals: ChapterSignal[] = [];
  for (const e of entries) {
    for (const sig of (e.fp.chapter_signals || [])) signals.push(sig);
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
