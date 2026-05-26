/**
 * LoraTrainingPage — fine-tune a LoRA adapter from selected reference works.
 *
 * UI flow:
 *   1. Load hardware report (/api/references/lora/hardware) on mount
 *      → shows GPU name + VRAM + recommended settings
 *   2. User picks reference works from the list
 *   3. User reviews / tweaks training config (base model, epochs, batch,
 *      device, dtype, 4bit)
 *   4. POST /api/references/lora/train kicks off background job
 *   5. Poll /api/references/lora/status every 2s for progress
 *   6. POST /api/references/lora/cancel to stop early
 *
 * Hardware-aware defaults: all "推荐" values come from the backend's
 * detect_hardware() — bf16 vs fp16 vs fp32, 4-bit on/off, batch size,
 * grad accum, all autoselected based on actual GPU/VRAM.
 */
import React, { useEffect, useState, useCallback } from "react";
import { apiGet, apiPost } from "../api/client";

interface ReferenceWork {
  ref_id: string;
  title: string;
  creator?: string;
  has_full_text?: number;
  preprocessing_status?: string;
}

interface GPUInfo {
  index: number;
  name: string;
  vram_total_mb: number;
  vram_free_mb: number;
  compute_capability?: string;
  bf16_supported: boolean;
  fp16_supported: boolean;
  bitsandbytes_available: boolean;
  notes?: string;
}

interface HardwareReport {
  platform: string;
  arch: string;
  cpu_cores: number;
  total_ram_mb: number;
  torch_installed: boolean;
  torch_version?: string;
  cuda_available: boolean;
  rocm_available: boolean;
  mps_available: boolean;
  gpus: GPUInfo[];
  recommended_device: string;
  recommended_dtype: string;
  recommended_4bit: boolean;
  recommended_batch_size: number;
  recommended_grad_accum: number;
  warnings: string[];
}

interface TrainStatus {
  status: "idle" | "running" | "done" | "error" | "cancelling";
  progress?: string;
  error?: string | null;
  work_ids?: string[];
  hardware?: HardwareReport | null;
  resolved_config?: Record<string, unknown>;
  result?: { adapter_path: string; metrics: any; training_time_s: number };
  samples_used?: number;
  started_at?: number;
}

