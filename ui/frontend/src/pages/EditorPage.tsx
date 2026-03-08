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

  const getAgentCompletionMessage = (agent: string): string => {
    const msgs: Record<string, string> = {
      "Scene Director": "已完成场景拆分，共生成 3 个场景，包含导演指令和镜头节奏标记。",
      "Actor Agents": "角色对话与内心独白已生成。主角情感弧线从困惑到决心，配角形成明确对立关系。",
      "Editor-Writer": "文学风格化完成。已按 ~600 字/段分段，融入了你的风格偏好和节奏模板。",
      "Evaluator": "评估完成。一致性得分 87/100，发现 2 个轻微问题。",
    };
    return msgs[agent] || "处理完成。";
  };

  const getAgentQuestion = (agent: string): string => {
    const q: Record<string, string> = {
      "Scene Director": "场景拆分完成。请确认以下场景划分是否满意：\n\n**方案 A：三幕式**\n1. 开场 - 主角入场（200字）\n2. 冲突 - 与对手对峙（350字）\n3. 转折 - 发现隐藏线索（250字）\n\n**方案 B：渐进式**\n1. 日常描写 - 铺垫氛围（150字）\n2. 伏笔 - 异常迹象（200字）\n3. 触发 - 冲突爆发（300字）\n4. 余波 - 信息揭示（150字）\n\n请选择方案或输入修改意见：",
      "Actor Agents": "角色对话已生成。请检查角色的说话风格是否符合预期。\n\n满意请点击「确认继续」，或说明需要调整的部分。",
      "Editor-Writer": "文学润色完成。当前风格偏向「冷峻简约」，如需调整风格倾向请说明。\n\n满意请点击「确认继续」进入最终评估。",
    };
    return q[agent] || "处理完成，请确认是否继续。";
  };

  const simulateAgentWork = useCallback((agentName: string, stepIndex: number) => {
    setPipelineSteps(prev => prev.map((s, i) => i === stepIndex ? { ...s, status: "running" } : s));
    setCurrentAgent(agentName);
    setChatMessages(prev => [...prev, { agent: agentName, content: "正在处理中...", status: "thinking", timestamp: Date.now() }]);
    setTimeout(() => {
      setPipelineSteps(prev => prev.map((s, i) => i === stepIndex ? { ...s, status: "done", detail: "已完成" } : s));
      setChatMessages(prev => {
        const filtered = prev.filter(m => !(m.agent === agentName && m.status === "thinking"));
        return [...filtered, { agent: agentName, content: getAgentCompletionMessage(agentName), status: "done", timestamp: Date.now() }];
      });
      if (stepIndex < PIPELINE_STEPS.length - 1) {
        setTimeout(() => {
          setChatMessages(prev => [...prev, { agent: agentName, content: getAgentQuestion(agentName), status: "waiting_confirm", timestamp: Date.now(), isQuestion: true }]);
          setWaitingForConfirm(true);
        }, 500);
      } else {
        setGenerating(false); setCurrentAgent(null);
        setChatMessages(prev => [...prev, { agent: "System", content: "Pipeline 全部完成！可在「评估」标签查看结果。", status: "done", timestamp: Date.now() }]);
      }
    }, 2000);
  }, []);

  const startGeneration = useCallback(async () => {
    if (!activeCh) return;
    setGenerating(true);
    setPipelineSteps(PIPELINE_STEPS.map(s => ({ ...s, status: "pending" })));
    setChatMessages([]); setWaitingForConfirm(false);
    try { await apiPost("/api/generation/start", { project_id: projectId, chapter_id: activeChId, synopsis: activeCh.synopsis || "" }); } catch {}
    setChatMessages([{ agent: "System", content: `Pipeline 启动！基于大纲「${(activeCh.synopsis || "").slice(0, 50)}${(activeCh.synopsis || "").length > 50 ? "..." : ""}」开始生成。`, status: "done", timestamp: Date.now() }]);
    setTimeout(() => simulateAgentWork("Scene Director", 0), 800);
  }, [activeCh, projectId, activeChId, simulateAgentWork]);

  const handleConfirmContinue = () => {
    setWaitingForConfirm(false);
    setChatMessages(prev => [...prev, { agent: "User", content: "确认满意，继续下一步。", status: "done", timestamp: Date.now() }]);
    const currentIdx = PIPELINE_STEPS.findIndex(s => s.step === currentAgent);
    if (currentIdx >= 0 && currentIdx < PIPELINE_STEPS.length - 1) {
      setTimeout(() => simulateAgentWork(PIPELINE_STEPS[currentIdx + 1].step, currentIdx + 1), 500);
    }
  };

  const sendChatMessage = () => {
    if (!chatInput.trim()) return;
    const msg = chatInput.trim();
    setChatMessages(prev => [...prev, { agent: "User", content: msg, status: "done", timestamp: Date.now() }]);
    setChatInput("");
    if (waitingForConfirm) {
      setWaitingForConfirm(false);
      setTimeout(() => {
        setChatMessages(prev => [...prev, { agent: currentAgent || "System", content: `收到反馈：「${msg}」。已根据你的意见调整，请再次确认。`, status: "waiting_confirm", timestamp: Date.now(), isQuestion: true }]);
        setWaitingForConfirm(true);
      }, 1000);
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
              onStartGeneration={() => { setAiTab("inspire"); setTimeout(() => { if (!generating) startGeneration(); }, 300); }} />}
            {aiTab === "inspire" && <InspireTab steps={pipelineSteps} generating={generating} onStart={startGeneration} chatMessages={chatMessages} chatInput={chatInput}
              onChatInputChange={setChatInput} onSendMessage={sendChatMessage} waitingForConfirm={waitingForConfirm} onConfirmContinue={handleConfirmContinue} />}
            {aiTab === "rewrite" && <RewriteTab selection={selection} prompt={rewritePrompt} onPromptChange={setRewritePrompt} model={rewriteModel} onModelChange={setRewriteModel} />}
            {aiTab === "eval" && <EvalTab result={evalResult} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function OutlineTab({ synopsis, onChange, onSave, onStartGeneration }: { synopsis: string; onChange: (v: string) => void; onSave: () => void; onStartGeneration: () => void; }) {
  const [time, setTime] = useState("");
  const [location, setLocation] = useState("");
  return (
    <div>
      <div className="label mb-8">章节剧情大纲</div>
      <textarea className="input" value={synopsis} onChange={e => onChange(e.target.value)} rows={10}
        placeholder={"在这里写这一章的剧情要点...\n\n例如：\n  主角初入宗门\n  与师兄发生冲突\n  发现隐藏洞穴"} style={{ lineHeight: 1.8, fontFamily: "var(--font-sans)" }} />
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
      <p className="text-xs text-muted mt-12" style={{ lineHeight: 1.6 }}>点击「开始生成」将自动跳转到灵感面板并启动 Pipeline。每步完成后 Agent 会询问你的意见。</p>
    </div>
  );
}

function InspireTab({ steps, generating, onStart, chatMessages, chatInput, onChatInputChange, onSendMessage, waitingForConfirm, onConfirmContinue }: {
  steps: PipelineStatus[]; generating: boolean; onStart: () => void; chatMessages: ChatMessage[]; chatInput: string;
  onChatInputChange: (v: string) => void; onSendMessage: () => void; waitingForConfirm: boolean; onConfirmContinue: () => void;
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
          <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", gap: 4 }}>
            <div style={{ width: "100%", height: 4, borderRadius: 2, background: s.status === "done" ? "var(--jade)" : s.status === "running" ? "var(--gold)" : "var(--border)", transition: "background 0.3s" }} />
            <span style={{ fontSize: 9, color: s.status === "done" ? "var(--jade)" : s.status === "running" ? "var(--gold)" : "var(--text-disabled)" }}>{s.step.split(" ")[0]}</span>
          </div>
        ))}
      </div>
      {/* Chat area */}
      <div style={{ flex: 1, overflowY: "auto", border: "1px solid var(--border)", borderRadius: "var(--radius-sm, 6px)", padding: 8, marginBottom: 10, minHeight: 200, maxHeight: 400, background: "var(--bg-app)" }}>
        {chatMessages.length === 0 && !generating && (
          <div style={{ padding: "32px 16px", textAlign: "center", color: "var(--text-tertiary)", fontSize: 13 }}>在「大纲」中点击「开始生成」启动 Pipeline</div>
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
                <div style={{ padding: "8px 12px", borderRadius: 10, background: style.bg, borderLeft: isUser ? "none" : `3px solid ${style.border}`, borderRight: isUser ? `3px solid ${style.border}` : "none", fontSize: 13, lineHeight: 1.6, color: "var(--text-primary)", wordBreak: "break-word", whiteSpace: "pre-wrap" }}>{msg.content}</div>
                {msg.agent !== "User" && msg.agent !== "System" && msg.status === "done" && (
                  <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px", marginTop: 4, color: "var(--text-tertiary)" }}
                    onClick={() => {}}>
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
            <button className="btn-primary" style={{ padding: "8px 20px", fontSize: 13, borderRadius: 20 }} onClick={() => {}}>
              确认完成，写入编辑器
            </button>
            <button className="btn" style={{ padding: "8px 16px", fontSize: 12, borderRadius: 20 }} onClick={() => {}}>
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
  return (
    <div>
      <div className="label mb-8">AI 重写选中文本</div>
      {selection ? (<>
        <div style={{ padding: "10px 12px", background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)", marginBottom: 12, fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.7, maxHeight: 120, overflowY: "auto", fontFamily: "var(--font-serif)", borderLeft: "3px solid var(--accent)" }}>&ldquo;{selection.text.length > 200 ? selection.text.slice(0, 200) + "..." : selection.text}&rdquo;</div>
        <div className="text-xs text-muted mb-12">选中了 {selection.text.length} 字（位置 {selection.start}-{selection.end}）</div>
        <div className="field mb-12"><label className="label">模型选择</label><select className="select w-full" value={model} onChange={e => onModelChange(e.target.value)}><option value="default">默认模型</option><option value="deepseek-chat">DeepSeek Chat</option><option value="gpt-4o">GPT-4o</option><option value="claude-sonnet">Claude Sonnet</option></select></div>
        <div className="field mb-12"><label className="label">重写指令（可选）</label><textarea className="input" value={prompt} onChange={e => onPromptChange(e.target.value)} rows={3} placeholder={"告诉 AI 你想怎么改...\n例如：更紧张、加入内心描写、换成第一人称"} /></div>
        <button className="btn-primary w-full" disabled>AI 重写此段落</button><p className="text-xs text-muted mt-8">连接模型后可用。</p>
      </>) : (<div className="empty-state" style={{ padding: "32px 16px" }}><h4>选中文本以重写</h4><p>在编辑器中选中文本，将出现「AI重写」按钮</p></div>)}
    </div>
  );
}

function EvalTab({ result }: { result: EvalResult | null }) {
  if (!result) return <div className="empty-state" style={{ padding: "32px 16px" }}><h4>暂无评估结果</h4><p>在「灵感」面板完成一次生成后，评估结果将显示在这里</p></div>;
  return (
    <div>
      <div className="flex items-center gap-10 mb-16">
        <div style={{ width: 48, height: 48, borderRadius: "50%", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18, fontWeight: 700, fontFamily: "var(--font-mono)", background: result.passed ? "var(--jade-subtle)" : "var(--accent-subtle)", color: result.passed ? "var(--jade)" : "var(--accent)" }}>{result.score}</div>
        <div><div style={{ fontWeight: 600, color: "var(--text-primary)" }}>{result.passed ? "通过评估" : "需要修改"}</div><div className="text-xs text-muted">发现 {result.issues.length} 个问题</div></div>
      </div>
      {result.issues.map((issue, i) => (
        <div key={i} style={{ padding: "10px 12px", background: "var(--bg-surface-2)", borderRadius: "var(--radius-sm)", marginBottom: 8, borderLeft: `3px solid ${issue.severity === "high" ? "var(--error)" : issue.severity === "medium" ? "var(--warning)" : "var(--info)"}` }}>
          <div className="flex items-center gap-6 mb-4"><span className={`tag ${issue.severity === "high" ? "accent" : issue.severity === "medium" ? "qidian" : "category"}`}>{issue.severity === "high" ? "严重" : issue.severity === "medium" ? "中等" : "轻微"}</span><span className="text-xs text-muted">{issue.type}</span></div>
          <div className="text-sm" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>{issue.description}</div>
          {issue.suggestion && <div className="text-xs mt-4" style={{ color: "var(--jade)", lineHeight: 1.5 }}>建议：{issue.suggestion}</div>}
        </div>
      ))}
    </div>
  );
}
