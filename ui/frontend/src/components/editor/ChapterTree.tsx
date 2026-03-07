import React, { useState } from "react";
import type { Volume } from "../../api/types";

interface Props {
  volumes: Volume[];
  activeChapterId: string;
  onSelectChapter: (id: string) => void;
  onAddVolume: () => void;
  onAddChapter: (volumeId: string) => void;
  onRename: (type: "volume" | "chapter", id: string, name: string) => void;
}

export default function ChapterTree({
  volumes,
  activeChapterId,
  onSelectChapter,
  onAddVolume,
  onAddChapter,
  onRename,
}: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");

  const toggleCollapse = (volumeId: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(volumeId)) next.delete(volumeId);
      else next.add(volumeId);
      return next;
    });
  };

  const startRename = (type: "volume" | "chapter", id: string, currentName: string) => {
    setEditingId(`${type}:${id}`);
    setEditText(currentName);
  };

  const commitRename = (type: "volume" | "chapter", id: string) => {
    if (editText.trim()) {
      onRename(type, id, editText.trim());
    }
    setEditingId(null);
    setEditText("");
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ flex: 1, overflowY: "auto" }}>
        {volumes.map((vol) => (
          <div key={vol.id}>
            {/* Volume header */}
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 6,
                padding: "8px 12px",
                background: "var(--bg-tertiary)",
                cursor: "pointer",
                userSelect: "none",
                borderBottom: "1px solid var(--border)",
              }}
              onClick={() => toggleCollapse(vol.id)}
            >
              <span
                style={{
                  fontSize: 10,
                  transition: "transform 0.15s",
                  transform: collapsed.has(vol.id) ? "rotate(-90deg)" : "rotate(0deg)",
                }}
              >
                {"\u25BC"}
              </span>

              {editingId === `volume:${vol.id}` ? (
                <input
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  onBlur={() => commitRename("volume", vol.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitRename("volume", vol.id);
                    if (e.key === "Escape") setEditingId(null);
                  }}
                  onClick={(e) => e.stopPropagation()}
                  autoFocus
                  style={{
                    flex: 1,
                    padding: "2px 6px",
                    border: "1px solid var(--accent)",
                    borderRadius: 3,
                    fontSize: 12,
                    background: "var(--bg-secondary)",
                    color: "var(--text-primary)",
                    outline: "none",
                  }}
                />
              ) : (
                <span
                  style={{ flex: 1, fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}
                  onDoubleClick={(e) => {
                    e.stopPropagation();
                    startRename("volume", vol.id, vol.title);
                  }}
                >
                  {vol.title}
                </span>
              )}

              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onAddChapter(vol.id);
                }}
                style={{
                  background: "none",
                  border: "none",
                  color: "var(--text-secondary)",
                  cursor: "pointer",
                  fontSize: 14,
                  padding: "0 4px",
                  lineHeight: 1,
                }}
                title="Add chapter"
              >
                +
              </button>
            </div>

            {/* Chapter items */}
            {!collapsed.has(vol.id) &&
              vol.chapters.map((ch) => (
                <div
                  key={ch.id}
                  className="chapter-tree-item"
                  onClick={() => onSelectChapter(ch.id)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                    padding: "7px 12px 7px 28px",
                    cursor: "pointer",
                    fontSize: 13,
                    color: activeChapterId === ch.id ? "var(--accent)" : "var(--text-primary)",
                    background: activeChapterId === ch.id ? "var(--accent-muted)" : "transparent",
                    borderLeft: activeChapterId === ch.id ? "2px solid var(--accent)" : "2px solid transparent",
                    transition: "background 0.15s",
                  }}
                >
                  {editingId === `chapter:${ch.id}` ? (
                    <input
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      onBlur={() => commitRename("chapter", ch.id)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitRename("chapter", ch.id);
                        if (e.key === "Escape") setEditingId(null);
                      }}
                      onClick={(e) => e.stopPropagation()}
                      autoFocus
                      style={{
                        flex: 1,
                        padding: "2px 6px",
                        border: "1px solid var(--accent)",
                        borderRadius: 3,
                        fontSize: 12,
                        background: "var(--bg-secondary)",
                        color: "var(--text-primary)",
                        outline: "none",
                      }}
                    />
                  ) : (
                    <span
                      style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      onDoubleClick={(e) => {
                        e.stopPropagation();
                        startRename("chapter", ch.id, ch.title);
                      }}
                    >
                      {ch.title}
                    </span>
                  )}

                  {ch.word_count != null && (
                    <span style={{ fontSize: 10, color: "var(--text-secondary)", flexShrink: 0 }}>
                      {ch.word_count.toLocaleString()}
                    </span>
                  )}

                  {ch.status && (
                    <span
                      style={{
                        fontSize: 9,
                        padding: "1px 6px",
                        borderRadius: 8,
                        background:
                          ch.status === "final"
                            ? "var(--accent-muted)"
                            : ch.status === "generating"
                            ? "rgba(250, 204, 21, 0.15)"
                            : "var(--bg-tertiary)",
                        color:
                          ch.status === "final"
                            ? "var(--accent)"
                            : ch.status === "generating"
                            ? "#facc15"
                            : "var(--text-secondary)",
                        flexShrink: 0,
                      }}
                    >
                      {ch.status}
                    </span>
                  )}
                </div>
              ))}
          </div>
        ))}
      </div>

      {/* Bottom actions */}
      <div
        style={{
          padding: "10px 12px",
          borderTop: "1px solid var(--border)",
          display: "flex",
          gap: 8,
        }}
      >
        <button
          className="btn-secondary"
          onClick={onAddVolume}
          style={{ flex: 1, fontSize: 12, padding: "6px 12px" }}
        >
          + Add Volume
        </button>
      </div>
    </div>
  );
}
