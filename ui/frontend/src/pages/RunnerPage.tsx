import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import type { ConfigRun, Task } from "../api/types";
import LogViewer from "../components/LogViewer";

const boxStyle: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: 14,
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "9px 10px",
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--bg-input)",
  color: "var(--text-primary)",
  fontFamily: "inherit",
};

const buttonStyle: React.CSSProperties = {
  padding: "10px 14px",
  borderRadius: 10,
  border: "1px solid var(--border)",
  background: "var(--bg-surface)",
  color: "var(--text-primary)",
  cursor: "pointer",
  fontFamily: "inherit",
};

export default function RunnerPage(props: { lastRunId: string | null; onRunSelected: (runId: string) => void }) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [runs, setRuns] = useState<ConfigRun[]>([]);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  async function refreshTasks() {
    const res = await apiGet<{ tasks: Task[] }>("/api/tasks");
    setTasks(res.tasks);
  }

  async function refreshRuns() {
    const res = await apiGet<{ runs: ConfigRun[] }>("/api/config/runs");
    setRuns(res.runs);
  }

  useEffect(() => {
    refreshTasks();
    refreshRuns();
    const t = window.setInterval(refreshTasks, 1200);
    return () => window.clearInterval(t);
  }, []);

  async function start() {
    if (!props.lastRunId) {
      alert("请先选择或保存一个配置");
      return;
    }
    setStarting(true);
    try {
      const res = await apiPost<{ task_id: string }>("/api/tasks/spider?run_id=" + encodeURIComponent(props.lastRunId), null);
      setActiveTaskId(res.task_id);
      await refreshTasks();
    } finally {
      setStarting(false);
    }
  }

  const selectedRun = useMemo(() => runs.find((x) => x.run_id === props.lastRunId), [runs, props.lastRunId]);

  return (
    <div style={{ color: "var(--text-primary)" }}>
      <h2 style={{ marginTop: 0, marginBottom: 8 }}>爬虫运行</h2>
      <p style={{ marginBottom: 16, color: "var(--text-secondary)" }}>选择一个配置 run，启动任务后可在右侧实时查看日志滚动。</p>

      <div style={{ ...boxStyle, marginBottom: 14 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 10, alignItems: "center" }}>
          <select style={inputStyle} value={props.lastRunId ?? ""} onChange={(e) => props.onRunSelected(e.target.value)}>
            <option value="">请选择配置</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.run_id} | {r.config.platform || "-"} | {r.config.rank_key || "ALL"}
              </option>
            ))}
          </select>

          <button onClick={refreshRuns} style={buttonStyle}>
            刷新配置
          </button>

          <button disabled={!props.lastRunId || starting} onClick={start} style={buttonStyle}>
            {starting ? "启动中..." : "启动爬虫"}
          </button>
        </div>

        {selectedRun && (
          <div
            style={{
              marginTop: 10,
              fontSize: 12,
              color: "var(--text-secondary)",
              background: "var(--bg-surface)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              padding: 10,
            }}
          >
            当前配置：platform={selectedRun.config.platform || "-"} | rank_key={selectedRun.config.rank_key || "ALL"} | use_proxy={String(selectedRun.config.use_proxy)} | max_retries={selectedRun.config.max_retries ?? "-"} | consecutive_threshold={selectedRun.config.consecutive_threshold ?? "-"}
          </div>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.05fr 1fr", gap: 14 }}>
        <section style={boxStyle}>
          <h3 style={{ marginTop: 0, marginBottom: 10 }}>任务列表</h3>
          <div style={{ maxHeight: 520, overflow: "auto", border: "1px solid var(--border)", borderRadius: 10 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead style={{ position: "sticky", top: 0, background: "var(--bg-surface)" }}>
                <tr>
                  <th align="left" style={{ padding: 8 }}>task_id</th>
                  <th align="left" style={{ padding: 8 }}>run_id</th>
                  <th align="left" style={{ padding: 8 }}>status</th>
                  <th align="left" style={{ padding: 8 }}>exit</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => {
                  const active = activeTaskId === t.task_id;
                  return (
                    <tr
                      key={t.task_id}
                      onClick={() => setActiveTaskId(t.task_id)}
                      style={{ cursor: "pointer", background: active ? "var(--accent-subtle)" : "transparent" }}
                    >
                      <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{t.task_id}</td>
                      <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{t.config_run_id || "-"}</td>
                      <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{t.status}</td>
                      <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{t.exit_code ?? "-"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section style={boxStyle}>
          <h3 style={{ marginTop: 0, marginBottom: 10 }}>实时日志</h3>
          {activeTaskId ? <LogViewer taskId={activeTaskId} pollMs={800} height={500} /> : <div style={{ color: "var(--text-secondary)" }}>点击左侧任务查看日志</div>}
        </section>
      </div>
    </div>
  );
}