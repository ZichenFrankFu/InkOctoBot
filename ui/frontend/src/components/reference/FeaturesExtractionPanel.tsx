/* Features-tab extraction panel for the style fingerprint.
 *
 * Mirrors the chunked-extraction pattern of the chronicle/characters/
 * settings tabs, but the data here is a single per-work object — there
 * is no "merge into list", and style fingerprints are coarse enough
 * that per-chunk extraction adds little value over per-segment. So we
 * expose:
 *   - A scope selector (全书 / 各卷)
 *   - Three actions per scope:
 *       1. NLP 自动计算 — runs compute_style_fingerprint (offline, no AI tokens)
 *       2. 内置 AI 提取  — runs ai_extract_style via ModelRouter
 *       3. 复制 prompt → 解析粘贴回复 — manual web-LLM path
 *   - A diff-style preview comparing the produced fingerprint with the
 *     work's current one, plus a "保存为风格指纹" button.
 *
 * The display side stays the StyleFingerprintEditor — this panel only
 * produces a candidate fingerprint.
 */
import React, { useState } from "react";
import { apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";
import { PromptCopyPanel } from "./AnalysisEditors";
import { useSegmentation } from "./segmentationCache";

interface StyleFingerprint {
  avg_sentence_length?: number;
  dialogue_ratio?: number;
  description_density?: number;
  rhetoric_frequency?: number;
  vocab_complexity?: number;
  pacing_profile?: { fast?: number; medium?: number; slow?: number };
}

type Scope = { kind: "all" } | { kind: "segment"; index: number };

type Status = "idle" | "running" | "ready" | "failed";

interface ScopeState {
  status: Status;
  source?: "nlp" | "ai" | "paste";
  fp?: StyleFingerprint;
  error?: string;
  elapsedS?: number;
  startedAt?: number;
  pasteRaw?: string;
  pasteError?: string;
  showPaste?: boolean;
}

function scopeKey(s: Scope): string {
  return s.kind === "all" ? "all" : `seg:${s.index}`;
}

/** Lenient JSON parse mirroring the chronicle/characters parsers: strip
 *  <think>, code fences, normalize smart quotes/NBSPs, find the longest
 *  balanced object that yields a style-shaped dict. */
function parseStylePaste(raw: string): { fp: StyleFingerprint | null; error?: string } {
  let s = (raw || "").replace(/<think>[\s\S]*?<\/think>/gi, "")
                     .replace(/<\/?think>/gi, "");
  const fence = s.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (fence) s = fence[1];
  s = s.replace(/[﻿​-‍]/g, "")
       .replace(/[“”]/g, '"')
       .replace(/[‘’]/g, "'")
       .replace(/ /g, " ")
       .trim();
  if (!s) return { fp: null, error: "请先粘贴 LLM 返回的 JSON" };

  const balancedExtract = (start: number): string | null => {
    if (s[start] !== "{") return null;
    let depth = 0, inString = false, escape = false;
    for (let i = start; i < s.length; i++) {
      const c = s[i];
      if (escape) { escape = false; continue; }
      if (c === "\\") { escape = true; continue; }
      if (c === '"') { inString = !inString; continue; }
      if (inString) continue;
      if (c === "{") depth++;
      else if (c === "}") {
        depth--;
        if (depth === 0) return s.slice(start, i + 1);
      }
    }
    return null;
  };
  // Same unescaped-inner-quote repair as the chronicle paste parser:
  // Claude.ai's copy button drops the backslash from \" inside string
  // values, so we re-escape any " that isn't followed by , } ] : or EOF.
  const repair = (input: string): string => {
    const out: string[] = [];
    let inString = false, escape = false;
    for (let i = 0; i < input.length; i++) {
      const c = input[i];
      if (escape) { out.push(c); escape = false; continue; }
      if (c === "\\") { out.push(c); escape = true; continue; }
      if (c !== '"') { out.push(c); continue; }
      if (!inString) { out.push(c); inString = true; continue; }
      let j = i + 1;
      while (j < input.length && /\s/.test(input[j])) j++;
      const nxt = j < input.length ? input[j] : "";
      if (nxt === "" || nxt === "," || nxt === "}" || nxt === "]" || nxt === ":") {
        out.push(c); inString = false;
      } else {
        out.push('\\"');
      }
    }
    return out.join("");
  };
  const isFingerprint = (obj: any): boolean =>
    obj && typeof obj === "object" && (
      "avg_sentence_length" in obj || "dialogue_ratio" in obj ||
      "description_density" in obj || "pacing_profile" in obj
    );
  let lastErr = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] !== "{") continue;
    const ext = balancedExtract(i);
    if (!ext) continue;
    try {
      const obj = JSON.parse(ext);
      if (isFingerprint(obj)) return { fp: obj as StyleFingerprint };
    } catch (e: any) {
      lastErr = e?.message || "";
      const repaired = repair(ext);
      if (repaired !== ext) {
        try {
          const obj = JSON.parse(repaired);
          if (isFingerprint(obj)) return { fp: obj as StyleFingerprint };
        } catch { /* fall through */ }
      }
    }
  }
  return {
    fp: null,
    error: lastErr
      ? `JSON 解析失败：${lastErr.slice(0, 120)}`
      : "在 JSON 中没找到风格指纹字段（需含 avg_sentence_length / dialogue_ratio 等）。",
  };
}

