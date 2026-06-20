/**
 * ConsistencyCheckModal — 世界书一致性检查的两种执行方式入口：
 *   1. 使用大模型 API：直接调 /api/worldbook/consistency-check
 *   2. 使用网页版：把完整 prompt 复制给用户去 ChatGPT/Claude/etc，
 *      手动把返回贴回来解析
 *
 * Prompt 包含全部条目（按分类聚合）；若整体长度超过单次粘贴的舒适
 * 区间（约 8000 字），自动分段，给出「复制本段 / 复制全部 N 段」
 * 按钮 + 段切换器，参考 AnalysisEditors 已有的 chunk 模式。
 *
 * 返回结果用 ConsistencyIssue[] 表给父页渲染。
 */
import React, { useMemo, useRef, useState } from "react";
import { apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";

export interface ConsistencyIssue {
  entry1: string;
  entry2: string;
  conflict: string;
  suggestion: string;
}

export interface WBEntryLike {
  id: string;
  title: string;
  content?: string;
  category?: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  projectId: string;
  entries: WBEntryLike[];
  /** Resolve a category key to a human label. */
  catLabel: (key?: string) => string;
  onResult: (payload: {
    issues: ConsistencyIssue[];
    message: string | null;
  }) => void;
}

/** Cap each chunk at this many characters — comfortable for a single
 *  paste into a web LLM without running into context length on the
 *  user's clipboard or the LLM's input box. */
const CHUNK_CHAR_BUDGET = 8000;

function buildEntryBlocks(entries: WBEntryLike[], catLabel: (k?: string) => string): string[] {
  return entries.map(e => {
    const cat = catLabel(e.category);
    const body = (e.content || "").trim();
    return `### [${cat}] ${e.title}\n${body || "（暂无内容）"}`;
  });
}

function chunkBlocks(header: string, blocks: string[], footer: string): string[] {
  const out: string[] = [];
  let cur = header;
  for (const b of blocks) {
    // Will the next block overflow? If so, seal the current chunk and
    // start a new one. We allow chunks larger than budget only when a
    // single entry itself is huge — splitting one entry would break
    // semantic continuity.
    if (cur.length + b.length + 2 > CHUNK_CHAR_BUDGET && cur.length > header.length) {
      out.push(cur + "\n\n" + footer);
      cur = header;
    }
    cur += "\n\n" + b;
  }
  if (cur.length > header.length) out.push(cur + "\n\n" + footer);
  return out;
}

const HEADER = `你是一名世界观设定校对编辑。请审查以下世界书条目，找出设定上互相矛盾的地方。
要求：
1. 只列出真正的逻辑/事实/时间线/权力体系/地理 等冲突，忽略风格差异。
2. 每条冲突必须指明涉及到的两个条目（标题）+ 冲突描述 + 建议的修改方向。
3. 输出严格 JSON 数组，每项 {"entry_a": "...", "entry_b": "...", "conflict": "...", "suggestion": "..."}。
4. 没有冲突就输出 [].`;

const FOOTER = `请只输出 JSON 数组，不要任何额外说明。`;

function parseLLMResponse(raw: string): ConsistencyIssue[] {
  if (!raw.trim()) return [];
  // Strip code fences if the LLM wrapped the JSON.
  let s = raw.trim();
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence) s = fence[1].trim();
  // Find the first `[` and last `]` — most LLMs sandwich prose.
  const start = s.indexOf("[");
  const end = s.lastIndexOf("]");
  if (start >= 0 && end > start) s = s.slice(start, end + 1);
  try {
    const arr = JSON.parse(s);
    if (!Array.isArray(arr)) return [];
    return arr.map((c: any) => ({
      entry1: c.entry_a || c.entry1 || (c.entries && c.entries[0]) || "",
      entry2: c.entry_b || c.entry2 || (c.entries && c.entries[1]) || "",
      conflict: c.conflict || c.description || "",
      suggestion: c.suggestion || c.fix || "",
    }));
  } catch {
    return [];
  }
}

