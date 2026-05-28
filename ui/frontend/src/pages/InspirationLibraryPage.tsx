/**
 * InspirationLibraryPage — combined library + on-demand reference-work
 * fragment matching.
 *
 * Latest UX revision:
 *  - Default view shows every inspiration with local text search.
 *  - Below the library, an embedding-driven "在参考作品中找相似片段"
 *    panel lets the user type any text (or click "用这条灵感搜" on a
 *    card) and surfaces matching reference-work segments / outlines
 *    via the existing /api/references/search endpoint.
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


export default function InspirationLibraryPage({ onNavigate }: Props) {
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
      // The API returns either {groups:[{ref_id,title,rows:[...]}]} or
      // a flat {rows:[...]} depending on version — handle both.
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
    // Scroll to the panel so the user sees the result.
    setTimeout(() => {
      const el = document.getElementById("ref-match-panel");
      el?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  }, [runRefSearch]);

  return (
    <div className="page" style={{ padding: 16 }}>
      <header style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>灵感库</h2>
        <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "4px 0 0" }}>
          自由记录的创作灵感片段。带 embedding ，可在生成时按相关性自动召回。
          下方面板可用任意文字（或某条灵感）在已索引的参考作品里找相似段落。
        </p>
      </header>

      <InspirationLibrary onSearchWorks={handleSearchFromCard} />

      {/* Reference-work fragment matching panel */}
      <div id="ref-match-panel" className="card" style={{ marginTop: 18 }}>
        <div className="card-header">
          <h3 style={{ margin: 0, fontSize: 14 }}>在参考作品中找相似片段</h3>
        </div>
        <div className="card-body">
          <div className="flex gap-8 items-center" style={{ flexWrap: "wrap" }}>
            <input
              className="input"
              value={refQuery}
              onChange={e => setRefQuery(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") runRefSearch(); }}
              placeholder='例："主角第一次觉醒能力" / "穿越后第一次见到女主"'
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
            <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-tertiary)" }}>
              已用灵感内容自动搜索：{autoQuery.slice(0, 80)}{autoQuery.length > 80 ? "..." : ""}
            </div>
          )}
          <div className="text-xs text-muted" style={{ marginTop: 8, lineHeight: 1.55 }}>
            搜索基于参考作品的 embedding 索引（先在「参考数据库工具 → 索引管理」给作品建索引）。
            距离 (distance) 越小越相似。
          </div>

          {refHits.length > 0 ? (
            <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 10 }}>
              {refHits.map((h, i) => (
                <div key={`${h.ref_id}-${i}`} className="card" style={{
                  padding: 10, background: "var(--bg-surface-2)",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <strong style={{ fontSize: 12 }}>
                      {h.work_title || h.ref_id}
                      <span style={{ color: "var(--text-tertiary)", fontWeight: 400, marginLeft: 6 }}>
                        · {h.level}
                      </span>
                    </strong>
                    <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                      distance {h.distance.toFixed(3)}
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
                    {h.text.slice(0, 360)}{h.text.length > 360 ? "..." : ""}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p style={{ marginTop: 14, color: "var(--text-tertiary)", fontSize: 12 }}>
              {refSearching ? "搜索中..." : "输入查询并点搜索，或在上面任意一条灵感卡片上点「搜索参考作品」。"}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
