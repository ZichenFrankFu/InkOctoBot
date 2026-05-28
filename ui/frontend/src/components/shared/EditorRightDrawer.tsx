/**
 * EditorRightDrawer — slides in from the right inside EditorPage.
 *
 * Surfaces the spec §5 right-drawer content: Prompt Inspector, the
 * Notification feed, plus deep-links to the new Preferences / Truth /
 * Domain pages. Lets the user check "what's actually in this prompt"
 * and "what's the system been learning" without leaving the editor.
 */
import React, { useState } from "react";
import PromptInspector from "./PromptInspector";
import NotificationFeed from "./NotificationFeed";


type Tab = "prompt" | "notify" | "links";


interface Props {
  open: boolean;
  onClose: () => void;
  projectId: string;
  chapterId: string;
  chapterNum: number;
  synopsis?: string;
  characters?: string[];
  onNavigate?: (page: string) => void;
}


export default function EditorRightDrawer({
  open, onClose, projectId, chapterId, chapterNum,
  synopsis = "", characters = [], onNavigate,
}: Props) {
  const [tab, setTab] = useState<Tab>("prompt");

  if (!open) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.2)",
          zIndex: 900,
        }}
      />
      {/* Drawer */}
      <aside
        style={{
          position: "fixed", top: 0, right: 0, bottom: 0,
          width: "min(480px, 95vw)",
          background: "var(--bg-surface)",
          borderLeft: "1px solid var(--border)",
          boxShadow: "-4px 0 12px rgba(0,0,0,0.15)",
          zIndex: 901,
          display: "flex", flexDirection: "column",
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <header style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--border)",
          display: "flex", justifyContent: "space-between",
          alignItems: "center",
        }}>
          <div style={{ display: "flex", gap: 4 }}>
            {([
              { key: "prompt", label: "Prompt" },
              { key: "notify", label: "通知" },
              { key: "links", label: "更多 ↗" },
            ] as { key: Tab; label: string }[]).map(t => (
              <button
                key={t.key}
                className={tab === t.key ? "btn primary" : "btn"}
                style={{ fontSize: 12, padding: "4px 12px" }}
                onClick={() => setTab(t.key)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <button
            className="btn-sm"
            onClick={onClose}
            style={{ padding: "2px 10px" }}
            aria-label="Close"
          >
            
          </button>
        </header>

        {/* Content */}
        <div style={{ flex: 1, overflowY: "auto" }}>
          {tab === "prompt" && (
            <PromptInspector
              projectId={projectId}
              chapterId={chapterId}
              chapterNum={chapterNum}
              synopsis={synopsis}
              characters={characters}
            />
          )}

          {tab === "notify" && (
            <NotificationFeed
              projectId={projectId}
              onNavigate={onNavigate}
            />
          )}

          {tab === "links" && (
            <div style={{ padding: 14 }}>
              <h3 style={{ marginTop: 0, fontSize: 14 }}>导航</h3>
              <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                跳转到学习反馈相关页面
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 12 }}>
                {[
                  { page: "preferences",     icon: "", label: "写作偏好", desc: "你被学到的风格 / 内容 / 节奏" },
                  { page: "domain-learning", icon: "", label: "领域知识", desc: "Gate 1 + Gate 2 编译流程" },
                  { page: "truth-review",    icon: "", label: "Truth 审阅", desc: "audit_failed 章节" },
                  { page: "paste-inbox",     icon: "", label: "粘贴收件箱", desc: "Manual mode 待处理 tokens" },
                  { page: "skills",          icon: "", label: "智能体管理", desc: "skill_index" },
                  { page: "settings",        icon: "",  label: "设置", desc: "阈值 / 模型 / Embedding" },
                ].map(l => (
                  <button
                    key={l.page}
                    className="card"
                    style={{
                      padding: 10, textAlign: "left", cursor: "pointer",
                      background: "var(--bg-surface-2)",
                      border: "1px solid var(--border)", borderRadius: 4,
                    }}
                    onClick={() => onNavigate?.(l.page)}
                  >
                    <div style={{ fontSize: 13, fontWeight: 600 }}>
                      <span style={{ marginRight: 8 }}>{l.icon}</span>{l.label}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
                      {l.desc}
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
