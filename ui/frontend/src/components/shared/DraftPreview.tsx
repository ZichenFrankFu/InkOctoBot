/**
 * DraftPreview — a wrapping save-button with content gating (P0).
 *
 * Drop-in replacement anywhere code currently does
 * ``apiPost('/api/editor/save-version', {text, ...})``. Forces the
 * user through a preview + word-count gate before the write, which
 * is what would have caught the recent "saved 0-char text_version"
 * incident.
 *
 * Usage:
 *
 *     <DraftPreview
 *        chapterId={ch.id}
 *        projectId={pid}
 *        draftText={editorText}
 *        source="ai"                  // ai | user_edit | rewrite | web_llm
 *        modelUsed="qwen2.5:14b"
 *        minChars={50}
 *        onSaved={v => setActiveVersion(v)}
 *     />
 */
import React, { useState } from "react";
import { apiPost } from "../../api/client";
import { useToast } from "./Toast";


export type DraftSource = "ai" | "user_edit" | "rewrite" | "web_llm";


const SOURCE_LABEL: Record<DraftSource, string> = {
  ai:        "AI 生成",
  user_edit: "用户编辑",
  rewrite:   "重写",
  web_llm:   "网页 LLM 粘回",
};


// The backend's text_versions.source CHECK constraint accepts only
// these three values; ``web_llm`` collapses to ``user_edit`` for
// learning-purpose semantics (it IS the user's chosen content).
function backendSource(s: DraftSource): "ai" | "user_edit" | "rewrite" {
  if (s === "ai") return "ai";
  if (s === "rewrite") return "rewrite";
  return "user_edit";
}


export interface DraftPreviewProps {
  chapterId: string;
  projectId: string;
  draftText: string;
  source: DraftSource;
  modelUsed?: string;
  minChars?: number;
  targetChars?: number;
  onSaved?: (saved: { version_id?: string; version_num?: number }) => void;
  // Render compact (inline button only) — use when the caller already
  // shows the draft text elsewhere.
  compact?: boolean;
}


export default function DraftPreview({
  chapterId, projectId, draftText, source,
  modelUsed = "", minChars = 50, targetChars = 2000,
  onSaved, compact = false,
}: DraftPreviewProps) {
  const { toast } = useToast();
  const [saving, setSaving] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const chars = (draftText || "").trim().length;
  const tooShort = chars < minChars;

  const save = async (force: boolean) => {
    if (!chapterId) { toast("无 chapter_id，无法保存", "error"); return; }
    if (!projectId) { toast("无 project_id，无法保存", "error"); return; }
    if (tooShort && !force) {
      setConfirmOpen(true);
      return;
    }
    setSaving(true);
    try {
      const saved = await apiPost<{ status: string; version: any }>(
        "/api/editor/save-version",
        {
          project_id: projectId,
          chapter_id: chapterId,
          text: draftText,
          source: backendSource(source),
          model_used: modelUsed || `draft.${source}`,
        },
      );
      toast(`已保存版本 v${saved.version?.version_num} (${chars} 字)`, "success");
      onSaved?.(saved.version || {});
      setConfirmOpen(false);
    } catch (e: any) {
      toast(`保存失败: ${e.message}`, "error");
    } finally {
      setSaving(false);
    }
  };

  if (compact) {
    return (
      <>
        <button
          className={tooShort ? "btn warn" : "btn primary"}
          onClick={() => save(false)}
          disabled={saving || chars === 0}
          title={tooShort ? `内容很短 (${chars} < ${minChars} 字)` : ""}
        >
          {saving ? "保存中..." : `保存版本 · ${SOURCE_LABEL[source]} · ${chars} 字`}
        </button>
        {confirmOpen && (
          <ConfirmTooShort
            chars={chars}
            minChars={minChars}
            onCancel={() => setConfirmOpen(false)}
            onConfirm={() => save(true)}
          />
        )}
      </>
    );
  }

  return (
    <div className="card" style={{ padding: 12, marginTop: 12 }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 8,
      }}>
        <div style={{ fontSize: 13 }}>
          <strong>来源:</strong>{" "}
          <span style={{ color: "var(--accent)" }}>{SOURCE_LABEL[source]}</span>
          {modelUsed && <span style={{ color: "var(--text-tertiary)" }}> · {modelUsed}</span>}
        </div>
        <div style={{ fontSize: 12, color: tooShort ? "var(--danger)" : "var(--text-tertiary)" }}>
          {chars} / {targetChars} 字
          {tooShort && <span> · ⚠️ 太短</span>}
        </div>
      </div>

      <pre style={{
        background: "var(--bg-surface-2)",
        padding: 10, fontSize: 12, borderRadius: 4,
        maxHeight: 240, overflow: "auto",
        whiteSpace: "pre-wrap", wordBreak: "break-word",
        fontFamily: "var(--font-mono)",
      }}>
        {draftText || "（草稿为空，无法保存）"}
      </pre>

      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
        <button
          className="btn primary"
          onClick={() => save(false)}
          disabled={saving || chars === 0}
        >
          {saving ? "保存中..." : "保存为新版本"}
        </button>
      </div>

      {confirmOpen && (
        <ConfirmTooShort
          chars={chars}
          minChars={minChars}
          onCancel={() => setConfirmOpen(false)}
          onConfirm={() => save(true)}
        />
      )}
    </div>
  );
}


function ConfirmTooShort({
  chars, minChars, onCancel, onConfirm,
}: {
  chars: number; minChars: number;
  onCancel: () => void; onConfirm: () => void;
}) {
  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onCancel}
    >
      <div
        className="card"
        style={{ padding: 20, maxWidth: 420, background: "var(--bg-surface)" }}
        onClick={e => e.stopPropagation()}
      >
        <h3 style={{ marginTop: 0 }}>⚠️ 内容看起来是空的或很短</h3>
        <p>
          只有 <strong>{chars}</strong> 字（最小推荐 {minChars} 字）。
          这是过去几次"text_versions 0 字符"灾难的根因，绝大多数情况下是：
        </p>
        <ul style={{ paddingLeft: 20 }}>
          <li>LLM 调用其实没返回内容</li>
          <li>粘贴到了错误的输入框</li>
          <li>编辑器内容被清空后误点了保存</li>
        </ul>
        <p>仍然要保存吗？</p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="btn" onClick={onCancel}>取消（推荐）</button>
          <button className="btn danger" onClick={onConfirm}>强制保存</button>
        </div>
      </div>
    </div>
  );
}
