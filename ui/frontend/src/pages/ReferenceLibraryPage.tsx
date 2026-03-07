import React, { useEffect, useState, useRef, useCallback } from "react";
import { apiGet, apiPost, apiDelete } from "../api/client";

/* ─── Types ─── */
interface RefWork {
  ref_id: string; title: string; creator: string; media_type: string;
  genre: string; source: string; user_rating: number | null;
  user_why_i_like: string | null; user_summary: string | null;
  preprocessing_status: string; has_full_text: number;
  style_fingerprint_json: string | null; narrative_structure_json: string | null;
  extracted_characters_json: string | null; rhythm_template_json: string | null;
  tags_json: string | null; learning_dimensions_json: string | null;
  created_at: string; updated_at: string; [k: string]: any;
}
interface RefEntry {
  entry_id: string; ref_id: string; entry_type: string; title: string;
  content: string; position_label: string; user_notes: string;
  user_rating: number | null; content_source: string; [k: string]: any;
}

const MEDIA_TYPES = [
  { value: "web_novel", label: "🖊️ 网文", color: "var(--accent)" },
  { value: "literature", label: "📖 文学", color: "var(--success)" },
  { value: "poetry", label: "🪶 诗歌", color: "#c084fc" },
  { value: "film", label: "🎬 电影", color: "var(--warning)" },
  { value: "anime", label: "🎨 动漫", color: "#f472b6" },
  { value: "tv_series", label: "📺 电视剧", color: "var(--info)" },
  { value: "other", label: "📝 其他", color: "var(--text-tertiary)" },
];
const ENTRY_TYPES = [
  "scene", "character", "worldbuilding", "dialogue", "technique",
  "atmosphere", "plot_structure", "emotional_beat", "hook", "style_sample", "other",
];

function mediaLabel(mt: string) {
  return MEDIA_TYPES.find(m => m.value === mt)?.label || mt;
}
function mediaColor(mt: string) {
  return MEDIA_TYPES.find(m => m.value === mt)?.color || "var(--text-tertiary)";
}
function pj(s: string | null): any { if (!s) return null; try { return JSON.parse(s); } catch { return null; } }
function stars(n: number | null) { return n ? "★".repeat(n) + "☆".repeat(5 - n) : "—"; }