function fmtPct(v: number | undefined, digits = 1): string {
  if (typeof v !== "number") return "—";
  return `${(v * 100).toFixed(digits)}%`;
}
function fmtNum(v: number | undefined, digits = 2): string {
  if (typeof v !== "number") return "—";
  return v.toFixed(digits);
}

/** Read-only side-by-side comparison of the existing fingerprint vs.
 *  the newly-produced one, so the user can tell at a glance how the
 *  numbers differ before clicking 保存. */
function StyleDiffPreview({ current, next }: {
  current: StyleFingerprint | null;
  next: StyleFingerprint;
}) {
  const cp = current?.pacing_profile || {};
  const np = next.pacing_profile || {};
  const row = (label: string, cur: any, nxt: any, fmt: (v: any) => string) => {
    const same = fmt(cur) === fmt(nxt);
    return (
      <div className="flex" key={label} style={{
        fontSize: 11, padding: "3px 0",
        borderBottom: "1px dashed var(--border)",
      }}>
        <span style={{ flex: 1, color: "var(--text-secondary)" }}>{label}</span>
        <span style={{ width: 80, textAlign: "right", color: "var(--text-tertiary)", fontFamily: "var(--font-mono)" }}>
          {fmt(cur)}
        </span>
        <span style={{ width: 20, textAlign: "center", color: "var(--text-tertiary)" }}>→</span>
        <span style={{
          width: 80, textAlign: "right", fontFamily: "var(--font-mono)",
          fontWeight: same ? 400 : 700,
          color: same ? "var(--text-secondary)" : "var(--accent)",
        }}>
          {fmt(nxt)}
        </span>
      </div>
    );
  };
  return (
    <div style={{
      marginTop: 8, padding: "6px 12px",
      border: "1px solid var(--border)", borderRadius: 4,
      background: "var(--bg-surface)",
    }}>
      <div className="flex" style={{
        fontSize: 10, color: "var(--text-tertiary)", padding: "2px 0",
      }}>
        <span style={{ flex: 1 }}>指标</span>
        <span style={{ width: 80, textAlign: "right" }}>当前</span>
        <span style={{ width: 20 }} />
        <span style={{ width: 80, textAlign: "right" }}>提取结果</span>
      </div>
      {row("平均句长 (字)", current?.avg_sentence_length, next.avg_sentence_length, v => fmtNum(v, 1))}
      {row("对话占比", current?.dialogue_ratio, next.dialogue_ratio, v => fmtPct(v))}
      {row("描写密度", current?.description_density, next.description_density, v => fmtPct(v))}
      {row("修辞频率 (每千字)", current?.rhetoric_frequency, next.rhetoric_frequency, v => fmtNum(v, 2))}
      {row("词汇丰富度", current?.vocab_complexity, next.vocab_complexity, v => fmtPct(v))}
      {row("节奏 · 快", cp.fast, np.fast, v => fmtPct(v))}
      {row("节奏 · 中", cp.medium, np.medium, v => fmtPct(v))}
      {row("节奏 · 慢", cp.slow, np.slow, v => fmtPct(v))}
    </div>
  );
}

