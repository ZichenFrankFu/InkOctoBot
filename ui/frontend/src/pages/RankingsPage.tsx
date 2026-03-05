import React, { useEffect, useState, useCallback, useMemo } from "react";
import { apiGet } from "../api/client";

type Platform = "" | "qidian" | "fanqie";
interface RankList { rank_list_id: number; platform: string; rank_family: string; rank_sub_cat: string; }
interface Snapshot { snapshot_id: number; rank_list_id: number; snapshot_date: string; item_count: number; platform: string; rank_family: string; rank_sub_cat: string; }
interface Entry { snapshot_id: number; novel_uid: number; rank: number; total_recommend: number | null; reading_count: number | null; title: string; author: string; platform: string; main_category: string; status: string; total_words: number; url: string; }
interface NovelDetail { novel: any; titles: any[]; tags: any[]; rank_history: any[]; chapters: any[]; }
type Step = "lists" | "snapshots" | "entries";

export default function RankingsPage() {
  const [platform, setPlatform] = useState<Platform>("");
  const [step, setStep] = useState<Step>("lists");
  const [rankLists, setRankLists] = useState<RankList[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedList, setSelectedList] = useState<RankList | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState<Snapshot | null>(null);
  const [novelDetail, setNovelDetail] = useState<NovelDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showPanel, setShowPanel] = useState(false);

  const pl = (p: string) => p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p;

  useEffect(() => {
    setLoading(true);
    apiGet<{ rows: RankList[] }>(`/api/db/rank_lists${platform ? `?platform=${platform}` : ""}`)
      .then(r => setRankLists(r.rows)).catch(console.error).finally(() => setLoading(false));
    setStep("lists"); setSelectedList(null); setSelectedSnapshot(null); setSnapshots([]); setEntries([]);
  }, [platform]);

  const selectList = useCallback(async (list: RankList) => {
    setSelectedList(list); setSelectedSnapshot(null); setEntries([]); setStep("snapshots"); setLoading(true);
    try { setSnapshots((await apiGet<{ rows: Snapshot[] }>(`/api/db/snapshots?rank_list_id=${list.rank_list_id}`)).rows); }
    catch (e) { console.error(e); } finally { setLoading(false); }
  }, []);

  const selectSnapshot = useCallback(async (snap: Snapshot) => {
    setSelectedSnapshot(snap); setStep("entries"); setLoading(true);
    try { setEntries((await apiGet<{ rows: Entry[] }>(`/api/db/entries?snapshot_id=${snap.snapshot_id}&limit=200`)).rows); }
    catch (e) { console.error(e); } finally { setLoading(false); }
  }, []);

  const openNovelDetail = useCallback(async (uid: number) => {
    setShowPanel(true); setDetailLoading(true); setNovelDetail(null);
    try { setNovelDetail(await apiGet<NovelDetail>(`/api/db/novel/${uid}`)); }
    catch (e) { console.error(e); } finally { setDetailLoading(false); }
  }, []);

  const goToLists = () => { setStep("lists"); setSelectedList(null); setSelectedSnapshot(null); };
  const goToSnapshots = () => { setStep("snapshots"); setSelectedSnapshot(null); setEntries([]); };

  const groupedLists = useMemo(() => {
    const g: Record<string, RankList[]> = {};
    for (const rl of rankLists) { const k = `${rl.platform}|${rl.rank_family}`; if (!g[k]) g[k] = []; g[k].push(rl); }
    return g;
  }, [rankLists]);

  return (
    <div className="page-container">
      <div className="page-header"><h2>榜单浏览</h2><p>逐级浏览各平台榜单数据</p></div>
      <div className="platform-tabs">
        <button className={`platform-tab${platform === "" ? " active" : ""}`} onClick={() => setPlatform("")}>全部平台</button>
        <button className={`platform-tab${platform === "qidian" ? " active" : ""}`} onClick={() => setPlatform("qidian")}>起点</button>
        <button className={`platform-tab${platform === "fanqie" ? " active" : ""}`} onClick={() => setPlatform("fanqie")}>番茄</button>
      </div>

      {/* Breadcrumb */}
      <div className="breadcrumb">
        <div className={`breadcrumb-item${step === "lists" ? " active" : ""}`}>
          {step === "lists" ? <span>📋 选择榜单</span> : <button onClick={goToLists}>📋 榜单列表</button>}
        </div>
        {(step === "snapshots" || step === "entries") && selectedList && <>
          <span className="breadcrumb-sep">›</span>
          <div className={`breadcrumb-item${step === "snapshots" ? " active" : ""}`}>
            {step === "snapshots" ? <span>{pl(selectedList.platform)} · {selectedList.rank_family}{selectedList.rank_sub_cat && ` · ${selectedList.rank_sub_cat}`}</span>
              : <button onClick={goToSnapshots}>{selectedList.rank_family}{selectedList.rank_sub_cat && ` · ${selectedList.rank_sub_cat}`}</button>}
          </div>
        </>}
        {step === "entries" && selectedSnapshot && <>
          <span className="breadcrumb-sep">›</span>
          <div className="breadcrumb-item active"><span>📅 {selectedSnapshot.snapshot_date}</span></div>
        </>}
      </div>

      {loading ? <div className="loading"><div className="loading-spinner" /> 加载中…</div> : <>
        {step === "lists" && (rankLists.length === 0 ? <div className="empty-state"><div className="empty-icon">📭</div><h4>暂无榜单数据</h4></div> :
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 16 }}>
            {Object.entries(groupedLists).map(([key, lists]) => { const [plat, family] = key.split("|"); return (
              <div className="card" key={key}><div className="card-header"><div style={{ display: "flex", alignItems: "center", gap: 8 }}><span className={`tag ${plat}`}>{pl(plat)}</span><h3>{family}</h3></div><p>{lists.length} 个子榜</p></div>
                <div className="card-body" style={{ padding: "8px 12px" }}>{lists.map(rl => (
                  <div key={rl.rank_list_id} onClick={() => selectList(rl)} style={{ padding: "10px 12px", borderRadius: "var(--radius-sm)", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between", transition: "background 0.15s" }}
                    onMouseEnter={e => (e.currentTarget.style.background = "var(--paper-warm)")} onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                    <span style={{ fontSize: 13 }}>{rl.rank_sub_cat || family}</span><span style={{ fontSize: 18, color: "var(--ink-300)" }}>›</span>
                  </div>
                ))}</div>
              </div>
            ); })}
          </div>
        )}

        {step === "snapshots" && <div className="card"><div className="card-header"><h3>选择日期</h3><p>共 {snapshots.length} 个快照</p></div>
          <div style={{ maxHeight: 560, overflowY: "auto" }}>{snapshots.length === 0 ? <div className="empty-state"><p>暂无快照</p></div> :
            <table className="data-table"><thead><tr><th>日期</th><th>榜单</th><th style={{ textAlign: "right" }}>收录数</th><th style={{ width: 48 }}></th></tr></thead>
              <tbody>{snapshots.map(s => <tr key={s.snapshot_id} className="clickable" onClick={() => selectSnapshot(s)}>
                <td className="font-mono" style={{ fontWeight: 500 }}>{s.snapshot_date}</td>
                <td>{s.rank_family}{s.rank_sub_cat && <span className="text-muted"> · {s.rank_sub_cat}</span>}</td>
                <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{s.item_count ?? "—"}</td>
                <td style={{ textAlign: "center", color: "var(--ink-300)" }}>›</td>
              </tr>)}</tbody></table>}
          </div></div>}

        {step === "entries" && <div className="card"><div className="card-header"><div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div><h3>{selectedList?.rank_family}{selectedList?.rank_sub_cat && ` · ${selectedList.rank_sub_cat}`}</h3><p>{selectedSnapshot?.snapshot_date} · {entries.length} 部作品</p></div>
          {selectedList && <span className={`tag ${selectedList.platform}`}>{pl(selectedList.platform)}</span>}
        </div></div>
          <div style={{ maxHeight: 600, overflowY: "auto" }}>{entries.length === 0 ? <div className="empty-state"><p>暂无条目</p></div> :
            <table className="data-table"><thead><tr><th style={{ width: 56 }}>排名</th><th>书名</th><th>作者</th><th>分类</th><th style={{ textAlign: "right" }}>指标</th><th style={{ textAlign: "right" }}>总字数</th></tr></thead>
              <tbody>{entries.map(e => <tr key={`${e.snapshot_id}-${e.novel_uid}`} className="clickable" onClick={() => openNovelDetail(e.novel_uid)}>
                <td><span className={`rank-badge ${e.rank <= 3 ? "top3" : e.rank <= 10 ? "top10" : "normal"}`}>{e.rank}</span></td>
                <td style={{ fontWeight: 500, maxWidth: 280 }} className="truncate">{e.title || `(uid: ${e.novel_uid})`}</td>
                <td className="text-muted">{e.author || "—"}</td>
                <td>{e.main_category && <span className="tag category">{e.main_category}</span>}</td>
                <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{e.total_recommend != null ? e.total_recommend.toLocaleString() : e.reading_count != null ? e.reading_count.toLocaleString() : "—"}</td>
                <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--ink-400)" }}>{e.total_words ? `${(e.total_words / 10000).toFixed(1)}万` : "—"}</td>
              </tr>)}</tbody></table>}
          </div></div>}
      </>}

      {/* Novel Detail Side Panel — NO chapter reading */}
      {showPanel && <>
        <div className="side-panel-overlay" onClick={() => setShowPanel(false)} />
        <div className="side-panel">
          <div className="side-panel-header"><h3>{detailLoading ? "加载中…" : "作品详情"}</h3><button className="side-panel-close" onClick={() => setShowPanel(false)}>✕</button></div>
          <div className="side-panel-body">
            {detailLoading ? <div className="loading"><div className="loading-spinner" /></div>
              : novelDetail ? <NPanel d={novelDetail} /> : <div className="text-muted">加载失败</div>}
          </div>
        </div>
      </>}
    </div>
  );
}

