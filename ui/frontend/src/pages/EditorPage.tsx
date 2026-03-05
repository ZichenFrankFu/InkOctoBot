import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";

/* ═══════════════════════════════════════════
   Types
   ═══════════════════════════════════════════ */
interface Chapter {
  id: string;
  title: string;
  content: string;
  createdAt: number;
  updatedAt: number;
}

interface Volume {
  id: string;
  title: string;
  chapters: Chapter[];
  collapsed: boolean;
}

/* ═══════════════════════════════════════════
   Helpers
   ═══════════════════════════════════════════ */
const uid = () => `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

function countWords(text: string): number {
  if (!text) return 0;
  // Chinese: count characters (exclude whitespace/punctuation roughly)
  const cjk = text.replace(/[\s\p{P}]/gu, "");
  return cjk.length;
}

function fmtDate(ts: number): string {
  const d = new Date(ts);
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}

/* ═══════════════════════════════════════════
   Main Component
   ═══════════════════════════════════════════ */
export default function EditorPage() {
  // ── Outline State ──
  const [volumes, setVolumes] = useState<Volume[]>(() => [
    {
      id: uid(),
      title: "第一卷",
      collapsed: false,
      chapters: [
        { id: uid(), title: "第一章", content: "", createdAt: Date.now(), updatedAt: Date.now() },
      ],
    },
  ]);
  const [activeChapterId, setActiveChapterId] = useState<string>(volumes[0].chapters[0].id);

  // ── Find active chapter ──
  const activeChapter = useMemo(() => {
    for (const v of volumes) {
      const ch = v.chapters.find((c) => c.id === activeChapterId);
      if (ch) return ch;
    }
    return null;
  }, [volumes, activeChapterId]);

  const activeVolume = useMemo(() => {
    return volumes.find((v) => v.chapters.some((c) => c.id === activeChapterId)) || null;
  }, [volumes, activeChapterId]);

  // ── Editor state ──
  const [editorContent, setEditorContent] = useState(activeChapter?.content || "");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [lastSaved, setLastSaved] = useState<number>(Date.now());
  const [showOutlineMenu, setShowOutlineMenu] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  // Sync editor content when switching chapters
  useEffect(() => {
    if (activeChapter) {
      setEditorContent(activeChapter.content);
    }
  }, [activeChapterId]);

  // Auto-save: persist content back to volumes state (debounced)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      setVolumes((prev) =>
        prev.map((v) => ({
          ...v,
          chapters: v.chapters.map((c) =>
            c.id === activeChapterId ? { ...c, content: editorContent, updatedAt: Date.now() } : c
          ),
        }))
      );
      setLastSaved(Date.now());
    }, 800);
    return () => { if (saveTimerRef.current) clearTimeout(saveTimerRef.current); };
  }, [editorContent, activeChapterId]);

  // ── Outline actions ──
  const addVolume = () => {
    const vol: Volume = { id: uid(), title: `第${volumes.length + 1}卷`, collapsed: false, chapters: [] };
    setVolumes([...volumes, vol]);
  };

  const addChapter = (volumeId: string) => {
    setVolumes((prev) =>
      prev.map((v) => {
        if (v.id !== volumeId) return v;
        const ch: Chapter = { id: uid(), title: `第${v.chapters.length + 1}章`, content: "", createdAt: Date.now(), updatedAt: Date.now() };
        return { ...v, chapters: [...v.chapters, ch] };
      })
    );
  };

  const toggleVolume = (volumeId: string) => {
    setVolumes((prev) => prev.map((v) => (v.id === volumeId ? { ...v, collapsed: !v.collapsed } : v)));
  };

  const deleteChapter = (chapterId: string) => {
    // Find all chapters flat
    const allChapters = volumes.flatMap((v) => v.chapters);
    if (allChapters.length <= 1) return; // Don't delete last chapter
    setVolumes((prev) =>
      prev.map((v) => ({ ...v, chapters: v.chapters.filter((c) => c.id !== chapterId) }))
    );
    if (activeChapterId === chapterId) {
      const remaining = allChapters.filter((c) => c.id !== chapterId);
      if (remaining.length) setActiveChapterId(remaining[0].id);
    }
  };

  const deleteVolume = (volumeId: string) => {
    if (volumes.length <= 1) return;
    const vol = volumes.find((v) => v.id === volumeId);
    if (vol && vol.chapters.some((c) => c.id === activeChapterId)) {
      const other = volumes.find((v) => v.id !== volumeId);
      if (other && other.chapters.length) setActiveChapterId(other.chapters[0].id);
    }
    setVolumes((prev) => prev.filter((v) => v.id !== volumeId));
  };

  const startRename = (id: string, currentTitle: string) => {
    setRenaming(id);
    setRenameValue(currentTitle);
    setShowOutlineMenu(null);
  };

  const commitRename = () => {
    if (!renaming || !renameValue.trim()) { setRenaming(null); return; }
    setVolumes((prev) =>
      prev.map((v) => {
        if (v.id === renaming) return { ...v, title: renameValue.trim() };
        return { ...v, chapters: v.chapters.map((c) => (c.id === renaming ? { ...c, title: renameValue.trim() } : c)) };
      })
    );
    setRenaming(null);
  };

  // ── Stats ──
  const wordCount = useMemo(() => countWords(editorContent), [editorContent]);
  const totalWords = useMemo(() => volumes.reduce((sum, v) => sum + v.chapters.reduce((s, c) => s + countWords(c.content), 0), 0), [volumes]);
  const totalChapters = useMemo(() => volumes.reduce((s, v) => s + v.chapters.length, 0), [volumes]);

  // ── Keyboard shortcuts ──
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      // Force save
      setVolumes((prev) =>
        prev.map((v) => ({ ...v, chapters: v.chapters.map((c) => (c.id === activeChapterId ? { ...c, content: editorContent, updatedAt: Date.now() } : c)) }))
      );
      setLastSaved(Date.now());
    }
  }, [editorContent, activeChapterId]);

  return (
    <div className="editor-layout" onKeyDown={handleKeyDown}>
      {/* ═══ LEFT: Chapter Tree ═══ */}
      <div className="editor-sidebar">
        <div className="editor-sidebar-header">
          <span style={{ fontFamily: "var(--font-serif)", fontWeight: 700, fontSize: 14 }}>📂 大纲</span>
          <button className="editor-icon-btn" onClick={addVolume} title="新建卷">＋</button>
        </div>
        <div className="editor-tree">
          {volumes.map((vol) => (
            <div key={vol.id}>
              <div
                className={`tree-volume ${activeVolume?.id === vol.id ? "active" : ""}`}
                onContextMenu={(e) => { e.preventDefault(); setShowOutlineMenu(vol.id); }}
              >
                <span className="tree-toggle" onClick={() => toggleVolume(vol.id)}>{vol.collapsed ? "▶" : "▼"}</span>
                {renaming === vol.id ? (
                  <input className="tree-rename-input" value={renameValue} onChange={(e) => setRenameValue(e.target.value)} onBlur={commitRename} onKeyDown={(e) => e.key === "Enter" && commitRename()} autoFocus />
                ) : (
                  <span className="tree-title" onDoubleClick={() => startRename(vol.id, vol.title)}>{vol.title}</span>
                )}
                <button className="tree-action-btn" onClick={() => addChapter(vol.id)} title="添加章节">+</button>
                {volumes.length > 1 && <button className="tree-action-btn danger" onClick={() => deleteVolume(vol.id)} title="删除卷">×</button>}
              </div>
              {!vol.collapsed && vol.chapters.map((ch) => (
                <div
                  key={ch.id}
                  className={`tree-chapter ${ch.id === activeChapterId ? "active" : ""}`}
                  onClick={() => setActiveChapterId(ch.id)}
                >
                  {renaming === ch.id ? (
                    <input className="tree-rename-input" value={renameValue} onChange={(e) => setRenameValue(e.target.value)} onBlur={commitRename} onKeyDown={(e) => e.key === "Enter" && commitRename()} autoFocus />
                  ) : (
                    <>
                      <span className="tree-chapter-title" onDoubleClick={() => startRename(ch.id, ch.title)}>{ch.title}</span>
                      <span className="tree-chapter-wc">{countWords(ch.content)}字</span>
                    </>
                  )}
                  {volumes.flatMap((v) => v.chapters).length > 1 && (
                    <button className="tree-action-btn danger small" onClick={(e) => { e.stopPropagation(); deleteChapter(ch.id); }} title="删除章节">×</button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="editor-sidebar-footer">
          <div>{totalChapters} 章 · {totalWords.toLocaleString()} 字</div>
        </div>
      </div>

      {/* ═══ CENTER: Text Editor ═══ */}
      <div className="editor-main">
        <div className="editor-main-header">
          <div>
            <h3 style={{ fontFamily: "var(--font-serif)", fontSize: 18, fontWeight: 700, marginBottom: 2 }}>
              {activeChapter?.title || "选择章节"}
            </h3>
            <span className="text-xs text-muted">
              {activeVolume?.title} · {wordCount.toLocaleString()} 字 · 自动保存于 {fmtDate(lastSaved)}
            </span>
          </div>
        </div>
        <div className="editor-textarea-wrap">
          <textarea
            ref={textareaRef}
            className="editor-textarea"
            value={editorContent}
            onChange={(e) => setEditorContent(e.target.value)}
            placeholder="在这里开始写作…&#10;&#10;提示：&#10;· Ctrl+S 手动保存&#10;· 内容会自动保存&#10;· 双击左侧目录项可重命名"
            spellCheck={false}
          />
        </div>
      </div>

      {/* ═══ RIGHT: AI Panel ═══ */}
      <div className="editor-panel">
        <div className="editor-panel-header">
          <span style={{ fontFamily: "var(--font-serif)", fontWeight: 700, fontSize: 14 }}>🤖 AI 助手</span>
        </div>

        {/* Chapter Stats */}
        <div className="panel-section">
          <div className="panel-section-title">本章统计</div>
          <div className="panel-stat-grid">
            <div className="panel-stat"><span className="panel-stat-value">{wordCount.toLocaleString()}</span><span className="panel-stat-label">字数</span></div>
            <div className="panel-stat"><span className="panel-stat-value">{editorContent.split(/\n\n+/).filter(Boolean).length}</span><span className="panel-stat-label">段落</span></div>
            <div className="panel-stat"><span className="panel-stat-value">{editorContent ? (editorContent.match(/[""「」『』]/g) || []).length / 2 | 0 : 0}</span><span className="panel-stat-label">对话</span></div>
            <div className="panel-stat"><span className="panel-stat-value">{Math.ceil(wordCount / 500)}</span><span className="panel-stat-label">预计阅读(分)</span></div>
          </div>
        </div>

        {/* AI Actions */}
        <div className="panel-section">
          <div className="panel-section-title">AI 操作</div>
          <div className="panel-actions">
            <button className="panel-action-btn primary" disabled>
              <span>✨</span> 生成本章内容
            </button>
            <button className="panel-action-btn" disabled>
              <span>📝</span> 续写下一段
            </button>
            <button className="panel-action-btn" disabled>
              <span>🔄</span> 改写选中文本
            </button>
            <button className="panel-action-btn" disabled>
              <span>📊</span> 风格分析
            </button>
          </div>
          <div className="panel-ai-notice">
            <span>🚧</span> AI 生成功能需要配置模型后启用。请前往「设置」页面配置 API Key。
          </div>
        </div>

        {/* Pipeline Preview */}
        <div className="panel-section">
          <div className="panel-section-title">Film Pipeline</div>
          <div className="pipeline-steps">
            {[
              { label: "Scene Planner", desc: "场景拆分", status: "idle" },
              { label: "Scene Director", desc: "导演指令", status: "idle" },
              { label: "Actor Agents", desc: "角色演绎", status: "idle" },
              { label: "Editor-Stylist", desc: "剪辑风格化", status: "idle" },
            ].map((step) => (
              <div className="pipeline-step" key={step.label}>
                <div className={`pipeline-step-dot ${step.status}`} />
                <div>
                  <div className="pipeline-step-label">{step.label}</div>
                  <div className="pipeline-step-desc">{step.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Quick Info */}
        <div className="panel-section">
          <div className="panel-section-title">项目总览</div>
          <div style={{ fontSize: 12, color: "var(--ink-400)", lineHeight: 1.8 }}>
            共 {volumes.length} 卷 · {totalChapters} 章<br />
            总字数: {totalWords.toLocaleString()} 字<br />
            {activeChapter && <>上次编辑: {fmtDate(activeChapter.updatedAt)}</>}
          </div>
        </div>
      </div>

      {/* ── Inline Styles (scoped to editor) ── */}
      <style>{editorStyles}</style>
    </div>
  );
}

/* ═══════════════════════════════════════════
   Editor-specific CSS (injected inline since
   we can't easily append to global.css)
   ═══════════════════════════════════════════ */
const editorStyles = `
.editor-layout {
  display: flex;
  height: calc(100vh - 0px);
  overflow: hidden;
}

