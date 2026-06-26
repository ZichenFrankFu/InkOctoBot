import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useToast } from "../components/shared/Toast";
import UniversalLLMDialog from "../components/shared/UniversalLLMDialog";
import ChapterTimeline from "../components/shared/ChapterTimeline";

// 故事中世界（Storyland）页面 — 双 tab：
// 1. Storyland 状态：创世入口/审阅区、实体管理、SPO 事实（时间线/按章节）
// 2. 读者视角记忆：按章节查看 L1-L4
// 注：原「故事线」tab 已迁至 故事线 page 的「故事线与伏笔总览」section，
//    将故事线 / 伏笔的 CRUD 与可视化时间线放在一起更聚拢。

type TabKey = "state" | "memory";

interface ChapterRef { chapter_id: string; chapter_num: number; title: string; }

/** Hook: pulls the project's chapter list from /api/data/editor and
 *  derives the {min, max, marks} the chapter-timeline needs. Re-fetches
 *  when the project changes; consumers don't need to know the wire
 *  format. */
function useProjectChapters(projectId: string) {
  const [chapters, setChapters] = useState<ChapterRef[]>([]);
  useEffect(() => {
    apiGet<any>(`/api/data/editor?project_id=${encodeURIComponent(projectId)}`)
      .then(doc => {
        const list: ChapterRef[] = [];
        (doc?.volumes || []).forEach((v: any) => (v.chapters || []).forEach((c: any) => {
          list.push({
            chapter_id: c.id || c.chapter_id,
            chapter_num: c.chapter_num || list.length + 1,
            title: c.title || "",
          });
        }));
        setChapters(list);
      })
      .catch(() => setChapters([]));
  }, [projectId]);
  return chapters;
}

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
interface GenesisProposal {
  proposal_id: string | null; stage?: string;
  entities?: { name: string; entity_type: string; description: string }[];
  facts?: { subject: string; predicate: string; object: string; step: number; basis: string }[];
}

const ENTITY_TYPE_LABEL: Record<string, string> = {
  character: "角色", location: "地点", item: "物品",
  organization: "组织", concept: "概念",
};

/** Section header styled like the other pages — small accent-coloured
 *  divider with optional right-aligned actions. */
function SectionHeader({ title, count, action }: {
  title: string; count?: number; action?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between" style={{
      padding: "10px 16px", borderBottom: "1px solid var(--border)",
    }}>
      <h3 style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>
        {title}
        {count !== undefined && (
          <span className="text-xs text-muted" style={{ marginLeft: 6, fontWeight: 400 }}>
            ({count})
          </span>
        )}
      </h3>
      {action}
    </div>
  );
}