export function FeaturesExtractionPanel({
  refId, hasFullText, current, onSave,
}: {
  refId: string;
  hasFullText: boolean;
  current: StyleFingerprint | null;
  onSave: (next: StyleFingerprint) => Promise<void> | void;
}) {
  const { toast } = useToast();
  const { plan } = useSegmentation(refId, hasFullText);
  const [scope, setScope] = useState<Scope>({ kind: "all" });
  // Per-scope state; the key is the same as scopeKey() so switching
  // scopes preserves a result the user already produced.
  const [byScope, setByScope] = useState<Record<string, ScopeState>>({});
  const [saving, setSaving] = useState(false);

  const k = scopeKey(scope);
  const state: ScopeState = byScope[k] || { status: "idle" };
  const patch = (next: Partial<ScopeState>) =>
    setByScope(prev => ({ ...prev, [k]: { ...(prev[k] || { status: "idle" }), ...next } }));

  const segIdx = scope.kind === "segment" ? scope.index : undefined;

  const run = async (useAi: boolean) => {
    patch({ status: "running", error: undefined, fp: undefined, startedAt: Date.now() });
    try {
      const r = await apiPost<{
        style_fingerprint: StyleFingerprint;
        elapsed_s: number;
        errors: string[];
      }>(
        `/api/references/works/${refId}/style/extract`,
        {
          segment_index: segIdx ?? null,
          use_ai: useAi,
        },
        { timeoutMs: useAi ? 600_000 : 120_000 },
      );
      if (r.errors && r.errors.length > 0) {
        patch({ status: "failed", error: r.errors.join("; "), elapsedS: r.elapsed_s });
      } else if (!r.style_fingerprint || Object.keys(r.style_fingerprint).length === 0) {
        patch({ status: "failed", error: "未返回任何风格指纹字段", elapsedS: r.elapsed_s });
      } else {
        patch({
          status: "ready", source: useAi ? "ai" : "nlp",
          fp: r.style_fingerprint, elapsedS: r.elapsed_s,
        });
      }
    } catch (e: any) {
      patch({ status: "failed", error: e?.message || (useAi ? "AI 提取失败" : "NLP 计算失败") });
    }
  };

  const parsePaste = () => {
    const { fp, error } = parseStylePaste(state.pasteRaw || "");
    if (error || !fp) {
      patch({ pasteError: error || "解析失败" });
      return;
    }
    patch({ status: "ready", source: "paste", fp, pasteError: undefined, pasteRaw: undefined });
  };

  const save = async () => {
    if (!state.fp) return;
    setSaving(true);
    try {
      await onSave(state.fp);
      toast(`已保存${state.source === "nlp" ? " · NLP 计算" : state.source === "ai" ? " · 内置 AI" : " · 粘贴解析"}`, "success");
      patch({ status: "idle", fp: undefined, source: undefined });
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    } finally {
      setSaving(false);
    }
  };

  const reset = () => patch({
    status: "idle", fp: undefined, source: undefined,
    error: undefined, pasteRaw: undefined, pasteError: undefined, showPaste: false,
  });

  if (!hasFullText) {
    return (
      <div className="text-xs text-muted" style={{ padding: 12 }}>
        上传正文后才能提取风格指纹。
      </div>
    );
  }

  const segments = plan?.segments || [];
  const running = state.status === "running";
  const ready = state.status === "ready";
  const failed = state.status === "failed";

  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
      padding: 12, background: "var(--bg-surface)",
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>
        风格指纹提取
      </div>

      {/* Scope selector */}
      <div className="flex items-center" style={{ gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
        <span className="text-xs text-muted" style={{ marginRight: 4 }}>范围:</span>
        <ScopeChip label="全书" active={scope.kind === "all"}
                   onClick={() => setScope({ kind: "all" })}
                   sub={plan ? `${plan.total_chapters} 章` : ""} />
        {segments.map(seg => (
          <ScopeChip key={seg.index}
                     label={seg.title || `第 ${seg.index + 1} 卷`}
                     active={scope.kind === "segment" && scope.index === seg.index}
                     onClick={() => setScope({ kind: "segment", index: seg.index })}
                     sub={`第 ${seg.start_chapter}–${seg.end_chapter} 章`} />
        ))}
      </div>

      {/* Action buttons */}
      <div className="flex items-center" style={{ gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <button className="btn-primary"
                onClick={() => run(false)}
                disabled={running || saving}
                title="本地 jieba + 正则计算，离线、不消耗 AI 额度">
          {running && state.source !== "ai" ? "NLP 计算中…" : "NLP 自动计算"}
        </button>
        <button className="btn"
                onClick={() => run(true)}
                disabled={running || saving}
                title="调用内置 LLM 让模型给出整体语感判断">
          {running && state.source === "ai" ? "AI 提取中…" : "内置 AI 提取"}
        </button>
        <button className="btn"
                onClick={() => patch({ showPaste: !state.showPaste })}
                disabled={running}>
          {state.showPaste ? "收起手动粘贴" : "复制 prompt 网页提取"}
        </button>
        {(ready || failed) && (
          <button className="btn-ghost"
                  onClick={reset}
                  style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            重置
          </button>
        )}
        {state.elapsedS != null && (
          <span className="text-xs text-muted">用时 {state.elapsedS}s</span>
        )}
      </div>

      {/* Manual paste mode */}
      {state.showPaste && (
        <div style={{ marginBottom: 10 }}>
          {segIdx != null ? (
            <PromptCopyPanel refId={refId} promptKey="reference.style"
                             segmentIndex={segIdx} chunked={false}
                             defaultOpen
                             label={`第 ${segIdx + 1} 卷的 prompt（含整卷正文）`} />
          ) : (
            <div className="text-xs text-muted" style={{
              padding: "6px 10px", marginBottom: 6,
              border: "1px dashed var(--border)", borderRadius: 4,
            }}>
              「全书」范围的 prompt 含全书正文，通常超出网页 LLM 的输入限制。建议挑一卷再用 prompt 模式，或直接用上面的 NLP / 内置 AI。
            </div>
          )}
          <textarea className="input font-mono"
                    rows={5}
                    value={state.pasteRaw || ""}
                    placeholder='将 LLM 返回的 {"avg_sentence_length": ..., "pacing_profile": {...}} 粘贴到这里'
                    onChange={e => patch({ pasteRaw: e.target.value, pasteError: undefined })}
                    style={{
                      fontSize: 11, lineHeight: 1.5, resize: "vertical",
                      background: "var(--bg-app)", marginBottom: 6,
                    }} />
          <div className="flex items-center" style={{ gap: 6 }}>
            <button className="btn-primary"
                    onClick={parsePaste}
                    disabled={!(state.pasteRaw && state.pasteRaw.trim())}
                    style={{ fontSize: 11, padding: "3px 12px" }}>
              解析并预览
            </button>
            {state.pasteError && (
              <span className="text-xs" style={{ color: "var(--error)", flex: 1 }}>
                {state.pasteError}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Errors */}
      {failed && state.error && (
        <div style={{
          padding: "6px 10px", marginBottom: 8,
          background: "var(--bg-card)", border: "1px solid var(--error)",
          borderRadius: 3, fontSize: 11, color: "var(--error)", lineHeight: 1.5,
        }}>
          <div style={{ fontWeight: 600, marginBottom: 2 }}>提取失败</div>
          <div style={{ wordBreak: "break-word" }}>{state.error}</div>
        </div>
      )}

      {/* Result preview + save */}
      {ready && state.fp && (
        <>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 6 }}>
            来源：{state.source === "nlp" ? "NLP 本地计算" : state.source === "ai" ? "内置 AI" : "网页 LLM（粘贴）"}
            {" · "}范围：{scope.kind === "all" ? "全书" : (segments[scope.index]?.title || `第 ${scope.index + 1} 卷`)}
          </div>
          <StyleDiffPreview current={current} next={state.fp} />
          <div className="flex" style={{
            justifyContent: "flex-end", gap: 6, marginTop: 8,
          }}>
            <button className="btn-primary"
                    onClick={save} disabled={saving}
                    style={{ fontSize: 12, padding: "4px 14px" }}>
              {saving ? "保存中…" : "保存为风格指纹"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

function ScopeChip({ label, sub, active, onClick }: {
  label: string;
  sub?: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button onClick={onClick}
            className="btn-ghost"
            style={{
              padding: "3px 10px",
              border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
              background: active ? "var(--accent-subtle)" : "transparent",
              color: active ? "var(--accent)" : "var(--text-secondary)",
              borderRadius: 14,
              fontSize: 11,
              display: "flex", alignItems: "center", gap: 6,
            }}>
      <span style={{ fontWeight: active ? 600 : 500 }}>{label}</span>
      {sub && <span className="text-xs text-muted" style={{ fontSize: 10 }}>{sub}</span>}
    </button>
  );
}
