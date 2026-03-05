import React, { useEffect, useRef, useState } from "react";
import { apiGet } from "../api/client";

export default function LogViewer(props: { taskId: string; pollMs?: number; height?: number }) {
  const [text, setText] = useState("");
  const [offset, setOffset] = useState(0);
  const [autoScroll, setAutoScroll] = useState(true);
  const [lastUpdateAt, setLastUpdateAt] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const preRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    setText("");
    setOffset(0);
    setError(null);
  }, [props.taskId]);

  useEffect(() => {
    let cancelled = false;
    const pollMs = props.pollMs ?? 800;

    async function tick(nextOffset: number) {
      try {
        const res = await apiGet<{ offset: number; text: string }>(`/api/tasks/${props.taskId}/logs?offset=${nextOffset}`);
        if (cancelled) return;

        if (res.text) {
          setText((t) => t + res.text);
          setLastUpdateAt(Date.now());
        }
        setOffset(res.offset);
        setError(null);

        window.setTimeout(() => tick(res.offset), pollMs);
      } catch (e: any) {
        if (cancelled) return;
        setError(String(e));
        window.setTimeout(() => tick(nextOffset), pollMs * 2);
      }
    }

    tick(0);
    return () => {
      cancelled = true;
    };
  }, [props.taskId, props.pollMs]);

  useEffect(() => {
    if (!autoScroll || !preRef.current) return;
    preRef.current.scrollTop = preRef.current.scrollHeight;
  }, [text, autoScroll]);

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 10, background: "var(--bg-surface)" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "8px 10px",
          borderBottom: "1px solid var(--border)",
          fontSize: 12,
          color: "var(--text-secondary)",
        }}
      >
        <div>offset: {offset}</div>
        <div>{lastUpdateAt ? `最后更新: ${new Date(lastUpdateAt).toLocaleTimeString()}` : "等待日志输出..."}</div>
        <label style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />
          自动滚动
        </label>
      </div>
      {error && <div style={{ color: "var(--warning)", fontSize: 12, padding: "8px 10px" }}>日志拉取重试中：{error}</div>}
      <pre
        ref={preRef}
        style={{
          margin: 0,
          padding: 12,
          height: props.height ?? 420,
          overflow: "auto",
          color: "var(--text-primary)",
          background: "transparent",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          lineHeight: 1.5,
        }}
      >
        {text || "(no logs yet)"}
      </pre>
    </div>
  );
}
