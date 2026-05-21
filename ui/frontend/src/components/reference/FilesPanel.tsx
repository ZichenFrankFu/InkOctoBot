import React, { useCallback, useEffect, useRef, useState } from "react";
import { apiGet } from "../../api/client";
import { useToast } from "../shared/Toast";
import { useDialog } from "../shared/Dialog";

interface UploadedFile {
  index: number;
  filename: string;
  char_start: number;
  char_end: number;
  char_count: number;
  uploaded_at: string | null;
  legacy: boolean;
}

interface FilesResponse {
  files: UploadedFile[];
  total_chars: number;
  has_full_text: boolean;
  file_path: string | null;
}

function fmtChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`;
  return `${n.toLocaleString()} 字`;
}

interface Props {
  refId: string;
  onAfterChange?: () => void | Promise<void>;
}

export default function FilesPanel({ refId, onAfterChange }: Props) {
  const { toast } = useToast();
  const { confirm } = useDialog();
  const [data, setData] = useState<FilesResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const replaceInputRef = useRef<HTMLInputElement | null>(null);
  const appendInputRef = useRef<HTMLInputElement | null>(null);
  const [viewer, setViewer] = useState<{ filename: string; content: string; charCount: number; loading?: boolean } | null>(null);

  const fetchFiles = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<FilesResponse>(`/api/references/works/${refId}/files`);
      setData(r);
    } catch (e: any) {
      toast(e?.message || "加载文件失败", "error");
    } finally {
      setLoading(false);
    }
  }, [refId, toast]);

  useEffect(() => { fetchFiles(); }, [fetchFiles]);

  const doUpload = async (file: File, append: boolean) => {
    if (!file.name.toLowerCase().endsWith(".txt")) {
      toast("仅支持 .txt 文件", "error");
      return;
    }
    // Replacing the text invalidates every extracted result — make the
    // user confirm before wiping the chronicle / characters / settings /
    // features and the stored chapters.
    if (!append && data && data.files.length > 0) {
      if (!(await confirm({ message:
        "重新上传会覆盖全部正文，并清除所有已提取的内容（编年史 / 角色 / 设定 / 文本特征）"
        + "以及预处理已存储的章节。此操作不可撤销，确定继续？",
        destructive: true }))) return;
    }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      if (append) fd.append("append", "true");
      const resp = await fetch(`/api/references/works/${refId}/upload`, {
        method: "POST", body: fd,
      });
      if (!resp.ok) throw new Error(await resp.text());
      toast(append ? `已追加 ${file.name}` : `已上传 ${file.name}`, "success");
      await fetchFiles();
      await onAfterChange?.();
    } catch (e: any) {
      toast(e?.message || "上传失败", "error");
    } finally {
      setUploading(false);
    }
  };

  const deleteFile = async (index: number, filename: string) => {
    if (!(await confirm({ message: `从正文中删除「${filename}」？此操作不可撤销。`, destructive: true }))) return;
    try {
      const resp = await fetch(`/api/references/works/${refId}/files/${index}`, {
        method: "DELETE",
      });
      if (!resp.ok) throw new Error(await resp.text());
      toast(`已删除「${filename}」`, "success");
      await fetchFiles();
      await onAfterChange?.();
    } catch (e: any) {
      toast(e?.message || "删除失败", "error");
    }
  };

  const clearAll = async () => {
    if (!(await confirm({ message: "清空所有上传文件？这会删除全部正文内容。", destructive: true }))) return;
    try {
      const resp = await fetch(`/api/references/works/${refId}/files`, { method: "DELETE" });
      if (!resp.ok) throw new Error(await resp.text());
      toast("已清空全部文件", "success");
      await fetchFiles();
      await onAfterChange?.();
    } catch (e: any) {
      toast(e?.message || "清空失败", "error");
    }
  };

  const openViewer = async (index: number, filename: string) => {
    setViewer({ filename, content: "", charCount: 0, loading: true });
    try {
      const r = await apiGet<{ content: string; char_count: number }>(
        `/api/references/works/${refId}/files/${index}/content`,
      );
      setViewer({ filename, content: r.content || "", charCount: r.char_count || 0, loading: false });
    } catch (e: any) {
      toast(e?.message || "加载文件内容失败", "error");
      setViewer(null);
    }
  };

  const onPickReplace = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) doUpload(f, false);
    if (replaceInputRef.current) replaceInputRef.current.value = "";
  };
  const onPickAppend = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) doUpload(f, true);
    if (appendInputRef.current) appendInputRef.current.value = "";
  };

  const hasFiles = (data?.files.length || 0) > 0;

  return (
    <div className="flex flex-col gap-12">
      {/* Toolbar */}
      <div style={{
        padding: 12,
        border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
        background: "var(--bg-surface)",
      }}>
        <div className="flex items-center justify-between" style={{ gap: 8, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
              原始文件{hasFiles ? `（共 ${data!.files.length} 个 · ${fmtChars(data!.total_chars)}）` : ""}
            </div>
          </div>
          <div className="flex gap-6" style={{ flexWrap: "wrap" }}>
            <input ref={replaceInputRef} type="file" accept=".txt" style={{ display: "none" }} onChange={onPickReplace} />
            <input ref={appendInputRef} type="file" accept=".txt" style={{ display: "none" }} onChange={onPickAppend} />
            <button className="btn-primary" style={{ fontSize: 11, padding: "4px 12px" }}
                    onClick={() => replaceInputRef.current?.click()}
                    disabled={uploading}>
              {uploading ? "上传中…" : (hasFiles ? "重新上传（覆盖全部）" : "上传文件")}
            </button>
            {hasFiles && (
              <button className="btn" style={{ fontSize: 11, padding: "4px 12px" }}
                      onClick={() => appendInputRef.current?.click()}
                      disabled={uploading}>
                追加文件
              </button>
            )}
            {hasFiles && (
              <button className="btn" style={{ fontSize: 11, padding: "4px 12px", color: "var(--error)" }}
                      onClick={clearAll}
                      disabled={uploading}>
                清空全部
              </button>
            )}
          </div>
        </div>
      </div>

      {loading && !data && (
        <div className="text-xs text-muted" style={{ padding: 16 }}>加载中…</div>
      )}

      {data && !hasFiles && (
        <div style={{
          padding: 32, textAlign: "center",
          border: "1px dashed var(--border)", borderRadius: 4,
        }}>
          <p style={{ marginBottom: 12 }}>本作品还没有上传任何文件。点击上方「上传文件」开始。</p>
        </div>
      )}

      {hasFiles && (
        <div className="flex flex-col gap-8">
          {data!.files.map(f => (
            <div key={f.index} style={{
              border: "1px solid var(--border)", borderRadius: 4,
              padding: 12, background: "var(--bg-surface)",
              display: "flex", alignItems: "center", gap: 12,
            }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="truncate" style={{
                  fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
                }}>
                  {f.filename}
                  {f.legacy && (
                    <span className="tag" style={{
                      marginLeft: 8, fontSize: 10, padding: "1px 6px",
                      color: "var(--text-tertiary)", border: "1px solid var(--border)",
                      background: "transparent",
                    }}>未跟踪</span>
                  )}
                </div>
                <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                  {fmtChars(f.char_count)}
                  {f.uploaded_at && ` · 上传于 ${f.uploaded_at}`}
                </div>
              </div>
              <button className="btn"
                      style={{ fontSize: 11, padding: "3px 10px", flexShrink: 0 }}
                      onClick={() => openViewer(f.index, f.filename)}>
                查看内容
              </button>
              <button className="btn"
                      style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)", flexShrink: 0 }}
                      onClick={() => deleteFile(f.index, f.filename)}>
                删除
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Content viewer modal — close ONLY via X / 关闭 button (no
          backdrop-click close, so accidental clicks don't lose the
          viewer state). */}
      {viewer && (
        <div style={{
          position: "fixed", inset: 0, zIndex: 1000,
          background: "rgba(0,0,0,0.55)",
          display: "flex", alignItems: "center", justifyContent: "center", padding: 20,
        }}>
          <div style={{
            width: "min(960px, 100%)", maxHeight: "90vh",
            display: "flex", flexDirection: "column",
            background: "var(--bg-app)",
            border: "1px solid var(--border)", borderRadius: 6,
          }}>
            <div style={{
              padding: "10px 14px", borderBottom: "1px solid var(--border)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                  {viewer.filename}
                </div>
                <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                  {viewer.loading ? "加载中…" : `共 ${fmtChars(viewer.charCount)}`}
                </div>
              </div>
              <button className="btn" onClick={() => setViewer(null)}>关闭</button>
            </div>
            <div style={{ padding: 14, flex: 1, overflow: "auto" }}>
              {viewer.loading ? (
                <div className="text-xs text-muted">加载文件中…</div>
              ) : (
                <pre className="font-mono" style={{
                  margin: 0, padding: 10, fontSize: 11, lineHeight: 1.65,
                  background: "var(--bg-card)", borderRadius: 3,
                  color: "var(--text-secondary)",
                  whiteSpace: "pre-wrap", wordBreak: "break-word",
                }}>{viewer.content}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