export default function LoraTrainingPage() {
  // B3 InkOS: SFT vs DPO mode
  const [mode, setMode] = useState<"sft" | "dpo">("sft");

  const [hardware, setHardware] = useState<HardwareReport | null>(null);
  const [hardwareLoading, setHardwareLoading] = useState(true);
  const [works, setWorks] = useState<ReferenceWork[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Training config (prefilled from hardware report once loaded)
  const [baseModel, setBaseModel] = useState("Qwen/Qwen2-1.5B");
  const [epochs, setEpochs] = useState(3);
  const [learningRate, setLearningRate] = useState(2e-4);
  const [rank, setRank] = useState(16);
  const [alpha, setAlpha] = useState(32);
  const [device, setDevice] = useState("auto");
  const [dtype, setDtype] = useState("auto");
  const [use4bit, setUse4bit] = useState(true);
  const [batchSize, setBatchSize] = useState(0);
  const [gradAccum, setGradAccum] = useState(0);

  // DPO-specific state
  const [dpoProjectId, setDpoProjectId] = useState("test_project_001");
  const [dpoSftAdapter, setDpoSftAdapter] = useState("");
  const [dpoBeta, setDpoBeta] = useState(0.1);
  const [dpoMinDiff, setDpoMinDiff] = useState(50);
  const [dpoPreview, setDpoPreview] = useState<{ total: number; preview: any[] } | null>(null);
  const [dpoPreviewLoading, setDpoPreviewLoading] = useState(false);

  const [status, setStatus] = useState<TrainStatus>({ status: "idle" });
  const [startError, setStartError] = useState("");

  // Load hardware report on mount
  useEffect(() => {
    setHardwareLoading(true);
    apiGet<HardwareReport>("/api/references/lora/hardware")
      .then((hw) => {
        setHardware(hw);
        // Prefill config with hardware recommendations
        setDevice(hw.recommended_device);
        setDtype(hw.recommended_dtype);
        setUse4bit(hw.recommended_4bit);
        setBatchSize(hw.recommended_batch_size);
        setGradAccum(hw.recommended_grad_accum);
      })
      .catch((e) => {
        // Backend may not be reachable; surface the issue
        setStartError(`无法获取硬件信息: ${e.message || e}`);
      })
      .finally(() => setHardwareLoading(false));

    // Load reference works
    apiGet<{ items: ReferenceWork[] }>("/api/references/works?has_full_text=1&limit=200")
      .then((r) => setWorks(r.items || []))
      .catch(() => { /* ignore */ });
  }, []);

  // Poll status when running
  useEffect(() => {
    if (status.status !== "running" && status.status !== "cancelling") return;
    const t = setInterval(() => {
      apiGet<TrainStatus>("/api/references/lora/status")
        .then(setStatus)
        .catch(() => { /* ignore transient failures */ });
    }, 2000);
    return () => clearInterval(t);
  }, [status.status]);

  const toggleWork = useCallback((rid: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(rid)) next.delete(rid);
      else next.add(rid);
      return next;
    });
  }, []);

  const startTraining = useCallback(async () => {
    setStartError("");
    if (mode === "sft") {
      if (selected.size === 0) {
        setStartError("SFT 模式：请至少选择一个参考作品");
        return;
      }
      try {
        const resp = await apiPost<TrainStatus>("/api/references/lora/train", {
          work_ids: Array.from(selected),
          base_model: baseModel,
          rank, alpha, epochs,
          learning_rate: learningRate,
          use_4bit: use4bit,
          device, dtype,
          batch_size: batchSize,
          gradient_accumulation_steps: gradAccum,
        });
        setStatus({ ...resp, status: "running" });
      } catch (e: any) {
        setStartError(e.message || String(e));
      }
    } else {
      // DPO mode
      if (!dpoProjectId) {
        setStartError("DPO 模式：请指定 project_id（用于挖掘 user_edit 偏好对）");
        return;
      }
      try {
        const resp = await apiPost<TrainStatus>("/api/references/lora/dpo/train", {
          project_id: dpoProjectId,
          base_model: baseModel,
          sft_adapter_path: dpoSftAdapter || "",
          rank, alpha,
          epochs: epochs,
          learning_rate: learningRate,
          beta: dpoBeta,
          use_4bit: use4bit,
          device, dtype,
          batch_size: batchSize,
          gradient_accumulation_steps: gradAccum,
          min_diff_chars: dpoMinDiff,
        });
        setStatus({ ...resp, status: "running" });
      } catch (e: any) {
        setStartError(e.message || String(e));
      }
    }
  }, [mode, selected, baseModel, rank, alpha, epochs, learningRate, use4bit,
      device, dtype, batchSize, gradAccum,
      dpoProjectId, dpoSftAdapter, dpoBeta, dpoMinDiff]);

  const previewDpo = useCallback(async () => {
    if (!dpoProjectId) return;
    setDpoPreviewLoading(true);
    setDpoPreview(null);
    try {
      const r = await apiGet<{ total: number; preview: any[] }>(
        `/api/references/lora/dpo/preview?project_id=${encodeURIComponent(dpoProjectId)}&min_diff_chars=${dpoMinDiff}&limit=5`,
      );
      setDpoPreview(r);
    } catch (e: any) {
      setStartError(`预览失败: ${e.message || e}`);
    } finally {
      setDpoPreviewLoading(false);
    }
  }, [dpoProjectId, dpoMinDiff]);

  const cancelTraining = useCallback(async () => {
    try {
      await apiPost("/api/references/lora/cancel", {});
      setStatus((prev) => ({ ...prev, status: "cancelling" }));
    } catch (e) { /* ignore */ }
  }, []);

  const isRunning = status.status === "running" || status.status === "cancelling";

  return (
    <div style={{ padding: 16, height: "100%", overflow: "auto" }}>
      <h2 style={{ margin: "0 0 8px", fontSize: 18 }}>LoRA 训练</h2>
      <p style={{ color: "var(--text-secondary)", fontSize: 12, marginTop: 0 }}>
        使用参考作品的全文 fine-tune 一个 LoRA 适配器 — 可注入风格 / 用语 / 节奏
        到本地基础模型。运行硬件自动检测，参数按显存自适应。
      </p>

      {/* Mode switcher: SFT vs Constitutional DPO */}
      <div style={{ display: "flex", gap: 0, marginTop: 8, marginBottom: 8 }} role="tablist">
        <button
          onClick={() => setMode("sft")}
          disabled={isRunning}
          style={{
            padding: "6px 16px", border: "1px solid var(--border)",
            borderRight: "none", borderRadius: "4px 0 0 4px",
            background: mode === "sft" ? "var(--accent)" : "var(--surface-1)",
            color: mode === "sft" ? "white" : "var(--text-primary)",
            cursor: isRunning ? "not-allowed" : "pointer",
          }}
        >
          SFT 训练（参考作品文本 → 模型）
        </button>
        <button
          onClick={() => setMode("dpo")}
          disabled={isRunning}
          style={{
            padding: "6px 16px", border: "1px solid var(--border)",
            borderRadius: "0 4px 4px 0",
            background: mode === "dpo" ? "var(--accent)" : "var(--surface-1)",
            color: mode === "dpo" ? "white" : "var(--text-primary)",
            cursor: isRunning ? "not-allowed" : "pointer",
          }}
        >
          Constitutional DPO（user_edit 偏好对 → 模型）
        </button>
      </div>

      {/* Hardware report card */}
      <section style={{ marginTop: 16, padding: 12, background: "var(--surface-1)", borderRadius: 6 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>检测到的硬件</h3>
        {hardwareLoading && <div style={{ marginTop: 8, fontSize: 12 }}>加载中…</div>}
        {hardware && (
          <div style={{ marginTop: 8, fontSize: 12 }}>
            <div>
              <b>{hardware.platform} {hardware.arch}</b>
              {" · CPU "}{hardware.cpu_cores}{" 核"}
              {" · RAM "}{(hardware.total_ram_mb / 1024).toFixed(1)}{" GB"}
            </div>
            <div style={{ marginTop: 4 }}>
              torch: {hardware.torch_installed ? hardware.torch_version : "未安装"}
              {hardware.cuda_available && " · CUDA ✓"}
              {hardware.rocm_available && " · ROCm ✓"}
              {hardware.mps_available && " · MPS ✓"}
            </div>
            {hardware.gpus.length > 0 && (
              <table style={{ marginTop: 8, fontSize: 11, borderCollapse: "collapse", width: "100%" }}>
                <thead>
                  <tr style={{ background: "var(--surface-2)" }}>
                    <th style={{ textAlign: "left", padding: "2px 6px" }}>GPU</th>
                    <th style={{ textAlign: "left", padding: "2px 6px" }}>VRAM 总/可用</th>
                    <th style={{ textAlign: "left", padding: "2px 6px" }}>能力</th>
                    <th style={{ textAlign: "left", padding: "2px 6px" }}>支持</th>
                  </tr>
                </thead>
                <tbody>
                  {hardware.gpus.map((g) => (
                    <tr key={g.index} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={{ padding: "2px 6px" }}>{g.name}</td>
                      <td style={{ padding: "2px 6px" }}>
                        {(g.vram_total_mb / 1024).toFixed(1)}GB / {(g.vram_free_mb / 1024).toFixed(1)}GB
                      </td>
                      <td style={{ padding: "2px 6px" }}>{g.compute_capability || "-"}</td>
                      <td style={{ padding: "2px 6px" }}>
                        {g.bf16_supported && <span style={{ marginRight: 4 }}>bf16</span>}
                        {g.fp16_supported && <span style={{ marginRight: 4 }}>fp16</span>}
                        {g.bitsandbytes_available && <span style={{ color: "lime" }}>4bit</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div style={{ marginTop: 8, padding: 8, background: "var(--surface-2)", borderRadius: 4 }}>
              <b>推荐配置：</b>
              device=<code>{hardware.recommended_device}</code>{", "}
              dtype=<code>{hardware.recommended_dtype}</code>{", "}
              4bit=<code>{String(hardware.recommended_4bit)}</code>{", "}
              batch=<code>{hardware.recommended_batch_size}</code>{", "}
              grad_accum=<code>{hardware.recommended_grad_accum}</code>
            </div>
            {hardware.warnings.length > 0 && (
              <ul style={{ marginTop: 8, marginBottom: 0, fontSize: 11, color: "orange" }}>
                {hardware.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            )}
          </div>
        )}
      </section>

      {/* DPO-specific panel (only in DPO mode) */}
      {mode === "dpo" && (
        <section style={{ marginTop: 16, padding: 12, background: "var(--surface-1)", borderRadius: 6 }}>
          <h3 style={{ margin: 0, fontSize: 14 }}>Constitutional DPO 数据源</h3>
          <p style={{ marginTop: 4, fontSize: 11, color: "var(--text-secondary)" }}>
            DPO 用 (AI 生成的章节, 用户手改的版本) 作为偏好对，
            训练模型偏向用户改后的写法。需要项目中已有至少 1 对 user_edit 版本。
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "140px 1fr 140px 1fr", gap: 8, marginTop: 8, fontSize: 12, alignItems: "center" }}>
            <label>项目 ID</label>
            <input value={dpoProjectId} onChange={(e) => setDpoProjectId(e.target.value)}
              disabled={isRunning} placeholder="test_project_001"
              style={{ padding: 4 }} />
            <label>最小编辑差异（字符）</label>
            <input type="number" min={10} max={1000} value={dpoMinDiff}
              onChange={(e) => setDpoMinDiff(Number(e.target.value) || 50)}
              disabled={isRunning} style={{ padding: 4 }} />

            <label>SFT 适配器路径</label>
            <input value={dpoSftAdapter} onChange={(e) => setDpoSftAdapter(e.target.value)}
              disabled={isRunning}
              placeholder="可选 — data/lora_output/adapter"
              style={{ padding: 4 }} />
            <label>DPO beta（温度）</label>
            <input type="number" min={0.01} max={1.0} step={0.05}
              value={dpoBeta}
              onChange={(e) => setDpoBeta(Number(e.target.value) || 0.1)}
              disabled={isRunning} style={{ padding: 4 }} />
          </div>
          <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center" }}>
            <button onClick={previewDpo} disabled={dpoPreviewLoading || !dpoProjectId}
              style={{ padding: "4px 12px", fontSize: 12 }}>
              {dpoPreviewLoading ? "查询中…" : "预览偏好对"}
            </button>
            {dpoPreview && (
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                共 <b style={{ color: "var(--text-primary)" }}>{dpoPreview.total}</b> 个偏好对可用
              </span>
            )}
          </div>
          {dpoPreview && dpoPreview.preview.length > 0 && (
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: "pointer", fontSize: 12 }}>
                前 {dpoPreview.preview.length} 个样本预览
              </summary>
              <div style={{ marginTop: 4, maxHeight: 280, overflow: "auto" }}>
                {dpoPreview.preview.map((p, i) => (
                  <div key={i} style={{ padding: 8, marginBottom: 6, background: "var(--surface-2)", borderRadius: 4, fontSize: 11 }}>
                    <div style={{ fontWeight: "bold" }}>章节 {p.source_chapter_id}</div>
                    {p.violation_categories && p.violation_categories.length > 0 && (
                      <div style={{ fontSize: 10, color: "orange" }}>
                        违规: {p.violation_categories.join(", ")}
                      </div>
                    )}
                    <div style={{ marginTop: 4, fontSize: 10, color: "var(--text-secondary)" }}>Prompt head:</div>
                    <div style={{ fontSize: 11 }}>{p.prompt_head}…</div>
                    <div style={{ marginTop: 4, fontSize: 10, color: "tomato" }}>✗ Rejected (AI):</div>
                    <div style={{ fontSize: 11 }}>{p.rejected_head}…</div>
                    <div style={{ marginTop: 4, fontSize: 10, color: "lime" }}>✓ Chosen (用户改):</div>
                    <div style={{ fontSize: 11 }}>{p.chosen_head}…</div>
                  </div>
                ))}
              </div>
            </details>
          )}
        </section>
      )}

      {/* Reference works picker (only in SFT mode) */}
      {mode === "sft" && (
      <section style={{ marginTop: 16, padding: 12, background: "var(--surface-1)", borderRadius: 6 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>选择参考作品（必须已上传全文）</h3>
        <div style={{ maxHeight: 280, overflow: "auto", marginTop: 8, fontSize: 12 }}>
          {works.length === 0 && (
            <div style={{ color: "var(--text-secondary)" }}>
              暂无已上传全文的参考作品。请先在「参考作品详情」上传文本。
            </div>
          )}
          {works.map((w) => (
            <label key={w.ref_id} style={{ display: "flex", padding: "4px 0", cursor: "pointer", borderBottom: "1px solid var(--border)" }}>
              <input
                type="checkbox" checked={selected.has(w.ref_id)}
                onChange={() => toggleWork(w.ref_id)}
                disabled={isRunning}
                style={{ marginRight: 8 }}
              />
              <span style={{ flex: 1 }}>{w.title}</span>
              <span style={{ color: "var(--text-secondary)" }}>{w.creator || ""}</span>
            </label>
          ))}
        </div>
        <div style={{ marginTop: 8, fontSize: 12 }}>
          已选: <b>{selected.size}</b> 个作品
        </div>
      </section>
      )}

      {/* Training config */}
      <section style={{ marginTop: 16, padding: 12, background: "var(--surface-1)", borderRadius: 6 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>训练参数</h3>
        <div style={{ display: "grid", gridTemplateColumns: "120px 1fr 120px 1fr", gap: 8, marginTop: 8, fontSize: 12, alignItems: "center" }}>
          <label>基础模型</label>
          <input value={baseModel} onChange={(e) => setBaseModel(e.target.value)} disabled={isRunning} style={{ padding: 4 }} />
          <label>训练轮次 (epochs)</label>
          <input type="number" min={1} max={10} value={epochs} onChange={(e) => setEpochs(Number(e.target.value) || 1)} disabled={isRunning} style={{ padding: 4 }} />

          <label>LoRA rank</label>
          <input type="number" min={4} max={128} value={rank} onChange={(e) => setRank(Number(e.target.value) || 16)} disabled={isRunning} style={{ padding: 4 }} />
          <label>LoRA alpha</label>
          <input type="number" min={4} max={256} value={alpha} onChange={(e) => setAlpha(Number(e.target.value) || 32)} disabled={isRunning} style={{ padding: 4 }} />

          <label>学习率</label>
          <input type="number" step={1e-5} value={learningRate} onChange={(e) => setLearningRate(Number(e.target.value) || 2e-4)} disabled={isRunning} style={{ padding: 4 }} />
          <label>批次大小 (batch_size)</label>
          <input type="number" min={1} max={32} value={batchSize} onChange={(e) => setBatchSize(Number(e.target.value) || 0)} disabled={isRunning} style={{ padding: 4 }} />

          <label>设备 (device)</label>
          <select value={device} onChange={(e) => setDevice(e.target.value)} disabled={isRunning} style={{ padding: 4 }}>
            <option value="auto">auto（按硬件推荐）</option>
            <option value="cuda">CUDA (NVIDIA / AMD ROCm)</option>
            <option value="mps">MPS (Apple Silicon)</option>
            <option value="cpu">CPU（极慢）</option>
          </select>
          <label>精度 (dtype)</label>
          <select value={dtype} onChange={(e) => setDtype(e.target.value)} disabled={isRunning} style={{ padding: 4 }}>
            <option value="auto">auto</option>
            <option value="bf16">bf16 (Ampere+)</option>
            <option value="fp16">fp16 (Volta+)</option>
            <option value="fp32">fp32 (兼容)</option>
          </select>

          <label>梯度累计</label>
          <input type="number" min={1} max={64} value={gradAccum} onChange={(e) => setGradAccum(Number(e.target.value) || 0)} disabled={isRunning} style={{ padding: 4 }} />
          <label>4-bit 量化</label>
          <label style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <input type="checkbox" checked={use4bit} onChange={(e) => setUse4bit(e.target.checked)} disabled={isRunning} />
            <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>仅 CUDA 上有效</span>
          </label>
        </div>
      </section>

      {/* Start / Cancel buttons + status */}
      <section style={{ marginTop: 16 }}>
        <div style={{ display: "flex", gap: 8 }}>
          <button
            onClick={startTraining}
            disabled={
              isRunning || hardwareLoading
              || (mode === "sft" && selected.size === 0)
              || (mode === "dpo" && !dpoProjectId)
            }
            style={{
              padding: "8px 24px", fontSize: 14, fontWeight: "bold",
              background: isRunning ? "var(--surface-2)" : "var(--accent)",
              color: isRunning ? "var(--text-secondary)" : "white",
              border: "none", borderRadius: 4, cursor: isRunning ? "not-allowed" : "pointer",
            }}
          >
            {isRunning ? "训练中…" : (mode === "sft" ? "开始 SFT 训练" : "开始 DPO 训练")}
          </button>
          {isRunning && (
            <button
              onClick={cancelTraining}
              disabled={status.status === "cancelling"}
              style={{ padding: "8px 16px", background: "tomato", color: "white", border: "none", borderRadius: 4 }}
            >
              {status.status === "cancelling" ? "取消中…" : "取消"}
            </button>
          )}
        </div>
        {startError && <div style={{ marginTop: 8, color: "tomato" }}>{startError}</div>}

        {status.status !== "idle" && (
          <div style={{ marginTop: 12, padding: 12, background: "var(--surface-1)", borderRadius: 6, fontSize: 12 }}>
            <div><b>状态:</b> {status.status}</div>
            {status.progress && <div style={{ marginTop: 4 }}>{status.progress}</div>}
            {status.error && <div style={{ marginTop: 4, color: "tomato" }}>{status.error}</div>}
            {status.resolved_config && (
              <details style={{ marginTop: 8 }}>
                <summary style={{ cursor: "pointer", fontSize: 11 }}>实际生效的配置</summary>
                <pre style={{ background: "var(--surface-2)", padding: 8, fontSize: 10, marginTop: 4 }}>
                  {JSON.stringify(status.resolved_config, null, 2)}
                </pre>
              </details>
            )}
            {status.result && (
              <div style={{ marginTop: 12, padding: 8, background: "var(--surface-2)", borderRadius: 4 }}>
                <div style={{ fontSize: 13, fontWeight: "bold" }}>✓ 训练完成</div>
                <div style={{ marginTop: 4 }}>适配器路径: <code>{status.result.adapter_path}</code></div>
                <div>用时: {status.result.training_time_s.toFixed(1)} 秒</div>
                {status.samples_used && <div>样本数: {status.samples_used}</div>}
                <details style={{ marginTop: 4 }}>
                  <summary style={{ cursor: "pointer", fontSize: 11 }}>训练指标</summary>
                  <pre style={{ background: "var(--surface-1)", padding: 8, fontSize: 10, marginTop: 4 }}>
                    {JSON.stringify(status.result.metrics, null, 2)}
                  </pre>
                </details>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
