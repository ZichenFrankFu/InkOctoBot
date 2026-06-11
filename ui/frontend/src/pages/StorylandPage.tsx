import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useToast } from "../components/shared/Toast";

// 故事中世界（Storyland）页面 — spec 6.4.5 三 tab：
// 1. Storyland 状态：创世入口/审阅区、实体管理、SPO 事实（时间线/按章节）
// 2. 读者视角记忆：按章节查看 L1-L4
// 3. 故事线：主线/支线 + 伏笔（规模/超期提示），新建/编辑/删除

type TabKey = "state" | "memory" | "plotline";

interface Entity {
  entity_id: string; name: string; entity_type: string;
  description: string; introduced_chapter: number | null;
  aliases_json?: string; metadata_json?: string; auto_created?: number;
}
interface Fact {
  triple_id: string; subject: string; subject_type: string;
  predicate: string; object: string;
  valid_from_chapter: number; valid_to_chapter: number | null;
}
interface Hook {
  id: string; title: string; content: string; status: string;
  importance: string; scale: string;
  origin_chapter: number | null; expected_payoff_chapter: number | null;
}
interface Thread {
  thread_id: string; name: string; description: string; status: string;
  thread_type: "main" | "sub"; start_chapter: number;
  last_advanced_chapter: number | null;
}
interface GenesisProposal {
  proposal_id: string | null; stage?: string;
  entities?: { name: string; entity_type: string; description: string }[];
  facts?: { subject: string; predicate: string; object: string; step: number; basis: string }[];
}

const ENTITY_TYPE_LABEL: Record<string, string> = {
  character: "角色", location: "地点", item: "物品",
  organization: "组织", concept: "概念",
};
const SCALE_LABEL: Record<string, string> = {
  boomerang: "回旋镖(≤3章)", event_clue: "事件线索(≤20章)",
  grand_plan: "大计划(≤100章)", world_truth: "世界真相",
};
const HOOK_STATUS_LABEL: Record<string, string> = {
  open: "埋设", progressing: "推进中", pressured: "超期/待推进",
  near_payoff: "临近回收", resolved: "已回收", abandoned: "已放弃",
};
const THREAD_STATUS_LABEL: Record<string, string> = {
  setup: "开启", building: "推进", climax: "高潮",
  resolution: "完结", dormant: "搁置",
};

const sectionStyle: React.CSSProperties = {
  background: "var(--bg-surface)", borderRadius: 8,
  border: "1px solid var(--border-subtle)", padding: "14px 16px",
  marginBottom: 14,
};
const h2Style: React.CSSProperties = {
  fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
  marginBottom: 10, display: "flex", alignItems: "center", gap: 8,
};

