/**
 * MarketFeatureExtractionPage — new top-level page under 市场数据库.
 *
 * Drives the market-extractor backend (``/api/market-extractor`` +
 * ``/api/platform-profiles``). Lets the user pull platform-level
 * style baselines that downstream feed into ``platform_directive``
 * loader in the prompt context.
 */
import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import { useToast } from "../components/shared/Toast";


interface PlatformProfile {
  platform: string;
  genre?: string;
  directive_text?: string;
  sample_count?: number;
  updated_at?: string;
}


interface ExtractorStatus {
  last_run?: string;
  rows_processed?: number;
  pending?: number;
}


export default function MarketFeatureExtractionPage() {
  const { toast } = useToast();
  const [profiles, setProfiles] = useState<PlatformProfile[]>([]);
  const [status, setStatus] = useState<ExtractorStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [p, s] = await Promise.all([
        apiGet<{ items?: PlatformProfile[]; profiles?: PlatformProfile[] }>(
          "/api/platform-profiles",
        ).catch(() => ({ items: [] })),
        apiGet<ExtractorStatus>("/api/market-extractor/status")
          .catch(() => ({})),
      ]);
      setProfiles((p.items || (p as any).profiles || []) as PlatformProfile[]);
      setStatus(s);
    } catch (e: any) {
      toast(`加载失败: ${e.message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { refresh(); }, [refresh]);

  const triggerExtract = useCallback(async () => {
    if (!window.confirm(
      "触发市场特征提取？\n" +
      "会从市场数据库读取近期上榜作品，通过 LLM 提取平台/类型级风格特征。\n" +
      "可能耗时数分钟，且会产生 LLM 费用。"
    )) return;
    setExtracting(true);
    try {
      await apiPost("/api/market-extractor/run", {});
      toast("提取已调度，几分钟后刷新查看结果", "success");
      setTimeout(refresh, 5000);
    } catch (e: any) {
      toast(`触发失败: ${e.message}`, "error");
    } finally {
      setExtracting(false);
    }
  }, [refresh, toast]);

  return (
    <div className="page" style={{ padding: 16 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0 }}>📊 市场特征提取</h2>
          <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "4px 0 0" }}>
            从市场数据库抽取平台 × 类型级别的风格基线 (platform directive)。
            生成时 platform_directive loader 会自动按你项目的 platform/genre 注入。
          </p>
        </div>
        <button
          className="btn primary"
          onClick={triggerExtract}
          disabled={extracting}
        >
          {extracting ? "提取中..." : "🚀 运行提取"}
        </button>
      </header>

      {/* Status */}
      {status && (
        <div className="card" style={{ padding: 12, marginBottom: 16 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>状态</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
            <StatTile label="上次运行" value={status.last_run || "未运行"} />
            <StatTile label="已处理" value={status.rows_processed ?? "—"} />
            <StatTile label="待处理" value={status.pending ?? "—"} />
          </div>
        </div>
      )}

      {/* Profile list */}
      <div className="card" style={{ padding: 14 }}>
        <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0, fontSize: 14 }}>
            已提取的平台 Profile ({profiles.length})
          </h3>
          <button className="btn" onClick={refresh} disabled={loading}>刷新</button>
        </header>
        {profiles.length === 0 ? (
          <p style={{ color: "var(--text-tertiary)", fontSize: 12, padding: 12 }}>
            还没有任何平台 profile。先确保市场库有数据，然后点上面的「🚀 运行提取」。
          </p>
        ) : (
          <table style={{ width: "100%", fontSize: 12 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--text-tertiary)" }}>
                <th style={{ padding: "4px 6px" }}>平台</th>
                <th style={{ padding: "4px 6px" }}>类型</th>
                <th style={{ padding: "4px 6px" }}>样本数</th>
                <th style={{ padding: "4px 6px" }}>方向</th>
                <th style={{ padding: "4px 6px" }}>更新时间</th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((p, i) => (
                <tr key={`${p.platform}-${p.genre || i}`} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={{ padding: "6px" }}><strong>{p.platform}</strong></td>
                  <td style={{ padding: "6px" }}>{p.genre || "—"}</td>
                  <td style={{ padding: "6px" }}>{p.sample_count ?? "—"}</td>
                  <td style={{ padding: "6px", maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={p.directive_text || ""}>
                    {(p.directive_text || "—").slice(0, 80)}{(p.directive_text || "").length > 80 ? "..." : ""}
                  </td>
                  <td style={{ padding: "6px" }}>{p.updated_at || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}


function StatTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div style={{ padding: 10, textAlign: "center", background: "var(--bg-surface-2)", borderRadius: 4 }}>
      <div style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 600, marginTop: 2 }}>{value}</div>
    </div>
  );
}
