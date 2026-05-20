/* Per-chunk extraction panels for the Characters and Settings tabs.
 *
 * Mirrors the chronicle tab's per-chunk pattern (volume → chunk →
 * AI extract OR paste from web LLM → preview → 确认入库) but for
 * the richer character / setting data models defined in
 * AnalysisEditors.tsx (CharacterItem with appearance/personality/
 * experiences lists, SettingItem with updates list).
 *
 * Why a separate file instead of generalising ChunkRow: the preview
 * renderer differs significantly per item type (events vs character
 * cards vs setting cards), and the merge semantics are different too
 * (by chapter range vs by name vs by category+title). A generic
 * component would be more abstract than helpful.
 */
import React, { useEffect, useState } from "react";
import { apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";
import { PromptCopyPanel } from "./AnalysisEditors";
import type {
  CharacterItem, CharacterListItem,
  SettingItem, SettingUpdate,
  ChronicleEpoch,
} from "./AnalysisEditors";
import { useSegmentation } from "./segmentationCache";
import type { ChunkMeta } from "./segmentationCache";

type Phase = "idle" | "extracting" | "ready" | "failed" | "committing" | "committed";

interface ChunkPhase<T> {
  status: Phase;
  source?: "ai" | "paste";
  items?: T[];
  error?: string;
  elapsedS?: number;
  startedAt?: number;
  pasteRaw?: string;
  pasteError?: string;
}

/** Generic shared state for the per-chunk extraction flow.
 *
 * Type param T is the extracted item shape (CharacterItem | SettingItem).
 * Callers supply:
 *   - resourceKey: stable key for this work × kind so the extracted
 *     phase state can be re-derived from persisted data on reload.
 *   - extractPath: backend URL suffix (extract_characters / extract_settings)
 *   - itemsKey:    field name in the response payload ("characters" / "settings")
 *   - parsePasted: convert raw textarea content to an items array
 *   - mergeIntoWork: how to dedupe + insert items into the work-level state
 *   - renderPreview: per-item preview rendering
 */
function fmtChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`;
  return `${n.toLocaleString()} 字`;
}

/** Pull a chapter number out of strings like "第 12 章", "第12章",
 *  "Chapter 12", or a bare "12". Returns NaN if nothing parseable. */
function chapterNum(s?: string): number {
  if (!s) return NaN;
  const m = String(s).match(/(\d+)/);
  return m ? parseInt(m[1], 10) : NaN;
}

/** Decide whether a chunk has already been extracted by scanning the
 *  persisted data for items first_introduced (or updated) inside this
 *  chunk's chapter range. Returns the count so the UI can show e.g.
 *  "已提取 · 4 条" — restores the marker on page reload without needing
 *  a separate per-chunk ledger. Works for both characters (first_chapter)
 *  and settings (first_chapter + updates[].chapter). */
function countPriorItemsInChunk(
  data: any[] | null | undefined,
  startCh: number,
  endCh: number,
): number {
  if (!data || data.length === 0) return 0;
  let n = 0;
  for (const item of data) {
    const inRange = (raw?: string) => {
      const c = chapterNum(raw);
      return Number.isFinite(c) && c >= startCh && c <= endCh;
    };
    if (inRange(item.first_chapter)) { n++; continue; }
    const updates = item.updates || item.appearance || item.personality || item.experiences;
    if (Array.isArray(updates) && updates.some((u: any) => inRange(u?.chapter))) {
      n++;
    }
  }
  return n;
}

/** Lenient JSON parse: strips <think> blocks, ```json fences, and finds
 *  the longest balanced JSON object/array in the text. Mirrors the
 *  chronicle's paste parser logic. */
function leniencyParse<T>(
  raw: string,
  itemsKey: string,
): { items: T[]; error?: string } {
  let s = (raw || "").replace(/<think>[\s\S]*?<\/think>/gi, "")
                     .replace(/<\/?think>/gi, "");
  const fence = s.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (fence) s = fence[1];
  // Defensive normalization for clipboard mangling (curly quotes, NBSPs).
  s = s.replace(/[﻿​-‍]/g, "")
       .replace(/[“”]/g, '"')
       .replace(/[‘’]/g, "'")
       .replace(/ /g, " ");
  s = s.trim();
  if (!s) return { items: [], error: "请先粘贴 LLM 返回的 JSON" };

  const balancedExtract = (start: number): string | null => {
    if (s[start] !== "{" && s[start] !== "[") return null;
    let depth = 0, inString = false, escape = false;
    for (let i = start; i < s.length; i++) {
      const c = s[i];
      if (escape) { escape = false; continue; }
      if (c === "\\") { escape = true; continue; }
      if (c === '"') { inString = !inString; continue; }
      if (inString) continue;
      if (c === "{" || c === "[") depth++;
      else if (c === "}" || c === "]") {
        depth--;
        if (depth === 0) return s.slice(start, i + 1);
      }
    }
    return null;
  };

  // Repair pass for the common breakage where inner string quotes get
  // unescaped by a clipboard / web UI (Claude.ai's copy button strips
  // `\` before `"`). A `"` is treated as a real string terminator only
  // when the next non-whitespace char is `,`, `}`, `]`, `:`, or EOF.
  const repairUnescapedQuotes = (input: string): string => {
    const out: string[] = [];
    let inString = false, escape = false;
    for (let i = 0; i < input.length; i++) {
      const c = input[i];
      if (escape) { out.push(c); escape = false; continue; }
      if (c === "\\") { out.push(c); escape = true; continue; }
      if (c !== '"') { out.push(c); continue; }
      if (!inString) { out.push(c); inString = true; continue; }
      let j = i + 1;
      while (j < input.length && /\s/.test(input[j])) j++;
      const nxt = j < input.length ? input[j] : "";
      if (nxt === "" || nxt === "," || nxt === "}" || nxt === "]" || nxt === ":") {
        out.push(c);
        inString = false;
      } else {
        out.push('\\"');
      }
    }
    return out.join("");
  };

  const extract = (parsed: any): T[] => {
    if (Array.isArray(parsed)) {
      // Array of items, or array of {[itemsKey]: [...]} wrappers
      if (parsed.length > 0 && parsed[0] && typeof parsed[0] === "object"
          && Array.isArray(parsed[0][itemsKey])) {
        const out: T[] = [];
        for (const o of parsed) for (const x of (o[itemsKey] || [])) out.push(x);
        return out;
      }
      return parsed;
    }
    if (parsed && typeof parsed === "object") {
      if (Array.isArray(parsed[itemsKey])) return parsed[itemsKey];
    }
    return [];
  };

  const candidates: T[][] = [];
  const tried = new Set<number>();
  let lastErr = "";
  const tryParse = (ext: string): T[] | null => {
    try {
      const arr = extract(JSON.parse(ext));
      if (arr.length > 0) return arr;
    } catch (e: any) { lastErr = e?.message || String(e); }
    const repaired = repairUnescapedQuotes(ext);
    if (repaired !== ext) {
      try {
        const arr = extract(JSON.parse(repaired));
        if (arr.length > 0) return arr;
      } catch (e: any) { lastErr = e?.message || String(e); }
    }
    return null;
  };
  const wrapperPattern = new RegExp(`\\{\\s*"${itemsKey}"\\s*:`, "g");
  let m: RegExpExecArray | null;
  while ((m = wrapperPattern.exec(s)) !== null) {
    if (tried.has(m.index)) continue;
    tried.add(m.index);
    const ext = balancedExtract(m.index);
    if (!ext) continue;
    const arr = tryParse(ext);
    if (arr) candidates.push(arr);
  }
  // Fallback: try every { or [ position
  if (candidates.length === 0) {
    for (let i = 0; i < s.length; i++) {
      if (s[i] !== "{" && s[i] !== "[") continue;
      if (tried.has(i)) continue;
      tried.add(i);
      const ext = balancedExtract(i);
      if (!ext) continue;
      const arr = tryParse(ext);
      if (arr) candidates.push(arr);
    }
  }
  if (candidates.length === 0) {
    let extra = "";
    const pm = lastErr.match(/position\s+(\d+)/i);
    if (pm) {
      const pos = parseInt(pm[1], 10);
      if (pos >= 0 && pos < s.length) {
        const a = Math.max(0, pos - 30), b = Math.min(s.length, pos + 30);
        extra = `\n出错位置附近：…${s.slice(a, pos)}【${s.slice(pos, pos + 1)}】${s.slice(pos + 1, b)}…`;
      }
    }
    return {
      items: [],
      error: lastErr
        ? `JSON 解析失败：${lastErr.slice(0, 120)}${extra}\n` +
          "常见原因：未转义的内嵌引号，或复制时直引号变成弯引号。"
        : `在 JSON 中没找到任何条目。预期形如 {${itemsKey}: [...]}。`,
    };
  }
  const items = candidates.sort((a, b) => b.length - a.length)[0];
  return { items };
}

/* ═══════════════════════════════════════════════════════════════
 * Characters extraction panel
 * ═══════════════════════════════════════════════════════════════ */

const ROLE_RANK: Record<string, number> = {
  "主角": 0, "女主角": 1, "反派": 2,
  "男配": 3, "女配": 3, "师长": 3,
  "重要配角": 4, "路人": 8, "其他": 9, "": 9,
};
const ROLE_COLORS: Record<string, string> = {
  "主角": "var(--accent)", "女主角": "#f472b6", "反派": "var(--error)",
  "男配": "var(--jade)", "女配": "var(--jade)", "师长": "var(--purple)",
  "重要配角": "var(--gold)", "路人": "var(--text-tertiary)",
  "其他": "var(--text-tertiary)", "": "var(--text-tertiary)",
};

/** Merge new characters into the existing list by name. Sums mentions,
 *  unions the rich fact lists (dedupe by chapter+text), keeps the
 *  highest-rank role_tag, and tracks earliest first_chapter. */
function mergeCharacters(existing: CharacterItem[], incoming: CharacterItem[]): CharacterItem[] {
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
    // Role: keep the higher-rank (lower index) tag
    const pr = ROLE_RANK[prior.role_tag || ""] ?? 9;
    const cr = ROLE_RANK[c.role_tag || ""] ?? 9;
    if (cr < pr) merged.role_tag = c.role_tag;
    // first_chapter: keep earliest if both look like 第N章
    const parseCh = (s?: string) => {
      const m = (s || "").match(/(\d+)/);
      return m ? parseInt(m[1], 10) : Number.MAX_SAFE_INTEGER;
    };
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
  // Sort by frequency (appearance_chapters fallback → mentions desc),
  // with role-rank breaking ties so the主角 stays on top.
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

export function CharactersExtractionSection({
  refId, hasFullText, data, onSave,
  activeSegmentIndex, onActiveSegmentChange,
}: {
  refId: string;
  hasFullText: boolean;
  data: CharacterItem[];
  onSave: (next: CharacterItem[]) => Promise<void> | void;
  activeSegmentIndex?: number;
  onActiveSegmentChange?: (idx: number) => void;
}) {
  return (
    <ExtractionSection<CharacterItem>
      refId={refId}
      hasFullText={hasFullText}
      data={data}
      onSave={onSave}
      kind="characters"
      promptKey="reference.characters"
      sectionTitle="角色提取"
      extractPath="extract_characters"
      itemsKey="characters"
      mergeIntoWork={mergeCharacters}
      renderPreview={CharactersPreview}
      activeSegmentIndex={activeSegmentIndex}
      onActiveSegmentChange={onActiveSegmentChange}
    />
  );
}

function CharactersPreview({ items }: { items: CharacterItem[] }) {
  if (!items || items.length === 0) {
    return <div className="text-xs text-muted">未生成任何角色。</div>;
  }
  return (
    <div style={{
      marginTop: 6, padding: 6,
      maxHeight: 320, overflowY: "auto",
      background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: 3,
    }}>
      {items.map((c, i) => {
        const tag = c.role_tag || "";
        return (
          <div key={i} style={{ marginBottom: 8 }}>
            <div className="flex items-center" style={{ gap: 6, marginBottom: 2, flexWrap: "wrap" }}>
              <span style={{ fontWeight: 700, fontSize: 12, color: "var(--text-primary)" }}>{c.name}</span>
              {tag && (
                <span className="tag" style={{
                  fontSize: 10, padding: "0 6px",
                  color: ROLE_COLORS[tag] || "var(--text-tertiary)",
                  border: `1px solid ${ROLE_COLORS[tag] || "var(--text-tertiary)"}`,
                  borderRadius: 3,
                }}>{tag}</span>
              )}
              {c.first_chapter && (
                <span className="tag" style={{
                  fontSize: 10, padding: "0 5px",
                  color: "var(--jade)", border: "1px solid var(--jade)",
                  borderRadius: 3,
                }}>{c.first_chapter}</span>
              )}
            </div>
            {c.intro && (
              <div className="text-xs" style={{ marginLeft: 8, color: "var(--text-secondary)" }}>
                {c.intro}
              </div>
            )}
            <CharacterFactLists c={c} compact />
          </div>
        );
      })}
    </div>
  );
}

/** Render appearance/personality/experiences lists with chapter tags.
 *  Public so the chronicle display section can reuse it. */
export function CharacterFactLists({ c, compact }: { c: CharacterItem; compact?: boolean }) {
  const sections: Array<{ label: string; items?: CharacterListItem[] }> = [
    { label: "外貌", items: c.appearance },
    { label: "性格", items: c.personality },
    { label: "经历", items: c.experiences },
  ];
  const fontSize = compact ? 11 : 12;
  const pad = compact ? "1px 6px" : "2px 7px";
  return (
    <div style={{ marginLeft: 8, marginTop: compact ? 2 : 6 }}>
      {sections.map(s => (
        (s.items && s.items.length > 0) ? (
          <div key={s.label} style={{ marginBottom: compact ? 2 : 4 }}>
            <span className="text-xs text-muted" style={{ marginRight: 4, fontWeight: 600 }}>
              {s.label}
            </span>
            {s.items.map((it, i) => (
              <span key={i} style={{
                display: "inline-block", marginRight: 4, marginBottom: 2,
                fontSize, lineHeight: 1.5, padding: pad,
                color: "var(--text-secondary)",
                background: "var(--bg-surface)",
                border: "1px solid var(--border)", borderRadius: 3,
              }}>
                {it.chapter && (
                  <span style={{ color: "var(--gold)", marginRight: 4 }}>{it.chapter}</span>
                )}
                {it.text}
              </span>
            ))}
          </div>
        ) : null
      ))}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
 * Settings extraction panel
 * ═══════════════════════════════════════════════════════════════ */

const CATEGORY_LABEL_CN: Record<string, string> = {
  power_system: "力量体系",
  factions:     "势力组织",
  geography:    "地理",
  social_rules: "社会规则",
  history:      "历史背景",
  hard_rules:   "硬规则",
  worldview:    "世界观",
  other:        "其他",
};
const CATEGORY_COLOR: Record<string, string> = {
  power_system: "var(--accent)",
  factions:     "var(--purple)",
  geography:    "var(--jade)",
  social_rules: "var(--indigo)",
  history:      "var(--gold)",
  hard_rules:   "#f472b6",
  worldview:    "var(--cyan)",
  other:        "var(--text-tertiary)",
};

/** Merge settings by (category, title). Concats updates while deduping
 *  by chapter+text so re-extraction doesn't double up entries. */
function mergeSettings(existing: SettingItem[], incoming: SettingItem[]): SettingItem[] {
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
    // Earliest first_chapter wins
    const parseCh = (str?: string) => {
      const m = (str || "").match(/(\d+)/);
      return m ? parseInt(m[1], 10) : Number.MAX_SAFE_INTEGER;
    };
    if (parseCh(s.first_chapter) < parseCh(prior.first_chapter)) {
      merged.first_chapter = s.first_chapter;
    }
    byKey.set(k, merged);
  }
  return Array.from(byKey.values());
}

export function SettingsExtractionSection({
  refId, hasFullText, data, onSave,
  activeSegmentIndex, onActiveSegmentChange,
}: {
  refId: string;
  hasFullText: boolean;
  data: SettingItem[];
  onSave: (next: SettingItem[]) => Promise<void> | void;
  activeSegmentIndex?: number;
  onActiveSegmentChange?: (idx: number) => void;
}) {
  return (
    <ExtractionSection<SettingItem>
      refId={refId}
      hasFullText={hasFullText}
      data={data}
      onSave={onSave}
      kind="settings"
      promptKey="reference.settings"
      sectionTitle="设定提取"
      extractPath="extract_settings"
      itemsKey="settings"
      mergeIntoWork={mergeSettings}
      renderPreview={SettingsPreview}
      activeSegmentIndex={activeSegmentIndex}
      onActiveSegmentChange={onActiveSegmentChange}
    />
  );
}

function SettingsPreview({ items }: { items: SettingItem[] }) {
  if (!items || items.length === 0) {
    return <div className="text-xs text-muted">未生成任何设定。</div>;
  }
  return (
    <div style={{
      marginTop: 6, padding: 6,
      maxHeight: 320, overflowY: "auto",
      background: "var(--bg-app)", border: "1px solid var(--border)", borderRadius: 3,
    }}>
      {items.map((s, i) => (
        <div key={i} style={{ marginBottom: 8 }}>
          <div className="flex items-center" style={{ gap: 6, marginBottom: 2, flexWrap: "wrap" }}>
            <span className="tag" style={{
              fontSize: 10, padding: "0 6px",
              color: CATEGORY_COLOR[s.category] || "var(--text-tertiary)",
              border: `1px solid ${CATEGORY_COLOR[s.category] || "var(--text-tertiary)"}`,
              borderRadius: 3,
            }}>{CATEGORY_LABEL_CN[s.category] || s.category}</span>
            <span style={{ fontWeight: 700, fontSize: 12, color: "var(--text-primary)" }}>
              {s.title}
            </span>
            {s.first_chapter && (
              <span className="tag" style={{
                fontSize: 10, padding: "0 5px",
                color: "var(--jade)", border: "1px solid var(--jade)",
                borderRadius: 3,
              }}>{s.first_chapter}</span>
            )}
          </div>
          {(s.updates || []).length > 0 ? (
            <div style={{ marginLeft: 8 }}>
              {(s.updates || []).map((u, ui) => (
                <div key={ui} style={{
                  fontSize: 11, lineHeight: 1.55, marginTop: 2,
                  color: "var(--text-secondary)",
                }}>
                  {u.chapter && (
                    <span style={{
                      color: "var(--gold)", marginRight: 6,
                      fontFamily: "var(--font-mono)",
                    }}>{u.chapter}</span>
                  )}
                  {u.text}
                </div>
              ))}
            </div>
          ) : s.content ? (
            <div className="text-xs" style={{ marginLeft: 8, color: "var(--text-secondary)" }}>
              {s.content}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/** Render the rich settings list grouped by category. Used in the
 *  Settings tab below the extraction section. When `onSave` is given,
 *  each card gets inline edit/delete and updates can be added/removed. */
export function SettingsRichDisplay({ data, onSave }: {
  data: SettingItem[];
  onSave?: (next: SettingItem[]) => Promise<void> | void;
}) {
  const editable = !!onSave;
  // Map from grouped index back to global index so handlers patch the
  // right entry in the source array.
  const findGlobalIdx = (target: SettingItem) =>
    (data || []).findIndex(x => x === target);
  const updateAt = (idx: number, next: SettingItem) => {
    if (!onSave || idx < 0) return;
    const copy = (data || []).slice();
    copy[idx] = next;
    onSave(copy);
  };
  const removeAt = (idx: number) => {
    if (!onSave || idx < 0) return;
    const copy = (data || []).slice();
    copy.splice(idx, 1);
    onSave(copy);
  };
  const addBlank = () => {
    if (!onSave) return;
    const copy = (data || []).slice();
    copy.unshift({ category: "other", title: "新设定", content: "", updates: [] });
    onSave(copy);
  };

  if (!data || data.length === 0) {
    return (
      <div className="empty-state" style={{ padding: 32 }}>
        <p>暂无设定。在上方分段提取后会自动入库。</p>
        {editable && (
          <button className="btn" onClick={addBlank}
                  style={{ fontSize: 12, padding: "4px 12px", marginTop: 12 }}>
            + 手动添加一条设定
          </button>
        )}
      </div>
    );
  }
  // Group by category, preserving category order
  const order = Object.keys(CATEGORY_LABEL_CN);
  const groups = new Map<string, SettingItem[]>();
  for (const cat of order) groups.set(cat, []);
  for (const s of data) {
    const cat = order.includes(s.category) ? s.category : "other";
    if (!groups.has(cat)) groups.set(cat, []);
    groups.get(cat)!.push(s);
  }
  return (
    <div className="flex flex-col gap-12" style={{ marginTop: 8 }}>
      {editable && (
        <div className="flex items-center" style={{ gap: 8 }}>
          <button className="btn" onClick={addBlank}
                  style={{ fontSize: 11, padding: "3px 12px" }}>
            + 添加设定
          </button>
          <span className="text-xs text-muted">共 {data.length} 条设定</span>
        </div>
      )}
      {Array.from(groups.entries()).map(([cat, items]) => (
        items.length > 0 ? (
          <div key={cat}>
            <div style={{
              fontSize: 12, fontWeight: 700, marginBottom: 6,
              color: CATEGORY_COLOR[cat] || "var(--text-tertiary)",
            }}>{CATEGORY_LABEL_CN[cat] || cat}</div>
            <div className="flex flex-col gap-6">
              {items.map((s) => {
                const gi = findGlobalIdx(s);
                return (
                  <SettingCard key={gi >= 0 ? gi : s.title}
                               s={s}
                               onChange={editable ? (next) => updateAt(gi, next) : undefined}
                               onDelete={editable ? () => removeAt(gi) : undefined} />
                );
              })}
            </div>
          </div>
        ) : null
      ))}
    </div>
  );
}

function SettingCard({ s, onChange, onDelete }: {
  s: SettingItem;
  onChange?: (next: SettingItem) => void;
  onDelete?: () => void;
}) {
  const editable = !!onChange;
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const updates = s.updates || [];
  const setUpdates = (next: SettingUpdate[]) => onChange?.({ ...s, updates: next });
  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: 4,
      background: "var(--bg-card)",
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "4px 6px 4px 10px",
      }}>
        <button
          className="btn-ghost"
          onClick={() => setOpen(o => !o)}
          disabled={updates.length === 0 && !s.content && !editable}
          style={{
            display: "flex", alignItems: "center", gap: 8, flex: 1,
            padding: "2px 4px", textAlign: "left",
            justifyContent: "flex-start", borderRadius: 0, minWidth: 0,
          }}>
          <span style={{
            transition: "transform 0.15s",
            transform: open ? "rotate(90deg)" : "none",
            display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
          }}>▶</span>
          <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary)" }}>
            {s.title || "(未命名)"}
          </span>
          <span className="text-xs text-muted">
            {updates.length > 0 ? `${updates.length} 条记录` : (s.content ? "1 条简介" : "(空)")}
          </span>
          {s.first_chapter && (
            <span className="tag" style={{
              fontSize: 10, padding: "0 6px",
              color: "var(--jade)", border: "1px solid var(--jade)",
              borderRadius: 3,
            }}>{s.first_chapter}</span>
          )}
        </button>
        {editable && (
          <>
            <button className="btn-ghost"
                    onClick={() => { setEditing(e => !e); setOpen(true); }}
                    title={editing ? "完成编辑" : "编辑标题/类别/首章"}
                    style={{ fontSize: 11, padding: "2px 8px", color: "var(--text-tertiary)" }}>
              {editing ? "完成" : "编辑"}
            </button>
            <button className="btn-ghost"
                    onClick={() => {
                      if (confirm(`确认删除设定「${s.title}」？此操作不可撤销。`)) onDelete?.();
                    }}
                    title="删除设定"
                    style={{ fontSize: 14, padding: "2px 8px", color: "var(--error)" }}>×</button>
          </>
        )}
      </div>
      {open && (
        <div style={{ padding: "6px 10px 10px", borderTop: "1px dashed var(--border)" }}>
          {editing && editable && (
            <div className="flex flex-col gap-6" style={{ marginBottom: 8 }}>
              <div className="flex items-center" style={{ gap: 6 }}>
                <span className="text-xs text-muted" style={{ minWidth: 50 }}>标题</span>
                <input className="input" value={s.title}
                       onChange={e => onChange?.({ ...s, title: e.target.value })}
                       style={{ fontSize: 12, padding: "3px 8px", flex: 1 }} />
              </div>
              <div className="flex items-center" style={{ gap: 6 }}>
                <span className="text-xs text-muted" style={{ minWidth: 50 }}>类别</span>
                <select className="select" value={s.category || "other"}
                        onChange={e => onChange?.({ ...s, category: e.target.value })}
                        style={{ fontSize: 12, padding: "3px 8px", flex: 1 }}>
                  {Object.entries(CATEGORY_LABEL_CN).map(([k, v]) =>
                    <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
              <div className="flex items-center" style={{ gap: 6 }}>
                <span className="text-xs text-muted" style={{ minWidth: 50 }}>首章</span>
                <input className="input" value={s.first_chapter || ""}
                       placeholder="如：第 4 章"
                       onChange={e => onChange?.({ ...s, first_chapter: e.target.value })}
                       style={{ fontSize: 12, padding: "3px 8px", flex: 1 }} />
              </div>
            </div>
          )}
          {updates.length > 0 || editable ? (
            <div className="flex flex-col gap-4">
              {updates.map((u, i) =>
                editable ? (
                  <div key={i} className="flex" style={{ gap: 4, alignItems: "flex-start" }}>
                    <input className="input"
                           value={u.chapter || ""}
                           placeholder="第 N 章"
                           onChange={e => {
                             const copy = updates.slice();
                             copy[i] = { ...copy[i], chapter: e.target.value };
                             setUpdates(copy);
                           }}
                           style={{ fontSize: 10, padding: "2px 6px", width: 90,
                                    fontFamily: "var(--font-mono)" }} />
                    <textarea className="input"
                              value={u.text || ""}
                              placeholder="内容…"
                              rows={1}
                              onChange={e => {
                                const copy = updates.slice();
                                copy[i] = { ...copy[i], text: e.target.value };
                                setUpdates(copy);
                              }}
                              style={{ fontSize: 12, padding: "2px 6px", flex: 1, resize: "vertical", lineHeight: 1.5 }} />
                    <button className="btn-ghost"
                            onClick={() => {
                              const copy = updates.slice();
                              copy.splice(i, 1);
                              setUpdates(copy);
                            }}
                            title="删除此条"
                            style={{ fontSize: 12, padding: "2px 6px", color: "var(--error)" }}>×</button>
                  </div>
                ) : (
                  <div key={i} style={{ fontSize: 12, lineHeight: 1.6 }}>
                    {u.chapter && (
                      <span style={{
                        color: "var(--gold)", marginRight: 6,
                        fontFamily: "var(--font-mono)", fontWeight: 600,
                      }}>{u.chapter}</span>
                    )}
                    <span style={{ color: "var(--text-secondary)" }}>{u.text}</span>
                  </div>
                )
              )}
              {editable && (
                <button className="btn-ghost"
                        onClick={() => setUpdates([...updates, { chapter: "", text: "" }])}
                        style={{
                          fontSize: 11, padding: "3px 8px",
                          alignSelf: "flex-start", color: "var(--accent)",
                        }}>
                  + 添加一条更新
                </button>
              )}
            </div>
          ) : (
            <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.55 }}>
              {s.content}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Render the rich character list — sorted by importance/frequency,
 *  expandable. Each character card → 3 sub-sections (外貌/性格/经历),
 *  each sub-section in turn expandable to its chapter-tagged items.
 *
 *  经历 is intentionally NOT read from the character's own
 *  `experiences` field. Instead we walk the chronicle (passed in via
 *  `chronicle`) and pull every event whose `subject` matches this
 *  character's name. That keeps the 经历 sub-tab synced with the
 *  chronicle: editing/deleting an event there flows into here. */
export function CharactersRichDisplay({
  data, chronicle, onSave,
}: {
  data: CharacterItem[];
  /** Optional plot outline; when supplied, the 经历 sub-section is
   *  derived from chronicle events. When omitted, falls back to the
   *  character's own experiences field (legacy data). */
  chronicle?: { epochs?: ChronicleEpoch[] } | null;
  /** When provided, enables inline CRUD: edit/delete buttons on each
   *  card, add/delete on appearance/personality sub-sections, and a
   *  「+ 添加角色」 button at the top of the list. */
  onSave?: (next: CharacterItem[]) => Promise<void> | void;
}) {
  // Pre-index chronicle events by subject so each card render is O(1).
  const eventsBySubject = React.useMemo(() => {
    const idx = new Map<string, CharacterListItem[]>();
    if (!chronicle?.epochs) return idx;
    for (const ep of chronicle.epochs) {
      for (const per of (ep.periods || [])) {
        for (const ev of (per.events || [])) {
          const subject = ((ev as any).subject || "").trim();
          if (!subject) continue;
          const text = ((ev as any).description || (ev as any).name || "").trim();
          if (!text) continue;
          const chapter = ((ev as any).first_chapter || per.time || "").trim();
          const list = idx.get(subject) || [];
          list.push({ chapter, text });
          idx.set(subject, list);
        }
      }
    }
    return idx;
  }, [chronicle]);

  const editable = !!onSave;
  const updateAt = (idx: number, next: CharacterItem) => {
    if (!onSave) return;
    const copy = (data || []).slice();
    copy[idx] = next;
    onSave(copy);
  };
  const removeAt = (idx: number) => {
    if (!onSave) return;
    const copy = (data || []).slice();
    copy.splice(idx, 1);
    onSave(copy);
  };
  const addBlank = () => {
    if (!onSave) return;
    const copy = (data || []).slice();
    copy.unshift({ name: "新角色", role_tag: "其他",
                   appearance: [], personality: [], experiences: [] });
    onSave(copy);
  };

  if (!data || data.length === 0) {
    return (
      <div className="empty-state" style={{ padding: 32 }}>
        <p>暂无角色。在上方分段提取后会自动入库。</p>
        {editable && (
          <button className="btn" onClick={addBlank}
                  style={{ fontSize: 12, padding: "4px 12px", marginTop: 12 }}>
            + 手动添加一个角色
          </button>
        )}
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-6" style={{ marginTop: 8 }}>
      {editable && (
        <div className="flex items-center" style={{ gap: 8 }}>
          <button className="btn" onClick={addBlank}
                  style={{ fontSize: 11, padding: "3px 12px" }}>
            + 添加角色
          </button>
          <span className="text-xs text-muted">共 {data.length} 个角色</span>
        </div>
      )}
      {data.map((c, i) => (
        <CharacterCard key={i} c={c}
                       chronicleExperiences={eventsBySubject.get(c.name) || null}
                       onChange={editable ? (next) => updateAt(i, next) : undefined}
                       onDelete={editable ? () => removeAt(i) : undefined} />
      ))}
    </div>
  );
}

function CharacterCard({
  c, chronicleExperiences, onChange, onDelete,
}: {
  c: CharacterItem;
  chronicleExperiences: CharacterListItem[] | null;
  onChange?: (next: CharacterItem) => void;
  onDelete?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const editable = !!onChange;
  const tag = c.role_tag || "";
  // 经历 prefers chronicle-derived events; falls back to legacy field.
  const experiences = chronicleExperiences && chronicleExperiences.length > 0
    ? chronicleExperiences
    : (c.experiences || []);
  const subSections: Array<{ key: "appearance" | "personality" | "experiences"; label: string; items: CharacterListItem[]; readOnly?: boolean }> = [
    { key: "appearance",  label: "外貌", items: c.appearance  || [] },
    { key: "personality", label: "性格", items: c.personality || [] },
    // 经历 is derived from chronicle when available — read-only here.
    { key: "experiences", label: "经历", items: experiences, readOnly: !!chronicleExperiences && chronicleExperiences.length > 0 },
  ];
  const totalItems = subSections.reduce((n, s) => n + s.items.length, 0);
  const hasAnything = totalItems > 0 || !!c.intro;

  const updateSubList = (key: "appearance" | "personality", next: CharacterListItem[]) => {
    if (!onChange) return;
    onChange({ ...c, [key]: next });
  };

  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: 4,
      background: "var(--bg-card)",
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "4px 6px 4px 10px",
      }}>
        <button
          className="btn-ghost"
          onClick={() => setOpen(o => !o)}
          disabled={!hasAnything && !editable}
          style={{
            display: "flex", alignItems: "center", gap: 8, flex: 1,
            padding: "2px 4px", textAlign: "left",
            justifyContent: "flex-start", borderRadius: 0, minWidth: 0,
          }}>
          <span style={{
            transition: "transform 0.15s",
            transform: open ? "rotate(90deg)" : "none",
            display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
          }}>▶</span>
          <span style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary)" }}>
            {c.name || "(未命名)"}
          </span>
          {tag && (
            <span className="tag" style={{
              fontSize: 10, padding: "0 6px",
              color: ROLE_COLORS[tag] || "var(--text-tertiary)",
              border: `1px solid ${ROLE_COLORS[tag] || "var(--text-tertiary)"}`,
              borderRadius: 3,
            }}>{tag}</span>
          )}
          {c.mentions ? (
            <span className="text-xs text-muted">{c.mentions} 次提及</span>
          ) : null}
          {c.first_chapter && (
            <span className="tag" style={{
              fontSize: 10, padding: "0 5px",
              color: "var(--jade)", border: "1px solid var(--jade)",
              borderRadius: 3,
            }}>{c.first_chapter}</span>
          )}
        </button>
        {editable && (
          <>
            <button className="btn-ghost"
                    onClick={() => { setEditing(e => !e); setOpen(true); }}
                    title={editing ? "完成编辑" : "编辑名称/角色/简介"}
                    style={{ fontSize: 11, padding: "2px 8px", color: "var(--text-tertiary)" }}>
              {editing ? "完成" : "编辑"}
            </button>
            <button className="btn-ghost"
                    onClick={() => {
                      if (confirm(`确认删除角色「${c.name}」？此操作不可撤销。`)) onDelete?.();
                    }}
                    title="删除角色"
                    style={{ fontSize: 14, padding: "2px 8px", color: "var(--error)" }}>×</button>
          </>
        )}
      </div>
      {open && (
        <div style={{ padding: "4px 10px 10px", borderTop: "1px dashed var(--border)" }}>
          {editing && editable ? (
            <div className="flex flex-col gap-6" style={{ marginBottom: 8 }}>
              <div className="flex items-center" style={{ gap: 6 }}>
                <span className="text-xs text-muted" style={{ minWidth: 50 }}>名称</span>
                <input className="input" value={c.name}
                       onChange={e => onChange?.({ ...c, name: e.target.value })}
                       style={{ fontSize: 12, padding: "3px 8px", flex: 1 }} />
              </div>
              <div className="flex items-center" style={{ gap: 6 }}>
                <span className="text-xs text-muted" style={{ minWidth: 50 }}>角色</span>
                <select className="select" value={c.role_tag || ""}
                        onChange={e => onChange?.({ ...c, role_tag: (e.target.value || undefined) as CharacterItem["role_tag"] })}
                        style={{ fontSize: 12, padding: "3px 8px", flex: 1 }}>
                  {["主角", "女主角", "反派", "男配", "女配", "师长", "重要配角", "路人", "其他"].map(o =>
                    <option key={o} value={o}>{o}</option>)}
                </select>
              </div>
              <div className="flex items-center" style={{ gap: 6 }}>
                <span className="text-xs text-muted" style={{ minWidth: 50 }}>首章</span>
                <input className="input" value={c.first_chapter || ""}
                       placeholder="如：第 4 章"
                       onChange={e => onChange?.({ ...c, first_chapter: e.target.value })}
                       style={{ fontSize: 12, padding: "3px 8px", flex: 1 }} />
              </div>
              <div className="flex" style={{ gap: 6, alignItems: "flex-start" }}>
                <span className="text-xs text-muted" style={{ minWidth: 50, paddingTop: 4 }}>简介</span>
                <textarea className="input" rows={2} value={c.intro || ""}
                          onChange={e => onChange?.({ ...c, intro: e.target.value })}
                          style={{ fontSize: 12, padding: "3px 8px", flex: 1, resize: "vertical" }} />
              </div>
            </div>
          ) : (
            c.intro && (
              <div style={{ fontSize: 12, marginBottom: 6, color: "var(--text-secondary)", lineHeight: 1.55 }}>
                {c.intro}
              </div>
            )
          )}
          <div className="flex flex-col gap-4">
            {subSections.map(s => (
              <CharacterSubSection key={s.key} label={s.label} items={s.items}
                                   onChange={editable && !s.readOnly
                                     ? (next) => updateSubList(s.key as "appearance" | "personality", next)
                                     : undefined} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Collapsible per-sub-type list (外貌 / 性格 / 经历). Each row is
 *  `[chapter tag][content text]`. The whole card stays compact until
 *  the user expands it. Empty lists render a muted placeholder so the
 *  user can see which sub-types haven't been filled yet.
 *
 *  When `onChange` is provided, inline add/delete is enabled: each row
 *  gets a × button and the sub-section gets a "+ 添加" button. */
function CharacterSubSection({ label, items, onChange }: {
  label: string;
  items: CharacterListItem[];
  onChange?: (next: CharacterListItem[]) => void;
}) {
  const editable = !!onChange;
  const [open, setOpen] = useState(false);
  const empty = !items || items.length === 0;
  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: 3,
      background: "var(--bg-surface)",
    }}>
      <button
        className="btn-ghost w-full"
        onClick={() => setOpen(o => !o)}
        disabled={empty && !editable}
        style={{
          display: "flex", alignItems: "center", gap: 6,
          padding: "4px 8px", textAlign: "left",
          justifyContent: "flex-start", borderRadius: 0,
        }}>
        <span style={{
          transition: "transform 0.15s",
          transform: open ? "rotate(90deg)" : "none",
          display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
        }}>▶</span>
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)" }}>
          {label}
        </span>
        <span className="text-xs text-muted">
          {empty ? "(空)" : `${items.length} 条`}
        </span>
      </button>
      {open && (
        <div style={{
          padding: "4px 8px 6px", borderTop: "1px dashed var(--border)",
        }}>
          <div className="flex flex-col gap-4">
            {items.map((it, i) => (
              <CharacterSubItem key={i} item={it}
                                onChange={editable ? (next) => {
                                  const copy = items.slice();
                                  copy[i] = next;
                                  onChange?.(copy);
                                } : undefined}
                                onDelete={editable ? () => {
                                  const copy = items.slice();
                                  copy.splice(i, 1);
                                  onChange?.(copy);
                                } : undefined} />
            ))}
            {editable && (
              <button className="btn-ghost"
                      onClick={() => onChange?.([...items, { chapter: "", text: "" }])}
                      style={{
                        fontSize: 11, padding: "3px 8px",
                        alignSelf: "flex-start", color: "var(--accent)",
                      }}>
                + 添加一条
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** One row in 外貌 / 性格 sub-section. Read-only by default; when
 *  `onChange` is supplied, the chapter tag becomes an editable inline
 *  input and the text content an inline textarea, plus a × delete. */
function CharacterSubItem({ item, onChange, onDelete }: {
  item: CharacterListItem;
  onChange?: (next: CharacterListItem) => void;
  onDelete?: () => void;
}) {
  const editable = !!onChange;
  if (!editable) {
    return (
      <div style={{ fontSize: 11, lineHeight: 1.55 }}>
        {item.chapter && (
          <span style={{
            marginRight: 6, padding: "0 5px",
            fontSize: 10, color: "var(--gold)",
            fontFamily: "var(--font-mono)",
            border: "1px solid var(--gold)", borderRadius: 3,
          }}>{item.chapter}</span>
        )}
        <span style={{ color: "var(--text-secondary)" }}>{item.text}</span>
      </div>
    );
  }
  return (
    <div className="flex" style={{ gap: 4, alignItems: "flex-start" }}>
      <input className="input"
             value={item.chapter || ""}
             placeholder="第 N 章"
             onChange={e => onChange?.({ ...item, chapter: e.target.value })}
             style={{ fontSize: 10, padding: "2px 6px", width: 90,
                      fontFamily: "var(--font-mono)" }} />
      <textarea className="input"
                value={item.text || ""}
                placeholder="内容…"
                rows={1}
                onChange={e => onChange?.({ ...item, text: e.target.value })}
                style={{ fontSize: 11, padding: "2px 6px", flex: 1, resize: "vertical", lineHeight: 1.4 }} />
      <button className="btn-ghost" onClick={onDelete}
              title="删除此条"
              style={{ fontSize: 12, padding: "2px 6px", color: "var(--error)" }}>×</button>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
 * Generic extraction section — owns the volume → chunk UI and the
 * AI / paste / commit flow. Specialised for an item type T via the
 * callbacks above.
 * ═══════════════════════════════════════════════════════════════ */
interface ExtractionSectionProps<T> {
  refId: string;
  hasFullText: boolean;
  data: T[];
  onSave: (next: T[]) => Promise<void> | void;
  kind: "characters" | "settings";
  promptKey: "reference.characters" | "reference.settings";
  sectionTitle: string;
  extractPath: "extract_characters" | "extract_settings";
  itemsKey: "characters" | "settings";
  mergeIntoWork: (existing: T[], incoming: T[]) => T[];
  renderPreview: React.FC<{ items: T[] }>;
  activeSegmentIndex?: number;
  onActiveSegmentChange?: (idx: number) => void;
}

function ExtractionSection<T>(props: ExtractionSectionProps<T>) {
  const {
    refId, hasFullText, data, onSave, kind, promptKey, sectionTitle,
    extractPath, itemsKey, mergeIntoWork, renderPreview: RenderPreview,
    onActiveSegmentChange,
  } = props;
  const { toast } = useToast();
  // Shared segmentation cache: this is the same data the chronicle
  // tab fetched first. We don't re-segment in the Characters /
  // Settings tabs; we just subscribe to whatever the Chronicle (or a
  // prior visit to these tabs) already loaded.
  const { plan, chunks: segChunks, chunkLoading: segChunksLoading, ensureChunks } =
    useSegmentation(refId, hasFullText);
  const [openSegs, setOpenSegs] = useState<Set<number>>(new Set());
  const [openChunks, setOpenChunks] = useState<Set<string>>(new Set());
  const [chunkPhases, setChunkPhases] = useState<Record<string, ChunkPhase<T>>>({});

  const toggleSeg = async (i: number) => {
    const isOpen = openSegs.has(i);
    setOpenSegs(prev => {
      const next = new Set(prev);
      if (isOpen) next.delete(i); else next.add(i);
      return next;
    });
    if (!isOpen) {
      await ensureChunks(i);
      onActiveSegmentChange?.(i);
    }
  };

  const ckKey = (s: number, c: number) => `${s}:${c}`;
  const toggleChunk = (s: number, c: number) => {
    const k = ckKey(s, c);
    setOpenChunks(prev => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });
  };
  const patch = (s: number, c: number, p: Partial<ChunkPhase<T>>) => {
    const k = ckKey(s, c);
    setChunkPhases(prev => ({ ...prev, [k]: { ...(prev[k] || { status: "idle" }), ...p } }));
  };

  const runAI = async (s: number, c: number) => {
    patch(s, c, { status: "extracting", error: undefined, items: undefined, startedAt: Date.now() });
    try {
      const r = await apiPost<{
        [k: string]: any; elapsed_s: number; errors: string[];
      }>(
        `/api/references/works/${refId}/segments/${s}/chunks/${c}/${extractPath}`,
        {},
        { timeoutMs: 600_000 },
      );
      const items = (r[itemsKey] || []) as T[];
      if ((r.errors && r.errors.length > 0) || items.length === 0) {
        patch(s, c, {
          status: "failed",
          error: (r.errors && r.errors.join("; ")) || `AI 返回 0 ${kind === "characters" ? "角色" : "设定"}`,
          elapsedS: r.elapsed_s,
        });
      } else {
        patch(s, c, { status: "ready", source: "ai", items, elapsedS: r.elapsed_s });
      }
    } catch (e: any) {
      patch(s, c, { status: "failed", error: e?.message || "AI 提取失败" });
    }
  };

  const parsePaste = (s: number, c: number, raw: string) => {
    const { items, error } = leniencyParse<T>(raw, itemsKey);
    if (error || items.length === 0) {
      patch(s, c, { pasteError: error || "无内容" });
      return;
    }
    patch(s, c, { status: "ready", source: "paste", items, pasteError: undefined, pasteRaw: undefined });
  };

  const commit = async (s: number, c: number) => {
    const k = ckKey(s, c);
    const phase = chunkPhases[k];
    if (!phase || phase.status !== "ready" || !phase.items?.length) return;
    patch(s, c, { status: "committing" });
    try {
      const merged = mergeIntoWork(data || [], phase.items);
      await onSave(merged);
      patch(s, c, { status: "committed" });
      const label = kind === "characters" ? "角色" : "设定";
      toast(`第 ${s + 1} 卷 · 分段 ${c + 1} 已入库（${phase.items.length} ${label}）`, "success");
    } catch (e: any) {
      patch(s, c, { status: "ready", error: e?.message || "入库失败" });
      toast(e?.message || "入库失败", "error");
    }
  };

  if (!hasFullText) {
    return (
      <div className="text-xs text-muted" style={{ padding: 12 }}>
        上传正文后才能分段提取{kind === "characters" ? "角色" : "设定"}。
      </div>
    );
  }
  if (!plan) {
    return <div className="text-xs text-muted" style={{ padding: 12 }}>加载分段计划中…</div>;
  }
  if (plan.segments.length === 0) {
    return (
      <div className="text-xs text-muted" style={{ padding: 12 }}>
        尚未划分卷。请到「预处理」tab 创建分卷后再来这里提取。
      </div>
    );
  }

  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
      padding: 12, background: "var(--bg-surface)",
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>
        {sectionTitle}
      </div>
      <div className="flex flex-col gap-4">
        {plan.segments.map(seg => {
          const isOpen = openSegs.has(seg.index);
          const chunks = segChunks[seg.index] || [];
          const loading = segChunksLoading.has(seg.index);
          return (
            <div key={seg.index}>
              <button
                className="btn-ghost w-full"
                onClick={() => toggleSeg(seg.index)}
                style={{
                  display: "flex", alignItems: "center", gap: 8,
                  padding: "6px 10px",
                  border: "1px solid var(--border)", borderRadius: 4,
                  background: "transparent",
                  justifyContent: "flex-start", textAlign: "left",
                }}>
                <span style={{
                  transition: "transform 0.15s",
                  transform: isOpen ? "rotate(90deg)" : "none",
                  display: "inline-block", color: "var(--text-tertiary)",
                }}>▶</span>
                <span className="tag" style={{
                  fontSize: 10, minWidth: 36, textAlign: "center",
                  color: "var(--text-secondary)",
                  border: "1px solid var(--border)",
                }}>{`#${seg.index + 1}`}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="truncate" style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)" }}>
                    {seg.title}
                  </div>
                  <div className="text-xs text-muted">
                    第 {seg.start_chapter}–{seg.end_chapter} 章 · {fmtChars(seg.char_count)}
                  </div>
                </div>
              </button>

              {isOpen && (
                <div style={{
                  marginTop: 6, marginLeft: 18, paddingLeft: 10,
                  borderLeft: "2px solid var(--border)",
                }}>
                  {loading && <div className="text-xs text-muted" style={{ padding: 6 }}>正在划分分段…</div>}
                  {!loading && chunks.length === 0 && (
                    <div className="text-xs text-muted" style={{ padding: 6 }}>本卷为空。</div>
                  )}
                  {!loading && chunks.map(ck => {
                    const k = ckKey(seg.index, ck.chunk_index);
                    const phase = chunkPhases[k] || { status: "idle" as Phase };
                    const isCkOpen = openChunks.has(k);
                    // Persistent marker: count items in the global data
                    // whose chapter tag falls inside this chunk's range,
                    // so the "已提取 · N" tag survives page reloads even
                    // though chunkPhases is in-memory only.
                    const priorCount = countPriorItemsInChunk(
                      data as any[], ck.start_chapter, ck.end_chapter,
                    );
                    return (
                      <ExtractionChunkRow
                        key={k}
                        refId={refId}
                        promptKey={promptKey}
                        segIdx={seg.index}
                        chunk={ck}
                        phase={phase}
                        priorCount={priorCount}
                        kind={kind}
                        open={isCkOpen}
                        onToggle={() => toggleChunk(seg.index, ck.chunk_index)}
                        onRunAI={() => runAI(seg.index, ck.chunk_index)}
                        onPasteChange={(v) => patch(seg.index, ck.chunk_index, { pasteRaw: v, pasteError: undefined })}
                        onParsePaste={() => parsePaste(seg.index, ck.chunk_index, phase.pasteRaw || "")}
                        onCommit={() => commit(seg.index, ck.chunk_index)}
                        onReset={() => patch(seg.index, ck.chunk_index, {
                          status: "idle", source: undefined, items: undefined,
                          error: undefined, pasteRaw: undefined, pasteError: undefined,
                        })}
                        Preview={RenderPreview}
                      />
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ExtractionChunkRow<T>({
  refId, promptKey, segIdx, chunk, phase, priorCount, kind, open,
  onToggle, onRunAI, onPasteChange, onParsePaste, onCommit, onReset, Preview,
}: {
  refId: string;
  promptKey: "reference.characters" | "reference.settings";
  segIdx: number;
  chunk: ChunkMeta;
  phase: ChunkPhase<T>;
  /** Number of persisted items whose first_chapter falls inside this
   *  chunk's range. Used to show "已提取 · N 条" when chunkPhases lost
   *  its committed state (e.g. after a page reload). */
  priorCount: number;
  kind: "characters" | "settings";
  open: boolean;
  onToggle: () => void;
  onRunAI: () => void;
  onPasteChange: (v: string) => void;
  onParsePaste: () => void;
  onCommit: () => void;
  onReset: () => void;
  Preview: React.FC<{ items: T[] }>;
}) {
  const [showPaste, setShowPaste] = useState(false);
  const ready = phase.status === "ready";
  const failed = phase.status === "failed";
  const extracting = phase.status === "extracting";
  const committing = phase.status === "committing";
  const committed = phase.status === "committed";
  const itemCount = (phase.items || []).length;
  // "Persisted" = this chunk already has data in the work-level list
  // even though chunkPhases status is still idle (typical on page reload).
  const hasPriorData = !committed && priorCount > 0 && phase.status === "idle";
  const label = kind === "characters" ? "角色" : "设定";

  return (
    <div style={{
      marginTop: 6, marginBottom: 6,
      border: `1px solid ${committed || hasPriorData ? "var(--jade)" : ready ? "var(--accent)" : failed ? "var(--error)" : "var(--border)"}`,
      borderRadius: 4,
      background: committed || hasPriorData ? "rgba(52,168,83,0.04)" : "var(--bg-card)",
    }}>
      <button
        className="btn-ghost w-full"
        onClick={onToggle}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "5px 10px", textAlign: "left",
          justifyContent: "flex-start", borderRadius: 0,
        }}>
        <span style={{
          transition: "transform 0.15s",
          transform: open ? "rotate(90deg)" : "none",
          display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
        }}>▶</span>
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)" }}>
          分段 {chunk.chunk_index + 1}/{chunk.total_chunks}
        </span>
        <span className="text-xs text-muted">
          第 {chunk.start_chapter}–{chunk.end_chapter} 章 · {chunk.n_chars.toLocaleString()} 字
        </span>
        <div style={{ flex: 1 }} />
        {committed && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--jade)", border: "1px solid var(--jade)",
          }}>已入库 · {itemCount}</span>
        )}
        {hasPriorData && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--jade)", border: "1px solid var(--jade)",
          }} title={`已有 ${priorCount} 条${label}的首章落在本段范围内`}>
            已提取 · {priorCount}
          </span>
        )}
        {ready && !committed && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--accent)", border: "1px solid var(--accent)",
          }}>{phase.source === "ai" ? "AI 已生成" : "已解析"} · {itemCount}</span>
        )}
        {failed && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--error)", border: "1px solid var(--error)",
          }}>AI 失败</span>
        )}
        {extracting && phase.startedAt && (
          <ChunkTimer startedAt={phase.startedAt} totalChars={chunk.n_chars} />
        )}
      </button>

      {open && (
        <div style={{ padding: "8px 10px", borderTop: "1px dashed var(--border)" }}>
          {!committed && (
            <PromptCopyPanel
              refId={refId}
              promptKey={promptKey}
              segmentIndex={segIdx}
              chunkIndex={chunk.chunk_index}
              defaultOpen
              label={`分段 ${chunk.chunk_index + 1} 的 prompt（含本段 ${chunk.n_chapters} 章正文）`}
            />
          )}

          {(phase.status === "idle" || failed) && !committed && (
            <div className="flex items-center gap-6" style={{ flexWrap: "wrap", marginBottom: 6 }}>
              <button className="btn-primary"
                      onClick={onRunAI}
                      disabled={extracting}
                      style={{ fontSize: 11, padding: "3px 12px" }}>
                {extracting ? "AI 提取中…" : "用内置 AI 提取本段"}
              </button>
              <button className="btn"
                      onClick={() => setShowPaste(p => !p)}
                      style={{ fontSize: 11, padding: "3px 10px" }}>
                {showPaste ? "收起" : "解析网页 LLM 回复"}
              </button>
            </div>
          )}

          {failed && phase.error && (
            <div style={{
              padding: "6px 10px", marginBottom: 6,
              background: "var(--bg-surface)", border: "1px solid var(--error)",
              borderRadius: 3, fontSize: 11, color: "var(--error)", lineHeight: 1.55,
            }}>
              <div style={{ fontWeight: 600, marginBottom: 2 }}>内置 AI 提取失败</div>
              <div style={{ wordBreak: "break-word" }}>{phase.error}</div>
            </div>
          )}

          {showPaste && !ready && !committed && (
            <div style={{ marginBottom: 8 }}>
              <textarea className="input font-mono"
                        rows={5}
                        value={phase.pasteRaw || ""}
                        onChange={e => onPasteChange(e.target.value)}
                        style={{
                          fontSize: 11, lineHeight: 1.5, resize: "vertical",
                          background: "var(--bg-app)", marginBottom: 6,
                        }} />
              <div className="flex items-center gap-6">
                <button className="btn-primary"
                        onClick={onParsePaste}
                        disabled={!(phase.pasteRaw && phase.pasteRaw.trim())}
                        style={{ fontSize: 11, padding: "3px 10px" }}>
                  解析并预览
                </button>
                {phase.pasteError && (
                  <pre className="text-xs" style={{
                    color: "var(--error)", whiteSpace: "pre-wrap",
                    wordBreak: "break-word", margin: "4px 0 0",
                    flexBasis: "100%", lineHeight: 1.55,
                  }}>{phase.pasteError}</pre>
                )}
              </div>
            </div>
          )}

          {ready && !committed && phase.items && <Preview items={phase.items} />}

          {ready && !committed && (
            <div className="flex items-center gap-6" style={{
              justifyContent: "flex-end", marginTop: 8,
              paddingTop: 8, borderTop: "1px dashed var(--border)",
            }}>
              <button className="btn" onClick={onReset} disabled={committing}
                      style={{ fontSize: 11, padding: "3px 10px" }}>重置</button>
              <button className="btn-primary" onClick={onCommit}
                      disabled={committing || itemCount === 0}
                      style={{ fontSize: 11, padding: "3px 14px" }}>
                {committing ? "入库中…" : `确认入库（${itemCount}）`}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ChunkTimer({ startedAt, totalChars }: { startedAt: number; totalChars?: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, []);
  const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000));
  const eta = totalChars && totalChars > 0
    ? Math.max(elapsed, Math.round(totalChars / 60))
    : null;
  return (
    <span className="text-xs" style={{ color: "var(--gold)", fontFamily: "var(--font-mono)" }}>
      提取中… 已用 {elapsed}s
      {eta != null && elapsed < eta && (
        <span style={{ color: "var(--text-tertiary)" }}>{" / 估计 "}{eta}s</span>
      )}
    </span>
  );
}