function NPanel({ d }: { d: NovelDetail }) {
  const { novel: n, titles, tags, rank_history: rh, chapters: chs } = d;
  const t = titles.find((x: any) => x.is_primary)?.title || n.author || "(未知)";
  const pl = (p: string) => p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p;
  return <>
    <div style={{ marginBottom: 20 }}>
      <h3 style={{ fontFamily: "var(--font-serif)", fontSize: 20, fontWeight: 700, marginBottom: 6 }}>{t}</h3>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <span className={`tag ${n.platform}`}>{pl(n.platform)}</span>
        {n.main_category && <span className="tag category">{n.main_category}</span>}
        {n.status && <span className={`tag ${n.status === "completed" ? "status-completed" : "status-ongoing"}`}>{n.status === "completed" ? "已完本" : "连载中"}</span>}
      </div>
    </div>
    <div className="detail-section"><h4>基本信息</h4><div className="detail-grid">
      <span className="label">作者</span><span className="value">{n.author || "—"}</span>
      <span className="label">总字数</span><span className="value">{n.total_words ? `${(n.total_words / 10000).toFixed(1)} 万字` : "—"}</span>
      <span className="label">首次采集</span><span className="value">{n.created_date || "—"}</span>
      <span className="label">最近出现</span><span className="value">{n.last_seen_date || "—"}</span>
      {n.url && <><span className="label">链接</span><span className="value"><a href={n.url} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)", textDecoration: "underline", fontSize: 12 }}>查看原文 ↗</a></span></>}
    </div></div>
    {n.intro && <div className="detail-section"><h4>简介</h4><p style={{ fontSize: 13, lineHeight: 1.7, color: "var(--ink-600)", whiteSpace: "pre-wrap" }}>{n.intro}</p></div>}
    {tags.length > 0 && <div className="detail-section"><h4>标签</h4><div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{tags.map((tg: any) => <span key={tg.tag_id} className="tag category">{tg.tag_name}</span>)}</div></div>}
    {rh.length > 0 && <div className="detail-section"><h4>排名历史</h4><div style={{ maxHeight: 200, overflowY: "auto" }}><table className="data-table"><thead><tr><th>日期</th><th>榜单</th><th style={{ textAlign: "right" }}>排名</th></tr></thead><tbody>{rh.slice(0, 20).map((h: any, i: number) => <tr key={i}><td className="font-mono" style={{ fontSize: 12 }}>{h.snapshot_date}</td><td style={{ fontSize: 12 }}>{h.rank_family}{h.rank_sub_cat && ` · ${h.rank_sub_cat}`}</td><td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>{h.rank}</td></tr>)}</tbody></table></div></div>}
    {chs.length > 0 && <div className="detail-section"><h4>已采集章节（{chs.length} 章）</h4>
      <div style={{ fontSize: 12, color: "var(--ink-500)", lineHeight: 1.8 }}>{chs.map((ch: any) => <div key={ch.chapter_num} style={{ padding: "4px 0", borderBottom: "1px solid var(--ink-50)" }}><span className="font-mono" style={{ color: "var(--ink-400)", marginRight: 8 }}>第{ch.chapter_num}章</span>{ch.chapter_title}<span style={{ float: "right", color: "var(--ink-300)" }}>{ch.word_count ? `${ch.word_count}字` : ""}</span></div>)}</div>
    </div>}
  </>;
}