/* Rich display + inline CRUD for the Characters and Settings tabs.
 *
 * Extraction itself happens once in the unified「特征提取」tab; these
 * components are the integrated browsing/editing view for the merged
 * result (CharacterItem with appearance/personality/experiences lists,
 * SettingItem with a per-chapter updates list).
 */
import React, { useState } from "react";
import { EditIconButton } from "./AnalysisEditors";
import { useDialog } from "../shared/Dialog";
import type {
  CharacterItem, CharacterListItem,
  SettingItem, SettingUpdate,
  ChronicleEpoch,
} from "./AnalysisEditors";
import { groupByChunk, chunkLabel } from "./referenceMerge";
import type { ChunkLoc } from "./referenceMerge";

/** 分段视图 / 全书视图 toggle shared by the characters & settings tabs. */
function ViewToggle({ mode, onChange, segmentLabel, bookLabel }: {
  mode: "segment" | "book";
  onChange: (m: "segment" | "book") => void;
  segmentLabel: string;
  bookLabel: string;
}) {
  return (
    <div className="flex items-center" style={{ gap: 6 }}>
      <span className="text-xs text-muted">视图：</span>
      {([
        { key: "segment" as const, label: segmentLabel },
        { key: "book" as const, label: bookLabel },
      ]).map(opt => (
        <button key={opt.key} className="btn-ghost"
                onClick={() => onChange(opt.key)}
                style={{
                  padding: "3px 10px", fontSize: 11,
                  fontWeight: mode === opt.key ? 600 : 400,
                  color: mode === opt.key ? "var(--accent)" : "var(--text-secondary)",
                  background: mode === opt.key ? "var(--accent-subtle)" : "transparent",
                  border: "1px solid var(--border)", borderRadius: 3,
                }}>
          {opt.label}
        </button>
      ))}
    </div>
  );
}

/** Collapsible group — used for the 设定 category sections (力量体系 /
 *  势力 …) so a long settings list can be folded down. */
function CollapsibleGroup({ title, color, count, children, defaultOpen = true }: {
  title: string; color?: string; count: number;
  children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button className="btn-ghost w-full" onClick={() => setOpen(o => !o)}
              style={{
                display: "flex", alignItems: "center", gap: 6,
                padding: "4px 6px", justifyContent: "flex-start", borderRadius: 0,
              }}>
        <span style={{
          transition: "transform 0.15s", transform: open ? "rotate(90deg)" : "none",
          display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
        }}>▶</span>
        <span style={{ fontSize: 12, fontWeight: 700, color: color || "var(--text-primary)" }}>
          {title}
        </span>
        <span className="text-xs text-muted">{count}</span>
      </button>
      {open && (
        <div className="flex flex-col gap-6" style={{ marginTop: 4 }}>
          {children}
        </div>
      )}
    </div>
  );
}

/** Header row for one segment bucket in 分段视图. */
function SegmentGroupHeader({ title, count }: { title: string; count: number }) {
  return (
    <div className="flex items-center" style={{
      gap: 8, padding: "4px 0 4px 8px", marginTop: 4,
      borderLeft: "3px solid var(--accent)",
    }}>
      <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>{title}</span>
      <span className="text-xs text-muted">{count}</span>
    </div>
  );
}

const ROLE_COLORS: Record<string, string> = {
  "主角": "var(--accent)", "女主角": "#f472b6", "反派": "var(--error)",
  "男配": "var(--jade)", "女配": "var(--jade)", "师长": "var(--purple)",
  "重要配角": "var(--gold)", "路人": "var(--text-tertiary)",
  "其他": "var(--text-tertiary)", "": "var(--text-tertiary)",
};

const CATEGORY_LABEL_CN: Record<string, string> = {
  power_system: "力量体系",
  factions:     "势力组织",
  geography:    "地理",
  social_rules: "社会规则",
  history:      "历史背景",
  worldview:    "世界观",
  other:        "其他",
};
const CATEGORY_COLOR: Record<string, string> = {
  power_system: "var(--accent)",
  factions:     "var(--purple)",
  geography:    "var(--jade)",
  social_rules: "var(--indigo)",
  history:      "var(--gold)",
  worldview:    "var(--cyan)",
  other:        "var(--text-tertiary)",
};

/** Render the rich settings list. 全书视图 groups by category; 分段视图
 *  groups by the volume each setting's first_chapter falls into. When
 *  `onSave` is given, each card gets inline edit/delete. */
