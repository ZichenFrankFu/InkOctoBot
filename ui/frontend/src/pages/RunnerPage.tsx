import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import type { Task } from "../api/types";
import LogViewer from "../components/LogViewer";

const boxStyle: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: 14,
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

type RunnerPageProps = {
  lastRunId: string | null;
  currentConfig?: any;
  configVersion?: number;
  onRunSelected?: (id: string) => void;
};

export default function RunnerPage(props: RunnerPageProps) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [metaLogs, setMetaLogs] = useState<string[]>([]);

  const safeConfig = props.currentConfig ?? {};
  const safeConfigVersion = props.configVersion ?? 0;

  async function refreshTasks() {
    const res = await apiGet<{ tasks: Task[] }>("/api/tasks");
    setTasks(res.tasks);
    if (!activeTaskId && res.tasks.length) {
      setActiveTaskId(res.tasks[0].task_id);
    }
  }

  useEffect(() => {
    refreshTasks();
    const t = window.setInterval(refreshTasks, 1200);
    return () => window.clearInterval(t);
  }, [activeTaskId]);

  useEffect(() => {
    setMetaLogs((logs) => [
      `${new Date().toLocaleTimeString()} [config] 配置已更新，可直接一键启动（未保存也可运行）`,
      ...logs,
    ].slice(0, 200));
  }, [safeConfigVersion]);

  async function start() {
    if (!window.confirm("确认按当前配置立即启动爬虫？")) return;
    setStarting(true);
    setMetaLogs((logs) => [`${new Date().toLocaleTimeString()} [start] 正在提交运行任务...`, ...logs].slice(0, 200));
    try {
      const res = await apiPost<{ task_id: string; command?: string[] }>("/api/tasks/spider/adhoc", safeConfig || {});
      setActiveTaskId(res.task_id);
      setMetaLogs((logs) => [`${new Date().toLocaleTimeString()} [started] task_id=${res.task_id}`, ...logs].slice(0, 200));
      await refreshTasks();
    } catch (e: any) {
      setMetaLogs((logs) => [`${new Date().toLocaleTimeString()} [error] ${String(e)}`, ...logs].slice(0, 200));
      alert(String(e));
    } finally {
      setStarting(false);
    }
  }

  const activeTask = useMemo(() => tasks.find((t) => t.task_id === activeTaskId) || null, [tasks, activeTaskId]);

  return (
    <div style={{ color: "var(--text-primary)" }}>
      <h2 style={{ marginTop: 0, marginBottom: 8 }}>爬虫运行</h2>
      <p style={{ marginBottom: 16, color: "var(--text-secondary)" }}>配置页修改会自动同步到这里；无需先“选择配置”，可直接启动。</p>

      <section style={{ ...boxStyle, marginBottom: 14 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 10 }}>
          <button disabled={starting} onClick={start} style={buttonStyle}>{starting ? "启动中..." : "一键启动爬虫"}</button>
          <button onClick={refreshTasks} style={buttonStyle}>刷新运行状态</button>
          <span style={{ color: "var(--text-secondary)", fontSize: 12 }}>最近保存配置: {props.lastRunId ?? "(未保存)"}</span>
        </div>
        <div style={{ fontSize: 12, color: "var(--text-secondary)", background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, padding: 10 }}>
          当前运行参数摘要：platform={safeConfig?.platform || "-"} | rank_key={safeConfig?.rank_key || "ALL"} | chapter_count={safeConfig?.chapter_count ?? "-"} | newbook_chapter_count={safeConfig?.newbook_chapter_count ?? "-"}
        </div>
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "0.9fr 1.1fr", gap: 14 }}>
        <section style={boxStyle}>
          <h3 style={{ marginTop: 0, marginBottom: 8 }}>运行 Meta-Log</h3>
          <pre style={{ margin: 0, height: 520, overflow: "auto", background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 8, padding: 10, color: "var(--text-secondary)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
            {metaLogs.length ? metaLogs.join("\n") : "(暂无事件)"}
          </pre>

          <div style={{ marginTop: 10, fontSize: 12, color: "var(--text-secondary)" }}>
            当前活跃任务：{activeTask?.task_id ?? "-"} | status={activeTask?.status ?? "-"} | exit={activeTask?.exit_code ?? "-"}
          </div>
        </section>

        <section style={boxStyle}>
          <h3 style={{ marginTop: 0, marginBottom: 10 }}>实时运行日志</h3>
          {activeTaskId ? <LogViewer taskId={activeTaskId} pollMs={700} height={540} /> : <div style={{ color: "var(--text-secondary)" }}>尚未启动任务</div>}
        </section>
      </div>
    </div>
  );
}