/* ── Left Sidebar ── */
.editor-sidebar {
  width: 220px;
  flex-shrink: 0;
  background: var(--paper-card);
  border-right: 1px solid var(--ink-100);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.editor-sidebar-header {
  padding: 14px 14px 10px;
  border-bottom: 1px solid var(--ink-50);
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.editor-icon-btn {
  width: 26px; height: 26px; border: 1px solid var(--ink-200); border-radius: 4px;
  background: transparent; cursor: pointer; font-size: 14px; color: var(--ink-500);
  display: flex; align-items: center; justify-content: center;
  transition: all 0.15s;
}
.editor-icon-btn:hover { background: var(--ink-50); color: var(--ink-800); }
.editor-tree { flex: 1; overflow-y: auto; padding: 8px 6px; }
.tree-volume {
  display: flex; align-items: center; gap: 4px;
  padding: 6px 8px; border-radius: 4px; font-size: 13px; font-weight: 600;
  color: var(--ink-700); cursor: default; margin-bottom: 2px;
}
.tree-volume.active { background: var(--paper-warm); }
.tree-toggle { cursor: pointer; font-size: 10px; color: var(--ink-400); width: 14px; text-align: center; flex-shrink: 0; }
.tree-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree-action-btn {
  width: 20px; height: 20px; border: none; border-radius: 3px; background: transparent;
  color: var(--ink-400); font-size: 14px; cursor: pointer; display: flex; align-items: center;
  justify-content: center; opacity: 0; transition: opacity 0.15s, background 0.15s; flex-shrink: 0;
}
.tree-volume:hover .tree-action-btn,
.tree-chapter:hover .tree-action-btn { opacity: 1; }
.tree-action-btn:hover { background: var(--ink-100); color: var(--ink-700); }
.tree-action-btn.danger:hover { background: var(--accent-muted); color: var(--accent); }
.tree-action-btn.small { width: 18px; height: 18px; font-size: 12px; }
.tree-chapter {
  display: flex; align-items: center; gap: 6px;
  padding: 5px 8px 5px 28px; border-radius: 4px; font-size: 12.5px;
  color: var(--ink-600); cursor: pointer; margin-bottom: 1px; transition: background 0.15s;
}
.tree-chapter:hover { background: var(--paper-warm); }
.tree-chapter.active { background: var(--accent-muted); color: var(--accent); font-weight: 600; }
.tree-chapter-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tree-chapter-wc { font-size: 10px; color: var(--ink-300); flex-shrink: 0; font-family: var(--font-mono); }
.tree-rename-input {
  flex: 1; border: 1px solid var(--accent); border-radius: 3px; padding: 2px 6px;
  font-size: 12px; font-family: var(--font-sans); outline: none; background: white;
}
.editor-sidebar-footer {
  padding: 10px 14px; border-top: 1px solid var(--ink-50);
  font-size: 11px; color: var(--ink-400);
}

/* ── Center: Main Editor ── */
.editor-main {
  flex: 1; display: flex; flex-direction: column; overflow: hidden; background: var(--paper);
}
.editor-main-header {
  padding: 16px 28px 12px;
  border-bottom: 1px solid var(--ink-50);
  background: var(--paper-card);
}
.editor-textarea-wrap {
  flex: 1; overflow: hidden; padding: 0;
}
.editor-textarea {
  width: 100%; height: 100%; border: none; outline: none; resize: none;
  padding: 28px 48px 48px;
  font-family: var(--font-serif);
  font-size: 16px;
  line-height: 2;
  color: var(--ink-800);
  background: var(--paper);
  max-width: 780px;
  margin: 0 auto;
  display: block;
}
.editor-textarea::placeholder {
  color: var(--ink-300);
  font-style: italic;
}

/* ── Right: AI Panel ── */
.editor-panel {
  width: 260px; flex-shrink: 0;
  background: var(--paper-card);
  border-left: 1px solid var(--ink-100);
  display: flex; flex-direction: column;
  overflow-y: auto;
}
.editor-panel-header {
  padding: 14px 16px 10px;
  border-bottom: 1px solid var(--ink-50);
}
.panel-section {
  padding: 14px 16px;
  border-bottom: 1px solid var(--ink-50);
}
.panel-section-title {
  font-size: 11px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.5px; color: var(--ink-400); margin-bottom: 10px;
}
.panel-stat-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
}
.panel-stat {
  background: var(--ink-50); border-radius: 6px; padding: 10px;
  text-align: center;
}
.panel-stat-value {
  display: block; font-family: var(--font-mono); font-size: 18px;
  font-weight: 700; color: var(--ink-800); line-height: 1.2;
}
.panel-stat-label {
  display: block; font-size: 10px; color: var(--ink-400); margin-top: 2px;
}
.panel-actions {
  display: flex; flex-direction: column; gap: 6px;
}
.panel-action-btn {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 12px; border: 1px solid var(--ink-200); border-radius: 6px;
  background: transparent; color: var(--ink-600); font-family: var(--font-sans);
  font-size: 12.5px; cursor: pointer; transition: all 0.15s; text-align: left;
}
.panel-action-btn:hover:not(:disabled) { background: var(--paper-warm); border-color: var(--ink-300); }
.panel-action-btn:disabled { opacity: 0.45; cursor: not-allowed; }
.panel-action-btn.primary { background: var(--accent-muted); border-color: var(--accent); color: var(--accent); font-weight: 600; }
.panel-action-btn.primary:hover:not(:disabled) { background: var(--accent); color: white; }
.panel-ai-notice {
  margin-top: 10px; padding: 10px 12px;
  background: var(--paper-warm); border-radius: 6px;
  font-size: 11px; color: var(--ink-400); line-height: 1.5;
  display: flex; align-items: flex-start; gap: 8px;
}
.panel-ai-notice span { flex-shrink: 0; }
.pipeline-steps { display: flex; flex-direction: column; gap: 8px; }
.pipeline-step { display: flex; align-items: center; gap: 10px; }
.pipeline-step-dot {
  width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
  background: var(--ink-200);
}
.pipeline-step-dot.done { background: var(--jade); }
.pipeline-step-dot.active { background: var(--gold); animation: pulse 1.5s infinite; }
.pipeline-step-label { font-size: 12px; font-weight: 600; color: var(--ink-700); }
.pipeline-step-desc { font-size: 10px; color: var(--ink-400); }
`;