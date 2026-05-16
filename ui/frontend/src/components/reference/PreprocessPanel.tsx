import React, { useCallback, useEffect, useState } from "react";
import { apiGet } from "../../api/client";
import { useToast } from "../shared/Toast";

interface Chapter {
  number: number;
  title: string;
  volume?: string | null;
  char_count: number;
  preview: string;
}

interface ChaptersResponse {
  chapters: Chapter[];
  total_chapters: number;
  total_chars: number;
  has_full_text: boolean;
}

function fmtChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`;
  return `${n.toLocaleString()} 字`;
}

interface Props {
  refId: string;
  hasFullText: boolean;
  onUpload: () => void;
}

/** Preprocessing tab — shows the chapter structure parsed out of the
 *  uploaded raw novel so the user can verify chapterization actually worked
 *  before kicking off feature extraction. When no text is uploaded yet, it
 *  prompts the user to upload via the parent's handler. */
export default function PreprocessPanel({ refId, hasFullText, onUpload }: Props) {
  const { toast } = useToast();
  const [data, setData] = useState<ChaptersResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState("");

  const load = useCallback(async () => {
    if (!hasFullText) { setData(null); return; }
    setLoading(true);
    try {
      const r = await apiGet<ChaptersResponse>(
        `/api/references/works/${refId}/chapters?preview_chars=160`,
      );
      setData(r);
    } catch (e: any) {
      toast(e?.message || "读取章节失败", "error");
    } finally { setLoading(false); }
  }, [refId, hasFullText, toast]);

  useEffect(() => { load(); }, [load]);

  if (!hasFullText) {
    return (
      <div className="empty-state" style={{ padding: 32, textAlign: "center" }}>
        <p style={{ marginBottom: 12 }}>本作品尚未上传正文。上传后会自动按「第 N 章」标记切分章节。</p>
        <button className="btn-primary" onClick={onUpload}>上传正文</button>
      </div>
    );
  }

  if (loading && !data) {
    return <div className="text-xs text-muted" style={{ padding: 16 }}>解析章节中...</div>;
  }

  if (!data || data.chapters.length === 0) {
    return (
      <div className="empty-state" style={{ padding: 32, textAlign: "center" }}>
        <p>未识别到任何章节。可能正文格式不规范（应包含「第 N 章」字样）。</p>
      </div>
    );
  }

  const filtered = filter.trim()
    ? data.chapters.filter(c =>
        c.title.includes(filter) ||
        c.preview.includes(filter) ||
        String(c.number).includes(filter))
    : data.chapters;

  // Group chapters by volume for a cleaner display when volumes exist
  const volumes: { name: string; chapters: Chapter[] }[] = [];
  for (const c of filtered) {
    const vname = c.volume || "";
    const last = volumes[volumes.length - 1];
    if (last && last.name === vname) last.chapters.push(c);
    else volumes.push({ name: vname, chapters: [c] });
  }
  const hasVolumes = volumes.some(v => v.name);

  const toggleExpand = (n: number) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  };

  const expandAll = () => setExpanded(new Set(data.chapters.map(c => c.number)));
  const collapseAll = () => setExpanded(new Set());

  return (
    <div className="flex flex-col gap-12">
      {/* Summary header */}
      <div style={{
        padding: 12,
        border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
        background: "var(--bg-surface)",
      }}>
        <div className="flex items-center justify-between" style={{ flexWrap: "wrap", gap: 8 }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", marginBottom: 4 }}>
              章节解析结果
            </div>
            <div className="text-xs text-muted">
              共 {data.total_chapters} 章 · {fmtChars(data.total_chars)}
              {hasVolumes && ` · ${volumes.filter(v => v.name).length} 卷`}
            </div>
          </div>
          <div className="flex gap-6">
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                    onClick={onUpload} title="重新上传正文（会覆盖现有章节解析）">
              重新上传
            </button>
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={load}>
              刷新
            </button>
          </div>
        </div>
      </div>

      {/* Filter + bulk-controls */}
      <div className="flex items-center gap-8" style={{ flexWrap: "wrap" }}>
        <input
          className="input"
          placeholder="按章节号 / 标题 / 内容搜索…"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          style={{ flex: 1, minWidth: 200, fontSize: 12 }}
        />
        <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={expandAll}>
          全部展开
        </button>
        <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={collapseAll}>
          全部收起
        </button>
      </div>

      {filtered.length === 0 && (
        <div className="text-xs text-muted" style={{ padding: 12, textAlign: "center" }}>
          没有匹配的章节。
        </div>
      )}

      {/* Volume → chapters list */}
      <div className="flex flex-col gap-10">
        {volumes.map((vol, vi) => (
          <div key={vi}>
            {vol.name && (
              <div style={{
                fontSize: 12, fontWeight: 700, color: "var(--accent)",
                padding: "6px 4px", marginBottom: 4,
                borderBottom: "1px solid var(--border)",
              }}>
                {vol.name}
                <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                  · {vol.chapters.length} 章 · {fmtChars(vol.chapters.reduce((n, c) => n + c.char_count, 0))}
                </span>
              </div>
            )}
            <div className="flex flex-col gap-4">
              {vol.chapters.map(c => {
                const isOpen = expanded.has(c.number);
                return (
                  <div key={c.number} style={{
                    border: "1px solid var(--border)", borderRadius: 4,
                    padding: "6px 10px",
                  }}>
                    <button
                      className="btn-ghost w-full"
                      onClick={() => toggleExpand(c.number)}
                      style={{
                        justifyContent: "space-between",
                        padding: 0, borderRadius: 0,
                      }}
                    >
                      <div className="flex items-center gap-8" style={{ flex: 1, minWidth: 0 }}>
                        <span className="tag" style={{
                          fontSize: 10, minWidth: 36, textAlign: "center",
                          color: "var(--text-secondary)",
                          background: "transparent",
                          border: "1px solid var(--border)",
                        }}>#{c.number}</span>
                        <div className="truncate" style={{
                          fontSize: 12, fontWeight: 500, color: "var(--text-primary)",
                          flex: 1, minWidth: 0, textAlign: "left",
                        }}>
                          {c.title}
                        </div>
                        <span className="text-xs text-muted" style={{ flexShrink: 0 }}>
                          {fmtChars(c.char_count)}
                        </span>
                      </div>
                      <span className="text-xs text-muted" style={{
                        marginLeft: 8,
                        transition: "transform 0.15s",
                        transform: isOpen ? "rotate(180deg)" : "none",
                        display: "inline-block",
                      }}>&#x25BC;</span>
                    </button>
                    {isOpen && c.preview && (
                      <div className="text-xs" style={{
                        marginTop: 6, padding: 8,
                        background: "var(--bg-surface)",
                        borderRadius: 3,
                        color: "var(--text-secondary)",
                        lineHeight: 1.65,
                        whiteSpace: "pre-wrap",
                      }}>
                        {c.preview}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