export default function ReferenceLibraryPage() {
  const [works, setWorks] = useState<RefWork[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [filterMedia, setFilterMedia] = useState("");
  const [sel, setSel] = useState<RefWork | null>(null);
  const [entries, setEntries] = useState<RefEntry[]>([]);
  const [loading, setLoading] = useState(false);

  // panels
  const [showAddWork, setShowAddWork] = useState(false);
  const [showAddEntry, setShowAddEntry] = useState(false);

  // add work form
  const [nTitle, setNTitle] = useState(""); const [nCreator, setNCreator] = useState("");
  const [nMedia, setNMedia] = useState("web_novel"); const [nGenre, setNGenre] = useState("");
  const [nWhy, setNWhy] = useState(""); const [nRating, setNRating] = useState(0);

  // add entry form
  const [eType, setEType] = useState("scene"); const [eTitle, setETitle] = useState("");
  const [eCont, setECont] = useState(""); const [ePos, setEPos] = useState("");
  const [eNotes, setENotes] = useState("");

  // resizable panel
  const [leftW, setLeftW] = useState(360);
  const dragging = useRef(false);
  const containerRef = useRef<HTMLDivElement>(null);

  /* ─── Data loading ─── */
  const load = useCallback(async () => {
    setLoading(true);
    const p = new URLSearchParams();
    if (search) p.set("search", search);
    if (filterMedia) p.set("media_type", filterMedia);
    try {
      const r = await apiGet<{ items: RefWork[]; total: number }>(`/api/references/works?${p}`);
      setWorks(r.items); setTotal(r.total);
    } catch (e) { console.error(e); }
    setLoading(false);
  }, [search, filterMedia]);

  useEffect(() => { load(); }, [load]);

  async function selectWork(w: RefWork) {
    setSel(w);
    try {
      const r = await apiGet<{ items: RefEntry[] }>(`/api/references/entries/${w.ref_id}`);
      setEntries(r.items);
    } catch { setEntries([]); }
  }

  async function addWork() {
    if (!nTitle.trim()) return;
    await apiPost("/api/references/works", {
      title: nTitle, creator: nCreator, media_type: nMedia, genre: nGenre,
      user_why_i_like: nWhy || undefined, user_rating: nRating || undefined,
      source: "manual",
    });
    setShowAddWork(false); setNTitle(""); setNCreator(""); setNGenre("");
    setNWhy(""); setNRating(0);
    load();
  }

  async function delWork(id: string) {
    if (!confirm("确定删除这部参考作品及其所有条目？")) return;
    await apiDelete(`/api/references/works/${id}`);
    setSel(null); setEntries([]); load();
  }

  async function runPreprocess(id: string) {
    try {
      const r = await apiPost<any>(`/api/references/preprocess/${id}`, {});
      alert(`✅ 特征提取完成 (${r.chapters || 0} 章, ${r.errors?.length || 0} 错误)`);
    } catch (e: any) {
      alert(`❌ 特征提取失败: ${e.message}`);
    }
    load();
    if (sel?.ref_id === id) {
      const w = await apiGet<RefWork>(`/api/references/works/${id}`);
      setSel(w);
    }
  }

  async function addEntry() {
    if (!sel || !eCont.trim()) return;
    await apiPost("/api/references/entries", {
      ref_id: sel.ref_id, entry_type: eType, title: eTitle,
      content: eCont, position_label: ePos, user_notes: eNotes,
    });
    setShowAddEntry(false); setETitle(""); setECont(""); setEPos(""); setENotes("");
    selectWork(sel);
  }

  async function delEntry(eid: string) {
    await apiDelete(`/api/references/entries/${eid}`);
    if (sel) selectWork(sel);
  }

  /* ─── Resize handle ─── */
  const onMouseDown = useCallback(() => {
    dragging.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current || !containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      setLeftW(Math.max(240, Math.min(560, e.clientX - rect.left)));
    };
    const onUp = () => { dragging.current = false; document.body.style.cursor = ""; document.body.style.userSelect = ""; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, []);

  const statusBadge = (s: string) => {
    const map: Record<string, { bg: string; text: string; label: string }> = {
      done: { bg: "rgba(74,222,128,0.15)", text: "var(--success)", label: "已分析" },
      pending: { bg: "rgba(251,191,36,0.15)", text: "var(--warning)", label: "待处理" },
      processing: { bg: "rgba(96,165,250,0.15)", text: "var(--info)", label: "处理中" },
      error: { bg: "rgba(248,113,113,0.15)", text: "var(--error)", label: "出错" },
      not_applicable: { bg: "rgba(96,96,120,0.1)", text: "var(--text-tertiary)", label: "手动" },
    };
    const m = map[s] || map.not_applicable;
    return <span style={{ padding: "2px 8px", borderRadius: 99, fontSize: 11, fontWeight: 500, background: m.bg, color: m.text }}>{m.label}</span>;
  };

  return (
    <div style={{ color: "var(--text-primary)", height: "100%", display: "flex", flexDirection: "column" }}>
      {/* ═══ Header ═══ */}
      <div style={{ padding: "0 0 16px", borderBottom: "1px solid var(--border)", marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>📚 参考作品库</h2>
          <p style={{ color: "var(--text-secondary)", fontSize: 13, marginTop: 4 }}>
            录入你喜欢的作品 — 网文、文学、电影、动漫均可 · 记录审美倾向 · 自动提取风格特征
          </p>
        </div>
        <button onClick={() => setShowAddWork(true)} style={S.btnAccent}>+ 添加作品</button>
      </div>

      {/* ═══ Toolbar ═══ */}
      <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap", alignItems: "center" }}>
        <div style={{ position: "relative", flex: "0 0 220px" }}>
          <input placeholder="搜索标题 / 创作者..." value={search} onChange={e => setSearch(e.target.value)}
            style={{ ...S.input, width: "100%", paddingLeft: 32 }} />
          <span style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", fontSize: 14, color: "var(--text-tertiary)", pointerEvents: "none" }}>🔍</span>
        </div>
        <select value={filterMedia} onChange={e => setFilterMedia(e.target.value)} style={S.input}>
          <option value="">全部类型</option>
          {MEDIA_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        <span style={{ fontSize: 12, color: "var(--text-tertiary)", marginLeft: "auto" }}>
          {loading ? "加载中..." : `${total} 部作品`}
        </span>
      </div>

      {/* ═══ Main two-panel layout ═══ */}
      <div ref={containerRef} style={{ flex: 1, display: "flex", minHeight: 0, overflow: "hidden" }}>
        {/* ─── Left: work list ─── */}
        <div style={{ width: leftW, flexShrink: 0, overflow: "auto", paddingRight: 6, display: "flex", flexDirection: "column", gap: 4 }}>
          {works.map(w => (
            <div key={w.ref_id} onClick={() => selectWork(w)}
              style={{
                padding: "12px 14px", borderRadius: "var(--radius-md)", cursor: "pointer",
                background: sel?.ref_id === w.ref_id ? "var(--accent-subtle)" : "var(--bg-card)",
                border: `1px solid ${sel?.ref_id === w.ref_id ? "var(--accent)" : "var(--border)"}`,
                transition: "var(--transition-fast)",
              }}
              onMouseEnter={e => { if (sel?.ref_id !== w.ref_id) (e.currentTarget.style.background = "var(--bg-surface-hover)"); }}
              onMouseLeave={e => { if (sel?.ref_id !== w.ref_id) (e.currentTarget.style.background = "var(--bg-card)"); }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <span style={{ fontWeight: 600, fontSize: 14 }}>{w.title}</span>
                {statusBadge(w.preprocessing_status)}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)", display: "flex", gap: 8, flexWrap: "wrap" }}>
                <span style={{ color: mediaColor(w.media_type) }}>{mediaLabel(w.media_type)}</span>
                {w.creator && <span>{w.creator}</span>}
                {w.genre && <span style={{ opacity: 0.7 }}>{w.genre}</span>}
                {w.user_rating && <span style={{ color: "var(--warning)" }}>{stars(w.user_rating)}</span>}
              </div>
            </div>
          ))}
          {!works.length && !loading && (
            <div style={{ textAlign: "center", color: "var(--text-tertiary)", padding: 40, fontSize: 13 }}>
              暂无参考作品<br />点击右上角「添加作品」开始
            </div>
          )}
        </div>

        {/* ─── Resize handle ─── */}
        <div onMouseDown={onMouseDown} style={{
          width: 5, cursor: "col-resize", flexShrink: 0,
          background: "transparent", position: "relative",
          transition: "var(--transition-fast)",
        }}
          onMouseEnter={e => (e.currentTarget.style.background = "var(--accent-subtle)")}
          onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
        >
          <div style={{ position: "absolute", left: 2, top: "50%", transform: "translateY(-50%)", width: 1, height: 32, background: "var(--border)", borderRadius: 1 }} />
        </div>

        {/* ─── Right: detail panel ─── */}
        <div style={{ flex: 1, overflow: "auto", paddingLeft: 10, minWidth: 0 }}>
          {sel ? (
            <div>
              {/* work header */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                    <h3 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>{sel.title}</h3>
                    <span style={{ color: mediaColor(sel.media_type), fontSize: 12, padding: "2px 8px", borderRadius: 99, background: "var(--bg-surface)" }}>
                      {mediaLabel(sel.media_type)}
                    </span>
                  </div>
                  <div style={{ fontSize: 13, color: "var(--text-secondary)", display: "flex", gap: 12 }}>
                    {sel.creator && <span>✍️ {sel.creator}</span>}
                    {sel.genre && <span>📂 {sel.genre}</span>}
                    <span>📅 {sel.created_at?.split("T")[0] || "—"}</span>
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  {sel.has_full_text === 1 && sel.preprocessing_status !== "done" && (
                    <button onClick={() => runPreprocess(sel.ref_id)} style={S.btn}>🔬 提取特征</button>
                  )}
                  <button onClick={() => delWork(sel.ref_id)} style={{ ...S.btn, color: "var(--error)" }}>删除</button>
                </div>
              </div>

              {/* user annotations */}
              {(sel.user_why_i_like || sel.user_rating) && (
                <div style={{ ...S.card, marginBottom: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6, color: "var(--accent)" }}>💡 我的审美笔记</div>
                  {sel.user_rating && <div style={{ color: "var(--warning)", marginBottom: 4, fontSize: 16 }}>{stars(sel.user_rating)}</div>}
                  {sel.user_why_i_like && <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6 }}>{sel.user_why_i_like}</div>}
                </div>
              )}

              {/* analysis results */}
              {sel.preprocessing_status === "done" && (
                <div style={{ ...S.card, marginBottom: 14 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10, color: "var(--accent)" }}>🔍 特征提取结果</div>
                  <div style={{ display: "grid", gap: 8 }}>
                    <AnalysisSection title="风格指纹" data={pj(sel.style_fingerprint_json)} />
                    <AnalysisSection title="叙事结构" data={pj(sel.narrative_structure_json)} />
                    <AnalysisSection title="提取角色" data={pj(sel.extracted_characters_json)} />
                    <AnalysisSection title="节奏模板" data={pj(sel.rhythm_template_json)} />
                  </div>
                </div>
              )}

              {/* entries */}
              <div style={{ ...S.card }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--accent)" }}>📝 参考条目 ({entries.length})</div>
                  <button onClick={() => setShowAddEntry(true)} style={S.btnSm}>+ 添加</button>
                </div>

                {/* add entry form (inline) */}
                {showAddEntry && (
                  <div style={{ padding: 12, borderRadius: "var(--radius-md)", background: "var(--bg-surface)", border: "1px solid var(--border)", marginBottom: 10 }}>
                    <div style={{ display: "flex", gap: 6, marginBottom: 8, flexWrap: "wrap" }}>
                      <select value={eType} onChange={e => setEType(e.target.value)} style={{ ...S.input, flex: "0 0 140px" }}>
                        {ENTRY_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                      <input placeholder="标题" value={eTitle} onChange={e => setETitle(e.target.value)} style={{ ...S.input, flex: 1 }} />
                      <input placeholder="位置 (第3章/S1E05)" value={ePos} onChange={e => setEPos(e.target.value)} style={{ ...S.input, flex: "0 0 140px" }} />
                    </div>
                    <textarea placeholder="内容（原文摘录或你的描述）" value={eCont} onChange={e => setECont(e.target.value)}
                      style={{ ...S.input, width: "100%", minHeight: 64, resize: "vertical", marginBottom: 6 }} />
                    <textarea placeholder="个人笔记（可选）" value={eNotes} onChange={e => setENotes(e.target.value)}
                      style={{ ...S.input, width: "100%", minHeight: 36, resize: "vertical", marginBottom: 8 }} />
                    <div style={{ display: "flex", gap: 6 }}>
                      <button onClick={addEntry} style={S.btnAccent}>保存</button>
                      <button onClick={() => setShowAddEntry(false)} style={S.btn}>取消</button>
                    </div>
                  </div>
                )}

                {/* entry list */}
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {entries.map(e => (
                    <div key={e.entry_id} style={{
                      padding: "10px 12px", borderRadius: "var(--radius-sm)",
                      background: "var(--bg-surface)", border: "1px solid var(--border)",
                      fontSize: 13, transition: "var(--transition-fast)",
                    }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <div>
                          <span style={{ padding: "1px 6px", borderRadius: 4, fontSize: 11, fontWeight: 600, background: "var(--accent-subtle)", color: "var(--accent)", marginRight: 6 }}>{e.entry_type}</span>
                          <span style={{ fontWeight: 500 }}>{e.title || "(无标题)"}</span>
                          {e.position_label && <span style={{ color: "var(--text-tertiary)", marginLeft: 6, fontSize: 11 }}>{e.position_label}</span>}
                        </div>
                        <button onClick={() => delEntry(e.entry_id)} style={{ background: "none", border: "none", color: "var(--text-tertiary)", cursor: "pointer", fontSize: 14, padding: "2px 6px", borderRadius: 4 }}
                          onMouseEnter={ev => (ev.currentTarget.style.color = "var(--error)")}
                          onMouseLeave={ev => (ev.currentTarget.style.color = "var(--text-tertiary)")}>✕</button>
                      </div>
                      {e.content && <div style={{ color: "var(--text-secondary)", marginTop: 6, lineHeight: 1.5 }}>{e.content.length > 200 ? e.content.slice(0, 200) + "..." : e.content}</div>}
                      {e.user_notes && <div style={{ color: "var(--text-tertiary)", fontSize: 11, marginTop: 4, fontStyle: "italic" }}>📌 {e.user_notes}</div>}
                    </div>
                  ))}
                  {!entries.length && <div style={{ color: "var(--text-tertiary)", fontSize: 12, padding: "16px 0", textAlign: "center" }}>暂无条目 — 点击上方「添加」记录你的感受</div>}
                </div>
              </div>
            </div>
          ) : (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-tertiary)", fontSize: 14 }}>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: 48, marginBottom: 12, opacity: 0.4 }}>📚</div>
                <div>选择一部作品查看详情</div>
                <div style={{ fontSize: 12, marginTop: 4 }}>或点击右上角添加新的参考作品</div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ═══ Add Work Modal ═══ */}
      {showAddWork && (
        <div style={{ position: "fixed", inset: 0, zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)" }}
          onClick={e => { if (e.target === e.currentTarget) setShowAddWork(false); }}>
          <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: 24, width: 480, maxHeight: "80vh", overflow: "auto", boxShadow: "var(--shadow-lg)" }}>
            <h3 style={{ margin: "0 0 16px", fontSize: 18, fontWeight: 700 }}>📚 添加参考作品</h3>
            <div style={{ display: "grid", gap: 12 }}>
              <Field label="标题 *"><input value={nTitle} onChange={e => setNTitle(e.target.value)} style={{ ...S.input, width: "100%" }} placeholder="作品名称" /></Field>
              <Field label="创作者"><input value={nCreator} onChange={e => setNCreator(e.target.value)} style={{ ...S.input, width: "100%" }} placeholder="作者 / 导演 / 制作组" /></Field>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <Field label="媒体类型">
                  <select value={nMedia} onChange={e => setNMedia(e.target.value)} style={{ ...S.input, width: "100%" }}>
                    {MEDIA_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </Field>
                <Field label="题材"><input value={nGenre} onChange={e => setNGenre(e.target.value)} style={{ ...S.input, width: "100%" }} placeholder="仙侠 / 悬疑 / 科幻..." /></Field>
              </div>
              <Field label="评分">
                <div style={{ display: "flex", gap: 4 }}>
                  {[1, 2, 3, 4, 5].map(n => (
                    <button key={n} onClick={() => setNRating(nRating === n ? 0 : n)}
                      style={{ background: "none", border: "none", fontSize: 22, cursor: "pointer", color: n <= nRating ? "var(--warning)" : "var(--text-disabled)", transition: "var(--transition-fast)" }}>★</button>
                  ))}
                </div>
              </Field>
              <Field label="为什么喜欢？">
                <textarea value={nWhy} onChange={e => setNWhy(e.target.value)} style={{ ...S.input, width: "100%", minHeight: 72, resize: "vertical" }}
                  placeholder="你喜欢这部作品的哪些方面？写作风格？世界观？角色塑造？情绪节奏？&#10;这是审美倾向的核心记录字段。" />
              </Field>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 20, justifyContent: "flex-end" }}>
              <button onClick={() => setShowAddWork(false)} style={S.btn}>取消</button>
              <button onClick={addWork} style={S.btnAccent} disabled={!nTitle.trim()}>保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Sub-components ─── */

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text-secondary)", marginBottom: 4 }}>{label}</div>
      {children}
    </div>
  );
}

function AnalysisSection({ title, data }: { title: string; data: any }) {
  const [open, setOpen] = useState(false);
  if (!data) return null;
  return (
    <div style={{ borderRadius: "var(--radius-sm)", border: "1px solid var(--border)", overflow: "hidden" }}>
      <button onClick={() => setOpen(!open)} style={{
        width: "100%", display: "flex", justifyContent: "space-between", alignItems: "center",
        padding: "8px 12px", background: "var(--bg-surface)", border: "none",
        color: "var(--text-primary)", cursor: "pointer", fontSize: 13, fontWeight: 500,
      }}>
        <span>{title}</span>
        <span style={{ color: "var(--text-tertiary)", fontSize: 11, transition: "var(--transition-fast)", transform: open ? "rotate(180deg)" : "none" }}>▼</span>
      </button>
      {open && (
        <pre style={{
          margin: 0, padding: 12, fontSize: 11, lineHeight: 1.5,
          background: "var(--bg-card)", color: "var(--text-secondary)",
          whiteSpace: "pre-wrap", wordBreak: "break-all", fontFamily: "var(--font-mono)",
          maxHeight: 260, overflow: "auto",
        }}>
          {JSON.stringify(data, null, 2)}
        </pre>
      )}
    </div>
  );
}

/* ─── Shared styles ─── */
const S = {
  input: {
    padding: "8px 10px", borderRadius: "var(--radius-sm)",
    border: "1px solid var(--border)", background: "var(--bg-input)",
    color: "var(--text-primary)", fontFamily: "inherit", fontSize: 13,
    outline: "none", transition: "var(--transition-fast)",
  } as React.CSSProperties,
  btn: {
    padding: "7px 14px", borderRadius: "var(--radius-sm)",
    border: "1px solid var(--border)", background: "var(--bg-surface)",
    color: "var(--text-primary)", cursor: "pointer", fontFamily: "inherit",
    fontSize: 13, fontWeight: 500, transition: "var(--transition-fast)",
  } as React.CSSProperties,
  btnAccent: {
    padding: "7px 16px", borderRadius: "var(--radius-sm)", border: "none",
    background: "var(--accent)", color: "#fff", cursor: "pointer",
    fontFamily: "inherit", fontSize: 13, fontWeight: 600,
    transition: "var(--transition-fast)",
  } as React.CSSProperties,
  btnSm: {
    padding: "4px 10px", borderRadius: "var(--radius-sm)",
    border: "1px solid var(--border)", background: "var(--bg-surface)",
    color: "var(--text-secondary)", cursor: "pointer", fontFamily: "inherit",
    fontSize: 12, transition: "var(--transition-fast)",
  } as React.CSSProperties,
  card: {
    background: "var(--bg-card)", border: "1px solid var(--border)",
    borderRadius: "var(--radius-md)", padding: 16,
  } as React.CSSProperties,
};