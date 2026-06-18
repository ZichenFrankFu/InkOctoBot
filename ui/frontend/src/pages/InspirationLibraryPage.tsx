/**
 * InspirationLibraryPage — combined library + on-demand reference-work
 * fragment matching.
 *
 * Layout:
 *  - Page header (title only — no verbose intro)
 *  - Library cards (with inline add/edit form)
 *  - Reference-work fragment search panel below
 */
import React, { useCallback, useState } from "react";
import { apiGet } from "../api/client";
import InspirationLibrary from "../components/InspirationLibrary";
import { useToast } from "../components/shared/Toast";


interface Props {
  onNavigate?: (tab: string, ctx?: any) => void;
}


interface RefSearchHit {
  ref_id: string;
  work_title?: string;
  level: string;
  text: string;
  distance: number;
  chapter?: string;
}


export default function InspirationLibraryPage({ onNavigate: _onNavigate }: Props) {
  const { toast } = useToast();
  const [refQuery, setRefQuery] = useState("");
  const [refSearching, setRefSearching] = useState(false);
  const [refHits, setRefHits] = useState<RefSearchHit[]>([]);
  const [autoQuery, setAutoQuery] = useState<string>("");

  const runRefSearch = useCallback(async (queryText?: string) => {
    const q = (queryText ?? refQuery).trim();
    if (!q) return;
    setRefSearching(true);
    try {
      const params = new URLSearchParams({ q, k: "10" });
      const r = await apiGet<{ groups?: any[]; rows?: RefSearchHit[] }>(
        `/api/references/search?${params.toString()}`,
      );
      let flat: RefSearchHit[] = [];
      if (r.groups && Array.isArray(r.groups)) {
        for (const g of r.groups) {
          for (const row of (g.rows || [])) {
            flat.push({ ...row, work_title: g.title, ref_id: g.ref_id });
          }
        }
      } else if (r.rows && Array.isArray(r.rows)) {
        flat = r.rows;
      }
      flat.sort((a, b) => a.distance - b.distance);
      setRefHits(flat.slice(0, 20));
    } catch (e: any) {
      toast(`搜索失败: ${e.message}`, "error");
      setRefHits([]);
    } finally {
      setRefSearching(false);
    }
  }, [refQuery, toast]);

  // Bridge: when the InspirationLibrary card calls onSearchWorks,
  // pre-fill the query box and trigger an immediate search.
  const handleSearchFromCard = useCallback((text: string) => {
    const q = text.trim().slice(0, 600);
    setRefQuery(q);
    setAutoQuery(q);
    runRefSearch(q);
    setTimeout(() => {
      const el = document.getElementById("ref-match-panel");
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  }, [runRefSearch]);

  return (
    <div className="page" style={{ padding: "24px 28px 48px" }}>
      <div className="page-header" style={{ padding: 0, marginBottom: 20 }}>
        <div className="page-header-row">
          <div>
            <h2>灵感库</h2>
            <p>自由记录的创作灵感片段 · 生成时按相关性自动召回</p>
          </div>
        </div>
      </div>

      <InspirationLibrary onSearchWorks={handleSearchFromCard} />

      {/* Reference-work fragment matching panel */}
      <div id="ref-match-panel" className="card" style={{ marginTop: 18 }}>
        <div className="card-header">
          <div>
            <h3 style={{ margin: 0 }}>在参考作品中找相似片段</h3>
            <p style={{ margin: "2px 0 0" }}>
              基于参考作品的 embedding 索引 · distance 越小越相似
            </p>
          </div>
        </div>
        <div className="card-body">
          <div className="flex items-center" style={{
            gap: 10, flexWrap: "wrap", marginBottom: 8,
          }}>
            <input
              className="input"
              value={refQuery}
              onChange={e => setRefQuery(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") runRefSearch(); }}
              placeholder='例：主角第一次觉醒能力 / 穿越后第一次见到女主'
              style={{ flex: 1, minWidth: 260 }}
            />
            <button
              className="btn-primary"
              onClick={() => runRefSearch()}
              disabled={refSearching || !refQuery.trim()}
            >
              {refSearching ? "搜索中..." : "搜索"}
            </button>
          </div>
          {autoQuery && (
            <div className="text-xs" style={{
              padding: "6px 10px", marginBottom: 8,
              background: "var(--accent-subtle)",
              border: "1px solid var(--accent)",
              borderRadius: "var(--radius-sm)",
              color: "var(--accent)", lineHeight: 1.6,
            }}>
              已用灵感内容自动搜索：{autoQuery.slice(0, 100)}{autoQuery.length > 100 ? "…" : ""}
            </div>
          )}

          {refHits.length > 0 ? (
            <div className="flex flex-col" style={{ gap: 8, marginTop: 8 }}>
              {refHits.map((h, i) => (
                <HitCard key={`${h.ref_id}-${i}`} hit={h} />
              ))}
            </div>
          ) : (
            <div style={{
              padding: "30px 20px", textAlign: "center",
              background: "var(--bg-surface)",
              border: "1px dashed var(--border)",
              borderRadius: "var(--radius-sm)",
              color: "var(--text-tertiary)",
              fontSize: 12, lineHeight: 1.7,
              marginTop: 8,
            }}>
              {refSearching
                ? "搜索中..."
                : "输入查询并搜索；或在上面任意一条灵感卡片上点「搜索参考作品」。"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


function HitCard({ hit }: { hit: RefSearchHit }) {
  return (
    <div style={{
      border: "1px solid var(--border)",
      borderLeft: "3px solid var(--accent)",
      borderRadius: "var(--radius-sm)",
      background: "var(--bg-surface)",
      padding: "10px 14px",
    }}>
      <div className="flex items-center" style={{
        gap: 8, marginBottom: 6, flexWrap: "wrap",
      }}>
        <strong style={{
          fontSize: 13, color: "var(--text-primary)",
          fontFamily: "var(--font-serif)",
        }}>{hit.work_title || hit.ref_id}</strong>
        <span style={{
          fontSize: 11, padding: "1px 8px", borderRadius: 4,
          color: "var(--text-secondary)",
          background: "var(--bg-surface-2)",
          fontWeight: 600,
        }}>{hit.level}</span>
        <div style={{ flex: 1 }} />
        <span style={{
          fontSize: 11, padding: "1px 8px", borderRadius: 4,
          color: "var(--accent)", background: "var(--accent-subtle)",
          fontFamily: "var(--font-mono)", fontWeight: 700,
        }}>{hit.distance.toFixed(3)}</span>
      </div>
      <div style={{
        fontSize: 12, lineHeight: 1.65,
        color: "var(--text-secondary)",
        whiteSpace: "pre-wrap",
      }}>
        {hit.text.slice(0, 360)}{hit.text.length > 360 ? "…" : ""}
      </div>
    </div>
  );
}