export default function StorylandPage({ projectId }: { projectId: string }) {
  const { toast } = useToast();
  const pid = projectId || "default";
  const [tab, setTab] = useState<TabKey>("state");
  const chapters = useProjectChapters(pid);

  return (
    <div className="page-container" style={{ maxWidth: 1100 }}>
      <div className="page-header" style={{ paddingBottom: 12 }}>
        <div className="page-header-row">
          <div>
            <h2 className="font-serif">故事中世界</h2>
            <p>跨章节的 Storyland 状态 · 读者视角记忆 · 故事线 / 伏笔</p>
          </div>
        </div>
      </div>

      <div className="tab-bar-underline" style={{ marginBottom: 14 }}>
        {([
          ["state",  "Storyland 状态"],
          ["memory", "章节回看（5 个回看面板）"],
        ] as [TabKey, string][]).map(([k, label]) => (
          <button key={k}
            className={`tab-item ${tab === k ? "active" : ""}`}
            onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {tab === "state" && <StateTab projectId={pid} chapters={chapters} toast={toast} />}
      {tab === "memory" && <MemoryTab projectId={pid} chapters={chapters} toast={toast} />}
    </div>
  );
}

// ─────────────── Tab 1: Storyland 状态 ───────────────

function StateTab({ projectId, chapters, toast }: {
  projectId: string;
  chapters: ChapterRef[];
  toast: (m: string, t?: any) => void;
}) {
  const [entities, setEntities] = useState<Entity[]>([]);
  const [facts, setFacts] = useState<Fact[]>([]);
  // Single-handle timeline scope for SPO facts. `chapterFocus === 0` means
  // "all chapters" — matches the legacy「留空」behaviour.
  const [chapterFocus, setChapterFocus] = useState<number>(0);
  const [proposal, setProposal] = useState<GenesisProposal | null>(null);
  const [genesisRunning, setGenesisRunning] = useState(false);

  // Drive the timeline's range from the project's actual chapter list so
  // it grows automatically as the user adds chapter outlines / commits
  // chapters. Marks are every chapter that exists.
  const chapterMin = 1;
  const chapterMax = chapters.length > 0
    ? Math.max(...chapters.map(c => c.chapter_num || 0))
    : 1;
  const chapterMarks = useMemo(() => chapters.map(c => c.chapter_num), [chapters]);

  const reload = useCallback(async () => {
    try {
      const [e, f, g] = await Promise.all([
        apiGet<{ items: Entity[] }>(`/api/entities?project_id=${projectId}`).catch(() => ({ items: [] })),
        apiGet<{ items: Fact[] }>(
          `/api/storyland/state?project_id=${projectId}` +
          (chapterFocus > 0 ? `&chapter_num=${chapterFocus}` : ""),
        ),
        apiGet<GenesisProposal>(`/api/genesis/proposal?project_id=${projectId}`),
      ]);
      setEntities((e as any).items || (e as any) || []);
      setFacts(f.items || []);
      setProposal(g.proposal_id ? g : null);
    } catch (err: any) { toast(err.message || "加载失败", "error"); }
  }, [projectId, chapterFocus]);

  useEffect(() => { reload(); }, [reload]);

  // LLM交互·机制4: 创世支持 API 模式与手动模式（复制 prompt → 网页版
  // → 粘贴回 → 自动解析存为待审提案），统一走 UniversalLLMDialog。
  const [genesisDialogOpen, setGenesisDialogOpen] = useState(false);
  const [genesisPrompt, setGenesisPrompt] = useState<{ prompt: string; system: string } | null>(null);

  const openGenesisDialog = async () => {
    setGenesisRunning(true);
    try {
      const r = await apiGet<{ prompt: string; system: string }>(
        `/api/genesis/prompt?project_id=${projectId}`);
      setGenesisPrompt(r);
      setGenesisDialogOpen(true);
    } catch (e: any) { toast(e.message || "无法装配创世 prompt", "error"); }
    finally { setGenesisRunning(false); }
  };

  const commitGenesis = async (payload: { text: string }) => {
    const r = await apiPost<GenesisProposal>("/api/genesis/submit", {
      project_id: projectId, raw: payload.text,
    });
    setProposal(r);
    setGenesisDialogOpen(false);
    toast("创世提案已解析，请在审阅区确认", "success");
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
      <UniversalLLMDialog
        open={genesisDialogOpen}
        onClose={() => setGenesisDialogOpen(false)}
        title="Storyland 创世"
        description="单次 LLM 调用按五步顺序补全舞台全貌；支持 API 调用或复制 prompt 到大模型网页版后粘贴回结果（自动解析为待审提案）。"
        prompt={genesisPrompt?.prompt || ""}
        system={genesisPrompt?.system || ""}
        invokeApi={async (signal) => {
          const resp = await fetch(
            `/api/genesis/generate?project_id=${encodeURIComponent(projectId)}`,
            { method: "POST", signal },
          );
          if (!resp.ok) {
            const err = await resp.json().catch(() => ({} as any));
            throw new Error(err?.detail || `HTTP ${resp.status}`);
          }
          const data = await resp.json();
          return data.raw || "";
        }}
        onCommit={commitGenesis}
        minChars={20}
      />
      <div className="card mb-16">
        <SectionHeader title="创世（从角色卡 + 世界书生成舞台全貌）"
          action={
            <button className="btn" style={{ fontSize: 11, padding: "3px 12px" }}
              onClick={openGenesisDialog} disabled={genesisRunning}>
              {genesisRunning ? "准备中..." : proposal ? "重新创世" : "运行创世"}
            </button>
          } />
        <div className="card-body">
          {!proposal && (
            <div className="text-xs text-muted" style={{ lineHeight: 1.6 }}>
              单次 LLM 调用按五步顺序（物质基础、结构骨架、实体落位、宏观状态、文化风气）补全舞台。
              生成内容标记为 AI 生成，须经审阅确认后才入库。
            </div>
          )}
          {proposal && (
            <div>
              <div style={{ fontSize: 12, color: "var(--gold)", marginBottom: 10 }}>
                审阅区 — 舞台：{proposal.stage || "（未指定）"}；不需要的条目可移除，确认后整体入库。
              </div>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>实体（{proposal.entities?.length || 0}）</div>
              {(proposal.entities || []).map(e => (
                <div key={e.name} style={{ display: "flex", gap: 8, alignItems: "center", padding: "4px 0", fontSize: 11, borderBottom: "1px solid var(--border)" }}>
                  <span className="tag" style={{ fontSize: 9 }}>{ENTITY_TYPE_LABEL[e.entity_type] || e.entity_type}</span>
                  <span style={{ fontWeight: 600 }}>{e.name}</span>
                  <span style={{ color: "var(--text-tertiary)", flex: 1 }}>{e.description}</span>
                  <button className="btn-icon" title="移除" style={{ fontSize: 13, color: "var(--text-tertiary)" }}
                    onClick={() => dropProposalEntity(e.name)}>×</button>
                </div>
              ))}
              <div style={{ fontSize: 12, fontWeight: 600, margin: "10px 0 4px" }}>事实（{proposal.facts?.length || 0}）</div>
              {(proposal.facts || []).map((f, i) => (
                <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", padding: "4px 0", fontSize: 11, borderBottom: "1px solid var(--border)" }}>
                  <span className="text-xs text-muted">步骤{f.step}</span>
                  <span>{f.subject} <span style={{ color: "var(--accent)" }}>{f.predicate}</span> {f.object}</span>
                  <span style={{ color: "var(--text-disabled)", flex: 1 }}>{f.basis}</span>
                  <button className="btn-icon" title="移除" style={{ fontSize: 13, color: "var(--text-tertiary)" }}
                    onClick={() => dropProposalFact(i)}>×</button>
                </div>
              ))}
              <div className="flex gap-8" style={{ marginTop: 12 }}>
                <button className="btn-primary" onClick={applyGenesis}>确认入库</button>
                <button className="btn" onClick={discardGenesis}>丢弃</button>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="card mb-16">
        <SectionHeader title="实体" count={entities.length} />
        <div className="card-body">
          {entities.length === 0 && (
            <div className="empty-state" style={{ padding: "16px 0" }}>
              <p>暂无实体。运行创世或提交章节后自动产生。</p>
            </div>
          )}
          {entities.length > 0 && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 10 }}>
              {entities.map(e => {
                let isGenesis = false;
                try { isGenesis = JSON.parse(e.metadata_json || "{}").origin === "genesis"; } catch { /* noop */ }
                return (
                  <div key={e.entity_id} style={{
                    border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
                    padding: "10px 12px", background: "var(--bg-surface)",
                  }}>
                    <div className="flex items-center gap-6" style={{ marginBottom: 4 }}>
                      <span style={{ fontWeight: 600, fontSize: 13 }}>{e.name}</span>
                      <span className="tag" style={{ fontSize: 10 }}>{ENTITY_TYPE_LABEL[e.entity_type] || e.entity_type}</span>
                      {(isGenesis || e.auto_created === 1) && (
                        <span className="tag" style={{ fontSize: 10, background: "var(--gold-subtle)", color: "var(--gold)", borderColor: "var(--gold)" }}>AI生成</span>
                      )}
                      <button className="btn-icon" title="删除" style={{ marginLeft: "auto", fontSize: 13, color: "var(--text-tertiary)" }}
                        onClick={() => deleteEntity(e.entity_id)}>×</button>
                    </div>
                    <div className="text-xs" style={{ color: "var(--text-tertiary)", lineHeight: 1.5 }}>
                      {e.description || "（无描述）"}
                    </div>
                    {e.introduced_chapter !== null && (
                      <div className="text-xs" style={{ color: "var(--text-disabled)", marginTop: 4 }}>
                        {e.introduced_chapter === 0 ? "初始生成" : `第${e.introduced_chapter}章引入`}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div className="card">
        <SectionHeader
          title="客观事实（SPO）"
          count={facts.length}
          action={chapters.length === 0 ? (
            <span className="text-xs text-muted">无已建章节</span>
          ) : (
            <span className="text-xs text-muted">
              {chapterFocus > 0 ? `仅看 第${chapterFocus}章 的事实` : "全部章节"}
            </span>
          )}
        />
        <div className="card-body">
          {chapters.length > 0 && (
            <ChapterTimeline
              mode="single"
              min={chapterMin} max={chapterMax}
              from={chapterFocus || chapterMin}
              to={chapterFocus || chapterMin}
              marks={chapterMarks}
              onChange={(f) => setChapterFocus(f === chapterMin && chapterFocus === 0 ? chapterMin : f)}
              label="按章节查看"
            />
          )}
          {chapters.length > 0 && (
            <div className="flex gap-6" style={{ marginTop: 4, marginBottom: 8 }}>
              <button className="btn" style={{ fontSize: 10, padding: "1px 8px" }}
                onClick={() => setChapterFocus(0)}
                title="清空章节过滤">查看全部</button>
            </div>
          )}
          {facts.length === 0 && <div className="text-xs text-muted">暂无事实。</div>}
          {facts.map(f => (
            <div key={f.triple_id} style={{
              display: "flex", gap: 10, alignItems: "center", padding: "5px 0",
              fontSize: 12, borderBottom: "1px solid var(--border)",
            }}>
              <span className="text-xs" style={{ color: "var(--text-tertiary)", width: 80 }}>
                {f.valid_from_chapter === 0 ? "初始" : `第${f.valid_from_chapter}章起`}
              </span>
              <span style={{ fontWeight: 600 }}>{f.subject}</span>
              <span style={{ color: "var(--accent)" }}>{f.predicate}</span>
              <span>{f.object}</span>
              {f.valid_to_chapter !== null && (
                <span className="text-xs" style={{ color: "var(--text-disabled)", marginLeft: "auto" }}>
                  已于第{f.valid_to_chapter}章失效
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ─────────────── Tab 2: 章节历史视图 ───────────────

// 后端 /api/historical-view/{chapter_id} 返回 5 个结构化板块. 之前 UI 是把
// 整段 JSON 倒进 <pre> 给用户看 —— 用户明确反对. 下面每个板块按自己的数据
// 形态做了 human-readable 渲染.

interface ChapterSegmentItem {
  segment_id: string;
  sequence_order: number;
  content: string;
  source: string;
}

interface StateTriple {
  subject: string;
  subject_type: string;
  predicate: string;
  value: string;
  from_chapter: number;
  trigger: string;
}

interface HistoricalView {
  chapter_id: string;
  chapter_num: number;
  snapshot_id: string | null;
  tab_1_content?: { segments?: ChapterSegmentItem[] };
  tab_2_storyland_state?: { chapter_num: number; active_state?: StateTriple[] };
  tab_3_reader_memory?: { chapter_num: number; rendered: string; error?: string };
  tab_4_full_prompt?: {
    full_prompt: string;
    embedding_model: string;
    generation_model: string;
  };
  tab_5_diagnostics?: Record<string, any>;
}

const SEGMENT_SOURCE_LABEL: Record<string, string> = {
  ai_generated:     "AI 生成",
  user_edit:        "用户改写",
  user_edited:      "用户改写",
  targeted_rewrite: "定向重写",
  manual:           "手动粘贴",
};

type HistoryTabKey = "segments" | "state" | "memory" | "prompt" | "diag";

const HISTORY_TAB_LABELS: Record<HistoryTabKey, string> = {
  segments: "章节段落（按段落看来源）",
  state:    "Storyland 状态（生成本章前的客观事实）",
  memory:   "读者视角记忆（生成本章时看到的累积）",
  prompt:   "完整 prompt（生成本章时实际发给 LLM 的内容）",
  diag:     "诊断数据（loader / 模型 / 字符数）",
};

function MemoryTab({ projectId: _projectId, chapters, toast }: {
  projectId: string;
  chapters: ChapterRef[];
  toast: (m: string, t?: any) => void;
}) {
  // Single-handle chapter timeline for picking 章节 — auto-tracks the
  // last chapter on first load so the page lands on the most useful view.
  const chapterMin = 1;
  const chapterMax = chapters.length > 0
    ? Math.max(...chapters.map(c => c.chapter_num || 0))
    : 1;
  const chapterMarks = useMemo(() => chapters.map(c => c.chapter_num), [chapters]);
  const [chapterNum, setChapterNum] = useState<number>(0);
  // Sync to the latest chapter once the list arrives.
  useEffect(() => {
    if (chapters.length > 0 && chapterNum === 0) {
      setChapterNum(chapters[chapters.length - 1].chapter_num || 1);
    }
  }, [chapters, chapterNum]);
  const selectedChapter = chapters.find(c => c.chapter_num === chapterNum);
  const selected = selectedChapter?.chapter_id || "";

  const [view, setView] = useState<HistoricalView | null>(null);
  const [loading, setLoading] = useState(false);
  const [subTab, setSubTab] = useState<HistoryTabKey>("memory");

  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    apiGet<HistoricalView>(`/api/historical-view/${selected}`)
      .then(setView)
      .catch((e: any) => { setView(null); toast(e.message || "加载失败", "error"); })
      .finally(() => setLoading(false));
  }, [selected, toast]);

  return (
    <div>
      <div className="card">
        <SectionHeader
          title="章节历史视图"
          action={selectedChapter ? (
            <span className="text-xs" style={{ color: "var(--text-secondary)" }}>
              第{selectedChapter.chapter_num}章 {selectedChapter.title}
            </span>
          ) : undefined}
        />
        <div className="card-body">
          <div className="text-xs text-muted" style={{ marginBottom: 8, lineHeight: 1.6 }}>
            选定章节, 即可回看「生成这一章时」: 段落来源 / Storyland 状态 / 读者视角记忆 /
            完整 prompt / 诊断 五个面板. 已 commit 的章节才有快照.
          </div>
          {chapters.length === 0 ? (
            <div className="empty-state" style={{ padding: "16px 0" }}>
              <p>暂无已建章节。请先在编辑器中添加章节。</p>
            </div>
          ) : (
            <ChapterTimeline
              mode="single"
              min={chapterMin} max={chapterMax}
              from={chapterNum || chapterMin}
              to={chapterNum || chapterMin}
              marks={chapterMarks}
              onChange={(f) => setChapterNum(f)}
              label="章节"
            />
          )}

          {loading && <div className="text-xs text-muted" style={{ marginTop: 8 }}>加载中...</div>}

          {!loading && view && (
            <>
              {/* 子 tab 选择栏 */}
              <div className="tab-bar-underline" style={{ marginTop: 12 }}>
                {(Object.keys(HISTORY_TAB_LABELS) as HistoryTabKey[]).map(k => (
                  <button key={k}
                    className={`tab-item ${subTab === k ? "active" : ""}`}
                    style={{ fontSize: 12 }}
                    onClick={() => setSubTab(k)}>
                    {HISTORY_TAB_LABELS[k]}
                  </button>
                ))}
              </div>

              <div style={{ marginTop: 12 }}>
                {subTab === "segments" && <SegmentsPanel data={view.tab_1_content} />}
                {subTab === "state"    && <StatePanel data={view.tab_2_storyland_state} />}
                {subTab === "memory"   && <MemoryRenderedPanel data={view.tab_3_reader_memory} />}
                {subTab === "prompt"   && <FullPromptPanel data={view.tab_4_full_prompt} toast={toast} />}
                {subTab === "diag"     && <DiagnosticsPanel data={view.tab_5_diagnostics} />}
              </div>
            </>
          )}

          {!loading && !view && selected && (
            <div className="text-xs text-muted" style={{ marginTop: 8 }}>
              该章节暂无快照（需先 commit 一次正文）。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── 5 个子面板的 human-readable 渲染 ─────────────────────────────

function PanelEmpty({ msg }: { msg: string }) {
  return (
    <div className="text-xs text-muted" style={{
      padding: "12px 0", textAlign: "center", fontStyle: "italic",
    }}>
      {msg}
    </div>
  );
}

function SegmentsPanel({ data }: { data?: { segments?: ChapterSegmentItem[] } }) {
  const segments = data?.segments || [];
  if (segments.length === 0) {
    return <PanelEmpty msg="本章尚无段落记录（commit 后才会生成 chapter_segments）" />;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {segments.map(s => (
        <div key={s.segment_id} style={{
          border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
          padding: "8px 10px", background: "var(--bg-surface)",
        }}>
          <div className="flex items-center gap-6" style={{ marginBottom: 4 }}>
            <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
              段 #{s.sequence_order}
            </span>
            <span className="tag" style={{
              fontSize: 10, padding: "1px 6px",
              color: "var(--accent)", borderColor: "var(--accent)",
            }}>
              {SEGMENT_SOURCE_LABEL[s.source] || s.source || "未知来源"}
            </span>
          </div>
          <div style={{
            fontSize: 12, lineHeight: 1.7, color: "var(--text-secondary)",
            whiteSpace: "pre-wrap", fontFamily: "var(--font-serif)",
          }}>
            {s.content}
          </div>
        </div>
      ))}
    </div>
  );
}

function StatePanel({ data }: {
  data?: { chapter_num: number; active_state?: StateTriple[] };
}) {
  const facts = data?.active_state || [];
  if (facts.length === 0) {
    return <PanelEmpty msg="生成本章前没有任何已记录的客观事实" />;
  }
  // Group by subject so the same actor's facts cluster together.
  const bySubject = new Map<string, StateTriple[]>();
  for (const f of facts) {
    if (!bySubject.has(f.subject)) bySubject.set(f.subject, []);
    bySubject.get(f.subject)!.push(f);
  }
  return (
    <div>
      <div className="text-xs text-muted" style={{ marginBottom: 8 }}>
        生成第 {data!.chapter_num} 章前, 共 {facts.length} 条客观事实
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {Array.from(bySubject.entries()).map(([subj, items]) => (
          <div key={subj} style={{
            border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
            padding: "8px 10px", background: "var(--bg-surface)",
          }}>
            <div className="flex items-center gap-6" style={{ marginBottom: 6 }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>{subj}</span>
              <span className="tag" style={{ fontSize: 10 }}>
                {ENTITY_TYPE_LABEL[items[0].subject_type] || items[0].subject_type || "其他"}
              </span>
              <span className="text-xs" style={{ color: "var(--text-tertiary)", marginLeft: "auto" }}>
                {items.length} 条
              </span>
            </div>
            {items.map((f, i) => (
              <div key={i} style={{
                display: "flex", gap: 8, alignItems: "baseline",
                fontSize: 12, padding: "3px 0",
                borderTop: i === 0 ? "none" : "1px solid var(--border-subtle)",
              }}>
                <span style={{ color: "var(--accent)" }}>{f.predicate}</span>
                <span>{f.value}</span>
                <span className="text-xs" style={{
                  color: "var(--text-tertiary)", marginLeft: "auto",
                }}>
                  第 {f.from_chapter} 章{f.trigger ? ` · ${f.trigger}` : ""}
                </span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function MemoryRenderedPanel({ data }: {
  data?: { chapter_num: number; rendered: string; error?: string };
}) {
  if (!data) return <PanelEmpty msg="无数据" />;
  if (data.error) {
    return (
      <div className="text-xs" style={{ color: "var(--danger)", padding: "12px 0" }}>
        加载读者视角记忆失败: {data.error}
      </div>
    );
  }
  const rendered = (data.rendered || "").trim();
  if (!rendered) {
    return <PanelEmpty msg="生成本章前还没有读者视角记忆 (前几章可能无前置摘要)" />;
  }
  // The body comes back as the loader's final prompt-injection text:
  // ``## 标题\n…\n## 标题\n…`` - render it as styled markdown-ish blocks.
  const blocks: { title: string; body: string }[] = [];
  let curTitle = "（默认）";
  let curBody  = "";
  for (const line of rendered.split("\n")) {
    const m = line.match(/^##\s+(.+?)\s*$/);
    if (m) {
      if (curBody.trim() || curTitle !== "（默认）") {
        blocks.push({ title: curTitle, body: curBody });
      }
      curTitle = m[1];
      curBody  = "";
    } else {
      curBody += line + "\n";
    }
  }
  if (curBody.trim() || curTitle !== "（默认）") {
    blocks.push({ title: curTitle, body: curBody });
  }
  return (
    <div>
      <div className="text-xs text-muted" style={{ marginBottom: 8 }}>
        生成第 {data.chapter_num + 1} 章时, 读者视角记忆 loader 看到的累积内容
      </div>
      {blocks.map((b, i) => (
        <div key={i} style={{
          border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
          padding: "10px 12px", marginBottom: 10, background: "var(--bg-surface)",
        }}>
          <div style={{
            fontSize: 12, fontWeight: 700,
            color: "var(--accent)", marginBottom: 6,
          }}>
            {b.title}
          </div>
          <div style={{
            fontSize: 12, lineHeight: 1.7,
            color: "var(--text-secondary)",
            whiteSpace: "pre-wrap", fontFamily: "var(--font-serif)",
          }}>
            {b.body.trim() || "（空）"}
          </div>
        </div>
      ))}
    </div>
  );
}

function FullPromptPanel({ data, toast }: {
  data?: { full_prompt: string; embedding_model: string; generation_model: string };
  toast: (m: string, t?: any) => void;
}) {
  if (!data || !data.full_prompt) {
    return <PanelEmpty msg="该章节没有保存完整 prompt 快照" />;
  }
  // 这条 prompt 是 markdown-flavored 自然语言 (写手 prompt), 不是 JSON.
  // 用 serif 字体大字号渲染, 方便用户人读, 不要套 mono / pre-wrap-json.
  const copy = () => {
    navigator.clipboard.writeText(data.full_prompt).then(
      () => toast("完整 prompt 已复制", "success"),
      () => toast("复制失败", "error"),
    );
  };
  return (
    <div>
      <div className="flex items-center gap-12" style={{
        marginBottom: 8, fontSize: 11, color: "var(--text-tertiary)",
        flexWrap: "wrap",
      }}>
        <span>生成模型: <strong style={{ color: "var(--text-secondary)" }}>
          {data.generation_model || "—"}
        </strong></span>
        <span>嵌入模型: <strong style={{ color: "var(--text-secondary)" }}>
          {data.embedding_model || "—"}
        </strong></span>
        <span style={{ marginLeft: "auto" }}>{data.full_prompt.length} 字</span>
        <button className="btn" style={{ fontSize: 10, padding: "1px 8px" }}
          onClick={copy}>复制</button>
      </div>
      <div style={{
        border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
        padding: "12px 14px", background: "var(--bg-surface)",
        maxHeight: 520, overflow: "auto",
        fontSize: 12, lineHeight: 1.75, fontFamily: "var(--font-serif)",
        color: "var(--text-secondary)", whiteSpace: "pre-wrap",
      }}>
        {data.full_prompt}
      </div>
    </div>
  );
}

function DiagnosticsPanel({ data }: { data?: Record<string, any> }) {
  if (!data || Object.keys(data).length === 0) {
    return <PanelEmpty msg="无诊断数据" />;
  }
  // diagnostics_snapshot 的典型 shape:
  //   { agent, total: {chars, tokens, tokenizer_method},
  //     sections: {system, context, user}, loaders: {<loader_id>: {...}},
  //     alerts: {empty, pressure, dominant} }
  const total    = data.total || {};
  const sections = data.sections || {};
  const loaders  = data.loaders || {};
  const alerts   = data.alerts || {};
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* 顶部统计 */}
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))",
        gap: 8,
      }}>
        <DiagStat label="Agent" value={data.agent || "—"} />
        <DiagStat label="总字符" value={total.chars ?? "—"} />
        <DiagStat label="总 token" value={total.tokens ?? "—"} />
        <DiagStat label="Tokenizer" value={total.tokenizer_method || "—"} />
      </div>

      {/* 三组 (system / context / user) */}
      {Object.keys(sections).length > 0 && (
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>分组字符 / token</div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 8 }}>
            {Object.entries(sections).map(([k, v]: [string, any]) => (
              <div key={k} style={{
                border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
                padding: "6px 10px", background: "var(--bg-surface)",
              }}>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{k}</div>
                <div style={{ fontSize: 13, fontWeight: 600 }}>
                  {v?.chars ?? 0} 字 · {v?.tokens ?? 0} token
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* loader 明细 */}
      {Object.keys(loaders).length > 0 && (
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>
            每个 loader 的明细
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {Object.entries(loaders).map(([k, v]: [string, any]) => (
              <DiagLoaderRow key={k} name={k} info={v} />
            ))}
          </div>
        </div>
      )}

      {/* 告警 */}
      {(alerts.empty?.length || alerts.pressure?.length || alerts.dominant?.length) ? (
        <div>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>告警</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {alerts.empty?.length > 0 && (
              <DiagAlertLine label="空 / 低利用率" items={alerts.empty} color="var(--text-tertiary)" />
            )}
            {alerts.pressure?.length > 0 && (
              <DiagAlertLine label="预算紧张 / 被裁" items={alerts.pressure} color="var(--gold)" />
            )}
            {alerts.dominant?.length > 0 && (
              <DiagAlertLine label="占比过高" items={alerts.dominant} color="var(--accent)" />
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function DiagStat({ label, value }: { label: string; value: any }) {
  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
      padding: "6px 10px", background: "var(--bg-surface)",
    }}>
      <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 600 }}>{String(value)}</div>
    </div>
  );
}

function DiagLoaderRow({ name, info }: { name: string; info: any }) {
  const status = info?.status || "—";
  const chars  = info?.chars ?? 0;
  const tokens = info?.tokens ?? 0;
  const alloc  = info?.allocated_budget ?? 0;
  const util   = typeof info?.utilization === "number"
    ? `${Math.round(info.utilization * 100)}%` : "—";
  const STATUS_COLOR: Record<string, string> = {
    rendered: "var(--accent)",
    clipped:  "var(--gold)",
    inactive: "var(--text-disabled)",
  };
  return (
    <div style={{
      display: "flex", alignItems: "baseline", gap: 10,
      fontSize: 11.5, padding: "4px 0",
      borderBottom: "1px solid var(--border-subtle)",
    }}>
      <span style={{ width: 160, fontFamily: "var(--font-mono)", fontSize: 11 }}>{name}</span>
      <span className="tag" style={{
        fontSize: 9, padding: "1px 5px",
        color: STATUS_COLOR[status] || "var(--text-tertiary)",
        borderColor: STATUS_COLOR[status] || "var(--border)",
      }}>{status}</span>
      <span style={{ color: "var(--text-tertiary)" }}>
        {chars} 字 · {tokens} token · 预算 {alloc} · 利用率 {util}
      </span>
    </div>
  );
}

function DiagAlertLine({ label, items, color }: {
  label: string; items: string[]; color: string;
}) {
  return (
    <div style={{ fontSize: 11.5, lineHeight: 1.7 }}>
      <span style={{ color, fontWeight: 600 }}>{label}：</span>
      <span style={{ color: "var(--text-secondary)" }}>
        {items.join("、")}
      </span>
    </div>
  );
}

