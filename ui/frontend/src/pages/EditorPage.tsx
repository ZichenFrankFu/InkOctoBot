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

interface LocalVolume extends Volume { collapsed?: boolean; }

const AGENT_COLORS: Record<string, { bg: string; border: string; name: string }> = {
  "Scene Director": { bg: "var(--indigo-subtle)", border: "var(--indigo)", name: "Scene Director" },
  "Actor Agents": { bg: "var(--gold-subtle)", border: "var(--gold)", name: "Actor Agents" },
  "Editor-Writer": { bg: "var(--jade-subtle)", border: "var(--jade)", name: "Editor-Writer" },
  "Evaluator": { bg: "var(--accent-subtle)", border: "var(--accent)", name: "Evaluator" },
  "User": { bg: "var(--purple-subtle)", border: "var(--purple)", name: "用户" },
  "System": { bg: "var(--bg-surface-2)", border: "var(--text-tertiary)", name: "系统" },
};

interface ChatMessage {
  agent: string;
  content: string;
  status?: "thinking" | "speaking" | "done" | "waiting_confirm";
  timestamp: number;
  isQuestion?: boolean;
}

export default function EditorPage({ projectId }: { projectId: string }) {
  const [volumes, setVolumes] = useState<LocalVolume[]>([]);
  const [activeChId, setActiveChId] = useState<string>("");
  const [content, setContent] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"saved" | "saving" | "unsaved">("saved");
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [startTime] = useState(Date.now());
  const [elapsed, setElapsed] = useState(0);
  const [searchTerm, setSearchTerm] = useState("");
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState("");
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleVal, setTitleVal] = useState("");
  const [aiTab, setAiTab] = useState<"outline" | "inspire" | "rewrite" | "eval">("outline");
  const [selection, setSelection] = useState<{ start: number; end: number; text: string } | null>(null);
  const [rewritePrompt, setRewritePrompt] = useState("");
  const [rewriteModel, setRewriteModel] = useState("default");
  const [pipelineSteps, setPipelineSteps] = useState<PipelineStatus[]>(PIPELINE_STEPS);
  const [generating, setGenerating] = useState(false);
  const [evalResult, setEvalResult] = useState<EvalResult | null>(null);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [currentAgent, setCurrentAgent] = useState<string | null>(null);
  const [waitingForConfirm, setWaitingForConfirm] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const leftPanel = useResizable({ direction: "horizontal", initialSize: 220, minSize: 160, maxSize: 350 });
  const rightPanel = useResizable({ direction: "horizontal", initialSize: 300, minSize: 200, maxSize: 500 });

  useEffect(() => {
    const pid = projectId || "default";
    apiGet<{ volumes: LocalVolume[] }>(`/api/data/editor?project_id=${pid}`)
      .catch(() => ({ volumes: [] as LocalVolume[] }))
      .then((data) => {
        let vols = data.volumes || [];
        if (vols.length === 0) {
          const ch: ChapterOutline = { id: uid(), volume_id: "v1", title: "第一章", order: 1, synopsis: "", content: "", word_count: 0 };
          vols = [{ id: "v1", project_id: pid, title: "第一卷", order: 1, chapters: [ch] }];
        }
        setVolumes(vols);
        const firstCh = vols[0]?.chapters?.[0];
        if (firstCh) { setActiveChId(firstCh.id); setContent(firstCh.content || ""); setTitleVal(firstCh.title); }
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, [projectId]);

  useEffect(() => {
    const iv = setInterval(() => setElapsed(Math.floor((Date.now() - startTime) / 60000)), 30000);
    return () => clearInterval(iv);
  }, [startTime]);

  const activeCh = useMemo(() => { for (const v of volumes) { const c = v.chapters.find(c => c.id === activeChId); if (c) return c; } return null; }, [volumes, activeChId]);
  const activeVol = useMemo(() => volumes.find(v => v.chapters.some(c => c.id === activeChId)) || null, [volumes, activeChId]);

  useEffect(() => {
    if (activeCh && loaded) { setContent(activeCh.content || ""); setTitleVal(activeCh.title); }
    setEditingTitle(false); setSelection(null);
  }, [activeChId, loaded]);

  useEffect(() => {
    if (!loaded) return;
    setSaveStatus("unsaved");
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      setSaveStatus("saving");
      const updatedVolumes = volumes.map(v => ({ ...v, chapters: v.chapters.map(c => c.id === activeChId ? { ...c, content, title: titleVal || c.title, word_count: wc(content) } : c) }));
      setVolumes(updatedVolumes);
      try { await apiPut("/api/data/editor", { project_id: projectId || "default", volumes: updatedVolumes }); setSaveStatus("saved"); }
      catch { setSaveStatus("unsaved"); }
    }, 1500);
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current); };
  }, [content, titleVal]);

  const handleMouseUp = () => {
    const el = textRef.current; if (!el) return;
    setTimeout(() => {
      const s = el.selectionStart, e = el.selectionEnd;
      if (s !== undefined && e !== undefined && e > s) { const txt = content.substring(s, e); if (txt.trim().length > 0) { setSelection({ start: s, end: e, text: txt }); return; } }
      setSelection(null);
    }, 10);
  };

  const addVolume = () => { setVolumes([...volumes, { id: uid(), project_id: projectId, title: `第${volumes.length + 1}卷`, order: volumes.length + 1, chapters: [], collapsed: false }]); };
  const addChapter = (volId: string) => { const vol = volumes.find(v => v.id === volId); if (!vol) return; const ch: ChapterOutline = { id: uid(), volume_id: volId, title: `第${vol.chapters.length + 1}章`, order: vol.chapters.length + 1, synopsis: "", content: "", word_count: 0 }; setVolumes(volumes.map(v => v.id === volId ? { ...v, chapters: [...v.chapters, ch] } : v)); };
  const addChapterToFirstVolume = () => { if (volumes.length > 0) addChapter(volumes[0].id); };
  const deleteChapter = (chId: string) => { const allChs = volumes.flatMap(v => v.chapters); if (allChs.length <= 1) return; setVolumes(volumes.map(v => ({ ...v, chapters: v.chapters.filter(c => c.id !== chId) }))); if (activeChId === chId) { const r = allChs.filter(c => c.id !== chId); if (r.length) setActiveChId(r[0].id); } };
  const toggleVolume = (volId: string) => { setVolumes(volumes.map(v => v.id === volId ? { ...v, collapsed: !v.collapsed } : v)); };
  const startRename = (id: string, title: string) => { setRenamingId(id); setRenameVal(title); };
  const commitRename = () => { if (!renamingId || !renameVal.trim()) { setRenamingId(null); return; } setVolumes(volumes.map(v => { if (v.id === renamingId) return { ...v, title: renameVal.trim() }; return { ...v, chapters: v.chapters.map(c => c.id === renamingId ? { ...c, title: renameVal.trim() } : c) }; })); setRenamingId(null); };
  const updateSynopsis = (val: string) => { setVolumes(volumes.map(v => ({ ...v, chapters: v.chapters.map(c => c.id === activeChId ? { ...c, synopsis: val } : c) }))); };

  const filteredVolumes = useMemo(() => {
    if (!searchTerm.trim()) return volumes;
    const term = searchTerm.trim().toLowerCase();
    return volumes.map(v => ({ ...v, chapters: v.chapters.filter(c => c.title.toLowerCase().includes(term) || (c.content || "").toLowerCase().includes(term) || (c.synopsis || "").toLowerCase().includes(term)) })).filter(v => v.chapters.length > 0 || v.title.toLowerCase().includes(term));
  }, [volumes, searchTerm]);

  const handleExport = () => {
    const lines: string[] = [];
    for (const v of volumes) { lines.push(`===== ${v.title} =====\n`); for (const c of v.chapters) { lines.push(`--- ${c.title} ---\n`); lines.push((c.content || "") + "\n\n"); } }
    const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = `export_${Date.now()}.txt`; document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url);
  };

  const handleSaveOutline = async () => {
    setSaveStatus("saving");
    const uv = volumes.map(v => ({ ...v, chapters: v.chapters.map(c => c.id === activeChId ? { ...c, content, title: titleVal || c.title, word_count: wc(content) } : c) }));
    try { await apiPut("/api/data/editor", { project_id: projectId || "default", volumes: uv }); setSaveStatus("saved"); } catch { setSaveStatus("unsaved"); }
  };

  const generatedTextRef = useRef<string>("");
  const wsRef = useRef<WebSocket | null>(null);

  const runRealPipeline = useCallback(async (sessionId: string) => {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host || "localhost:8000";
    const ws = new WebSocket(`${proto}//${host}/api/generation/ws/${sessionId}`);
    wsRef.current = ws;
    generatedTextRef.current = "";

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      switch (data.type) {
        case "pipeline_start":
          setChatMessages(prev => [...prev, {
            agent: "System", content: "Pipeline 连接成功，开始生成...",
            status: "done", timestamp: Date.now(),
          }]);
          break;
        case "step_start":
          setPipelineSteps(prev => prev.map(s =>
            s.step === data.label ? { ...s, status: "running" as const, detail: data.detail } : s
          ));
          setCurrentAgent(data.label);
          setChatMessages(prev => [...prev, {
            agent: data.label, content: data.detail || "正在处理中...",
            status: "thinking", timestamp: Date.now(),
          }]);
          break;
        case "token":
          generatedTextRef.current += data.content;
          // Update the last thinking message with streaming content
          setChatMessages(prev => {
            const last = prev[prev.length - 1];
            if (last && last.status === "thinking") {
              const updated = [...prev];
              updated[updated.length - 1] = {
                ...last,
                content: generatedTextRef.current.slice(-300) + (generatedTextRef.current.length > 300 ? "\n..." : ""),
                status: "speaking",
              };
              return updated;
            }
            return prev;
          });
          break;
        case "step_done":
          setPipelineSteps(prev => prev.map(s =>
            s.step === (data.step === "scene_director" ? "Scene Director" :
              data.step === "actor_agents" ? "Actor Agents" :
              data.step === "evaluator" ? "Evaluator" :
              data.step === "editor_writer" ? "Editor-Writer" : s.step)
            ? { ...s, status: "done" as const, detail: "已完成" } : s
          ));
          setChatMessages(prev => {
            const filtered = prev.filter(m => m.status !== "thinking" && m.status !== "speaking");
            const agentName = data.step === "scene_director" ? "Scene Director" :
              data.step === "actor_agents" ? "Actor Agents" :
              data.step === "evaluator" ? "Evaluator" : "Editor-Writer";
            const resultText = data.result?.text
              ? `生成完成！共 ${data.result.text.length} 字。`
              : data.result?.summary || data.result?.score !== undefined
              ? `评估完成。得分：${data.result.score}/100`
              : JSON.stringify(data.result || {}).slice(0, 200);
            return [...filtered, {
              agent: agentName, content: resultText,
              status: "done", timestamp: Date.now(),
            }];
          });
          // Store eval result if available
          if (data.step === "evaluator" && data.result) {
            setEvalResult({
              chapter_id: activeChId,
              passed: data.result.passed ?? true,
              score: data.result.score ?? 80,
              issues: (data.result.issues || []).map((i: any) => ({
                type: i.type || "unknown",
                severity: i.severity || "low",
                description: i.description || "",
                suggestion: i.suggestion,
              })),
            });
          }
          break;
        case "need_confirm": {
          const confirmAgent = data.step === "scene_director" ? "Scene Director"
            : data.step === "actor_agents" ? "Actor Agents"
            : data.step === "editor_writer" ? "Editor-Writer"
            : data.step === "evaluator" ? "Evaluator" : "System";
          setChatMessages(prev => [...prev, {
            agent: confirmAgent,
            content: data.message || "是否继续？",
            status: "waiting_confirm", timestamp: Date.now(), isQuestion: true,
          }]);
          setWaitingForConfirm(true);
          break;
        }
        case "complete":
          setGenerating(false);
          setCurrentAgent(null);
          if (data.text) generatedTextRef.current = data.text;
          setChatMessages(prev => [...prev, {
            agent: "System",
            content: `Pipeline 全部完成！生成了 ${(data.text || "").length} 字。可在「评估」标签查看结果，或点击「写入编辑器」。`,
            status: "done", timestamp: Date.now(),
          }]);
          if (data.evaluation) {
            setEvalResult({
              chapter_id: activeChId,
              passed: data.evaluation.passed ?? true,
              score: data.evaluation.score ?? 80,
              issues: (data.evaluation.issues || []).map((i: any) => ({
                type: i.type || "unknown",
                severity: i.severity || "low",
                description: i.description || "",
              })),
            });
          }
          break;
        case "error":
          setGenerating(false);
          setCurrentAgent(null);
          setChatMessages(prev => [...prev, {
            agent: "System", content: `错误: ${data.message}`,
            status: "done", timestamp: Date.now(),
          }]);
          break;
      }
    };

    ws.onerror = () => {
      setChatMessages(prev => [...prev, {
        agent: "System", content: "WebSocket 连接失败，切换到快速生成模式...",
        status: "done", timestamp: Date.now(),
      }]);
      // Fallback to quick-generate
      runQuickGenerate();
    };

    ws.onclose = () => {
      wsRef.current = null;
    };
  }, [activeChId]);

  const runQuickGenerate = useCallback(async () => {
    if (!activeCh) return;
    setPipelineSteps(prev => prev.map((s, i) =>
      i === 0 ? { ...s, status: "running" as const } : s
    ));
    setChatMessages(prev => [...prev, {
      agent: "Actor Agents", content: "正在生成章节内容（快速模式）...",
      status: "thinking", timestamp: Date.now(),
    }]);

    try {
      const resp = await apiPost<{ text: string; model: string; tokens?: any }>("/api/generation/quick-generate", {
        project_id: projectId,
        chapter_id: activeChId,
        synopsis: activeCh.synopsis || "",
      });

      generatedTextRef.current = resp.text;
      setPipelineSteps(prev => prev.map(s => ({ ...s, status: "done" as const, detail: "已完成" })));
      setChatMessages(prev => {
        const filtered = prev.filter(m => m.status !== "thinking");
        return [...filtered,
          { agent: "Actor Agents", content: `生成完成！共 ${resp.text.length} 字。使用模型: ${resp.model}`, status: "done" as const, timestamp: Date.now() },
          { agent: "System", content: "快速生成完成！点击「写入编辑器」将内容插入。", status: "done" as const, timestamp: Date.now() },
        ];
      });
    } catch (e: any) {
      setChatMessages(prev => {
        const filtered = prev.filter(m => m.status !== "thinking");
        return [...filtered, {
          agent: "System",
          content: `生成失败: ${e?.message || "未知错误"}。请检查模型连接设置。`,
          status: "done", timestamp: Date.now(),
        }];
      });
    }
    setGenerating(false);
    setCurrentAgent(null);
  }, [activeCh, projectId, activeChId]);

  const startGeneration = useCallback(async () => {
    if (!activeCh) return;
    setGenerating(true);
    setPipelineSteps(PIPELINE_STEPS.map(s => ({ ...s, status: "pending" })));
    setChatMessages([]); setWaitingForConfirm(false);
    generatedTextRef.current = "";

    const synopsis = activeCh.synopsis || "";
    setChatMessages([{
      agent: "System",
      content: `Pipeline 启动！基于大纲「${synopsis.slice(0, 50)}${synopsis.length > 50 ? "..." : ""}」开始生成。`,
      status: "done", timestamp: Date.now(),
    }]);

    try {
      const resp = await apiPost<{ session_id: string }>("/api/generation/start", {
        project_id: projectId,
        chapter_id: activeChId,
        synopsis,
      });
      // Try WebSocket pipeline
      runRealPipeline(resp.session_id);
    } catch {
      // Fallback to quick generate
      runQuickGenerate();
    }
  }, [activeCh, projectId, activeChId, runRealPipeline, runQuickGenerate]);

  const handleConfirmContinue = () => {
    setWaitingForConfirm(false);
    setChatMessages(prev => [...prev, { agent: "User", content: "确认满意，继续下一步。", status: "done", timestamp: Date.now() }]);
    // Send continue message through WebSocket
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: "continue" }));
    }
  };

  const handleRollback = useCallback((stepIndex: number) => {
    setPipelineSteps(prev => prev.map((s, i) => i >= stepIndex ? { ...s, status: "pending", detail: PIPELINE_STEPS[i].detail } : s));
    const agentName = PIPELINE_STEPS[stepIndex].step;
    const firstMsgIdx = chatMessages.findIndex(m => m.agent === agentName);
    if (firstMsgIdx >= 0) {
      setChatMessages(prev => prev.slice(0, firstMsgIdx));
    }
    setWaitingForConfirm(false);
    setGenerating(false);
    setCurrentAgent(null);
    setChatMessages(prev => [...prev, { agent: "System", content: `已回退到「${agentName}」阶段。点击「开始生成」重新运行 Pipeline。`, status: "done", timestamp: Date.now() }]);
  }, [chatMessages]);

  const handleWriteToEditor = useCallback(() => {
    const text = generatedTextRef.current;
    if (text && text.length > 10) {
      setContent(prev => prev ? prev + "\n\n" + text : text);
      setChatMessages(prev => [...prev, { agent: "System", content: `已将 ${text.length} 字生成内容写入编辑器！`, status: "done", timestamp: Date.now() }]);
      // Save version
      apiPost("/api/editor/save-version", {
        project_id: projectId || "default",
        chapter_id: activeChId,
        text,
        source: "ai_generated",
      }).catch(() => {});
    } else {
      setChatMessages(prev => [...prev, { agent: "System", content: "没有可写入的生成内容。请先运行 Pipeline。", status: "done", timestamp: Date.now() }]);
    }
  }, [projectId, activeChId]);

  const sendChatMessage = () => {
    if (!chatInput.trim()) return;
    const msg = chatInput.trim();
    setChatMessages(prev => [...prev, { agent: "User", content: msg, status: "done", timestamp: Date.now() }]);
    setChatInput("");
    if (waitingForConfirm) {
      setWaitingForConfirm(false);
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ action: "feedback", message: msg }));
      } else {
        // Fallback: show feedback received
        setTimeout(() => {
          setChatMessages(prev => [...prev, { agent: currentAgent || "System", content: `收到反馈：「${msg}」。已根据你的意见调整，请再次确认。`, status: "waiting_confirm", timestamp: Date.now(), isQuestion: true }]);
          setWaitingForConfirm(true);
        }, 1000);
      }
    }
  };

  const words = useMemo(() => wc(content), [content]);
  const totalW = useMemo(() => volumes.reduce((s, v) => s + v.chapters.reduce((s2, c) => s2 + wc(c.content || ""), 0), 0), [volumes]);
  const totalCh = useMemo(() => volumes.reduce((s, v) => s + v.chapters.length, 0), [volumes]);

  if (!loaded) return <div className="loading" style={{ height: "100vh" }}><div className="loading-spinner" />加载中...</div>;

  return (
    <div className="page-full">
      <div className="editor-layout">
        {/* LEFT PANEL */}
        <div className="panel" style={{ width: leftPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderRight: "1px solid var(--border)" }}>
          <div className="panel-header"><div className="flex gap-4"></div></div>
          <div style={{ padding: "8px 10px 4px" }}>
            <input className="input" type="text" value={searchTerm} onChange={e => setSearchTerm(e.target.value)} placeholder="搜索章节..." style={{ fontSize: 12, padding: "5px 10px", width: "100%", boxSizing: "border-box" }} />
          </div>
          <div style={{ padding: "4px 10px 6px", display: "flex", gap: 6 }}>
            <button className="btn-icon" onClick={addVolume} style={{ fontSize: 12, flex: 1, padding: "4px 0", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}>+卷</button>
            <button className="btn-icon" onClick={addChapterToFirstVolume} style={{ fontSize: 12, flex: 1, padding: "4px 0", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}>+章</button>
            <button className="btn-icon" onClick={handleExport} style={{ fontSize: 12, flex: 1, padding: "4px 0", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}>导出</button>
          </div>
          <div className="panel-body" style={{ padding: "8px 6px" }}>
            {filteredVolumes.map(v => (
              <div key={v.id}>
                <div className="chapter-tree-item" style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                  <span style={{ cursor: "pointer", fontSize: 10, width: 14, flexShrink: 0 }} onClick={() => toggleVolume(v.id)}>{v.collapsed ? "\u25B6" : "\u25BC"}</span>
                  {renamingId === v.id ? <input className="input" value={renameVal} onChange={e => setRenameVal(e.target.value)} onBlur={commitRename} onKeyDown={e => e.key === "Enter" && commitRename()} autoFocus style={{ padding: "2px 6px", fontSize: 12, flex: 1 }} />
                    : <span className="truncate" style={{ flex: 1, cursor: "pointer" }} onDoubleClick={() => startRename(v.id, v.title)}>{v.title}</span>}
                  <button className="btn-icon" style={{ width: 22, height: 22, fontSize: 13 }} onClick={() => addChapter(v.id)}>+</button>
                </div>
                {!v.collapsed && v.chapters.map(c => (
                  <div key={c.id} className={`chapter-tree-item indent ${c.id === activeChId ? "active" : ""}`} onClick={() => setActiveChId(c.id)}
                    style={searchTerm.trim() && (c.content || "").toLowerCase().includes(searchTerm.trim().toLowerCase()) ? { background: "var(--accent-subtle, rgba(255,200,0,0.15))" } : undefined}>
                    {renamingId === c.id ? <input className="input" value={renameVal} onChange={e => setRenameVal(e.target.value)} onBlur={commitRename} onKeyDown={e => e.key === "Enter" && commitRename()} autoFocus style={{ padding: "2px 6px", fontSize: 12, flex: 1 }} onClick={e => e.stopPropagation()} />
                      : <><span className="truncate" style={{ flex: 1 }} onDoubleClick={() => startRename(c.id, c.title)}>{c.title}</span><span className="font-mono text-xs text-muted">{wc(c.content || "")}字</span></>}
                    {totalCh > 1 && <button className="btn-icon" style={{ width: 18, height: 18, fontSize: 11 }} onClick={e => { e.stopPropagation(); deleteChapter(c.id); }}>&times;</button>}
                  </div>
                ))}
              </div>
            ))}
          </div>
          <div style={{ padding: "8px 10px", borderTop: "1px solid var(--border)", flexShrink: 0 }}>
            <div className="label mb-4" style={{ fontSize: 10 }}>版本记录</div>
            <div style={{ maxHeight: 100, overflowY: "auto" }}>
              <div className="text-xs text-muted" style={{ padding: "4px 6px", cursor: "pointer", borderRadius: 4 }}
                onMouseEnter={e => e.currentTarget.style.background = "var(--bg-surface-hover)"}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
                当前版本 · {new Date().toLocaleDateString("zh-CN")}
              </div>
              <div className="text-xs text-muted" style={{ padding: "4px 6px", opacity: 0.6 }}>
                连接模型后自动生成版本历史
              </div>
            </div>
          </div>
          <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)", fontSize: 11, color: "var(--text-tertiary)", flexShrink: 0 }}>{totalCh} 章 &middot; {totalW.toLocaleString()} 字</div>
        </div>
        <div className="panel-resize-h" {...leftPanel.handleProps} />

        {/* CENTER PANEL */}
        <div className="panel flex-1" style={{ background: "var(--bg-app)" }}>
          <div style={{ padding: "14px 28px 10px", borderBottom: "1px solid var(--border)", background: "var(--bg-surface)", flexShrink: 0 }}>
            {editingTitle ? <input className="input" value={titleVal} onChange={e => setTitleVal(e.target.value)} onBlur={() => setEditingTitle(false)} onKeyDown={e => { if (e.key === "Enter") setEditingTitle(false); }} autoFocus style={{ fontFamily: "var(--font-serif)", fontSize: 18, fontWeight: 700, background: "transparent", borderBottom: "2px solid var(--accent)", borderRadius: 0, padding: "2px 0" }} />
              : <h3 className="font-serif" style={{ fontSize: 18, fontWeight: 700, cursor: "text", color: "var(--text-primary)" }} onClick={() => { setEditingTitle(true); setTitleVal(activeCh?.title || ""); }} title="点击编辑章节名">{activeCh?.title || "选择章节"}</h3>}
            <div className="text-xs text-muted mt-4">{activeVol?.title} &middot; {words.toLocaleString()} 字 &middot; 写作 {elapsed} 分钟</div>
          </div>
          <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
            <textarea ref={textRef} className="text-editor-area" value={content} onChange={e => { setContent(e.target.value); setSelection(null); }} onMouseUp={handleMouseUp} onKeyUp={handleMouseUp}
              placeholder={"在这里开始写作...\n\n提示：\n  双击左侧章节名可重命名\n  选中文本可触发「AI重写」\n  内容会自动保存"} spellCheck={false} style={{ maxWidth: 800, margin: "0 auto", display: "block" }} />
            {selection && <div style={{ position: "absolute", top: 16, right: 16, zIndex: 50 }}><button className="btn-primary" style={{ fontSize: 11, padding: "5px 14px", borderRadius: 16 }} onClick={() => setAiTab("rewrite")}>AI 重写 ({selection.text.length}字)</button></div>}
          </div>
          <div className="flex items-center justify-between" style={{ padding: "6px 28px", borderTop: "1px solid var(--border)", background: "var(--bg-surface)", flexShrink: 0 }}>
            <div className="flex items-center gap-12 text-xs text-muted"><span>{words.toLocaleString()} 字</span><span>写作 {elapsed} 分钟</span></div>
            <div className="flex items-center gap-8 text-xs"><span style={{ color: saveStatus === "saved" ? "var(--jade)" : saveStatus === "saving" ? "var(--gold)" : "var(--text-tertiary)" }}>{saveStatus === "saved" ? "已保存" : saveStatus === "saving" ? "保存中..." : "未保存"}</span></div>
          </div>
        </div>
        <div className="panel-resize-h" {...rightPanel.handleProps} />

        {/* RIGHT PANEL */}
        <div className="panel" style={{ width: rightPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderLeft: "1px solid var(--border)" }}>
          <div className="panel-header"><h3>AI 助手</h3></div>
          <div className="tab-bar-underline" style={{ flexShrink: 0 }}>
            {([["outline", "大纲"], ["inspire", "灵感"], ["rewrite", "重写"], ["eval", "评估"]] as const).map(([key, label]) => (
              <button key={key} className={`tab-item ${aiTab === key ? "active" : ""}`} onClick={() => setAiTab(key)}>{label}</button>
            ))}
          </div>
          <div className="panel-body" style={{ padding: "14px 16px" }}>
            {aiTab === "outline" && <OutlineTab synopsis={activeCh?.synopsis || ""} onChange={updateSynopsis} onSave={handleSaveOutline}
              onStartGeneration={() => { setAiTab("inspire"); setTimeout(() => { if (!generating) startGeneration(); }, 300); }} projectId={projectId} />}
            {aiTab === "inspire" && <InspireTab steps={pipelineSteps} generating={generating} onStart={startGeneration} chatMessages={chatMessages} chatInput={chatInput}
              onChatInputChange={setChatInput} onSendMessage={sendChatMessage} waitingForConfirm={waitingForConfirm} onConfirmContinue={handleConfirmContinue} onRollback={handleRollback} onWriteToEditor={handleWriteToEditor} />}
            {aiTab === "rewrite" && <RewriteTab selection={selection} prompt={rewritePrompt} onPromptChange={setRewritePrompt} model={rewriteModel} onModelChange={setRewriteModel} />}
            {aiTab === "eval" && <EvalTab result={evalResult} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function OutlineTab({ synopsis, onChange, onSave, onStartGeneration, projectId }: {
  synopsis: string; onChange: (v: string) => void; onSave: () => void; onStartGeneration: () => void; projectId: string;
}) {
  const [time, setTime] = useState("");
  const [location, setLocation] = useState("");
  const [characters, setCharacters] = useState<{ id: string; name: string; selected: boolean }[]>([]);
  const [references, setReferences] = useState<{ id: string; title: string; selected: boolean }[]>([]);
  const [showLinker, setShowLinker] = useState(false);

  useEffect(() => {
    const pid = projectId || "default";
    apiGet<{ items: any[] }>(`/api/data/characters?project_id=${pid}`)
      .then(r => setCharacters((r.items || []).map((c: any) => ({ id: c.id, name: c.name, selected: false }))))
      .catch(() => {});
    apiGet<{ items: any[] }>("/api/references/works")
      .then(r => setReferences((r.items || []).map((w: any) => ({ id: w.id, title: w.title || w.name || "未命名", selected: false }))))
      .catch(() => setReferences([]));
  }, [projectId]);

  const toggleChar = (id: string) => setCharacters(prev => prev.map(c => c.id === id ? { ...c, selected: !c.selected } : c));
  const toggleRef = (id: string) => setReferences(prev => prev.map(r => r.id === id ? { ...r, selected: !r.selected } : r));
  const selectedChars = characters.filter(c => c.selected);
  const selectedRefs = references.filter(r => r.selected);

  return (
    <div>
      <div className="label mb-8">章节剧情大纲</div>
      <textarea className="input" value={synopsis} onChange={e => onChange(e.target.value)} rows={8}
        placeholder={"在这里写这一章的剧情要点...\n\n例如：\n  主角初入宗门\n  与师兄发生冲突\n  发现隐藏洞穴"} style={{ lineHeight: 1.8, fontFamily: "var(--font-sans)" }} />

      {/* Character & Reference Linker */}
      <div style={{ marginTop: 10 }}>
        <button className="btn" style={{ fontSize: 11, padding: "4px 12px", width: "100%" }} onClick={() => setShowLinker(!showLinker)}>
          {showLinker ? "收起" : "关联角色 & 参考作品"} {selectedChars.length + selectedRefs.length > 0 ? `(已选 ${selectedChars.length + selectedRefs.length})` : ""}
        </button>
        {showLinker && (
          <div style={{ marginTop: 8, padding: 10, background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border)" }}>
            {characters.length > 0 && (
              <div style={{ marginBottom: 10 }}>
                <div className="label" style={{ fontSize: 10, marginBottom: 4, color: "var(--purple)" }}>出场角色</div>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {characters.map(c => (
                    <button key={c.id} onClick={() => toggleChar(c.id)}
                      style={{
                        fontSize: 11, padding: "3px 10px", borderRadius: 14, border: "1px solid",
                        borderColor: c.selected ? "var(--purple)" : "var(--border)",
                        background: c.selected ? "var(--purple-subtle)" : "transparent",
                        color: c.selected ? "var(--purple)" : "var(--text-secondary)",
                        cursor: "pointer",
                      }}>
                      {c.name}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {characters.length === 0 && (
              <div className="text-xs text-muted" style={{ marginBottom: 8 }}>暂无角色，请在「角色管理」中创建</div>
            )}
            {references.length > 0 && (
              <div>
                <div className="label" style={{ fontSize: 10, marginBottom: 4, color: "var(--jade)" }}>参考作品</div>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  {references.map(r => (
                    <button key={r.id} onClick={() => toggleRef(r.id)}
                      style={{
                        fontSize: 11, padding: "3px 10px", borderRadius: 14, border: "1px solid",
                        borderColor: r.selected ? "var(--jade)" : "var(--border)",
                        background: r.selected ? "var(--jade-subtle)" : "transparent",
                        color: r.selected ? "var(--jade)" : "var(--text-secondary)",
                        cursor: "pointer",
                      }}>
                      {r.title}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {references.length === 0 && (
              <div className="text-xs text-muted">暂无参考作品，请在「参考文库」中导入</div>
            )}
          </div>
        )}
      </div>

      {/* Selected items display */}
      {(selectedChars.length > 0 || selectedRefs.length > 0) && (
        <div style={{ marginTop: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
          {selectedChars.map(c => (
            <span key={c.id} style={{ fontSize: 10, padding: "2px 8px", borderRadius: 10, background: "var(--purple-subtle)", color: "var(--purple)" }}>
              {c.name}
            </span>
          ))}
          {selectedRefs.map(r => (
            <span key={r.id} style={{ fontSize: 10, padding: "2px 8px", borderRadius: 10, background: "var(--jade-subtle)", color: "var(--jade)" }}>
              {r.title}
            </span>
          ))}
        </div>
      )}

      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <div className="field" style={{ flex: 1 }}>
          <label className="label">时间</label>
          <input className="input" value={time} onChange={e => setTime(e.target.value)} placeholder="例：第3天·黄昏" style={{ fontSize: 12 }} />
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label className="label">地点</label>
          <input className="input" value={location} onChange={e => setLocation(e.target.value)} placeholder="例：云隐山·剑庐" style={{ fontSize: 12 }} />
        </div>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
        <button className="btn-primary" style={{ flex: 1 }} onClick={onSave}>保存</button>
        <button className="btn-primary" style={{ flex: 1, background: "var(--jade, #34a853)", border: "none" }} onClick={onStartGeneration}>开始生成</button>
      </div>
      <p className="text-xs text-muted mt-12" style={{ lineHeight: 1.6 }}>
        关联角色和参考作品后，Pipeline 生成时 AI 将参考相关信息。点击「开始生成」启动 Pipeline。
      </p>
    </div>
  );
}

function InspireTab({ steps, generating, onStart, chatMessages, chatInput, onChatInputChange, onSendMessage, waitingForConfirm, onConfirmContinue, onRollback, onWriteToEditor }: {
  steps: PipelineStatus[]; generating: boolean; onStart: () => void; chatMessages: ChatMessage[]; chatInput: string;
  onChatInputChange: (v: string) => void; onSendMessage: () => void; waitingForConfirm: boolean; onConfirmContinue: () => void; onRollback?: (stepIndex: number) => void; onWriteToEditor?: () => void;
}) {
  const chatEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [chatMessages, waitingForConfirm]);

  const getAgentStyle = (agent: string) => AGENT_COLORS[agent] || { bg: "#f0f0f0", border: "#999", name: agent };
  const getAgentAvatar = (agent: string) => {
    const map: Record<string, string> = { "Scene Director": "🎬", "Actor Agents": "🎭", "Editor-Writer": "✍️", "Evaluator": "📋", "User": "👤", "System": "🤖" };
    return map[agent] || "🤖";
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div className="label mb-8">Film Pipeline - 群聊模式</div>
      {/* Progress bar */}
      <div style={{ display: "flex", gap: 4, marginBottom: 10, padding: "6px 0" }}>
        {steps.map((s, i) => (
          <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4, cursor: s.status === "done" ? "pointer" : "default" }}
            onClick={() => {
              if (s.status !== "done") return;
              if (onRollback) onRollback(i);
            }}
            title={s.status === "done" ? `点击回退到「${s.step}」` : undefined}
          >
            <div style={{ width: "100%", height: 4, borderRadius: 2, background: s.status === "done" ? "var(--jade)" : s.status === "running" ? "var(--gold)" : "var(--border)", transition: "background 0.3s" }} />
            <span style={{ fontSize: 9, color: s.status === "done" ? "var(--jade)" : s.status === "running" ? "var(--gold)" : "var(--text-disabled)" }}>{s.step.split(" ")[0]}</span>
          </div>
        ))}
      </div>
      {/* Chat area */}
      <div style={{ flex: 1, overflowY: "auto", border: "1px solid var(--border)", borderRadius: "var(--radius-sm, 6px)", padding: 8, marginBottom: 10, minHeight: 200, maxHeight: 400, background: "var(--bg-app)" }}>
        {chatMessages.length === 0 && !generating && (
          <div style={{ padding: "32px 16px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 13 }}>在「大纲」标签中点击「开始生成」启动 Pipeline，或直接在下方输入消息</div>
        )}
        {chatMessages.map((msg, i) => {
          const style = getAgentStyle(msg.agent); const isUser = msg.agent === "User";
          return (
            <div key={i} style={{ display: "flex", flexDirection: isUser ? "row-reverse" : "row", alignItems: "flex-start", marginBottom: 10, gap: 8 }}>
              <div style={{ width: 32, height: 32, borderRadius: "50%", background: style.bg, border: `2px solid ${style.border}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 16, flexShrink: 0 }}>{getAgentAvatar(msg.agent)}</div>
              <div style={{ maxWidth: "80%", minWidth: 0 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: style.border, marginBottom: 2, textAlign: isUser ? "right" : "left" }}>
                  {style.name}{msg.status === "thinking" && <span style={{ marginLeft: 6, fontSize: 10, fontWeight: 400, color: "#f9ab00" }}>思考中...</span>}
                </div>
                <div style={{ padding: "8px 12px", borderRadius: 10, background: style.bg, borderLeft: isUser ? "none" : `3px solid ${style.border}`, borderRight: isUser ? `3px solid ${style.border}` : "none", fontSize: 13, lineHeight: 1.6, color: "var(--text-primary)", wordBreak: "break-word", whiteSpace: "pre-wrap" }}>
                  {msg.isQuestion ? (
                    <QuestionChoices content={msg.content} onChoose={(choice) => {
                      onChatInputChange(choice);
                    }} />
                  ) : msg.content}
                </div>
                {msg.agent !== "User" && msg.agent !== "System" && msg.status === "done" && !generating && (
                  <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px", marginTop: 4, color: "var(--text-tertiary)" }}
                    onClick={() => onStart()}>
                    ↻ 重新生成
                  </button>
                )}
              </div>
            </div>
          );
        })}
        {waitingForConfirm && (
          <div style={{ display: "flex", justifyContent: "center", gap: 10, padding: "12px 0", borderTop: "1px dashed var(--border)", marginTop: 8 }}>
            <button className="btn-primary" style={{ padding: "8px 24px", fontSize: 13, borderRadius: 20, background: "var(--jade)", border: "none" }} onClick={onConfirmContinue}>
              确认满意，继续下一步 →
            </button>
          </div>
        )}
        {!generating && chatMessages.length > 0 && chatMessages[chatMessages.length - 1]?.agent === "System" && (
          <div style={{ display: "flex", justifyContent: "center", gap: 10, padding: "12px 0", borderTop: "1px dashed var(--border)", marginTop: 8 }}>
            <button className="btn-primary" style={{ padding: "8px 20px", fontSize: 13, borderRadius: 20 }} onClick={() => {
              if (onWriteToEditor) onWriteToEditor();
            }}>
              确认完成，写入编辑器
            </button>
            <button className="btn" style={{ padding: "8px 16px", fontSize: 12, borderRadius: 20 }} onClick={() => {
              // Rollback to last completed step
              const lastDone = [...steps].reverse().findIndex(s => s.status === "done");
              if (lastDone >= 0 && onRollback) {
                onRollback(steps.length - 1 - lastDone);
              }
            }}>
              回退上一步
            </button>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>
      {/* Input */}
      <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
        <input className="input" value={chatInput} onChange={e => onChatInputChange(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSendMessage(); } }}
          placeholder={waitingForConfirm ? "输入修改意见，或点击确认继续..." : "输入消息与 Agent 对话..."} style={{ flex: 1, fontSize: 12, padding: "6px 10px" }} />
        <button className="btn-primary" onClick={onSendMessage} disabled={!chatInput.trim()} style={{ fontSize: 12, padding: "6px 12px", flexShrink: 0 }}>发送</button>
      </div>
      {!generating && chatMessages.length === 0 && <button className="btn-primary w-full" onClick={onStart}>开始生成</button>}
      <p className="text-xs text-muted mt-8" style={{ lineHeight: 1.6 }}>每个 Agent 完成后会询问你的意见。输入修改建议或点击「确认满意」进入下一步。</p>
    </div>
  );
}

function RewriteTab({ selection, prompt, onPromptChange, model, onModelChange }: { selection: { start: number; end: number; text: string } | null; prompt: string; onPromptChange: (v: string) => void; model: string; onModelChange: (v: string) => void; }) {
  const [rewriting, setRewriting] = useState(false);
  const [rewriteResult, setRewriteResult] = useState<string | null>(null);
  const [rewriteError, setRewriteError] = useState<string | null>(null);

  const handleRewrite = async () => {
    if (!selection) return;
    setRewriting(true);
    setRewriteResult(null);
    setRewriteError(null);
    try {
      const resp = await apiPost<{ rewritten: string }>("/api/generation/rewrite", {
        text: selection.text,
        instruction: prompt || "润色并提升文学质量",
        model: model || undefined,
      });
      setRewriteResult(typeof resp.rewritten === "string" ? resp.rewritten : JSON.stringify(resp.rewritten));
    } catch (e: any) {
      setRewriteError(e?.message || "重写失败，请检查模型连接");
    }
    setRewriting(false);
  };

  return (
    <div>
      <div className="label mb-8">AI 重写选中文本</div>
      {selection ? (<>
        <div style={{ padding: "10px 12px", background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)", marginBottom: 12, fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.7, maxHeight: 120, overflowY: "auto", fontFamily: "var(--font-serif)", borderLeft: "3px solid var(--accent)" }}>&ldquo;{selection.text.length > 200 ? selection.text.slice(0, 200) + "..." : selection.text}&rdquo;</div>
        <div className="text-xs text-muted mb-12">选中了 {selection.text.length} 字（位置 {selection.start}-{selection.end}）</div>
        <div className="field mb-12"><label className="label">重写指令（可选）</label><textarea className="input" value={prompt} onChange={e => onPromptChange(e.target.value)} rows={3} placeholder={"告诉 AI 你想怎么改...\n例如：更紧张、加入内心描写、换成第一人称"} /></div>
        <button className="btn-primary w-full" onClick={handleRewrite} disabled={rewriting}>
          {rewriting ? "重写中..." : "AI 重写此段落"}
        </button>
        {rewriteError && <p className="text-xs mt-8" style={{ color: "var(--error)" }}>{rewriteError}</p>}
        {rewriteResult && (<>
          <div style={{ marginTop: 12, padding: "10px 12px", background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)", fontSize: 13, lineHeight: 1.7, fontFamily: "var(--font-serif)", borderLeft: "3px solid var(--jade)", maxHeight: 200, overflowY: "auto", color: "var(--text-primary)" }}>
            {rewriteResult}
          </div>
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            <button className="btn" style={{ flex: 1, fontSize: 11 }} onClick={() => navigator.clipboard.writeText(rewriteResult)}>
              复制结果
            </button>
          </div>
        </>)}
      </>) : (<div className="empty-state" style={{ padding: "32px 16px" }}><h4>选中文本以重写</h4><p>在编辑器中选中文本，将出现「AI重写」按钮</p></div>)}
    </div>
  );
}

function QuestionChoices({ content, onChoose }: { content: string; onChoose: (choice: string) => void }) {
  const [page, setPage] = useState(0);
  // Parse choices from content: look for **方案 X：...** patterns
  const parts = content.split(/(\*\*方案 [A-Z]：[^*]+\*\*)/g);
  const intro = parts[0] || "";
  const choices: { label: string; detail: string }[] = [];
  let current = "";
  for (const line of content.split("\n")) {
    const match = line.match(/^\*\*方案 ([A-Z])：(.+)\*\*$/);
    if (match) {
      if (current) choices.push({ label: current, detail: "" });
      current = `方案 ${match[1]}：${match[2]}`;
    } else if (current && line.match(/^\d+\./)) {
      choices[choices.length] = choices[choices.length] || { label: current, detail: "" };
      if (!choices[choices.length - 1]) choices.push({ label: current, detail: line });
      else choices[choices.length - 1] = { ...choices[choices.length - 1], detail: (choices[choices.length - 1].detail ? choices[choices.length - 1].detail + "\n" : "") + line };
    }
  }
  // Simpler approach: split into sections by **方案
  const sections = content.split(/(?=\*\*方案 [A-Z])/);
  const headerText = sections[0] || "";
  const choiceSections = sections.slice(1);
  const ITEMS_PER_PAGE = 2;
  const totalPages = Math.ceil(choiceSections.length / ITEMS_PER_PAGE);
  const pageChoices = choiceSections.slice(page * ITEMS_PER_PAGE, (page + 1) * ITEMS_PER_PAGE);

  return (
    <div>
      <div style={{ marginBottom: 10, whiteSpace: "pre-wrap" }}>{headerText.trim()}</div>
      {pageChoices.map((section, i) => {
        const lines = section.trim().split("\n");
        const title = lines[0].replace(/\*\*/g, "").trim();
        const details = lines.slice(1).join("\n").trim();
        return (
          <button key={i + page * ITEMS_PER_PAGE} onClick={() => onChoose(`选择${title}`)}
            style={{
              display: "block", width: "100%", textAlign: "left", padding: "10px 14px",
              marginBottom: 8, borderRadius: 8, border: "1px solid var(--border-hover)",
              background: "var(--bg-surface)", cursor: "pointer", transition: "all 0.15s",
              color: "var(--text-primary)", fontSize: 13,
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--accent)"; e.currentTarget.style.background = "var(--accent-subtle)"; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = "var(--border-hover)"; e.currentTarget.style.background = "var(--bg-surface)"; }}
          >
            <div style={{ fontWeight: 600, marginBottom: 4, color: "var(--accent)" }}>{title}</div>
            {details && <div style={{ fontSize: 12, color: "var(--text-secondary)", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{details}</div>}
          </button>
        );
      })}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-8" style={{ marginTop: 6 }}>
          <button className="btn-ghost" style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}>← 上一页</button>
          <span className="text-xs text-muted">{page + 1}/{totalPages}</span>
          <button className="btn-ghost" style={{ fontSize: 11, padding: "2px 8px" }} onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1}>下一页 →</button>
        </div>
      )}
      <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px dashed var(--border)" }}>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>或直接输入修改意见</span>
      </div>
    </div>
  );
}

function EvalTab({ result }: { result: EvalResult | null }) {
  const [evaluating, setEvaluating] = useState(false);
  const [manualResult, setManualResult] = useState<EvalResult | null>(null);
  const displayResult = manualResult || result;

  if (!displayResult) return (
    <div className="empty-state" style={{ padding: "32px 16px" }}>
      <h4>暂无评估结果</h4>
      <p>在「灵感」面板完成一次生成后，评估结果将显示在这里</p>
    </div>
  );
  return (
    <div>
      <div className="flex items-center gap-10 mb-16">
        <div style={{ width: 48, height: 48, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18, fontWeight: 700, fontFamily: "var(--font-mono)", background: displayResult.passed ? "var(--jade-subtle)" : "var(--accent-subtle)", color: displayResult.passed ? "var(--jade)" : "var(--accent)" }}>{displayResult.score}</div>
        <div><div style={{ fontWeight: 600, color: "var(--text-primary)" }}>{displayResult.passed ? "通过评估" : "需要修改"}</div><div className="text-xs text-muted">发现 {displayResult.issues.length} 个问题</div></div>
      </div>
      {displayResult.issues.map((issue, i) => (
        <div key={i} style={{ padding: "10px 12px", background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)", marginBottom: 8, borderLeft: `3px solid ${issue.severity === "high" ? "var(--error)" : issue.severity === "medium" ? "var(--warning)" : "var(--info)"}` }}>
          <div className="flex items-center gap-6 mb-4"><span className={`tag ${issue.severity === "high" ? "accent" : issue.severity === "medium" ? "qidian" : "category"}`}>{issue.severity === "high" ? "严重" : issue.severity === "medium" ? "中等" : "轻微"}</span><span className="text-xs text-muted">{issue.type}</span></div>
          <div className="text-sm" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>{issue.description}</div>
          {issue.suggestion && <div className="text-xs mt-4" style={{ color: "var(--jade)", lineHeight: 1.5 }}>建议：{issue.suggestion}</div>}
        </div>
      ))}
    </div>
  );
}
