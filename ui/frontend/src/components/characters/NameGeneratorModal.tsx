import React from "react";
import { apiGet, apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";

/** 取名：基于人名库按题材/性别/姓氏重组生成一批新名供复制（中文/日文/西方）。
 *  含「性别用字」编辑（增删用于性别启发的字）。 */
const KIND_OPTS: [string, string][] = [
  ["chinese", "中文名"], ["japanese", "日文名"], ["western", "西方名"],
];
const GENDER_OPTS: [string, string][] = [
  ["", "不限"], ["male", "男"], ["female", "女"],
];

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

  // 性别用字编辑
  const [showChars, setShowChars] = React.useState(false);
  const [maleChars, setMaleChars] = React.useState<string[]>([]);
  const [femaleChars, setFemaleChars] = React.useState<string[]>([]);
  const [newMale, setNewMale] = React.useState("");
  const [newFemale, setNewFemale] = React.useState("");

  const loadChars = React.useCallback(async () => {
    try {
      const [m, f] = await Promise.all([
        apiGet<any>("/api/analysis/wordlist?list=male_name_chars"),
        apiGet<any>("/api/analysis/wordlist?list=female_name_chars"),
      ]);
      setMaleChars((m.items || []).map((i: any) => i.word));
      setFemaleChars((f.items || []).map((i: any) => i.word));
    } catch { /* ignore */ }
  }, []);
  React.useEffect(() => { if (showChars) loadChars(); }, [showChars, loadChars]);

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

  const addChar = async (list: string, ch: string) => {
    const w = ch.trim();
    if (!w) return;
    try { await apiPost("/api/analysis/wordlist/add", { list, word: w }); loadChars(); }
    catch (e: any) { toast(`添加失败：${e.message}`, "error"); }
  };
  const removeChar = async (list: string, ch: string) => {
    try { await apiPost("/api/analysis/wordlist/remove", { list, word: ch }); loadChars(); }
    catch (e: any) { toast(`删除失败：${e.message}`, "error"); }
  };

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
          <h3 style={{ margin: 0, fontSize: 15 }}>取名 · 基于人名库重组生成</h3>
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

          {/* 性别用字编辑 */}
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 8 }}>
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
              onClick={() => setShowChars(s => !s)}>
              {showChars ? "▾" : "▸"} 调整性别用字（影响中文名性别判定）
            </button>
            {showChars && (
              <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 10 }}>
                {([["male_name_chars", "男性用字", maleChars, newMale, setNewMale, "var(--cyan)"],
                   ["female_name_chars", "女性用字", femaleChars, newFemale, setNewFemale, "#f472b6"]] as const).map(
                  ([list, label, chars, nv, setNv, color]) => (
                    <div key={list}>
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                        <span style={{ fontSize: 12, color, fontWeight: 600 }}>{label}</span>
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{chars.length} 字</span>
                        <input className="input" value={nv} onChange={e => setNv(e.target.value)}
                          onKeyDown={e => { if (e.key === "Enter" && nv.trim()) { addChar(list, nv.trim()); setNv(""); } }}
                          placeholder="加字" style={{ width: 70, padding: "3px 8px", fontSize: 12, marginLeft: "auto" }} />
                        <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                          disabled={!nv.trim()} onClick={() => { addChar(list, nv.trim()); setNv(""); }}>添加</button>
                      </div>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, maxHeight: 110, overflow: "auto" }}>
                        {chars.map(c => (
                          <span key={c} onClick={() => removeChar(list, c)} title="点击删除"
                            style={{ fontSize: 12, padding: "1px 7px", border: "1px solid var(--border)",
                              borderRadius: 4, cursor: "pointer", background: "var(--bg-surface-2)" }}>{c}</span>
                        ))}
                      </div>
                    </div>
                  ))}
                <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>点字即删除；改完重新「生成」即按新字表判定性别。</div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
