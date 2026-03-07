import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { apiGet, apiPost, apiPut } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import type { Volume, ChapterOutline, PipelineStatus, EvalResult } from "../api/types";

const uid = () => `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
const wc = (t: string) => (t ? t.replace(/[\s\p{P}]/gu, "").length : 0);

const PIPELINE_STEPS: PipelineStatus[] = [
  { step: "Scene Director", status: "pending", detail: "将大纲拆为场景并注入导演指令" },
  { step: "Actor Agents", status: "pending", detail: "角色扮演生成原始对话与内心" },
  { step: "Editor-Writer", status: "pending", detail: "剪辑+文学风格化，~600字/段输出" },
  { step: "Evaluator", status: "pending", detail: "一致性检查 & 质量评估" },
];

interface LocalVolume extends Volume {
  collapsed?: boolean;
}

export default function EditorPage({ projectId }: { projectId: string }) {
  // --- State ---
  const [volumes, setVolumes] = useState<LocalVolume[]>([]);
  const [activeChId, setActiveChId] = useState<string>("");
  const [content, setContent] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"saved" | "saving" | "unsaved">("saved");
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [startTime] = useState(Date.now());
  const [elapsed, setElapsed] = useState(0);

  // Rename
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState("");

  // Chapter title editing
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleVal, setTitleVal] = useState("");

  // AI Panel
  const [aiTab, setAiTab] = useState<"outline" | "inspire" | "rewrite" | "eval">("outline");
  const [selection, setSelection] = useState<{ start: number; end: number; text: string } | null>(null);
  const [rewritePrompt, setRewritePrompt] = useState("");
  const [rewriteModel, setRewriteModel] = useState("default");
  const [pipelineSteps, setPipelineSteps] = useState<PipelineStatus[]>(PIPELINE_STEPS);
  const [generating, setGenerating] = useState(false);
  const [evalResult, setEvalResult] = useState<EvalResult | null>(null);

  const textRef = useRef<HTMLTextAreaElement>(null);

  // --- Resizable panels ---
  const leftPanel = useResizable({ direction: "horizontal", initialSize: 220, minSize: 160, maxSize: 350 });
  const rightPanel = useResizable({ direction: "horizontal", initialSize: 300, minSize: 200, maxSize: 500 });

  // --- Load data ---
  useEffect(() => {
    const pid = projectId || "default";
    Promise.all([
      apiGet<{ items: LocalVolume[] }>(`/api/data/projects/${pid}/volumes`).catch(() => ({ items: [] })),
    ]).then(([volData]) => {
      let vols = volData.items || [];
      if (vols.length === 0) {
        const ch: ChapterOutline = { id: uid(), volume_id: "v1", title: "第一章", order: 1, synopsis: "", content: "", word_count: 0 };
        vols = [{ id: "v1", project_id: pid, title: "第一卷", order: 1, chapters: [ch] }];
      }
      setVolumes(vols);
      const firstCh = vols[0]?.chapters?.[0];
      if (firstCh) {
        setActiveChId(firstCh.id);
        setContent(firstCh.content || "");
        setTitleVal(firstCh.title);
      }
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, [projectId]);

  // --- Elapsed timer ---
  useEffect(() => {
    const iv = setInterval(() => setElapsed(Math.floor((Date.now() - startTime) / 60000)), 30000);
    return () => clearInterval(iv);
  }, [startTime]);

  // --- Active chapter & volume ---
  const activeCh = useMemo(() => {
    for (const v of volumes) {
      const c = v.chapters.find(c => c.id === activeChId);
      if (c) return c;
    }
    return null;
  }, [volumes, activeChId]);

  const activeVol = useMemo(
    () => volumes.find(v => v.chapters.some(c => c.id === activeChId)) || null,
    [volumes, activeChId]
  );

  // --- Switch chapter ---
  useEffect(() => {
    if (activeCh && loaded) {
      setContent(activeCh.content || "");
      setTitleVal(activeCh.title);
    }
    setEditingTitle(false);
    setSelection(null);
  }, [activeChId, loaded]);

  // --- Auto-save with debounce ---
  useEffect(() => {
    if (!loaded) return;
    setSaveStatus("unsaved");
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      setSaveStatus("saving");
      const updated = volumes.map(v => ({
        ...v,
        chapters: v.chapters.map(c =>
          c.id === activeChId ? { ...c, content, title: titleVal || c.title, word_count: wc(content) } : c
        ),
      }));
      setVolumes(updated);
      try {
        await apiPut(`/api/data/projects/${projectId || "default"}/chapters/${activeChId}`, {
          content,
          title: titleVal || activeCh?.title,
          word_count: wc(content),
        });
        setSaveStatus("saved");
      } catch {
        setSaveStatus("unsaved");
      }
    }, 1500);
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [content, titleVal]);

  // --- Text selection ---
  const handleMouseUp = () => {
    const el = textRef.current;
    if (!el) return;
    setTimeout(() => {
      const s = el.selectionStart;
      const e = el.selectionEnd;
      if (s !== undefined && e !== undefined && e > s) {
        const txt = content.substring(s, e);
        if (txt.trim().length > 0) {
          setSelection({ start: s, end: e, text: txt });
          return;
        }
      }
      setSelection(null);
    }, 10);
  };

  // --- Volume / chapter operations ---
  const addVolume = () => {
    const vol: LocalVolume = {
      id: uid(), project_id: projectId, title: `第${volumes.length + 1}卷`,
      order: volumes.length + 1, chapters: [], collapsed: false,
    };
    setVolumes([...volumes, vol]);
    apiPost(`/api/data/projects/${projectId}/volumes`, { title: vol.title, order: vol.order }).catch(console.error);
  };

  const addChapter = (volId: string) => {
    const vol = volumes.find(v => v.id === volId);
    if (!vol) return;
    const ch: ChapterOutline = {
      id: uid(), volume_id: volId, title: `第${vol.chapters.length + 1}章`,
      order: vol.chapters.length + 1, synopsis: "", content: "", word_count: 0,
    };
    setVolumes(volumes.map(v => v.id === volId ? { ...v, chapters: [...v.chapters, ch] } : v));
  };

  const deleteChapter = (chId: string) => {
    const allChs = volumes.flatMap(v => v.chapters);
    if (allChs.length <= 1) return;
    setVolumes(volumes.map(v => ({ ...v, chapters: v.chapters.filter(c => c.id !== chId) })));
    if (activeChId === chId) {
      const remaining = allChs.filter(c => c.id !== chId);
      if (remaining.length) setActiveChId(remaining[0].id);
    }
  };

  const toggleVolume = (volId: string) => {
    setVolumes(volumes.map(v => v.id === volId ? { ...v, collapsed: !v.collapsed } : v));
  };

  const startRename = (id: string, title: string) => {
    setRenamingId(id);
    setRenameVal(title);
  };

  const commitRename = () => {
    if (!renamingId || !renameVal.trim()) {
      setRenamingId(null);
      return;
    }
    setVolumes(volumes.map(v => {
      if (v.id === renamingId) return { ...v, title: renameVal.trim() };
      return { ...v, chapters: v.chapters.map(c => c.id === renamingId ? { ...c, title: renameVal.trim() } : c) };
    }));
    setRenamingId(null);
  };

  const updateSynopsis = (val: string) => {
    setVolumes(volumes.map(v => ({
      ...v, chapters: v.chapters.map(c => c.id === activeChId ? { ...c, synopsis: val } : c),
    })));
  };

  // --- Pipeline / Generation ---
  const startGeneration = async () => {
    if (!activeCh) return;
    setGenerating(true);
    setPipelineSteps(PIPELINE_STEPS.map(s => ({ ...s, status: "pending" })));
    try {
      await apiPost("/api/generation/start", {
        project_id: projectId,
        chapter_id: activeChId,
        synopsis: activeCh.synopsis || "",
      });
      // Poll status
      const poll = setInterval(async () => {
        try {
          const st = await apiGet<{ steps: PipelineStatus[] }>("/api/generation/status");
          if (st.steps) setPipelineSteps(st.steps);
          if (st.steps?.every(s => s.status === "done" || s.status === "error")) {
            clearInterval(poll);
            setGenerating(false);
          }
        } catch {
          clearInterval(poll);
          setGenerating(false);
        }
      }, 2000);
    } catch {
      setGenerating(false);
    }
  };

  // --- Computed ---
  const words = useMemo(() => wc(content), [content]);
  const totalW = useMemo(() => volumes.reduce((s, v) => s + v.chapters.reduce((s2, c) => s2 + wc(c.content || ""), 0), 0), [volumes]);
  const totalCh = useMemo(() => volumes.reduce((s, v) => s + v.chapters.length, 0), [volumes]);

  if (!loaded) {
    return (
      <div className="loading" style={{ height: "100vh" }}>
        <div className="loading-spinner" />
        加载中...
      </div>
    );
  }

  return (
    <div className="page-full">
      <div className="editor-layout">
        {/* ======== LEFT PANEL: Volume/Chapter Tree ======== */}
        <div className="panel" style={{ width: leftPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderRight: "1px solid var(--border)" }}>
          <div className="panel-header">
            <h3>大纲</h3>
            <div className="flex gap-4">
              <button className="btn-icon" title="添加卷" onClick={addVolume} style={{ fontSize: 14 }}>+&#x5377;</button>
            </div>
          </div>
          <div className="panel-body" style={{ padding: "8px 6px" }}>
            {volumes.map(v => (
              <div key={v.id}>
                {/* Volume header */}
                <div
                  className="chapter-tree-item"
                  style={{ fontWeight: 600, color: "var(--text-primary)" }}
                >
                  <span
                    style={{ cursor: "pointer", fontSize: 10, width: 14, flexShrink: 0 }}
                    onClick={() => toggleVolume(v.id)}
                  >
                    {v.collapsed ? "\u25B6" : "\u25BC"}
                  </span>
                  {renamingId === v.id ? (
                    <input
                      className="input"
                      value={renameVal}
                      onChange={e => setRenameVal(e.target.value)}
                      onBlur={commitRename}
                      onKeyDown={e => e.key === "Enter" && commitRename()}
                      autoFocus
                      style={{ padding: "2px 6px", fontSize: 12, flex: 1 }}
                    />
                  ) : (
                    <span
                      className="truncate"
                      style={{ flex: 1, cursor: "pointer" }}
                      onDoubleClick={() => startRename(v.id, v.title)}
                    >
                      {v.title}
                    </span>
                  )}
                  <button
                    className="btn-icon"
                    style={{ width: 22, height: 22, fontSize: 13 }}
                    title="添加章"
                    onClick={() => addChapter(v.id)}
                  >
                    +
                  </button>
                </div>

                {/* Chapters */}
                {!v.collapsed && v.chapters.map(c => (
                  <div
                    key={c.id}
                    className={`chapter-tree-item indent ${c.id === activeChId ? "active" : ""}`}
                    onClick={() => setActiveChId(c.id)}
                  >
                    {renamingId === c.id ? (
                      <input
                        className="input"
                        value={renameVal}
                        onChange={e => setRenameVal(e.target.value)}
                        onBlur={commitRename}
                        onKeyDown={e => e.key === "Enter" && commitRename()}
                        autoFocus
                        style={{ padding: "2px 6px", fontSize: 12, flex: 1 }}
                        onClick={e => e.stopPropagation()}
                      />
                    ) : (
                      <>
                        <span
                          className="truncate"
                          style={{ flex: 1 }}
                          onDoubleClick={() => startRename(c.id, c.title)}
                        >
                          {c.title}
                        </span>
                        <span className="font-mono text-xs text-muted">{wc(c.content || "")}字</span>
                      </>
                    )}
                    {totalCh > 1 && (
                      <button
                        className="btn-icon"
                        style={{ width: 18, height: 18, fontSize: 11 }}
                        onClick={e => { e.stopPropagation(); deleteChapter(c.id); }}
                      >
                        &times;
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
          {/* Footer stats */}
          <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)", fontSize: 11, color: "var(--text-tertiary)", flexShrink: 0 }}>
            {totalCh} 章 &middot; {totalW.toLocaleString()} 字
          </div>
        </div>

        {/* Left resize handle */}
        <div className="panel-resize-h" {...leftPanel.handleProps} />

        {/* ======== CENTER PANEL: Text Editor ======== */}
        <div className="panel flex-1" style={{ background: "var(--bg-app)" }}>
          {/* Editor header */}
          <div style={{ padding: "14px 28px 10px", borderBottom: "1px solid var(--border)", background: "var(--bg-surface)", flexShrink: 0 }}>
            {editingTitle ? (
              <input
                className="input"
                value={titleVal}
                onChange={e => setTitleVal(e.target.value)}
                onBlur={() => setEditingTitle(false)}
                onKeyDown={e => { if (e.key === "Enter") setEditingTitle(false); }}
                autoFocus
                style={{ fontFamily: "var(--font-serif)", fontSize: 18, fontWeight: 700, background: "transparent", borderBottom: "2px solid var(--accent)", borderRadius: 0, padding: "2px 0" }}
              />
            ) : (
              <h3
                className="font-serif"
                style={{ fontSize: 18, fontWeight: 700, cursor: "text", color: "var(--text-primary)" }}
                onClick={() => { setEditingTitle(true); setTitleVal(activeCh?.title || ""); }}
                title="点击编辑章节名"
              >
                {activeCh?.title || "选择章节"}
              </h3>
            )}
            <div className="text-xs text-muted mt-4">
              {activeVol?.title} &middot; {words.toLocaleString()} 字 &middot; 写作 {elapsed} 分钟
            </div>
          </div>

          {/* Textarea */}
          <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
            <textarea
              ref={textRef}
              className="text-editor-area"
              value={content}
              onChange={e => { setContent(e.target.value); setSelection(null); }}
              onMouseUp={handleMouseUp}
              onKeyUp={handleMouseUp}
              placeholder={"在这里开始写作...\n\n提示：\n  双击左侧章节名可重命名\n  选中文本可触发「AI重写」\n  内容会自动保存"}
              spellCheck={false}
              style={{ maxWidth: 800, margin: "0 auto", display: "block" }}
            />
            {/* Floating rewrite button */}
            {selection && (
              <div style={{ position: "absolute", top: 16, right: 16, zIndex: 50 }}>
                <button
                  className="btn-primary"
                  style={{ fontSize: 11, padding: "5px 14px", borderRadius: 16 }}
                  onClick={() => setAiTab("rewrite")}
                >
                  AI 重写 ({selection.text.length}字)
                </button>
              </div>
            )}
          </div>

          {/* Bottom bar */}
          <div className="flex items-center justify-between" style={{ padding: "6px 28px", borderTop: "1px solid var(--border)", background: "var(--bg-surface)", flexShrink: 0 }}>
            <div className="flex items-center gap-12 text-xs text-muted">
              <span>{words.toLocaleString()} 字</span>
              <span>写作 {elapsed} 分钟</span>
            </div>
            <div className="flex items-center gap-8 text-xs">
              <span style={{
                color: saveStatus === "saved" ? "var(--jade)" : saveStatus === "saving" ? "var(--gold)" : "var(--text-tertiary)",
              }}>
                {saveStatus === "saved" ? "已保存" : saveStatus === "saving" ? "保存中..." : "未保存"}
              </span>
            </div>
          </div>
        </div>

        {/* Right resize handle */}
        <div className="panel-resize-h" {...rightPanel.handleProps} />

        {/* ======== RIGHT PANEL: AI Assistant ======== */}
        <div className="panel" style={{ width: rightPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderLeft: "1px solid var(--border)" }}>
          <div className="panel-header">
            <h3>AI 助手</h3>
          </div>

          {/* Tab bar */}
          <div className="tab-bar-underline" style={{ flexShrink: 0 }}>
            {([
              ["outline", "大纲"],
              ["inspire", "灵感"],
              ["rewrite", "重写"],
              ["eval", "评估"],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                className={`tab-item ${aiTab === key ? "active" : ""}`}
                onClick={() => setAiTab(key)}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="panel-body" style={{ padding: "14px 16px" }}>
            {aiTab === "outline" && (
              <OutlineTab synopsis={activeCh?.synopsis || ""} onChange={updateSynopsis} />
            )}
            {aiTab === "inspire" && (
              <InspireTab
                steps={pipelineSteps}
                generating={generating}
                onStart={startGeneration}
              />
            )}
            {aiTab === "rewrite" && (
              <RewriteTab
                selection={selection}
                prompt={rewritePrompt}
                onPromptChange={setRewritePrompt}
                model={rewriteModel}
                onModelChange={setRewriteModel}
              />
            )}
            {aiTab === "eval" && (
              <EvalTab result={evalResult} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ====== Outline Tab ====== */
function OutlineTab({ synopsis, onChange }: { synopsis: string; onChange: (v: string) => void }) {
  return (
    <div>
      <div className="label mb-8">章节剧情大纲</div>
      <textarea
        className="input"
        value={synopsis}
        onChange={e => onChange(e.target.value)}
        rows={10}
        placeholder={"在这里写这一章的剧情要点...\n\n例如：\n  主角初入宗门\n  与师兄发生冲突\n  发现隐藏洞穴"}
        style={{ lineHeight: 1.8, fontFamily: "var(--font-sans)" }}
      />
      <p className="text-xs text-muted mt-12" style={{ lineHeight: 1.6 }}>
        大纲将作为 Scene Planner 的输入，AI 根据大纲拆分场景并生成内容。也会自动同步到「剧情线」页面。
      </p>
    </div>
  );
}

/* ====== Inspire Tab (Film Pipeline) ====== */
function InspireTab({
  steps,
  generating,
  onStart,
}: {
  steps: PipelineStatus[];
  generating: boolean;
  onStart: () => void;
}) {
  const [expandedStep, setExpandedStep] = useState<number | null>(null);

  return (
    <div>
      <div className="label mb-12">Film Pipeline</div>

      {steps.map((step, i) => {
        const dotClass = step.status === "done" ? "done" : step.status === "running" ? "active" : step.status === "error" ? "error" : "";
        return (
          <div key={i} style={{ marginBottom: 6 }}>
            <div
              className="pipeline-step"
              style={{ cursor: "pointer" }}
              onClick={() => setExpandedStep(expandedStep === i ? null : i)}
            >
              <div className={`pipeline-dot ${dotClass}`} />
              <div style={{ flex: 1 }}>
                <div className="pipeline-step-label" style={{ fontWeight: 600 }}>{step.step}</div>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{step.detail}</div>
              </div>
              <span className="pipeline-step-status">
                {step.status === "done" ? "完成" : step.status === "running" ? "运行中" : step.status === "error" ? "错误" : "等待"}
              </span>
            </div>
            {expandedStep === i && step.progress !== undefined && (
              <div style={{ padding: "8px 12px 8px 32px" }}>
                <div className="bar-track" style={{ height: 6 }}>
                  <div className="bar-fill red" style={{ width: `${(step.progress || 0) * 100}%`, height: 6 }} />
                </div>
              </div>
            )}
          </div>
        );
      })}

      <button
        className="btn-primary w-full mt-16"
        onClick={onStart}
        disabled={generating}
      >
        {generating ? "生成中..." : "开始生成"}
      </button>

      <p className="text-xs text-muted mt-12" style={{ lineHeight: 1.6 }}>
        每章不少于 2000 字，按 ~600 字/段分段生成。连接模型后此面板将展示实时 Pipeline 进度。
      </p>
    </div>
  );
}

/* ====== Rewrite Tab ====== */
function RewriteTab({
  selection,
  prompt,
  onPromptChange,
  model,
  onModelChange,
}: {
  selection: { start: number; end: number; text: string } | null;
  prompt: string;
  onPromptChange: (v: string) => void;
  model: string;
  onModelChange: (v: string) => void;
}) {
  return (
    <div>
      <div className="label mb-8">AI 重写选中文本</div>
      {selection ? (
        <>
          {/* Selected text preview */}
          <div
            style={{
              padding: "10px 12px",
              background: "var(--bg-surface-2)",
              borderRadius: "var(--radius-sm)",
              marginBottom: 12,
              fontSize: 13,
              color: "var(--text-secondary)",
              lineHeight: 1.7,
              maxHeight: 120,
              overflowY: "auto",
              fontFamily: "var(--font-serif)",
              borderLeft: "3px solid var(--accent)",
            }}
          >
            &ldquo;{selection.text.length > 200 ? selection.text.slice(0, 200) + "..." : selection.text}&rdquo;
          </div>
          <div className="text-xs text-muted mb-12">
            选中了 {selection.text.length} 字（位置 {selection.start}-{selection.end}）
          </div>

          {/* Model selector */}
          <div className="field mb-12">
            <label className="label">模型选择</label>
            <select className="select w-full" value={model} onChange={e => onModelChange(e.target.value)}>
              <option value="default">默认模型</option>
              <option value="deepseek-chat">DeepSeek Chat</option>
              <option value="gpt-4o">GPT-4o</option>
              <option value="claude-sonnet">Claude Sonnet</option>
            </select>
          </div>

          {/* Prompt input */}
          <div className="field mb-12">
            <label className="label">重写指令（可选）</label>
            <textarea
              className="input"
              value={prompt}
              onChange={e => onPromptChange(e.target.value)}
              rows={3}
              placeholder={"告诉 AI 你想怎么改...\n例如：更紧张、加入内心描写、换成第一人称"}
            />
          </div>

          <button className="btn-primary w-full" disabled>
            AI 重写此段落
          </button>
          <p className="text-xs text-muted mt-8">
            连接模型后可用。AI 将保持上下文一致性进行局部重写。
          </p>
        </>
      ) : (
        <div className="empty-state" style={{ padding: "32px 16px" }}>
          <h4>选中文本以重写</h4>
          <p>在编辑器中选中文本，将出现「AI重写」按钮</p>
          <p style={{ marginTop: 4 }}>支持选中任意长度的文本：单词、句子、段落均可</p>
        </div>
      )}
    </div>
  );
}

/* ====== Eval Tab ====== */
function EvalTab({ result }: { result: EvalResult | null }) {
  if (!result) {
    return (
      <div className="empty-state" style={{ padding: "32px 16px" }}>
        <h4>暂无评估结果</h4>
        <p>在「灵感」面板完成一次生成后，评估结果将显示在这里</p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-10 mb-16">
        <div
          style={{
            width: 48,
            height: 48,
            borderRadius: "50%",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 18,
            fontWeight: 700,
            fontFamily: "var(--font-mono)",
            background: result.passed ? "var(--jade-subtle)" : "var(--accent-subtle)",
            color: result.passed ? "var(--jade)" : "var(--accent)",
          }}
        >
          {result.score}
        </div>
        <div>
          <div style={{ fontWeight: 600, color: "var(--text-primary)" }}>
            {result.passed ? "通过评估" : "需要修改"}
          </div>
          <div className="text-xs text-muted">
            发现 {result.issues.length} 个问题
          </div>
        </div>
      </div>

      {result.issues.map((issue, i) => (
        <div
          key={i}
          style={{
            padding: "10px 12px",
            background: "var(--bg-surface-2)",
            borderRadius: "var(--radius-sm)",
            marginBottom: 8,
            borderLeft: `3px solid ${
              issue.severity === "high" ? "var(--error)" : issue.severity === "medium" ? "var(--warning)" : "var(--info)"
            }`,
          }}
        >
          <div className="flex items-center gap-6 mb-4">
            <span className={`tag ${issue.severity === "high" ? "accent" : issue.severity === "medium" ? "qidian" : "category"}`}>
              {issue.severity === "high" ? "严重" : issue.severity === "medium" ? "中等" : "轻微"}
            </span>
            <span className="text-xs text-muted">{issue.type}</span>
          </div>
          <div className="text-sm" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
            {issue.description}
          </div>
          {issue.suggestion && (
            <div className="text-xs mt-4" style={{ color: "var(--jade)", lineHeight: 1.5 }}>
              建议：{issue.suggestion}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