export default function StorylandPage({ projectId }: { projectId: string }) {
  const { toast } = useToast();
  const pid = projectId || "default";
  const [tab, setTab] = useState<TabKey>("state");

  return (
    <div style={{ padding: 16, maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 14 }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>故事中世界</div>
        <div style={{ display: "flex", gap: 4 }}>
          {([["state", "Storyland 状态"], ["memory", "读者视角记忆"], ["plotline", "故事线"]] as [TabKey, string][]).map(([k, label]) => (
            <button key={k} className="btn"
              style={{
                fontSize: 12, padding: "4px 14px",
                background: tab === k ? "var(--accent-subtle, rgba(99,102,241,0.15))" : undefined,
                color: tab === k ? "var(--accent)" : undefined,
                borderColor: tab === k ? "var(--accent)" : undefined,
              }}
              onClick={() => setTab(k)}>{label}</button>
          ))}
        </div>
      </div>
      {tab === "state" && <StateTab projectId={pid} toast={toast} />}
      {tab === "memory" && <MemoryTab projectId={pid} toast={toast} />}
      {tab === "plotline" && <PlotlineTab projectId={pid} toast={toast} />}
    </div>
  );
}

// ─────────────── Tab 1: Storyland 状态 ───────────────

function StateTab({ projectId, toast }: { projectId: string; toast: (m: string, t?: any) => void }) {
  const [entities, setEntities] = useState<Entity[]>([]);
  const [facts, setFacts] = useState<Fact[]>([]);
  const [chapterFilter, setChapterFilter] = useState<string>("");
  const [proposal, setProposal] = useState<GenesisProposal | null>(null);
  const [genesisRunning, setGenesisRunning] = useState(false);

  const reload = useCallback(async () => {
    try {
      const [e, f, g] = await Promise.all([
        apiGet<{ items: Entity[] }>(`/api/entities?project_id=${projectId}`).catch(() => ({ items: [] })),
        apiGet<{ items: Fact[] }>(
          `/api/storyland/state?project_id=${projectId}` +
          (chapterFilter ? `&chapter_num=${chapterFilter}` : ""),
        ),
        apiGet<GenesisProposal>(`/api/genesis/proposal?project_id=${projectId}`),
      ]);
      setEntities((e as any).items || (e as any) || []);
      setFacts(f.items || []);
      setProposal(g.proposal_id ? g : null);
    } catch (err: any) { toast(err.message || "加载失败", "error"); }
  }, [projectId, chapterFilter]);

  useEffect(() => { reload(); }, [reload]);

  const runGenesis = async () => {
    setGenesisRunning(true);
    try {
      const r = await apiPost<GenesisProposal>(
        `/api/genesis/run?project_id=${projectId}`, {}, { timeoutMs: 300000 });
      setProposal(r);
      toast("创世提案已生成，请在审阅区确认", "success");
    } catch (e: any) { toast(e.message || "创世失败", "error"); }
    finally { setGenesisRunning(false); }
  };

  const applyGenesis = async () => {
    if (!proposal?.proposal_id) return;
    try {
      const r = await apiPost<any>("/api/genesis/apply", {
        project_id: projectId, proposal_id: proposal.proposal_id,
        entities: proposal.entities, facts: proposal.facts,
      });
      toast(`创世完成：实体 ${r.entities_created} 个、事实 ${r.facts_created} 条已入库`, "success");
      setProposal(null);
      reload();
    } catch (e: any) { toast(e.message || "入库失败", "error"); }
  };

  const discardGenesis = async () => {
    if (!proposal?.proposal_id) return;
    try {
      await apiPost(`/api/genesis/discard?project_id=${projectId}&proposal_id=${proposal.proposal_id}`, {});
      setProposal(null);
      toast("已丢弃创世提案", "info");
    } catch (e: any) { toast(e.message || "操作失败", "error"); }
  };

  const dropProposalEntity = (name: string) => {
    if (!proposal) return;
    setProposal({
      ...proposal,
      entities: (proposal.entities || []).filter(e => e.name !== name),
      facts: (proposal.facts || []).filter(f => f.subject !== name && f.object !== name),
    });
  };
  const dropProposalFact = (idx: number) => {
    if (!proposal) return;
    setProposal({ ...proposal, facts: (proposal.facts || []).filter((_, i) => i !== idx) });
  };

  const deleteEntity = async (id: string) => {
    try { await apiDelete(`/api/entities/${id}`); reload(); }
    catch (e: any) { toast(e.message || "删除失败", "error"); }
  };

  return (
    <div>
      <div style={sectionStyle}>
        <div style={h2Style}>
          创世（从角色卡 + 世界书生成舞台全貌）
          <button className="btn" style={{ fontSize: 11, padding: "3px 12px", marginLeft: "auto" }}
            onClick={runGenesis} disabled={genesisRunning}>
            {genesisRunning ? "生成中..." : proposal ? "重新创世" : "运行创世"}
          </button>
        </div>
        {!proposal && (
          <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            单次 LLM 调用按五步顺序（物质基础、结构骨架、实体落位、宏观状态、文化风气）补全舞台。
            生成内容标记为 AI 生成，须经审阅确认后才入库。
          </div>
        )}
        {proposal && (
          <div>
            <div style={{ fontSize: 12, color: "var(--gold)", marginBottom: 8 }}>
              审阅区 — 舞台：{proposal.stage || "（未指定）"}；不需要的条目可移除，确认后整体入库。
            </div>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>实体（{proposal.entities?.length || 0}）</div>
            {(proposal.entities || []).map(e => (
              <div key={e.name} style={{ display: "flex", gap: 8, alignItems: "center", padding: "3px 0", fontSize: 11, borderBottom: "1px solid var(--border-subtle)" }}>
                <span style={{ padding: "1px 6px", borderRadius: 8, background: "var(--bg-secondary)" }}>{ENTITY_TYPE_LABEL[e.entity_type] || e.entity_type}</span>
                <span style={{ fontWeight: 600 }}>{e.name}</span>
                <span style={{ color: "var(--text-tertiary)", flex: 1 }}>{e.description}</span>
                <button className="btn" style={{ fontSize: 10, padding: "1px 8px", color: "var(--error)" }}
                  onClick={() => dropProposalEntity(e.name)}>移除</button>
              </div>
            ))}
            <div style={{ fontSize: 12, fontWeight: 600, margin: "8px 0 4px" }}>事实（{proposal.facts?.length || 0}）</div>
            {(proposal.facts || []).map((f, i) => (
              <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", padding: "3px 0", fontSize: 11, borderBottom: "1px solid var(--border-subtle)" }}>
                <span style={{ color: "var(--text-tertiary)" }}>步骤{f.step}</span>
                <span>{f.subject} <span style={{ color: "var(--accent)" }}>{f.predicate}</span> {f.object}</span>
                <span style={{ color: "var(--text-disabled)", flex: 1 }}>{f.basis}</span>
                <button className="btn" style={{ fontSize: 10, padding: "1px 8px", color: "var(--error)" }}
                  onClick={() => dropProposalFact(i)}>移除</button>
              </div>
            ))}
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button className="btn-primary" style={{ fontSize: 11, padding: "4px 16px" }} onClick={applyGenesis}>确认入库</button>
              <button className="btn" style={{ fontSize: 11, padding: "4px 16px" }} onClick={discardGenesis}>丢弃</button>
            </div>
          </div>
        )}
      </div>

      <div style={sectionStyle}>
        <div style={h2Style}>
          实体（{entities.length}）
        </div>
        {entities.length === 0 && <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无实体。运行创世或提交章节后自动产生。</div>}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 8 }}>
          {entities.map(e => {
            let isGenesis = false;
            try { isGenesis = JSON.parse(e.metadata_json || "{}").origin === "genesis"; } catch {}
            return (
              <div key={e.entity_id} style={{ border: "1px solid var(--border-subtle)", borderRadius: 6, padding: "8px 10px", fontSize: 11 }}>
                <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 3 }}>
                  <span style={{ fontWeight: 600, fontSize: 12 }}>{e.name}</span>
                  <span style={{ padding: "1px 6px", borderRadius: 8, background: "var(--bg-secondary)", fontSize: 9 }}>{ENTITY_TYPE_LABEL[e.entity_type] || e.entity_type}</span>
                  {(isGenesis || e.auto_created === 1) && (
                    <span style={{ padding: "1px 6px", borderRadius: 8, background: "var(--gold-subtle, rgba(212,168,83,0.12))", color: "var(--gold)", fontSize: 9 }}>AI生成</span>
                  )}
                  <button className="btn" style={{ fontSize: 9, padding: "0 6px", marginLeft: "auto", color: "var(--error)" }}
                    onClick={() => deleteEntity(e.entity_id)}>删</button>
                </div>
                <div style={{ color: "var(--text-tertiary)" }}>{e.description || "（无描述）"}</div>
                {e.introduced_chapter !== null && (
                  <div style={{ color: "var(--text-disabled)", fontSize: 9, marginTop: 2 }}>
                    {e.introduced_chapter === 0 ? "初始生成" : `第${e.introduced_chapter}章引入`}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={h2Style}>
          客观事实（SPO）
          <input className="input" placeholder="按章节查看（留空=当前全部）"
            value={chapterFilter}
            onChange={e => setChapterFilter(e.target.value.replace(/[^0-9]/g, ""))}
            style={{ fontSize: 11, width: 180, marginLeft: "auto" }} />
        </div>
        {facts.length === 0 && <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无事实。</div>}
        {facts.map(f => (
          <div key={f.triple_id} style={{ display: "flex", gap: 8, alignItems: "center", padding: "3px 0", fontSize: 11, borderBottom: "1px solid var(--border-subtle)" }}>
            <span style={{ color: "var(--text-disabled)", width: 70 }}>
              {f.valid_from_chapter === 0 ? "初始" : `第${f.valid_from_chapter}章起`}
            </span>
            <span style={{ fontWeight: 600 }}>{f.subject}</span>
            <span style={{ color: "var(--accent)" }}>{f.predicate}</span>
            <span>{f.object}</span>
            {f.valid_to_chapter !== null && (
              <span style={{ color: "var(--text-disabled)", marginLeft: "auto" }}>已于第{f.valid_to_chapter}章失效</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────── Tab 2: 读者视角记忆 ───────────────

function MemoryTab({ projectId, toast }: { projectId: string; toast: (m: string, t?: any) => void }) {
  const [chapters, setChapters] = useState<{ chapter_id: string; chapter_num: number; title: string }[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [view, setView] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    apiGet<any>(`/api/data/editor?project_id=${encodeURIComponent(projectId)}`).then(doc => {
      const list: any[] = [];
      (doc?.volumes || []).forEach((v: any) => (v.chapters || []).forEach((c: any) => {
        list.push({ chapter_id: c.id || c.chapter_id, chapter_num: c.chapter_num || list.length + 1, title: c.title || "" });
      }));
      setChapters(list);
      if (list.length && !selected) setSelected(list[list.length - 1].chapter_id);
    }).catch(() => setChapters([]));
  }, [projectId]);

  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    apiGet<any>(`/api/historical-view/${selected}`)
      .then(setView)
      .catch((e: any) => { setView(null); toast(e.message || "加载失败", "error"); })
      .finally(() => setLoading(false));
  }, [selected]);

  return (
    <div>
      <div style={sectionStyle}>
        <div style={h2Style}>
          按章节查看读者视角记忆
          <select className="select" value={selected} onChange={e => setSelected(e.target.value)}
            style={{ fontSize: 11, marginLeft: "auto", minWidth: 200 }}>
            {chapters.length === 0 && <option value="">（无已建章节）</option>}
            {chapters.map(c => (
              <option key={c.chapter_id} value={c.chapter_id}>第{c.chapter_num}章 {c.title}</option>
            ))}
          </select>
        </div>
        <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 8 }}>
          展示生成第 K 章时读者的记忆状态（前 K-1 章的累积）：L1 刚刚发生、L2 近期摘要、L3 相似召回、L4 标志性事件。
        </div>
        {loading && <div style={{ fontSize: 11 }}>加载中...</div>}
        {!loading && view && (
          <div>
            {(view.tabs || []).map((t: any, i: number) => (
              <div key={i} style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "var(--accent)", marginBottom: 4 }}>{t.label || t.name || `板块${i + 1}`}</div>
                <pre style={{ fontSize: 11, whiteSpace: "pre-wrap", color: "var(--text-secondary)", background: "var(--bg-secondary)", borderRadius: 6, padding: "8px 10px", maxHeight: 320, overflow: "auto" }}>
                  {typeof t.content === "string" ? t.content : JSON.stringify(t.content ?? t, null, 2)}
                </pre>
              </div>
            ))}
            {!view.tabs && (
              <pre style={{ fontSize: 11, whiteSpace: "pre-wrap", color: "var(--text-secondary)", background: "var(--bg-secondary)", borderRadius: 6, padding: "8px 10px", maxHeight: 480, overflow: "auto" }}>
                {JSON.stringify(view, null, 2)}
              </pre>
            )}
          </div>
        )}
        {!loading && !view && selected && (
          <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>该章节暂无记忆快照（需先 commit 一次）。</div>
        )}
      </div>
    </div>
  );
}

// ─────────────── Tab 3: 故事线（主线/支线 + 伏笔） ───────────────

function PlotlineTab({ projectId, toast }: { projectId: string; toast: (m: string, t?: any) => void }) {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [currentChapter, setCurrentChapter] = useState<number>(0);
  const [newThread, setNewThread] = useState({ name: "", description: "", thread_type: "sub" as "main" | "sub" });
  const [newHook, setNewHook] = useState({ description: "", scale: "event_clue", origin_chapter: 1 });

  const reload = useCallback(async () => {
    try {
      const [t, h] = await Promise.all([
        apiGet<{ items: Thread[] }>(`/api/storyland/subplots?project_id=${projectId}`),
        apiGet<{ items: Hook[] }>(`/api/data/foreshadowing/${projectId}`),
      ]);
      setThreads(t.items || []);
      setHooks(h.items || []);
      const maxOrigin = Math.max(0, ...(h.items || []).map(x => x.origin_chapter || 0));
      setCurrentChapter(c => c || maxOrigin);
    } catch (e: any) { toast(e.message || "加载失败", "error"); }
  }, [projectId]);

  useEffect(() => { reload(); }, [reload]);

  const createThread = async () => {
    if (!newThread.name.trim()) { toast("故事线名称必填", "error"); return; }
    try {
      await apiPost("/api/storyland/subplots", { project_id: projectId, ...newThread });
      setNewThread({ name: "", description: "", thread_type: "sub" });
      reload();
    } catch (e: any) { toast(e.message || "创建失败", "error"); }
  };

  const createHook = async () => {
    if (!newHook.description.trim()) { toast("伏笔概述必填", "error"); return; }
    try {
      await apiPost("/api/storyland/hooks", { project_id: projectId, ...newHook });
      setNewHook({ description: "", scale: "event_clue", origin_chapter: 1 });
      reload();
    } catch (e: any) { toast(e.message || "创建失败", "error"); }
  };

  const isOverdue = (h: Hook) =>
    h.expected_payoff_chapter !== null && h.scale !== "world_truth" &&
    currentChapter > 0 && currentChapter >= h.expected_payoff_chapter &&
    !["resolved", "abandoned"].includes(h.status);

  const mains = threads.filter(t => t.thread_type === "main");
  const subs = threads.filter(t => t.thread_type === "sub");
  const activeHooks = hooks.filter(h => !["resolved", "abandoned"].includes(h.status));
  const doneHooks = hooks.filter(h => ["resolved", "abandoned"].includes(h.status));

  const renderThread = (t: Thread) => (
    <div key={t.thread_id} style={{ display: "flex", gap: 8, alignItems: "center", padding: "5px 0", fontSize: 11, borderBottom: "1px solid var(--border-subtle)" }}>
      <span style={{ padding: "1px 6px", borderRadius: 8, fontSize: 9, background: t.thread_type === "main" ? "var(--accent-subtle, rgba(99,102,241,0.15))" : "var(--bg-secondary)", color: t.thread_type === "main" ? "var(--accent)" : "var(--text-secondary)" }}>
        {t.thread_type === "main" ? "主线" : "支线"}
      </span>
      <span style={{ fontWeight: 600 }}>{t.name}</span>
      <span style={{ color: "var(--text-tertiary)", flex: 1 }}>{t.description}</span>
      <span style={{ color: "var(--text-disabled)" }}>{THREAD_STATUS_LABEL[t.status] || t.status}</span>
      <span style={{ color: "var(--text-disabled)" }}>
        第{t.start_chapter}章起{t.last_advanced_chapter ? ` · 最近第${t.last_advanced_chapter}章推进` : ""}
      </span>
      <select className="select" style={{ fontSize: 10 }} value={t.status}
        onChange={async e => {
          try { await apiPut(`/api/storyland/subplots/${t.thread_id}`, { status: e.target.value }); reload(); }
          catch (err: any) { toast(err.message || "更新失败", "error"); }
        }}>
        {Object.entries(THREAD_STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      <button className="btn" style={{ fontSize: 9, padding: "1px 8px", color: "var(--error)" }}
        onClick={async () => {
          try { await apiDelete(`/api/storyland/subplots/${t.thread_id}`); reload(); }
          catch (err: any) { toast(err.message || "删除失败", "error"); }
        }}>删除</button>
    </div>
  );

  const renderHook = (h: Hook) => {
    const overdue = isOverdue(h);
    return (
      <div key={h.id} style={{
        display: "flex", gap: 8, alignItems: "center", padding: "5px 0",
        fontSize: 11, borderBottom: "1px solid var(--border-subtle)",
        borderLeft: overdue ? "3px solid var(--error)" : "3px solid transparent",
        paddingLeft: 6,
      }}>
        <span style={{ padding: "1px 6px", borderRadius: 8, fontSize: 9, background: "var(--bg-secondary)" }}>{SCALE_LABEL[h.scale] || h.scale}</span>
        <span style={{ flex: 1 }}>{h.content}</span>
        <span style={{ color: overdue ? "var(--error)" : "var(--text-disabled)" }}>
          {HOOK_STATUS_LABEL[h.status] || h.status}{overdue ? " · 应回收" : ""}
        </span>
        <span style={{ color: "var(--text-disabled)" }}>
          第{h.origin_chapter}章埋{h.expected_payoff_chapter ? ` · 预期第${h.expected_payoff_chapter}章前收` : " · 不限期"}
        </span>
        {!["resolved", "abandoned"].includes(h.status) && (
          <button className="btn" style={{ fontSize: 9, padding: "1px 8px" }}
            onClick={async () => {
              try {
                await apiPost(`/api/data/foreshadowing/${h.id}/fully-resolve`, { chapter_num: currentChapter || null });
                reload();
              } catch (err: any) { toast(err.message || "操作失败", "error"); }
            }}>标记已回收</button>
        )}
        <button className="btn" style={{ fontSize: 9, padding: "1px 8px", color: "var(--error)" }}
          onClick={async () => {
            try { await apiDelete(`/api/storyland/hooks/${h.id}`); reload(); }
            catch (err: any) { toast(err.message || "删除失败", "error"); }
          }}>删除</button>
      </div>
    );
  };

  return (
    <div>
      <div style={sectionStyle}>
        <div style={h2Style}>
          主线 / 支线（{threads.length}）
          <input className="input" placeholder="当前章号（超期判断用）" value={currentChapter || ""}
            onChange={e => setCurrentChapter(parseInt(e.target.value) || 0)}
            style={{ fontSize: 11, width: 160, marginLeft: "auto" }} />
        </div>
        {mains.length === 0 && (
          <div style={{ fontSize: 11, color: "var(--gold)", marginBottom: 6 }}>
            尚未设定主线（每个项目有且仅有一条主线）。
          </div>
        )}
        {mains.map(renderThread)}
        {subs.map(renderThread)}
        <div style={{ display: "flex", gap: 6, marginTop: 10, alignItems: "center" }}>
          <select className="select" style={{ fontSize: 11 }} value={newThread.thread_type}
            onChange={e => setNewThread({ ...newThread, thread_type: e.target.value as any })}>
            <option value="sub">支线</option>
            <option value="main">主线</option>
          </select>
          <input className="input" placeholder="故事线名称" value={newThread.name}
            onChange={e => setNewThread({ ...newThread, name: e.target.value })}
            style={{ fontSize: 11, width: 160 }} />
          <input className="input" placeholder="一段话概述（这条线讲什么）" value={newThread.description}
            onChange={e => setNewThread({ ...newThread, description: e.target.value })}
            style={{ fontSize: 11, flex: 1 }} />
          <button className="btn-primary" style={{ fontSize: 11, padding: "3px 14px" }} onClick={createThread}>新建</button>
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={h2Style}>未回收伏笔（{activeHooks.length}）</div>
        {activeHooks.length === 0 && <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无未回收伏笔。</div>}
        {activeHooks.map(renderHook)}
        <div style={{ display: "flex", gap: 6, marginTop: 10, alignItems: "center" }}>
          <select className="select" style={{ fontSize: 11 }} value={newHook.scale}
            onChange={e => setNewHook({ ...newHook, scale: e.target.value })}>
            {Object.entries(SCALE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <input className="input" type="number" min={1} value={newHook.origin_chapter}
            onChange={e => setNewHook({ ...newHook, origin_chapter: parseInt(e.target.value) || 1 })}
            style={{ fontSize: 11, width: 90 }} title="埋设章节" />
          <input className="input" placeholder="伏笔概述（含待揭示的真相）" value={newHook.description}
            onChange={e => setNewHook({ ...newHook, description: e.target.value })}
            style={{ fontSize: 11, flex: 1 }} />
          <button className="btn-primary" style={{ fontSize: 11, padding: "3px 14px" }} onClick={createHook}>埋设</button>
        </div>
      </div>

      {doneHooks.length > 0 && (
        <div style={sectionStyle}>
          <div style={h2Style}>已回收 / 已放弃（{doneHooks.length}）</div>
          {doneHooks.map(renderHook)}
        </div>
      )}
    </div>
  );
}