export function SettingsRichDisplay({ data: rawData, onSave, chunkList }: {
  data: SettingItem[];
  onSave?: (next: SettingItem[]) => Promise<void> | void;
  chunkList?: ChunkLoc[];
}) {
  const [viewMode, setViewMode] = useState<"segment" | "book">("book");
  const editable = !!onSave;
  // 世界观规则 (hard_rules) was merged into 世界观 (worldview); fold any
  // legacy category so old data groups under 世界观 and the next save
  // migrates it on disk.
  const data = (rawData || []).map(
    s => s.category === "hard_rules" ? { ...s, category: "worldview" } : s,
  );
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
  const hasSegments = !!chunkList && chunkList.length > 0;
  const mode: "segment" | "book" = hasSegments ? viewMode : "book";

  const segNoOf = (firstChapter?: string): number | null => {
    if (!chunkList || chunkList.length === 0) return null;
    const m = (firstChapter || "").match(/(\d+)/);
    if (!m) return null;
    const ch = parseInt(m[1], 10);
    const hit = chunkList.find(c => ch >= c.startChapter && ch <= c.endChapter);
    return hit ? hit.globalIndex : null;
  };

  const renderCard = (s: SettingItem) => {
    const gi = findGlobalIdx(s);
    return (
      <SettingCard key={gi >= 0 ? gi : s.title}
                   s={s}
                   segNo={segNoOf(s.first_chapter)}
                   onChange={editable ? (next) => updateAt(gi, next) : undefined}
                   onDelete={editable ? () => removeAt(gi) : undefined} />
    );
  };

  return (
    <div className="flex flex-col gap-12" style={{ marginTop: 8 }}>
      <div className="flex items-center justify-between" style={{ gap: 8, flexWrap: "wrap" }}>
        <div className="flex items-center" style={{ gap: 8 }}>
          {editable && (
            <button className="btn" onClick={addBlank}
                    style={{ fontSize: 11, padding: "3px 12px" }}>
              + 添加设定
            </button>
          )}
          <span className="text-xs text-muted">共 {data.length} 条设定</span>
        </div>
        {hasSegments && (
          <ViewToggle mode={mode} onChange={setViewMode}
                      segmentLabel="分段视图" bookLabel="全书（按类别）" />
        )}
      </div>
      {mode === "book" ? (
        Array.from(groups.entries()).map(([cat, items]) => (
          items.length > 0 ? (
            <CollapsibleGroup key={cat}
                              title={CATEGORY_LABEL_CN[cat] || cat}
                              color={CATEGORY_COLOR[cat] || "var(--text-tertiary)"}
                              count={items.length}>
              {items.map(renderCard)}
            </CollapsibleGroup>
          ) : null
        ))
      ) : (
        groupByChunk(data, chunkList || [], s => s.first_chapter).map((g, gi) => (
          <CollapsibleGroup key={g.loc ? g.loc.key : `un${gi}`}
                            title={g.loc ? chunkLabel(g.loc) : "未分段（无法定位章节）"}
                            count={g.items.length}>
            {g.items.map(renderCard)}
          </CollapsibleGroup>
        ))
      )}
    </div>
  );
}

