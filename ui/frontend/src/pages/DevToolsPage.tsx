import React, { useState } from "react";
import { apiPost, apiGet } from "../api/client";

interface Props {
  projectId: string;
}

export default function DevToolsPage({ projectId }: Props) {
  const [activeTab, setActiveTab] = useState<"health" | "seed" | "test">("health");
  const [healthResult, setHealthResult] = useState<string>("");
  const [healthRunning, setHealthRunning] = useState(false);
  const [seedResult, setSeedResult] = useState<string>("");
  const [seedRunning, setSeedRunning] = useState(false);
  const [testResult, setTestResult] = useState<string>("");
  const [testRunning, setTestRunning] = useState(false);

  const runHealthCheck = async () => {
    setHealthRunning(true);
    setHealthResult("正在运行项目自检...");
    try {
      const resp = await apiPost<{ output: string; issues: number }>("/api/dev/health-check", {});
      setHealthResult(resp.output || `检查完成。发现 ${resp.issues || 0} 个问题。`);
    } catch (e: any) {
      setHealthResult(`自检失败: ${e?.message || "请确认后端已启动"}`);
    }
    setHealthRunning(false);
  };

  const runTestSeed = async () => {
    setSeedRunning(true);
    setSeedResult("正在生成测试数据...");
    try {
      const resp = await apiPost<{ message: string }>("/api/dev/seed-test-data", { project_id: projectId || "default" });
      setSeedResult(resp.message || "测试数据生成完成。");
    } catch (e: any) {
      setSeedResult(`生成失败: ${e?.message || "请确认后端已启动"}`);
    }
    setSeedRunning(false);
  };

  const runModelTest = async () => {
    setTestRunning(true);
    setTestResult("正在测试模型连接...");
    try {
      const resp = await apiPost<{ connected: boolean; message: string }>("/api/models/test", {
        provider: "mock", api_key: "", base_url: "",
      });
      setTestResult(resp.connected ? "Mock 模型连接正常。" : `连接失败: ${resp.message}`);
    } catch (e: any) {
      setTestResult(`测试失败: ${e?.message || "请确认后端已启动"}`);
    }
    setTestRunning(false);
  };

  const checkBackendStatus = async () => {
    try {
      const resp = await apiGet<{ ok: boolean; version: string; test_mode: boolean }>("/api/health");
      setTestResult(`后端状态: OK\n版本: ${resp.version}\n测试模式: ${resp.test_mode ? "已启用" : "未启用"}`);
    } catch (e: any) {
      setTestResult(`后端无法连接: ${e?.message || "请确认后端已启动"}`);
    }
  };

  return (
    <div className="page-full" style={{ overflow: "auto" }}>
      <div style={{ maxWidth: 800, margin: "0 auto", padding: "32px 32px 64px" }}>
        <h2 className="font-serif" style={{ fontSize: 24, fontWeight: 700, marginBottom: 8 }}>
          开发者工具
        </h2>
        <p className="text-muted" style={{ fontSize: 13, marginBottom: 24 }}>
          集合所有开发、测试和维护工具。仅供开发者使用。
        </p>

        {/* Tab bar */}
        <div className="tab-bar-underline" style={{ marginBottom: 20 }}>
          {([
            ["health", "项目自检"],
            ["seed", "测试数据"],
            ["test", "连接测试"],
          ] as const).map(([key, label]) => (
            <button key={key} className={`tab-item ${activeTab === key ? "active" : ""}`} onClick={() => setActiveTab(key)}>
              {label}
            </button>
          ))}
        </div>

        {/* Health Check */}
        {activeTab === "health" && (
          <div className="card">
            <div className="card-header">
              <h3>项目健康检查</h3>
              <p className="text-xs text-muted">检测冗余文件、空文件、同名模块、失效import</p>
            </div>
            <div className="card-body">
              <button className="btn-primary" onClick={runHealthCheck} disabled={healthRunning}
                style={{ marginBottom: 12 }}>
                {healthRunning ? "检查中..." : "运行自检"}
              </button>
              {healthResult && (
                <pre style={{
                  padding: 12, background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border)", fontSize: 12, lineHeight: 1.6,
                  color: "var(--text-secondary)", whiteSpace: "pre-wrap", maxHeight: 400, overflowY: "auto",
                }}>
                  {healthResult}
                </pre>
              )}
              <p className="text-xs text-muted" style={{ marginTop: 8 }}>
                对应命令行: <code>python scripts/check_project_health.py [--fix]</code>
              </p>
            </div>
          </div>
        )}

        {/* Test Data Seeding */}
        {activeTab === "seed" && (
          <div className="card">
            <div className="card-header">
              <h3>测试数据生成</h3>
              <p className="text-xs text-muted">为当前项目生成示例角色、世界书、大纲等测试数据</p>
            </div>
            <div className="card-body">
              <div style={{ marginBottom: 12, padding: "8px 12px", background: "var(--accent-subtle)", borderRadius: "var(--radius-sm)", fontSize: 12, color: "var(--accent)" }}>
                当前项目: {projectId || "default"}
              </div>
              <button className="btn-primary" onClick={runTestSeed} disabled={seedRunning}
                style={{ marginBottom: 12 }}>
                {seedRunning ? "生成中..." : "生成测试数据"}
              </button>
              {seedResult && (
                <pre style={{
                  padding: 12, background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border)", fontSize: 12, lineHeight: 1.6,
                  color: "var(--text-secondary)", whiteSpace: "pre-wrap",
                }}>
                  {seedResult}
                </pre>
              )}
              <p className="text-xs text-muted" style={{ marginTop: 8 }}>
                对应命令行: <code>python test_seed.py</code>
              </p>
            </div>
          </div>
        )}

        {/* Connection Test */}
        {activeTab === "test" && (
          <div className="card">
            <div className="card-header">
              <h3>连接与环境测试</h3>
              <p className="text-xs text-muted">测试后端连接、模型可用性、环境状态</p>
            </div>
            <div className="card-body">
              <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
                <button className="btn-primary" onClick={checkBackendStatus} disabled={testRunning}>
                  检查后端状态
                </button>
                <button className="btn" onClick={runModelTest} disabled={testRunning}>
                  测试 Mock 模型
                </button>
              </div>
              {testResult && (
                <pre style={{
                  padding: 12, background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border)", fontSize: 12, lineHeight: 1.6,
                  color: "var(--text-secondary)", whiteSpace: "pre-wrap",
                }}>
                  {testResult}
                </pre>
              )}
              <div style={{ marginTop: 16, padding: "10px 12px", background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)" }}>
                <div className="label" style={{ fontSize: 11, marginBottom: 6 }}>环境变量</div>
                <div className="text-xs text-muted" style={{ lineHeight: 1.8 }}>
                  <code>WN_TEST_MODE=1</code> - 启用测试模式（使用 Mock 模型）<br/>
                  <code>WN_DATA_DIR=path</code> - 自定义数据目录
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
