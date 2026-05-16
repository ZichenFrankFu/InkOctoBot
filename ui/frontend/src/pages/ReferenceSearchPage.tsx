import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiDelete } from "../api/client";
import { useToast } from "../components/shared/Toast";

interface SearchHit {
  id: string;
  text: string;
  distance: number;
  metadata: Record<string, any>;
}

interface SearchResponse {
  q: string;
  k: number;
  levels: string[];
  hits: SearchHit[];
}

interface IndexProgressRow {
  ref_id: string;
  level: string;
  backend: string;
  done: number;
  total: number;
  status: string;
  error?: string;
  updated_at?: string;
}

const SOURCE_LABEL: Record<string, string> = {
  chronicle_event: "大纲",
  character: "角色",
  setting: "设定",
  chapter_summary: "章节摘要",
  chapter_chunk: "正文片段",
};
const SOURCE_COLOR: Record<string, string> = {
  chronicle_event: "var(--accent)",
  character: "var(--purple)",
  setting: "var(--indigo)",
  chapter_summary: "var(--jade)",
  chapter_chunk: "var(--gold)",
};

interface Props {
  onNavigate?: (tab: string) => void;
}

export default function ReferenceSearchPage({ onNavigate }: Props) {
  const { toast } = useToast();
  const [q, setQ] = useState("");
  const [k, setK] = useState(10);
  const [includeL3, setIncludeL3] = useState(false);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [drillingRefId, setDrillingRefId] = useState<string | null>(null);
  const [drillHits, setDrillHits] = useState<SearchHit[]>([]);
  const [indexing, setIndexing] = useState<Record<string, boolean>>({});

  const search = useCallback(async () => {
    if (!q.trim()) return;
    setLoading(true);
    setHits([]);
    setDrillingRefId(null);
    setDrillHits([]);
    try {
      const params = new URLSearchParams({
        q: q.trim(),
        k: String(k),
        levels: "L1,L2",
      });
      const r = await apiGet<SearchResponse>(`/api/references/search?${params}`);
      setHits(r.hits || []);
      if ((r.hits || []).length === 0) {
        toast("无匹配结果。可能尚未对作品建立索引。", "info");
      }
    } catch (e: any) {
      toast(e?.message || "搜索失败", "error");
    } finally { setLoading(false); }
  }, [q, k, toast]);

  const drillDeep = async (refId: string) => {
    if (!q.trim()) return;
    setDrillingRefId(refId);
    setDrillHits([]);
    setLoading(true);
    try {
      const params = new URLSearchParams({
        q: q.trim(),
        k: String(k),
        levels: "L3",
        ref_id: refId,
      });
      const r = await apiGet<SearchResponse>(`/api/references/search?${params}`);
      setDrillHits(r.hits || []);
      if ((r.hits || []).length === 0) {
        toast("该作品尚未建立 L3 深度索引。点击「为本作品深度索引」 后再搜索。", "info");
      }
    } catch (e: any) {
      toast(e?.message || "深度搜索失败", "error");
    } finally { setLoading(false); }
  };

  const buildIndex = async (refId: string, includeL3Flag: boolean) => {
    setIndexing(prev => ({ ...prev, [refId]: true }));
    try {
      const r = await apiPost<any>(
        `/api/references/works/${refId}/index/run`,
        { level: "all", include_l3: includeL3Flag },
        { timeoutMs: 600_000 },
      );
      toast(`索引完成：L1=${r?.L1?.embedded ?? 0}, L2=${r?.L2?.embedded ?? 0}` +
            (includeL3Flag ? `, L3=${r?.L3?.embedded ?? 0}` : ""), "success");
    } catch (e: any) {
      toast(e?.message || "索引失败", "error");
    } finally { setIndexing(prev => ({ ...prev, [refId]: false })); }
  };

  // Hits grouped by ref_id for the per-work card rendering
  const hitsByWork = useMemo(() => {
    const groups: Record<string, { title: string; rows: SearchHit[] }> = {};
    for (const h of hits) {
      const refId = h.metadata.ref_id as string;
      const title = (h.metadata.title as string) || refId;
      if (!groups[refId]) groups[refId] = { title, rows: [] };
      groups[refId].rows.push(h);
    }
    // Sort by best (smallest distance) hit in each group
    return Object.entries(groups)
      .map(([refId, g]) => ({
        ref_id: refId,
        title: g.title,
        rows: g.rows.sort((a, b) => a.distance - b.distance),
        best_dist: Math.min(...g.rows.map(r => r.distance)),
      }))
      .sort((a, b) => a.best_dist - b.best_dist);
  }, [hits]);

  const openDetail = (refId: string) => {
    try { sessionStorage.setItem("ref_open_ref_id", refId); } catch { /* noop */ }
    onNavigate?.("references");
  };

  return (
    <div className="page-full" style={{ overflow: "auto" }}>
      <div className="page-header" style={{ paddingBottom: 12 }}>
        <div className="page-header-row">
          <div>
            <h2>相似搜索</h2>
            <p>跨作品自然语言检索 · 默认两段式（先粗后细，L1 大纲/角色/设定 + L2 章节摘要 → 单作品 L3 正文）</p>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-body">
          <div className="flex gap-8 items-center" style={{ flexWrap: "wrap" }}>
            <input
              className="input"
              placeholder='例如："主角第一次觉醒能力" / "镇潮部队成立的那段历史"'
              value={q}
              onChange={e => setQ(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") search(); }}
              style={{ flex: 1, minWidth: 280 }}
            />
            <label className="flex items-center gap-4" style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              <span>top-k</span>
              <input
                type="number"
                value={k}
                onChange={e => setK(Math.max(1, Math.min(50, parseInt(e.target.value, 10) || 10)))}
                className="input"
                style={{ width: 60 }}
              />
            </label>
            <label className="flex items-center gap-4" style={{ fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}
                   title="勾选时为目标作品建立 L3 深度索引（包含正文块），用于「深度搜索」">
              <input type="checkbox" checked={includeL3} onChange={e => setIncludeL3(e.target.checked)} style={{ width: 14, height: 14 }} />
              建索引时包含 L3 正文
            </label>
            <button className="btn-primary" onClick={search} disabled={loading || !q.trim()}>
              {loading ? "搜索中..." : "搜索"}
            </button>
          </div>
          <div className="text-xs text-muted" style={{ marginTop: 8, lineHeight: 1.55 }}>
            提示：作品需要先建立索引才能被搜索到。在「参考作品详情」中提交分段后，L1 (大纲/角色/设定) 会自动入索引；L2 (章节摘要) 与 L3 (正文片段) 在「数据库概览」逐部触发，或在每个结果上方点击「为本作品建索引」。
          </div>
        </div>
      </div>

      {hitsByWork.length === 0 ? (
        loading ? (
          <div className="empty-state" style={{ paddingTop: 60 }}><p>搜索中...</p></div>
        ) : (
          <div className="empty-state" style={{ paddingTop: 40 }}>
            <p>输入查询并回车开始搜索。</p>
          </div>
        )
      ) : (
        <div className="flex flex-col gap-12">
          {hitsByWork.map(g => (
            <div key={g.ref_id} className="card">
              <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <h3>
                  {g.title}
                  <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                    {g.rows.length} 条匹配 · 最佳距离 {g.best_dist.toFixed(3)}
                  </span>
                </h3>
                <div className="flex gap-6">
                  <button
                    className="btn"
                    style={{ fontSize: 11, padding: "3px 10px" }}
                    onClick={() => drillDeep(g.ref_id)}
                    disabled={loading}
                  >查看本作品的更多匹配片段</button>
                  <button
                    className="btn"
                    style={{ fontSize: 11, padding: "3px 10px" }}
                    onClick={() => buildIndex(g.ref_id, includeL3)}
                    disabled={indexing[g.ref_id]}
                    title={includeL3 ? "为本作品建立 L1+L2+L3 索引" : "为本作品建立 L1+L2 索引"}
                  >{indexing[g.ref_id] ? "索引中..." : "为本作品建索引"}</button>
                  <button
                    className="btn-primary"
                    style={{ fontSize: 11, padding: "3px 10px" }}
                    onClick={() => openDetail(g.ref_id)}
                  >打开详情</button>
                </div>
              </div>
              <div className="card-body">
                <ResultsList rows={g.rows} />
                {drillingRefId === g.ref_id && drillHits.length > 0 && (
                  <div style={{
                    marginTop: 14,
                    padding: 10,
                    background: "var(--bg-surface)",
                    border: "1px dashed var(--gold)",
                    borderRadius: 4,
                  }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "var(--gold)", marginBottom: 6 }}>
                      L3 深度匹配（正文片段，按距离排序）
                    </div>
                    <ResultsList rows={drillHits} />
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ResultsList({ rows }: { rows: SearchHit[] }) {
  return (
    <div className="flex flex-col gap-8">
      {rows.map(h => {
        const src = (h.metadata.source_type as string) || "chapter_chunk";
        const tm = (h.metadata.time_marker as string)
                || (h.metadata.first_seen_at as string)
                || (h.metadata.first_introduced_at as string)
                || (typeof h.metadata.chapter === "number" ? `第 ${h.metadata.chapter} 章` : "");
        const color = SOURCE_COLOR[src] || "var(--text-tertiary)";
        return (
          <div key={h.id} style={{
            padding: "8px 12px",
            background: "var(--bg-surface)",
            borderRadius: 4,
            borderLeft: `3px solid ${color}`,
          }}>
            <div className="flex items-center gap-6" style={{ marginBottom: 4, flexWrap: "wrap" }}>
              <span className="tag" style={{
                fontSize: 10, padding: "1px 6px",
                color, background: "var(--bg-surface-2)",
                border: `1px solid ${color}`,
              }}>{SOURCE_LABEL[src] || src}</span>
              {tm && (
                <span className="tag" style={{
                  fontSize: 10, padding: "1px 6px",
                  color: "var(--accent)", background: "var(--accent-subtle)",
                  border: "1px solid var(--accent)",
                }}>{tm}</span>
              )}
              <span className="text-xs text-muted" style={{ marginLeft: "auto", fontFamily: "var(--font-mono)" }}>
                距离 {h.distance.toFixed(3)}
              </span>
            </div>
            <div style={{
              fontSize: 12, lineHeight: 1.6,
              color: "var(--text-secondary)",
              whiteSpace: "pre-wrap",
              display: "-webkit-box",
              WebkitBoxOrient: "vertical",
              WebkitLineClamp: 5,
              overflow: "hidden",
            }}>{h.text}</div>
          </div>
        );
      })}
    </div>
  );
}