function SettingCard({ s, segNo, onChange, onDelete }: {
  s: SettingItem;
  /** Which extraction 分段 this setting first appears in. */
  segNo?: number | null;
  onChange?: (next: SettingItem) => void;
  onDelete?: () => void;
}) {
  const { confirm } = useDialog();
  const editable = !!onChange;
  const [open, setOpen] = useState(false);
  // Single per-card toggle: 「编辑」 opens edit mode for the header
  // fields AND the updates list; 「完成」 exits all of it at once.
  const [editing, setEditing] = useState(false);
  const updates = s.updates || [];
  const setUpdates = (next: SettingUpdate[]) => onChange?.({ ...s, updates: next });
  return (
    <div style={{
      border: `1px solid ${editing ? "var(--accent)" : "var(--border)"}`,
      borderRadius: 4,
      background: "var(--bg-card)",
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "4px 6px 4px 10px",
      }}>
        <button
          className="btn-ghost"
          onClick={() => { if (!editing) setOpen(o => !o); }}
          disabled={(updates.length === 0 && !s.content && !editable) || editing}
          style={{
            display: "flex", alignItems: "center", gap: 8, flex: 1,
            padding: "2px 4px", textAlign: "left",
            justifyContent: "flex-start", borderRadius: 0, minWidth: 0,
          }}>
          <span style={{
            transition: "transform 0.15s",
            transform: (open || editing) ? "rotate(90deg)" : "none",
            display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
          }}>▶</span>
          <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary)" }}>
            {s.title || "(未命名)"}
          </span>
          <span className="text-xs text-muted">
            {[s.content ? "简介" : null,
              updates.length > 0 ? `${updates.length} 条更新` : null]
              .filter(Boolean).join(" · ") || "(空)"}
          </span>
          {s.first_chapter && (
            <span className="tag" style={{
              fontSize: 10, padding: "0 6px",
              color: "var(--jade)", border: "1px solid var(--jade)",
              borderRadius: 3,
            }}>{s.first_chapter}</span>
          )}
          {segNo != null && (
            <span className="tag" style={{
              fontSize: 10, padding: "0 6px",
              color: "var(--text-tertiary)", border: "1px solid var(--border)",
              borderRadius: 3,
            }} title="该设定首次出现所在的提取分段">第 {segNo} 段</span>
          )}
        </button>
        {editable && (editing ? (
          <button className="btn-primary"
                  onClick={() => setEditing(false)}
                  style={{ fontSize: 11, padding: "3px 12px" }}>
            完成
          </button>
        ) : (
          <>
            <EditIconButton
              onClick={() => { setEditing(true); setOpen(true); }}
              title="编辑此设定的全部字段" />
            <button className="btn-ghost"
                    onClick={async () => {
                      if (await confirm({ message: `确认删除设定「${s.title}」？此操作不可撤销。`, destructive: true })) onDelete?.();
                    }}
                    title="删除设定"
                    style={{ fontSize: 14, padding: "2px 8px", color: "var(--error)" }}>×</button>
          </>
        ))}
      </div>
      {(open || editing) && (
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
          {editing ? (
            <div className="flex" style={{ gap: 6, marginBottom: 10, alignItems: "flex-start" }}>
              <span className="text-xs text-muted" style={{ minWidth: 50, paddingTop: 4 }}>简介</span>
              <textarea className="input"
                        value={s.content || ""}
                        placeholder="一句话简介这个设定是什么、有何作用"
                        rows={2}
                        onChange={e => onChange?.({ ...s, content: e.target.value })}
                        style={{ fontSize: 12, padding: "3px 8px", flex: 1, resize: "vertical", lineHeight: 1.6 }} />
            </div>
          ) : (s.content ? (
            <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6,
                          marginBottom: updates.length > 0 ? 10 : 0 }}>
              {s.content}
            </div>
          ) : null)}
          {(updates.length > 0 || editing) && (
            <div className="flex flex-col gap-4">
              <span className="text-xs text-muted" style={{ fontWeight: 600 }}>更新章节</span>
              {updates.map((u, i) =>
                editing ? (
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
              {editing && (
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
  data, chronicle, onSave, chunkList,
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
  /** Extraction chunk list — enables the 分段视图 (group characters by
   *  the 分段 their first_chapter falls into). */
  chunkList?: ChunkLoc[];
}) {
  const [viewMode, setViewMode] = useState<"segment" | "book">("book");
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
  // Display order: 主角 first, 女主角 second, then everyone else by
  // "出场次数" (appearance frequency) descending — more screen time
  // means a more important character. Frequency proxy: explicit
  // `mentions`, else the count of chronicle events for this character,
  // else the total of the rich fact lists. We keep the ORIGINAL index
  // alongside each entry so edit/delete handlers still patch the right
  // slot in the source array.
  // Which extraction 分段 a chapter falls in — shown as a「第 N 段」tag
  // on every card so the source 分段 is visible in any view mode.
  const segNoOf = (firstChapter?: string): number | null => {
    if (!chunkList || chunkList.length === 0) return null;
    const m = (firstChapter || "").match(/(\d+)/);
    if (!m) return null;
    const ch = parseInt(m[1], 10);
    const hit = chunkList.find(c => ch >= c.startChapter && ch <= c.endChapter);
    return hit ? hit.globalIndex : null;
  };
  const roleRank = (r?: string): number =>
    r === "主角" ? 0 : r === "女主角" ? 1 : 2;
  const frequency = (c: CharacterItem): number => {
    if (c.mentions && c.mentions > 0) return c.mentions;
    const chron = eventsBySubject.get(c.name);
    if (chron && chron.length > 0) return chron.length;
    return (c.appearance?.length || 0) + (c.personality?.length || 0)
         + (c.experiences?.length || 0);
  };
  const ordered = (data || [])
    .map((c, originalIndex) => ({ c, originalIndex }))
    .sort((a, b) => {
      const ra = roleRank(a.c.role_tag), rb = roleRank(b.c.role_tag);
      if (ra !== rb) return ra - rb;
      if (ra < 2) return a.originalIndex - b.originalIndex; // 主角/女主角 stable
      return frequency(b.c) - frequency(a.c);
    });
  const hasSegments = !!chunkList && chunkList.length > 0;
  const mode: "segment" | "book" = hasSegments ? viewMode : "book";

  const renderCard = ({ c, originalIndex }: { c: CharacterItem; originalIndex: number }) => (
    <CharacterCard key={originalIndex} c={c}
                   chronicleExperiences={eventsBySubject.get(c.name) || null}
                   segNo={segNoOf(c.first_chapter)}
                   onChange={editable ? (next) => updateAt(originalIndex, next) : undefined}
                   onDelete={editable ? () => removeAt(originalIndex) : undefined} />
  );

  return (
    <div className="flex flex-col gap-6" style={{ marginTop: 8 }}>
      <div className="flex items-center justify-between" style={{ gap: 8, flexWrap: "wrap" }}>
        <div className="flex items-center" style={{ gap: 8 }}>
          {editable && (
            <button className="btn" onClick={addBlank}
                    style={{ fontSize: 11, padding: "3px 12px" }}>
              + 添加角色
            </button>
          )}
          <span className="text-xs text-muted">
            共 {data.length} 个角色
            {mode === "book" && " · 主角/女主角置顶，其余按出场次数排序"}
          </span>
        </div>
        {hasSegments && (
          <ViewToggle mode={mode} onChange={setViewMode}
                      segmentLabel="分段视图" bookLabel="全书（按出场次数）" />
        )}
      </div>
      {mode === "book" ? (
        ordered.map(renderCard)
      ) : (
        groupByChunk(ordered, chunkList || [], x => x.c.first_chapter).map((g, gi) => (
          <div key={g.loc ? g.loc.key : `un${gi}`} className="flex flex-col gap-6">
            <SegmentGroupHeader
              title={g.loc ? chunkLabel(g.loc) : "未分段（无法定位章节）"}
              count={g.items.length} />
            {g.items.map(renderCard)}
          </div>
        ))
      )}
    </div>
  );
}

function CharacterCard({
  c, chronicleExperiences, segNo, onChange, onDelete,
}: {
  c: CharacterItem;
  chronicleExperiences: CharacterListItem[] | null;
  /** Which extraction 分段 this character first appears in. */
  segNo?: number | null;
  onChange?: (next: CharacterItem) => void;
  onDelete?: () => void;
}) {
  const { confirm } = useDialog();
  const [open, setOpen] = useState(false);
  // A single per-card edit toggle: 「编辑」 puts the whole card —
  // header fields AND every sub-section — into edit mode; 「完成」
  // exits all of it at once.
  const [editing, setEditing] = useState(false);
  const editable = !!onChange;
  const tag = c.role_tag || "";
  // Read-mode 经历: prefer the character's OWN experiences field (so
  // manual edits stick); fall back to chronicle-derived events only
  // when the character has no experiences of its own.
  const ownExp = c.experiences || [];
  const expDerived = ownExp.length === 0
    && !!chronicleExperiences && chronicleExperiences.length > 0;
  const readExperiences = ownExp.length > 0
    ? ownExp
    : (chronicleExperiences || []);
  // Sub-sections. In edit mode all three bind to the character's own
  // field; in read mode 经历 may show chronicle-derived events.
  const subSections: Array<{
    key: "appearance" | "personality" | "experiences";
    label: string; readItems: CharacterListItem[]; note?: string;
  }> = [
    { key: "appearance",  label: "外貌", readItems: c.appearance  || [] },
    { key: "personality", label: "性格", readItems: c.personality || [] },
    { key: "experiences", label: "经历", readItems: readExperiences,
      note: expDerived ? "（自动读取自编年史，可在此覆盖）" : undefined },
  ];
  const totalItems = subSections.reduce((n, s) => n + s.readItems.length, 0);
  const hasAnything = totalItems > 0 || !!c.intro;

  const startEdit = () => { setEditing(true); setOpen(true); };
  const finishEdit = () => setEditing(false);

  return (
    <div style={{
      border: `1px solid ${editing ? "var(--accent)" : "var(--border)"}`,
      borderRadius: 4,
      background: "var(--bg-card)",
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "4px 6px 4px 10px",
      }}>
        <button
          className="btn-ghost"
          onClick={() => { if (!editing) setOpen(o => !o); }}
          disabled={(!hasAnything && !editable) || editing}
          style={{
            display: "flex", alignItems: "center", gap: 8, flex: 1,
            padding: "2px 4px", textAlign: "left",
            justifyContent: "flex-start", borderRadius: 0, minWidth: 0,
          }}>
          <span style={{
            transition: "transform 0.15s",
            transform: (open || editing) ? "rotate(90deg)" : "none",
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
            <span className="text-xs text-muted">{c.mentions} 次出场</span>
          ) : null}
          {c.first_chapter && (
            <span className="tag" style={{
              fontSize: 10, padding: "0 5px",
              color: "var(--jade)", border: "1px solid var(--jade)",
              borderRadius: 3,
            }}>{c.first_chapter}</span>
          )}
          {segNo != null && (
            <span className="tag" style={{
              fontSize: 10, padding: "0 5px",
              color: "var(--text-tertiary)", border: "1px solid var(--border)",
              borderRadius: 3,
            }} title="该角色首次出现所在的提取分段">第 {segNo} 段</span>
          )}
        </button>
        {editable && (editing ? (
          <button className="btn-primary"
                  onClick={finishEdit}
                  style={{ fontSize: 11, padding: "3px 12px" }}>
            完成
          </button>
        ) : (
          <>
            <EditIconButton onClick={startEdit} title="编辑此角色的全部字段" />
            <button className="btn-ghost"
                    onClick={async () => {
                      if (await confirm({ message: `确认删除角色「${c.name}」？此操作不可撤销。`, destructive: true })) onDelete?.();
                    }}
                    title="删除角色"
                    style={{ fontSize: 14, padding: "2px 8px", color: "var(--error)" }}>×</button>
          </>
        ))}
      </div>
      {(open || editing) && (
        <div style={{ padding: "4px 10px 10px", borderTop: "1px dashed var(--border)" }}>
          {editing ? (
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
                <span className="text-xs text-muted" style={{ minWidth: 50 }}>出场次数</span>
                <input className="input" type="number" min={0}
                       value={c.mentions ?? 0}
                       onChange={e => onChange?.({ ...c, mentions: parseInt(e.target.value, 10) || 0 })}
                       style={{ fontSize: 12, padding: "3px 8px", width: 100 }} />
                <span className="text-xs text-muted">用于角色列表排序</span>
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
              <CharacterSubSection key={s.key} label={s.label}
                                   items={editing ? (c[s.key] || []) : s.readItems}
                                   note={s.note}
                                   editing={editing}
                                   onChange={editing
                                     ? (next) => onChange?.({ ...c, [s.key]: next })
                                     : undefined} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Per-sub-type list (外貌 / 性格 / 经历). In read mode it's a
 *  collapsible `[chapter tag][text]` list; in edit mode (`editing`)
 *  it stays expanded and every row becomes editable with ×/+ controls. */
function CharacterSubSection({ label, items, note, editing, onChange }: {
  label: string;
  items: CharacterListItem[];
  note?: string;
  editing: boolean;
  onChange?: (next: CharacterListItem[]) => void;
}) {
  const editable = editing && !!onChange;
  const [open, setOpen] = useState(false);
  const isOpen = editing || open;
  const empty = !items || items.length === 0;
  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: 3,
      background: "var(--bg-surface)",
    }}>
      <button
        className="btn-ghost w-full"
        onClick={() => { if (!editing) setOpen(o => !o); }}
        disabled={(empty && !editable) || editing}
        style={{
          display: "flex", alignItems: "center", gap: 6,
          padding: "4px 8px", textAlign: "left",
          justifyContent: "flex-start", borderRadius: 0,
        }}>
        <span style={{
          transition: "transform 0.15s",
          transform: isOpen ? "rotate(90deg)" : "none",
          display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
        }}>▶</span>
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)" }}>
          {label}
        </span>
        <span className="text-xs text-muted">
          {empty ? "(空)" : `${items.length} 条`}
        </span>
        {note && (
          <span className="text-xs text-muted" style={{ fontSize: 10 }}>{note}</span>
        )}
      </button>
      {isOpen && (
        <div style={{
          padding: "4px 8px 6px", borderTop: "1px dashed var(--border)",
        }}>
          <div className="flex flex-col gap-4">
            {empty && !editable && (
              <span className="text-xs text-muted">（暂无内容）</span>
            )}
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
