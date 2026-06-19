import React from "react";
import { apiGet, apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";

/** 取名：基于人名库按题材/性别/姓氏重组生成一批新名供复制。
 *  题材选定后展示「取名规律」（来自市场特征提取的统计画像），让用户
 *  直观看到这批名字遵循的命名特征 —— 名长分布、姓氏分布、典型用字。
 */
const KIND_OPTS: [string, string][] = [
  ["chinese", "中文名"], ["japanese", "日文名"], ["western", "西方名"],
];
const GENDER_OPTS: [string, string][] = [
  ["", "不限"], ["male", "男"], ["female", "女"],
];

interface NamingPattern {
  available?: boolean;
  reason?: string;
  book_count?: number;
  name_count?: number;
  name_length_structure?: { length: number; pct: number }[];
  surname_distribution?: { surname: string; pct: number }[];
  given_char_distribution?: { char: string; pct: number }[];
  typical_char_pairs?: { pair: string; count: number }[];
  rare_char_rate?: number;
  note?: string;
}

export default function NameGeneratorModal({ onClose }: { onClose: () => void }) {
  const { toast } = useToast();
  const [kind, setKind] = React.useState("chinese");
  const [gender, setGender] = React.useState("");
  const [surname, setSurname] = React.useState("");
  const [category, setCategory] = React.useState("");
  const [count, setCount] = React.useState(20);
  const [names, setNames] = React.useState<any[]>([]);
  const [note, setNote] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  // 取名规律 — 当 category 非空时拉取该题材的命名画像，作为生成依据 + UI 展示。
  const [pattern, setPattern] = React.useState<NamingPattern | null>(null);
  const [patternLoading, setPatternLoading] = React.useState(false);
  React.useEffect(() => {
    const cat = category.trim();
    if (!cat) { setPattern(null); return; }
    let alive = true;
    setPatternLoading(true);
    apiGet<NamingPattern>(`/api/analysis/naming-patterns?category=${encodeURIComponent(cat)}`)
      .then(r => { if (alive) setPattern(r || null); })
      .catch(() => { if (alive) setPattern(null); })
      .finally(() => { if (alive) setPatternLoading(false); });
    return () => { alive = false; };
  }, [category]);

  const gen = async () => {
    setBusy(true);
    try {
      const r = await apiPost<any>("/api/analysis/name-generator",
        { kind, gender, surname: surname.trim(), category: category.trim(), count });
      setNames(r.names || []);
      setNote(r.note || "");
      if (!r.names?.length && !r.note) toast("没有生成任何名字", "info");
    } catch (e: any) { toast(`生成失败：${e.message}`, "error"); }
    finally { setBusy(false); }
  };

  const copy = async (text: string, label: string) => {
    try { await navigator.clipboard.writeText(text); toast(`已复制${label}`, "success"); }
    catch { toast("复制失败（浏览器不支持）", "error"); }
  };
  const copyAll = () => copy(names.map(n => n.full_name).join("\n"), `${names.length} 个名字`);

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 1000,
      display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
    }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{
        width: "min(680px, 96vw)", maxHeight: "90vh", overflow: "auto",
        background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 10,
      }}>
        <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>取名 · 按题材取名规律生成</h3>
          <button className="btn" style={{ fontSize: 12, padding: "3px 10px" }} onClick={onClose}>关闭</button>
        </div>
        <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* 约束 */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
            <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>分类
              <select className="select" value={kind} onChange={e => setKind(e.target.value)} style={{ fontSize: 12, padding: "4px 6px" }}>
                {KIND_OPTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>性别
              <select className="select" value={gender} onChange={e => setGender(e.target.value)} style={{ fontSize: 12, padding: "4px 6px" }}>
                {GENDER_OPTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
            </label>
            {kind !== "western" && (
              <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>姓氏
                <input className="input" value={surname} onChange={e => setSurname(e.target.value)}
                  placeholder="可空" style={{ width: 80, padding: "4px 8px", fontSize: 12 }} />
              </label>
            )}
            <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>题材
              <input className="input" value={category} onChange={e => setCategory(e.target.value)}
                placeholder="如 玄幻（可空）" style={{ width: 110, padding: "4px 8px", fontSize: 12 }} />
            </label>
            <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>数量
              <input className="input" type="number" min={1} max={100} value={count}
                onChange={e => setCount(Math.max(1, Math.min(100, Number(e.target.value) || 20)))}
                style={{ width: 60, padding: "4px 8px", fontSize: 12 }} />
            </label>
            <button className="btn-primary" disabled={busy} onClick={gen}
              style={{ fontSize: 12, padding: "6px 18px" }}>{busy ? "生成中…" : "生成"}</button>
          </div>

          {/* 取名规律 (来自市场特征提取 → 基础特征提取) — 当 category 填了才显示，
              让用户清楚这批名字是按题材命名画像采样的。 */}
          {category.trim() && (
            <NamingPatternPreview
              pattern={pattern}
              loading={patternLoading}
              category={category.trim()}
            />
          )}

          {note && <div style={{ fontSize: 12, color: "var(--gold)" }}>{note}</div>}

          {/* 结果 */}
          {names.length > 0 && (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>生成 {names.length} 个，点名字复制</span>
                <button className="btn" style={{ fontSize: 11, padding: "3px 12px" }} onClick={copyAll}>全部复制</button>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {names.map((n, i) => (
                  <button key={i} className="btn" onClick={() => copy(n.full_name, `「${n.full_name}」`)}
                    title="点击复制"
                    style={{ fontSize: 13, padding: "5px 12px", display: "flex", alignItems: "center", gap: 4 }}>
                    {n.full_name}
                    {n.gender === "male" ? <span style={{ color: "var(--cyan)", fontSize: 11 }}>♂</span>
                      : n.gender === "female" ? <span style={{ color: "#f472b6", fontSize: 11 }}>♀</span> : null}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/** 取名规律预览 — 顶部小一栏标注「来源：市场特征提取 / 基础特征提取」，
 *  下面把名长分布 / 姓氏分布 / 典型用字 / 典型搭配 排列出来。
 *  规律不可用时显示原因（避免用户以为是生成器坏了）。 */
function NamingPatternPreview({ pattern, loading, category }: {
  pattern: NamingPattern | null; loading: boolean; category: string;
}) {
  return (
    <div style={{
      padding: "10px 12px",
      background: "var(--bg-surface-2)",
      border: "1px solid var(--border-subtle)",
      borderRadius: 8,
      fontSize: 11,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontSize: 11, fontWeight: 700, color: "var(--text-primary)" }}>
          取名规律 · {category}
        </span>
        <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
          数据来源：市场特征提取 / 基础特征提取
        </span>
      </div>
      {loading ? (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>计算中…</div>
      ) : !pattern ? (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>未能加载该题材的取名规律。</div>
      ) : pattern.available === false ? (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          {pattern.reason || "该题材的样本太少，暂时没有可用的命名画像。"}
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 16px" }}>
          {pattern.name_length_structure && pattern.name_length_structure.length > 0 && (
            <PatternFacet
              label="名长分布"
              chips={pattern.name_length_structure.slice(0, 4).map(x => ({
                k: `${x.length} 字`, v: `${Math.round((x.pct || 0) * 100)}%`,
              }))}
            />
          )}
          {pattern.surname_distribution && pattern.surname_distribution.length > 0 && (
            <PatternFacet
              label="高频姓氏"
              chips={pattern.surname_distribution.slice(0, 6).map(x => ({
                k: x.surname, v: `${Math.round((x.pct || 0) * 100)}%`,
              }))}
            />
          )}
          {pattern.given_char_distribution && pattern.given_char_distribution.length > 0 && (
            <PatternFacet
              label="名字高频字"
              chips={pattern.given_char_distribution.slice(0, 8).map(x => ({
                k: x.char, v: `${Math.round((x.pct || 0) * 100)}%`,
              }))}
            />
          )}
          {pattern.typical_char_pairs && pattern.typical_char_pairs.length > 0 && (
            <PatternFacet
              label="典型搭配"
              chips={pattern.typical_char_pairs.slice(0, 6).map(x => ({
                k: x.pair, v: `${x.count} 次`,
              }))}
            />
          )}
        </div>
      )}
      {pattern && pattern.available !== false && (pattern.book_count || pattern.name_count) && (
        <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 6 }}>
          统计自 {pattern.book_count || "—"} 部作品 · {pattern.name_count || "—"} 个名字样本
        </div>
      )}
    </div>
  );
}

function PatternFacet({ label, chips }: {
  label: string; chips: { k: string; v: string }[];
}) {
  return (
    <div style={{ minWidth: 0 }}>
      <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginBottom: 4, letterSpacing: 0.3 }}>
        {label}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
        {chips.map((c, i) => (
          <span key={i} style={{
            fontSize: 11, padding: "1px 7px", borderRadius: 8,
            background: "var(--bg-surface)", border: "1px solid var(--border-subtle)",
            color: "var(--text-secondary)",
            display: "inline-flex", alignItems: "center", gap: 4,
          }}>
            <span style={{ color: "var(--text-primary)", fontWeight: 600 }}>{c.k}</span>
            <span style={{ color: "var(--text-tertiary)", fontSize: 10 }}>{c.v}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
