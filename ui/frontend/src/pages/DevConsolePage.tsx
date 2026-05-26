/**
 * DevConsolePage — developer-facing inspector.
 *
 * 4 tabs:
 *   - RAG 预览   /api/debug/rag-preview
 *   - 记忆       /api/debug/memory
 *   - Truth     /api/debug/truth-files
 *   - 日志       /api/debug/recent-logs + diagnostics + event-bus
 *
 * The page is gated by the server: /api/debug/* endpoints are only
 * registered when INKOCTO_DEBUG=1 or launcher --test is set. If those
 * endpoints aren't available, the page shows a friendly notice.
 */
import React, { useEffect, useMemo, useState, useCallback } from "react";
import { apiGet } from "../api/client";

interface Project { id: string; name: string; }

type DevTab = "rag" | "memory" | "truth" | "audit" | "logs";

const TAB_LABELS: Record<DevTab, string> = {
  rag: "RAG 上下文预览",
  memory: "记忆 (L1/L2/L3/L4)",
  truth: "Truth Files (7 文件)",
  audit: "事实库审计",
  logs: "日志 + 诊断",
};

interface DiagnosticsResp {
  repo_root?: string;
  data_dir?: string;
  test_mode?: boolean;
  databases?: Record<string, { exists: boolean; bytes?: number; size_bytes?: number }>;
  log_buffer?: Record<string, number>;
  active_generation_sessions?: number;
}

interface LogRecord {
  timestamp?: string;
  level?: string;
  logger?: string;
  message?: string;
  trace_id?: string;
  session_id?: string;
}

interface RagPreviewResp {
  blocks?: Record<string, string>;
  block_sizes?: Record<string, number>;
  token_estimate?: number;
  assembled_context?: string;
  single_agent_vars?: Record<string, unknown>;
  chapter_fields?: Record<string, unknown>;
}

interface MemoryResp {
  L1_immediate?: unknown;
  L2_chapter_buffer?: { rows?: unknown[]; count?: number };
  L3_semantic_memory?: { hits?: unknown[]; count?: number; _error?: string };
  L4_episodic_timeline?: { events?: unknown[]; unresolved_foreshadowing?: unknown[]; event_count?: number };
  knowledge_isolation?: unknown[];
}

interface TruthResp {
  tables?: Record<string, unknown[] | { _error?: string }>;
  markdown?: Record<string, string>;
  db_path?: string;
}

interface Props {
  projects: Project[];
  activeProject: string;
}