type Phase = "picker" | "running" | "manual_pending" | "result" | "error";

export default function ConsistencyCheckModal({
  open, onClose, projectId, entries, catLabel, onResult,
}: Props) {
  const { toast } = useToast();
  const [phase, setPhase] = useState<Phase>("picker");
  const [chunkIdx, setChunkIdx] = useState(0);
  const [pasteBack, setPasteBack] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [issues, setIssues] = useState<ConsistencyIssue[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const chunks = useMemo(
    () => chunkBlocks(HEADER, buildEntryBlocks(entries, catLabel), FOOTER),
    [entries, catLabel],
  );
  const totalChars = chunks.reduce((s, c) => s + c.length, 0);

  if (!open) return null;

  const reset = () => {
    setPhase("picker");
    setChunkIdx(0);
    setPasteBack("");
    setError(null);
    setIssues([]);
    setMessage(null);
  };

  const close = () => { reset(); onClose(); };

  const copy = async (text: string, label: string) => {
    try { await navigator.clipboard.writeText(text); toast(`已复制${label}`, "success"); }
    catch { toast("复制失败（浏览器不支持）", "error"); }
  };

  const runApi = async () => {
    setPhase("running");
    setError(null);
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      const entries_text = entries.map(e =>
        `[${catLabel(e.category)}] ${e.title}: ${e.content || ""}`
      ).join("\n");
      const resp = await apiPost<{ result?: string; issues?: any[]; conflicts?: any[] }>(
        "/api/worldbook/consistency-check",
        { project_id: projectId, entries_text },
      );
      let parsed: ConsistencyIssue[] = [];
      let msg: string | null = null;
      if (resp.conflicts && resp.conflicts.length > 0) {
        parsed = resp.conflicts.map((c: any) => ({
          entry1: c.entry_a || "",
          entry2: c.entry_b || "",
          conflict: c.conflict || "",
          suggestion: c.suggestion || "",
        }));
      } else if (resp.issues && resp.issues.length > 0) {
        parsed = resp.issues.map((issue: any) => {
          if (typeof issue === "string") {
            return { entry1: "", entry2: "", conflict: issue, suggestion: "" };
          }
          return {
            entry1: issue.entry1 || (issue.entries && issue.entries[0]) || "",
            entry2: issue.entry2 || (issue.entries && issue.entries[1]) || "",
            conflict: issue.conflict || issue.description || "",
            suggestion: issue.suggestion || issue.fix || "",
          };
        });
      } else {
        msg = resp.result || "未发现明显矛盾，世界观设定一致性良好。";
      }
      setIssues(parsed);
      setMessage(msg);
      setPhase("result");
    } catch (e: any) {
      setError(e?.message || "API 调用失败");
      setPhase("error");
    }
  };

  const submitManual = () => {
    const parsed = parseLLMResponse(pasteBack);
    if (parsed.length === 0 && pasteBack.trim()) {
      // No JSON parse but text exists — surface it as a single message.
      setIssues([]);
      setMessage(pasteBack.trim().slice(0, 1000));
    } else {
      setIssues(parsed);
      setMessage(parsed.length === 0 ? "未发现明显矛盾，世界观设定一致性良好。" : null);
    }
    setPhase("result");
  };

  const commitResult = () => {
    onResult({ issues, message });
    close();
  };

  return (
    <div onClick={close} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)", zIndex: 1000,
      display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
    }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{
        width: "min(820px, 96vw)", maxHeight: "92vh", overflow: "hidden",
        background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 10,
        display: "flex", flexDirection: "column",
      }}>
        <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 15 }}>世界书一致性检查</h3>
            <span className="text-xs text-muted">
              {entries.length} 个条目 · {totalChars.toLocaleString()} 字{chunks.length > 1 ? ` · 分 ${chunks.length} 段` : ""}
            </span>
          </div>
          <button className="btn" style={{ fontSize: 12, padding: "3px 10px" }} onClick={close}>关闭</button>
        </div>

        <div className="card-body" style={{
          flex: 1, minHeight: 0, overflow: "auto",
          display: "flex", flexDirection: "column", gap: 12,
        }}>
          {phase === "picker" && (
            <>
              <div style={{ fontSize: 12, lineHeight: 1.7, color: "var(--text-secondary)" }}>
                将检查 <strong>全部 {entries.length} 个</strong>世界书条目之间的逻辑 / 时间线 / 权力体系 等冲突。
                请选择执行方式：
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 4 }}>
                <button className="btn-primary"
                  onClick={runApi}
                  disabled={entries.length === 0}
                  style={{ padding: "14px 20px", fontSize: 14, textAlign: "left" }}>
                  <div style={{ fontWeight: 600 }}>使用大模型 API 检查</div>
                  <div className="text-xs" style={{ color: "rgba(255,255,255,0.85)", marginTop: 4 }}>
                    直接调用已配置的 API → 自动解析结果。需要已配置且账户充值的模型。
                  </div>
                </button>
                <button className="btn"
                  onClick={() => setPhase("manual_pending")}
                  disabled={entries.length === 0}
                  style={{ padding: "14px 20px", fontSize: 14, textAlign: "left" }}>
                  <div style={{ fontWeight: 600 }}>使用大模型网页版检查</div>
                  <div className="text-xs" style={{ color: "var(--text-tertiary)", marginTop: 4 }}>
                    把 prompt 复制去 ChatGPT / Claude / 通义 等网页，再把返回的 JSON 贴回来 → 自动解析。
                    {chunks.length > 1 ? `prompt 较长，已自动分 ${chunks.length} 段，逐段复制即可。` : ""}
                  </div>
                </button>
              </div>
            </>
          )}

          {phase === "running" && (
            <div style={{ padding: "40px 20px", textAlign: "center" }}>
              <div className="loading-spinner" style={{ margin: "0 auto 12px" }} />
              <div style={{ fontSize: 13 }}>正在调用 API 进行一致性检查…</div>
              <button className="btn" style={{ fontSize: 11, padding: "3px 10px", marginTop: 12 }}
                onClick={() => { abortRef.current?.abort(); setPhase("picker"); }}>
                取消
              </button>
            </div>
          )}

          {phase === "error" && (
            <div style={{
              padding: 14, borderLeft: "3px solid var(--error)",
              background: "var(--bg-surface-2)", fontSize: 12, color: "var(--error)",
            }}>
              一致性检查失败：{error}
              <div style={{ marginTop: 8 }}>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={() => setPhase("picker")}>返回</button>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px", marginLeft: 6 }} onClick={runApi}>重试</button>
              </div>
            </div>
          )}

          {phase === "manual_pending" && (
            <ManualPasteFlow
              chunks={chunks}
              activeIdx={chunkIdx}
              onIdxChange={setChunkIdx}
              copy={copy}
              pasteBack={pasteBack}
              onPasteBackChange={setPasteBack}
              onCancel={() => setPhase("picker")}
              onSubmit={submitManual}
            />
          )}

          {phase === "result" && (
            <ResultPane
              issues={issues}
              message={message}
              onRedo={() => setPhase("picker")}
              onCommit={commitResult}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function ManualPasteFlow({
  chunks, activeIdx, onIdxChange, copy, pasteBack, onPasteBackChange, onCancel, onSubmit,
}: {
  chunks: string[]; activeIdx: number;
  onIdxChange: (i: number) => void;
  copy: (text: string, label: string) => void;
  pasteBack: string;
  onPasteBackChange: (v: string) => void;
  onCancel: () => void;
  onSubmit: () => void;
}) {
  const current = chunks[activeIdx] || "";
  const copyAll = () => {
    const joined = chunks.length === 1
      ? chunks[0]
      : chunks.map((c, i) => `===== 分段 ${i + 1}/${chunks.length} =====\n${c}`).join("\n\n");
    copy(joined, `${chunks.length === 1 ? "" : `全部 ${chunks.length} 段 `}prompt`);
  };

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>
          Prompt {chunks.length > 1 ? `· 第 ${activeIdx + 1} / ${chunks.length} 段` : ""}
        </span>
        {chunks.length > 1 && (
          <div style={{ display: "flex", gap: 4, marginLeft: 8 }}>
            {chunks.map((_, i) => (
              <button key={i}
                onClick={() => onIdxChange(i)}
                className={i === activeIdx ? "btn-primary" : "btn"}
                style={{ fontSize: 10, padding: "2px 8px" }}>{i + 1}</button>
            ))}
          </div>
        )}
        <div style={{ flex: 1 }} />
        <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
          onClick={() => copy(current, "本段 prompt")}>
          复制本段
        </button>
        {chunks.length > 1 && (
          <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
            onClick={copyAll}>
            复制全部 {chunks.length} 段
          </button>
        )}
      </div>
      <textarea readOnly value={current}
        style={{
          width: "100%", height: 220, padding: 10,
          fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.5,
          background: "var(--bg-surface-2)", border: "1px solid var(--border)",
          borderRadius: 6, resize: "vertical",
        }} />

      <div style={{ marginTop: 8, fontSize: 12, fontWeight: 600 }}>
        粘贴大模型返回的 JSON
      </div>
      <textarea value={pasteBack}
        onChange={e => onPasteBackChange(e.target.value)}
        placeholder='[{"entry_a":"...","entry_b":"...","conflict":"...","suggestion":"..."}, ...]'
        style={{
          width: "100%", height: 140, padding: 10,
          fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.5,
          background: "var(--bg-surface)", border: "1px solid var(--border)",
          borderRadius: 6, resize: "vertical",
        }} />

      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn" style={{ fontSize: 12, padding: "5px 14px" }} onClick={onCancel}>返回</button>
        <button className="btn-primary" style={{ fontSize: 12, padding: "5px 18px" }}
          onClick={onSubmit}
          disabled={!pasteBack.trim()}>
          解析并展示结果
        </button>
      </div>
    </>
  );
}

function ResultPane({ issues, message, onRedo, onCommit }: {
  issues: ConsistencyIssue[]; message: string | null;
  onRedo: () => void; onCommit: () => void;
}) {
  return (
    <>
      {issues.length === 0 ? (
        <div style={{
          padding: 14, borderLeft: "3px solid var(--jade)",
          background: "var(--jade-subtle)", fontSize: 13,
        }}>
          {message || "未发现明显矛盾，世界观设定一致性良好。"}
        </div>
      ) : (
        <>
          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--accent)" }}>
            发现 {issues.length} 处可能的设定冲突：
          </div>
          <div style={{ overflow: "auto", maxHeight: "50vh", border: "1px solid var(--border)", borderRadius: 6 }}>
            <table className="data-table" style={{ fontSize: 12, width: "100%" }}>
              <thead>
                <tr>
                  <th style={{ width: "16%" }}>条目 A</th>
                  <th style={{ width: "16%" }}>条目 B</th>
                  <th style={{ width: "38%" }}>冲突描述</th>
                  <th style={{ width: "30%" }}>修改建议</th>
                </tr>
              </thead>
              <tbody>
                {issues.map((it, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 500 }}>{it.entry1 || "--"}</td>
                    <td style={{ fontWeight: 500 }}>{it.entry2 || "--"}</td>
                    <td style={{ color: "var(--error)" }}>{it.conflict || "--"}</td>
                    <td style={{ color: "var(--text-secondary)" }}>{it.suggestion || "--"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn" style={{ fontSize: 12, padding: "5px 14px" }} onClick={onRedo}>重新检查</button>
        <button className="btn-primary" style={{ fontSize: 12, padding: "5px 18px" }} onClick={onCommit}>
          采纳结果并关闭
        </button>
      </div>
    </>
  );
}