export default function DevConsolePage({ projects, activeProject }: Props) {
  const [tab, setTab] = useState<DevTab>("rag");
  const [debugAvailable, setDebugAvailable] = useState<boolean | null>(null);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsResp | null>(null);
  const [error, setError] = useState<string>("");

  // Check whether /api/debug/* is wired up
  useEffect(() => {
    apiGet<{ ok: boolean; debug_enabled?: boolean; test_mode?: boolean }>("/api/debug/status")
      .then((r) => {
        setDebugAvailable(true);
        if (!r.debug_enabled && !r.test_mode) {
          setError(
            "调试端点存在但未启用 — 启动 launcher 时加 --test 或设置 INKOCTO_DEBUG=1"
          );
        }
      })
      .catch((e) => {
        setDebugAvailable(false);
        setError(`调试端点不可用: ${e.message || e}`);
      });
    apiGet<DiagnosticsResp>("/api/debug/diagnostics")
      .then(setDiagnostics)
      .catch(() => { /* silently — debug may be off */ });
  }, []);

  if (debugAvailable === null) {
    return <div style={{ padding: 24 }}>加载开发者面板…</div>;
  }

  return (
    <div style={{ padding: 16, height: "100%", overflow: "hidden", display: "flex", flexDirection: "column" }}>
      <header style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>开发者控制台</h2>
        <div style={{ color: "var(--text-secondary)", fontSize: 12, marginTop: 4 }}>
          检视 RAG 上下文、记忆 4 层、Truth Files、日志 — 用于人工校准与排查问题
        </div>
      </header>

      {!debugAvailable && (
        <div style={{ padding: 12, background: "var(--surface-1)", borderRadius: 6, marginBottom: 12 }}>
          ⚠️ {error}
        </div>
      )}
      {debugAvailable && error && (
        <div style={{ padding: 8, background: "var(--surface-1)", borderRadius: 6, marginBottom: 12, fontSize: 12 }}>
          ⚠️ {error}
        </div>
      )}

      {diagnostics && (
        <DiagnosticsBar diagnostics={diagnostics} />
      )}

      <div style={{ display: "flex", gap: 8, marginBottom: 12 }} role="tablist">
        {(Object.keys(TAB_LABELS) as DevTab[]).map((k) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={tab === k ? "active" : ""}
            style={{
              padding: "6px 12px",
              border: "1px solid var(--border)",
              borderRadius: 4,
              background: tab === k ? "var(--accent)" : "var(--surface-1)",
              color: tab === k ? "white" : "var(--text-primary)",
              cursor: "pointer",
            }}
            role="tab"
            aria-selected={tab === k}
          >
            {TAB_LABELS[k]}
          </button>
        ))}
      </div>

      <div style={{ flex: 1, overflow: "auto", border: "1px solid var(--border)", borderRadius: 6, padding: 12 }}>
        {tab === "rag" && <RagPreviewTab projects={projects} activeProject={activeProject} />}
        {tab === "memory" && <MemoryTab projects={projects} activeProject={activeProject} />}
        {tab === "truth" && <TruthTab projects={projects} activeProject={activeProject} />}
        {tab === "audit" && <AuditTab projects={projects} activeProject={activeProject} />}
        {tab === "logs" && <LogsTab />}
      </div>
    </div>
  );
}

// ─────────────── Diagnostics bar ───────────────────────────────────

function DiagnosticsBar({ diagnostics: d }: { diagnostics: DiagnosticsResp }) {
  return (
    <div style={{
      display: "flex", gap: 16, padding: "6px 12px",
      background: "var(--surface-1)", borderRadius: 4, fontSize: 11,
      marginBottom: 12, flexWrap: "wrap",
    }}>
      <span title={d.repo_root || ""}>📁 {d.test_mode ? "测试模式" : "生产模式"}</span>
      <span>📂 {d.data_dir || "?"}</span>
      {d.databases && Object.entries(d.databases).map(([name, info]) => (
        <span key={name}>
          🗄️ {name}: {info.exists ? `${(((info.bytes || info.size_bytes) || 0) / 1024).toFixed(1)} KB` : "缺失"}
        </span>
      ))}
      <span>🔄 活动 sessions: {d.active_generation_sessions ?? 0}</span>
    </div>
  );
}

// ─────────────── Tab: RAG preview ───────────────────────────────────

function RagPreviewTab({ projects, activeProject }: Props) {
  const [projectId, setProjectId] = useState(activeProject || projects[0]?.id || "");
  const [chapterNum, setChapterNum] = useState(1);
  const [characters, setCharacters] = useState("");
  const [data, setData] = useState<RagPreviewResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    if (!projectId) return;
    setLoading(true);
    setErr("");
    const params = new URLSearchParams({
      project_id: projectId,
      chapter_num: String(chapterNum),
    });
    if (characters) params.set("characters", characters);
    apiGet<RagPreviewResp>(`/api/debug/rag-preview?${params}`)
      .then(setData)
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setLoading(false));
  }, [projectId, chapterNum, characters]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} style={{ padding: 4 }}>
          <option value="">— 选择项目 —</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <label>
          章节:
          <input type="number" min={1} value={chapterNum}
            onChange={(e) => setChapterNum(Number(e.target.value) || 1)}
            style={{ width: 60, marginLeft: 4, padding: 4 }} />
        </label>
        <input placeholder="角色（逗号分隔，可选）" value={characters}
          onChange={(e) => setCharacters(e.target.value)}
          style={{ flex: 1, padding: 4 }} />
        <button onClick={load} disabled={loading || !projectId} style={{ padding: "4px 12px" }}>
          {loading ? "加载中…" : "预览"}
        </button>
      </div>

      {err && <div style={{ color: "tomato", marginBottom: 8 }}>{err}</div>}

      {data && (
        <div style={{ flex: 1, overflow: "auto" }}>
          <div style={{ marginBottom: 12, fontSize: 12, color: "var(--text-secondary)" }}>
            估算 tokens: <b>{data.token_estimate ?? "?"}</b>
            {data.block_sizes && (
              <> · 总字符: <b>{Object.values(data.block_sizes).reduce((a, b) => a + b, 0)}</b></>
            )}
          </div>
          {data.block_sizes && (
            <div style={{ marginBottom: 12 }}>
              <h4 style={{ margin: "8px 0", fontSize: 13 }}>各块大小（字符）</h4>
              <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                <tbody>
                  {Object.entries(data.block_sizes).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
                    <tr key={k}>
                      <td style={{ padding: "2px 8px", color: "var(--text-secondary)" }}>{k}</td>
                      <td style={{ padding: "2px 8px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                          <div style={{ background: "var(--accent)", height: 4, width: Math.min(200, v / 10), borderRadius: 2 }} />
                          <span>{v}</span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data.blocks && (
            <details style={{ marginBottom: 12 }}>
              <summary style={{ cursor: "pointer", fontSize: 13 }}>各块内容（点击展开）</summary>
              {Object.entries(data.blocks).map(([k, v]) => v && (
                <details key={k} style={{ marginLeft: 16, marginTop: 4 }}>
                  <summary style={{ cursor: "pointer", fontSize: 12 }}>{k} ({v.length} 字符)</summary>
                  <pre style={{ background: "var(--surface-1)", padding: 8, borderRadius: 4, fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 300, overflow: "auto" }}>
                    {v}
                  </pre>
                </details>
              ))}
            </details>
          )}
          {data.assembled_context && (
            <details open>
              <summary style={{ cursor: "pointer", fontSize: 13 }}>组装后的完整上下文 (LLM 实际看到)</summary>
              <pre style={{ background: "var(--surface-1)", padding: 8, borderRadius: 4, fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 600, overflow: "auto" }}>
                {data.assembled_context}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────── Tab: Memory inspection ─────────────────────────────

function MemoryTab({ projects, activeProject }: Props) {
  const [projectId, setProjectId] = useState(activeProject || projects[0]?.id || "");
  const [layer, setLayer] = useState<"all" | "L1" | "L2" | "L3" | "L4">("all");
  const [query, setQuery] = useState("");
  const [data, setData] = useState<MemoryResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    if (!projectId) return;
    setLoading(true);
    setErr("");
    const params = new URLSearchParams({ project_id: projectId, layer });
    if (query && layer === "L3") params.set("query", query);
    apiGet<MemoryResp>(`/api/debug/memory?${params}`)
      .then(setData)
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setLoading(false));
  }, [projectId, layer, query]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} style={{ padding: 4 }}>
          <option value="">— 选择项目 —</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <select value={layer} onChange={(e) => setLayer(e.target.value as any)} style={{ padding: 4 }}>
          <option value="all">所有层</option>
          <option value="L1">L1 Immediate</option>
          <option value="L2">L2 ChapterBuffer</option>
          <option value="L3">L3 Semantic (向量)</option>
          <option value="L4">L4 Episodic (事件)</option>
        </select>
        {layer === "L3" && (
          <input placeholder="向量查询语句" value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{ flex: 1, padding: 4 }} />
        )}
        <button onClick={load} disabled={loading || !projectId} style={{ padding: "4px 12px" }}>
          {loading ? "加载中…" : "刷新"}
        </button>
      </div>

      {err && <div style={{ color: "tomato", marginBottom: 8 }}>{err}</div>}

      {data && (
        <div style={{ flex: 1, overflow: "auto" }}>
          {data.L1_immediate !== undefined && (
            <SectionBlock title="L1 Immediate（进程内瞬时）" data={data.L1_immediate} />
          )}
          {data.L2_chapter_buffer && (
            <SectionBlock
              title={`L2 ChapterBuffer（最近章节摘要，${(data.L2_chapter_buffer.rows || []).length} 条）`}
              data={data.L2_chapter_buffer}
            />
          )}
          {data.L3_semantic_memory && (
            <SectionBlock
              title={`L3 Semantic（向量搜索结果${data.L3_semantic_memory.hits ? `，${data.L3_semantic_memory.hits.length} 条命中` : ""}）`}
              data={data.L3_semantic_memory}
            />
          )}
          {data.L4_episodic_timeline && (
            <SectionBlock
              title={`L4 Episodic（事件时间线，${data.L4_episodic_timeline.event_count ?? 0} 条事件）`}
              data={data.L4_episodic_timeline}
            />
          )}
          {data.knowledge_isolation && data.knowledge_isolation.length > 0 && (
            <SectionBlock title={`知识隔离（${data.knowledge_isolation.length} 条规则）`} data={data.knowledge_isolation} />
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────── Tab: Truth Files ───────────────────────────────────

function TruthTab({ projects, activeProject }: Props) {
  const [projectId, setProjectId] = useState(activeProject || projects[0]?.id || "");
  const [data, setData] = useState<TruthResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [view, setView] = useState<"markdown" | "raw">("markdown");

  const load = useCallback(() => {
    if (!projectId) return;
    setLoading(true);
    setErr("");
    apiGet<TruthResp>(`/api/debug/truth-files?project_id=${projectId}`)
      .then(setData)
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} style={{ padding: 4 }}>
          <option value="">— 选择项目 —</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <select value={view} onChange={(e) => setView(e.target.value as any)} style={{ padding: 4 }}>
          <option value="markdown">Markdown 视图（注入给 prompt 的）</option>
          <option value="raw">原始 SQL 行</option>
        </select>
        <button onClick={load} disabled={loading || !projectId} style={{ padding: "4px 12px" }}>
          {loading ? "加载中…" : "刷新"}
        </button>
      </div>

      {err && <div style={{ color: "tomato", marginBottom: 8 }}>{err}</div>}

      {data && view === "markdown" && data.markdown && (
        <div style={{ flex: 1, overflow: "auto" }}>
          {Object.entries(data.markdown).map(([k, v]) => (
            <details key={k} open style={{ marginBottom: 12 }}>
              <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: "bold" }}>
                {k} {v ? `(${v.length} 字符)` : "(空)"}
              </summary>
              <pre style={{ background: "var(--surface-1)", padding: 8, borderRadius: 4, fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 400, overflow: "auto" }}>
                {v || "（无数据）"}
              </pre>
            </details>
          ))}
        </div>
      )}

      {data && view === "raw" && data.tables && (
        <div style={{ flex: 1, overflow: "auto" }}>
          {Object.entries(data.tables).map(([table, rows]) => {
            const isError = !Array.isArray(rows);
            const count = Array.isArray(rows) ? rows.length : 0;
            return (
              <details key={table} style={{ marginBottom: 12 }}>
                <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: "bold" }}>
                  {table} ({isError ? "错误" : `${count} 行`})
                </summary>
                <pre style={{ background: "var(--surface-1)", padding: 8, borderRadius: 4, fontSize: 10, whiteSpace: "pre-wrap", maxHeight: 400, overflow: "auto" }}>
                  {JSON.stringify(rows, null, 2)}
                </pre>
              </details>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─────────────── Tab: Logs + EventBus + Diagnostics ─────────────────

function LogsTab() {
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [eventBus, setEventBus] = useState<any[]>([]);
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [filterLevel, setFilterLevel] = useState<string>("");
  const [filterLogger, setFilterLogger] = useState<string>("");

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: "200" });
    if (filterLevel) params.set("level", filterLevel);
    if (filterLogger) params.set("logger_prefix", filterLogger);
    apiGet<{ records?: LogRecord[] }>(`/api/debug/recent-logs?${params}`)
      .then((r) => setLogs(r.records || []))
      .catch(() => { /* silent */ });
    apiGet<{ events?: any[] }>(`/api/debug/event-bus?limit=50`)
      .then((r) => setEventBus(r.events || []))
      .catch(() => { /* silent */ });
  }, [filterLevel, filterLogger]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!autoRefresh) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [autoRefresh, load]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", gap: 12 }}>
      <div style={{ display: "flex", gap: 8 }}>
        <select value={filterLevel} onChange={(e) => setFilterLevel(e.target.value)} style={{ padding: 4 }}>
          <option value="">所有级别</option>
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
        </select>
        <input placeholder="logger 前缀过滤 (e.g. inkoctobot.agents)" value={filterLogger}
          onChange={(e) => setFilterLogger(e.target.value)}
          style={{ flex: 1, padding: 4 }} />
        <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          自动刷新
        </label>
        <button onClick={load} style={{ padding: "4px 12px" }}>刷新</button>
      </div>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12, overflow: "hidden" }}>
        <div style={{ border: "1px solid var(--border)", borderRadius: 4, overflow: "auto" }}>
          <div style={{ padding: "6px 12px", borderBottom: "1px solid var(--border)", background: "var(--surface-1)", fontSize: 12, fontWeight: "bold" }}>
            日志 ({logs.length} 条)
          </div>
          <table style={{ width: "100%", fontSize: 10, borderCollapse: "collapse" }}>
            <tbody>
              {logs.map((l, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "2px 4px", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                    {(l.timestamp || "").slice(11, 19)}
                  </td>
                  <td style={{ padding: "2px 4px", color: levelColor(l.level || ""), fontWeight: "bold", width: 60 }}>
                    {l.level}
                  </td>
                  <td style={{ padding: "2px 4px", color: "var(--text-secondary)", fontFamily: "monospace" }}>
                    {(l.logger || "").replace("inkoctobot.", "")}
                  </td>
                  <td style={{ padding: "2px 4px" }}>{l.message}</td>
                </tr>
              ))}
              {logs.length === 0 && (
                <tr><td colSpan={4} style={{ padding: 12, textAlign: "center", color: "var(--text-secondary)" }}>无日志（操作 app 后会出现）</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div style={{ border: "1px solid var(--border)", borderRadius: 4, overflow: "auto" }}>
          <div style={{ padding: "6px 12px", borderBottom: "1px solid var(--border)", background: "var(--surface-1)", fontSize: 12, fontWeight: "bold" }}>
            EventBus ({eventBus.length} 条)
          </div>
          <table style={{ width: "100%", fontSize: 10, borderCollapse: "collapse" }}>
            <tbody>
              {eventBus.map((e, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: "2px 4px", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
                    {(e.timestamp || "").slice(11, 19)}
                  </td>
                  <td style={{ padding: "2px 4px", fontWeight: "bold" }}>{e.type || e.event_type}</td>
                </tr>
              ))}
              {eventBus.length === 0 && (
                <tr><td colSpan={2} style={{ padding: 12, textAlign: "center", color: "var(--text-secondary)" }}>无事件</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function levelColor(level: string): string {
  switch (level.toUpperCase()) {
    case "DEBUG": return "var(--text-secondary)";
    case "INFO": return "var(--text-primary)";
    case "WARNING": return "orange";
    case "ERROR": return "tomato";
    default: return "inherit";
  }
}

// ─────────────── Shared SectionBlock for nested data ────────────────

function SectionBlock({ title, data }: { title: string; data: unknown }) {
  const json = useMemo(() => JSON.stringify(data, null, 2), [data]);
  return (
    <details open style={{ marginBottom: 12 }}>
      <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: "bold" }}>{title}</summary>
      <pre style={{ background: "var(--surface-1)", padding: 8, borderRadius: 4, fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 400, overflow: "auto" }}>
        {json}
      </pre>
    </details>
  );
}


// ─────────────── Tab: Audit (B2 — InkOS audit gate, CoT view) ───────

interface AuditIssue {
  rule_id: string;
  severity: "error" | "warning" | "info";
  title: string;
  thought: string;
  observation: string;
  suggestion: string;
}

interface AuditState {
  audit_status: "audit_pending" | "audit_passed" | "audit_failed" | "audit_overridden";
  audit_issues: AuditIssue[];
  exists: boolean;
}

function AuditTab({ projects, activeProject }: Props) {
  const [projectId, setProjectId] = useState(activeProject || projects[0]?.id || "");
  const [chapterNum, setChapterNum] = useState(1);
  const [data, setData] = useState<AuditState | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [overriding, setOverriding] = useState(false);

  const load = useCallback(() => {
    if (!projectId) return;
    setLoading(true);
    setErr("");
    apiGet<AuditState>(`/api/generation/audit/${chapterNum}?project_id=${encodeURIComponent(projectId)}`)
      .then(setData)
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setLoading(false));
  }, [projectId, chapterNum]);

  const doOverride = useCallback(async () => {
    if (!projectId) return;
    setOverriding(true);
    try {
      const resp = await fetch(
        `/api/generation/audit/${chapterNum}/override?project_id=${encodeURIComponent(projectId)}`,
        { method: "POST" },
      );
      const j = await resp.json();
      if (j.audit_status) {
        setData({
          audit_status: j.audit_status,
          audit_issues: j.audit_issues || [],
          exists: j.exists ?? true,
        });
      }
    } catch (e: any) {
      setErr(`override 失败: ${e.message || e}`);
    } finally {
      setOverriding(false);
    }
  }, [projectId, chapterNum]);

  useEffect(() => { load(); }, [load]);

  const status = data?.audit_status;
  const statusColor =
    status === "audit_passed" ? "lime"
    : status === "audit_failed" ? "tomato"
    : status === "audit_overridden" ? "orange"
    : "var(--text-secondary)";
  const statusLabel =
    status === "audit_passed" ? "✓ AUDIT PASSED"
    : status === "audit_failed" ? "✗ AUDIT FAILED"
    : status === "audit_overridden" ? "⚠ AUDIT OVERRIDDEN"
    : "○ AUDIT PENDING";

  const errors = (data?.audit_issues || []).filter((i) => i.severity === "error" && i.rule_id !== "_meta");
  const warnings = (data?.audit_issues || []).filter((i) => i.severity === "warning");
  const infos = (data?.audit_issues || []).filter((i) => i.severity === "info");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} style={{ padding: 4 }}>
          <option value="">— 选择项目 —</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <label>
          章节:
          <input type="number" min={1} value={chapterNum}
            onChange={(e) => setChapterNum(Number(e.target.value) || 1)}
            style={{ width: 60, marginLeft: 4, padding: 4 }} />
        </label>
        <button onClick={load} disabled={loading || !projectId} style={{ padding: "4px 12px" }}>
          {loading ? "加载中…" : "刷新"}
        </button>
        {status === "audit_failed" && (
          <button onClick={doOverride} disabled={overriding}
            style={{ padding: "4px 12px", background: "orange", color: "white", border: "none", borderRadius: 4 }}>
            {overriding ? "处理中…" : "Override 此章"}
          </button>
        )}
      </div>

      {err && <div style={{ color: "tomato", marginBottom: 8 }}>{err}</div>}

      {!data && !loading && (
        <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>
          选择项目与章节后点「刷新」查看 Truth Files 审计状态
        </div>
      )}

      {data && (
        <div style={{ flex: 1, overflow: "auto" }}>
          {/* Status banner */}
          <div style={{
            padding: "8px 12px", background: "var(--surface-1)",
            border: `2px solid ${statusColor}`, borderRadius: 6,
            marginBottom: 12, display: "flex", justifyContent: "space-between",
            alignItems: "center",
          }}>
            <div>
              <div style={{ fontSize: 14, fontWeight: "bold", color: statusColor }}>
                {statusLabel}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>
                第 {chapterNum} 章 · {errors.length} error · {warnings.length} warning · {infos.length} info
              </div>
            </div>
            {status === "audit_failed" && (
              <div style={{ fontSize: 11, color: "tomato", maxWidth: 280, textAlign: "right" }}>
                ⛔ Finalize 被阻断 — 需要修正问题，或显式 Override
              </div>
            )}
            {status === "audit_overridden" && (
              <div style={{ fontSize: 11, color: "orange", maxWidth: 280, textAlign: "right" }}>
                ⚠ 用户已 Override — 允许 finalize 但 issues 仍记录在册
              </div>
            )}
          </div>

          {/* Issues — CoT-style chain */}
          {data.audit_issues.length === 0 ? (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>
              {status === "audit_pending" ? "此章尚未运行 Phase 2 settlement（先用单 Writer 模式生成）"
                : "无 issues — 完美通过 ✓"}
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {data.audit_issues.map((issue, i) => (
                <AuditIssueCard key={i} issue={issue} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AuditIssueCard({ issue }: { issue: AuditIssue }) {
  const isMeta = issue.rule_id === "_meta";
  const color =
    issue.severity === "error" ? "tomato"
    : issue.severity === "warning" ? "orange"
    : isMeta ? "lime" : "var(--text-secondary)";
  const sevLabel =
    issue.severity === "error" ? "ERROR"
    : issue.severity === "warning" ? "WARN"
    : isMeta ? "INFO" : "INFO";

  return (
    <div style={{
      border: `1px solid ${color}`, borderRadius: 6, overflow: "hidden",
      background: "var(--surface-1)",
    }}>
      {/* Header */}
      <div style={{
        padding: "6px 12px", background: "var(--surface-2)",
        display: "flex", alignItems: "center", gap: 8, fontSize: 12,
      }}>
        <span style={{
          padding: "1px 6px", background: color, color: "white",
          borderRadius: 3, fontSize: 10, fontWeight: "bold",
        }}>
          {sevLabel}
        </span>
        <span style={{ fontWeight: "bold" }}>{issue.title}</span>
        {!isMeta && (
          <code style={{ fontSize: 10, color: "var(--text-secondary)", marginLeft: "auto" }}>
            {issue.rule_id}
          </code>
        )}
      </div>
      {/* CoT chain */}
      <div style={{ padding: "8px 12px", fontSize: 12, lineHeight: 1.6 }}>
        <div>
          <span style={{ color: "var(--text-secondary)", marginRight: 6 }}>💭</span>
          <span>{issue.thought}</span>
        </div>
        <div style={{ marginTop: 4 }}>
          <span style={{ color: "var(--text-secondary)", marginRight: 6 }}>🔍</span>
          <span style={{ fontFamily: "monospace", fontSize: 11 }}>{issue.observation}</span>
        </div>
        {issue.suggestion && (
          <div style={{ marginTop: 4 }}>
            <span style={{ color: "var(--text-secondary)", marginRight: 6 }}>💡</span>
            <span style={{ color: "var(--text-secondary)", fontStyle: "italic" }}>
              {issue.suggestion}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
