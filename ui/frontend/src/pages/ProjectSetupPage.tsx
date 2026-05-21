import React, { useEffect, useState, useCallback } from "react";
import { apiGet, apiPut } from "../api/client";
import { useToast } from "../components/shared/Toast";
import { PLATFORMS } from "../utils/platforms";
import type {
  Project, Character, WorldBookEntry,
  ReferenceFeatureSelection, WritingKnowledgeEntry,
} from "../api/types";

interface SetupProps {
  projectId: string;
  onNavigate?: (page: string) => void;
}

interface RefLink {
  ref_id: string;
  title: string;
}

const FEATURE_TYPES: { key: keyof ReferenceFeatureSelection; label: string }[] = [
  { key: "characters", label: "角色原型" },
  { key: "settings", label: "世界设定" },
  { key: "plot", label: "剧情结构" },
  { key: "rhythm", label: "叙事节奏" },
];

export default function ProjectSetupPage({ projectId, onNavigate }: SetupProps) {
  const { toast } = useToast();
  const [project, setProject] = useState<Project | null>(null);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [worldEntries, setWorldEntries] = useState<WorldBookEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    worldview: true,
    characters: true,
    outline: true,
    constraints: false,
    references: false,
    knowledge: false,
  });

  // Editable project fields
  const [platform, setPlatform] = useState("");
  const [worldSummary, setWorldSummary] = useState("");
  const [outlineText, setOutlineText] = useState("");
  const [constraints, setConstraints] = useState("");
  const [dirty, setDirty] = useState(false);

  // R2 — reference-work × feature-type injection selection
  const [refLinks, setRefLinks] = useState<RefLink[]>([]);
  const [refSelections, setRefSelections] = useState<Record<string, ReferenceFeatureSelection>>({});

  // R4 — writing-knowledge injection selection
  const [knowledge, setKnowledge] = useState<WritingKnowledgeEntry[]>([]);
  const [knowledgeIds, setKnowledgeIds] = useState<string[]>([]);

  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [proj, chars, wb, links, refInj, kn, knInj] = await Promise.all([
        apiGet<Project>(`/api/data/projects/${projectId}`),
        apiGet<{ items: Character[] }>(`/api/data/characters?project_id=${projectId}`)
          .catch(() => ({ items: [] as Character[] })),
        apiGet<{ items: WorldBookEntry[] }>(`/api/data/worldbook?project_id=${projectId}`)
          .catch(() => ({ items: [] as WorldBookEntry[] })),
        apiGet<{ items: any[] }>(`/api/references/links/${projectId}`)
          .catch(() => ({ items: [] as any[] })),
        apiGet<{ selections: Record<string, ReferenceFeatureSelection> }>(
          `/api/data/reference_injection/${projectId}`,
        ).catch(() => ({ selections: {} })),
        apiGet<{ items: WritingKnowledgeEntry[] }>(`/api/data/writing_knowledge`)
          .catch(() => ({ items: [] as WritingKnowledgeEntry[] })),
        apiGet<{ knowledge_ids: string[] }>(`/api/data/knowledge_injection/${projectId}`)
          .catch(() => ({ knowledge_ids: [] as string[] })),
      ]);
      setProject(proj);
      setPlatform((proj as any).platform || "");
      setWorldSummary((proj as any).world_summary || "");
      setOutlineText((proj as any).outline_text || "");
      setConstraints((proj as any).constraints || "");
      setCharacters(chars.items || []);
      setWorldEntries(wb.items || []);
      // Dedupe links by ref_id (a work may be linked under several dimensions).
      const seen = new Set<string>();
      const uniqueLinks: RefLink[] = [];
      for (const l of links.items || []) {
        if (l.ref_id && !seen.has(l.ref_id)) {
          seen.add(l.ref_id);
          uniqueLinks.push({ ref_id: l.ref_id, title: l.title || l.ref_id });
        }
      }
      setRefLinks(uniqueLinks);
      setRefSelections(refInj.selections || {});
      setKnowledge(kn.items || []);
      setKnowledgeIds(knInj.knowledge_ids || []);
      setDirty(false);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  const toggleSection = (key: string) => {
    setExpandedSections(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const handleSave = async () => {
    try {
      await apiPut(`/api/data/projects/${projectId}`, {
        ...project,
        platform,
        world_summary: worldSummary,
        outline_text: outlineText,
        constraints,
      });
      setDirty(false);
      toast("设置已保存", "success");
    } catch (e: any) {
      toast(e.message || "操作失败", "error");
    }
  };

  // R2 — toggle a reference-work feature type; persist immediately.
  const toggleRefFeature = async (refId: string, feat: keyof ReferenceFeatureSelection) => {
    const next = { ...refSelections };
    const cur: ReferenceFeatureSelection = { ...(next[refId] || {}) };
    cur[feat] = !cur[feat];
    next[refId] = cur;
    setRefSelections(next);
    try {
      await apiPut(`/api/data/reference_injection/${projectId}`, { selections: next });
    } catch (e: any) {
      toast(e.message || "保存失败", "error");
    }
  };

  // R4 — toggle a writing-knowledge entry; persist immediately.
  const toggleKnowledge = async (id: string) => {
    const next = knowledgeIds.includes(id)
      ? knowledgeIds.filter(k => k !== id)
      : [...knowledgeIds, id];
    setKnowledgeIds(next);
    try {
      await apiPut(`/api/data/knowledge_injection/${projectId}`, { knowledge_ids: next });
    } catch (e: any) {
      toast(e.message || "保存失败", "error");
    }
  };

  const nav = (page: string) => {
    if (onNavigate) onNavigate(page);
  };

  if (loading) {
    return (
      <div className="loading" style={{ height: "100vh" }}>
        <div className="loading-spinner" />
        加载中...
      </div>
    );
  }

  return (
    <div className="page-container" style={{ maxWidth: 900 }}>
      {/* Header */}
      <div className="page-header" style={{ padding: 0 }}>
        <div className="page-header-row mb-24">
          <div>
            <h2>{project?.name || "项目设置"}</h2>
            <p>配置项目的创作维度、约束规则与 AI 注入素材</p>
          </div>
          {dirty && (
            <button className="btn-primary" onClick={handleSave}>
              保存设置
            </button>
          )}
        </div>
      </div>

      {/* Publishing platform */}
      <div className="card mb-24">
        <div className="card-body">
          <div className="flex items-center gap-12" style={{ flexWrap: "wrap" }}>
            <div style={{ minWidth: 0 }}>
              <div className="label" style={{ marginBottom: 4 }}>发布平台</div>
              <div className="text-xs text-muted">
                AI 生成时会按平台特性调整章节长度与节奏（如番茄偏短章快节奏、起点章节更长）。
              </div>
            </div>
            <select
              className="select"
              value={platform}
              onChange={e => { setPlatform(e.target.value); setDirty(true); }}
              style={{ minWidth: 160, marginLeft: "auto" }}
            >
              <option value="">未选择</option>
              {PLATFORMS.map(p => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
        </div>
      </div>

      {/* Section: World View */}
      <SectionCard
        title="世界观概述"
        icon="&#x2295;"
        expanded={expandedSections.worldview}
        onToggle={() => toggleSection("worldview")}
        actions={
          <button className="btn-ghost" onClick={() => nav("worldbook")}>
            打开世界书 &rarr;
          </button>
        }
      >
        <p className="text-sm text-muted mb-12">
          概述你的故事世界：时代背景、力量体系、社会结构等核心设定。
          详细条目请在「世界书」页面管理。
        </p>
        <textarea
          className="input"
          value={worldSummary}
          onChange={e => { setWorldSummary(e.target.value); setDirty(true); }}
          rows={6}
          placeholder={"例：故事发生在一个修仙与科技并存的大陆...\n\n力量体系：灵气修炼九境...\n社会结构：五大宗门、三大帝国...\n关键设定：灵脉枯竭危机..."}
          style={{ fontFamily: "var(--font-serif)", lineHeight: 1.8 }}
        />
        {worldEntries.length > 0 && (
          <div className="mt-12">
            <div className="label mb-4">已有世界书条目 ({worldEntries.length})</div>
            <div className="flex gap-6" style={{ flexWrap: "wrap" }}>
              {worldEntries.slice(0, 8).map(entry => (
                <span key={entry.id} className="tag category">
                  {entry.title}
                </span>
              ))}
              {worldEntries.length > 8 && (
                <span className="tag category">+{worldEntries.length - 8} 更多</span>
              )}
            </div>
          </div>
        )}
      </SectionCard>

      {/* Section: Characters Preview */}
      <SectionCard
        title="角色列表"
        icon="&#x2662;"
        expanded={expandedSections.characters}
        onToggle={() => toggleSection("characters")}
        actions={
          <button className="btn-ghost" onClick={() => nav("characters")}>
            管理角色 &rarr;
          </button>
        }
      >
        {characters.length === 0 ? (
          <div className="empty-state" style={{ padding: "24px 0" }}>
            <p>暂无角色</p>
            <button className="btn-primary mt-12" onClick={() => nav("characters")}>
              + 创建角色
            </button>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 10 }}>
            {characters.map(ch => (
              <div
                key={ch.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "10px 12px",
                  background: "var(--bg-surface)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border)",
                  cursor: "pointer",
                }}
                onClick={() => nav("characters")}
              >
                <div
                  className="char-avatar"
                  style={{
                    width: 34,
                    height: 34,
                    fontSize: 14,
                    background: ch.role === "主角" ? "var(--accent-subtle)" : "var(--bg-surface-active)",
                    color: ch.role === "主角" ? "var(--accent)" : "var(--text-secondary)",
                  }}
                >
                  {ch.name.charAt(0)}
                </div>
                <div style={{ minWidth: 0 }}>
                  <div className="truncate" style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                    {ch.name}
                  </div>
                  <div className="text-xs text-muted">{ch.role || "角色"}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      {/* Section: Outline */}
      <SectionCard
        title="大纲编辑"
        icon="&#x270E;"
        expanded={expandedSections.outline}
        onToggle={() => toggleSection("outline")}
        actions={
          <button className="btn-ghost" onClick={() => nav("storyline")}>
            打开剧情线 &rarr;
          </button>
        }
      >
        <p className="text-sm text-muted mb-12">
          在这里撰写全书的分卷大纲。每章的细纲可在编辑器右侧面板编辑。
        </p>
        <textarea
          className="input"
          value={outlineText}
          onChange={e => { setOutlineText(e.target.value); setDirty(true); }}
          rows={10}
          placeholder={"第一卷：初入江湖\n  第1章：少年下山\n  第2章：集镇遇袭\n  第3章：拜入宗门\n\n第二卷：宗门风云\n  第4章：外门试炼\n  ..."}
          style={{ fontFamily: "var(--font-serif)", lineHeight: 2 }}
        />
      </SectionCard>

      {/* Section: Constraints */}
      <SectionCard
        title="约束设定"
        icon="&#x2500;"
        expanded={expandedSections.constraints}
        onToggle={() => toggleSection("constraints")}
      >
        <p className="text-sm text-muted mb-12">
          定义创作约束规则：字数限制、禁用词汇、风格要求、一致性规则等。
          AI 生成时将严格遵守这些约束。
        </p>
        <textarea
          className="input"
          value={constraints}
          onChange={e => { setConstraints(e.target.value); setDirty(true); }}
          rows={8}
          placeholder={"例：\n· 每章字数：2000-4000字\n· 禁止出现现代网络用语\n· 主角性格不可突变（除非有重大剧情触发）\n· 武力体系严格遵循九境划分\n· 对话风格偏古风，但不用文言文\n· 不写后宫剧情"}
          style={{ lineHeight: 1.8 }}
        />
      </SectionCard>

      {/* Section: Reference-work injection (R2) */}
      <SectionCard
        title="参考作品注入"
        icon="&#x29C9;"
        expanded={expandedSections.references}
        onToggle={() => toggleSection("references")}
        actions={
          <button className="btn-ghost" onClick={() => nav("references")}>
            参考作品库 &rarr;
          </button>
        }
      >
        <p className="text-sm text-muted mb-12">
          为每部已关联的参考作品勾选要注入生成的特征类型。AI 创作时会借鉴对应特征，
          勾选越少越省 token。
        </p>
        {refLinks.length === 0 ? (
          <div className="empty-state" style={{ padding: "24px 0" }}>
            <p>暂无关联的参考作品</p>
            <button className="btn-primary mt-12" onClick={() => nav("references")}>
              去参考作品库关联
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {refLinks.map(link => (
              <div
                key={link.ref_id}
                style={{
                  padding: "10px 12px",
                  background: "var(--bg-surface)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border)",
                }}
              >
                <div className="truncate" style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", marginBottom: 6 }}>
                  《{link.title}》
                </div>
                <div className="flex gap-12" style={{ flexWrap: "wrap" }}>
                  {FEATURE_TYPES.map(ft => (
                    <label
                      key={ft.key}
                      style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, cursor: "pointer", color: "var(--text-secondary)" }}
                    >
                      <input
                        type="checkbox"
                        checked={!!refSelections[link.ref_id]?.[ft.key]}
                        onChange={() => toggleRefFeature(link.ref_id, ft.key)}
                      />
                      {ft.label}
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </SectionCard>

      {/* Section: Writing-knowledge injection (R4) */}
      <SectionCard
        title="写作知识引用"
        icon="&#x2756;"
        expanded={expandedSections.knowledge}
        onToggle={() => toggleSection("knowledge")}
        actions={
          <button className="btn-ghost" onClick={() => nav("skills")}>
            知识库管理 &rarr;
          </button>
        }
      >
        <p className="text-sm text-muted mb-12">
          勾选要注入本项目生成的专业写作知识条目，AI 会据此让世界观与设定更严谨。
        </p>
        {knowledge.length === 0 ? (
          <div className="empty-state" style={{ padding: "24px 0" }}>
            <p>知识库暂无条目</p>
            <button className="btn-primary mt-12" onClick={() => nav("skills")}>
              + 添加写作知识
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {knowledge.map(k => (
              <label
                key={k.id}
                style={{
                  display: "flex",
                  alignItems: "flex-start",
                  gap: 8,
                  padding: "8px 12px",
                  background: "var(--bg-surface)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border)",
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={knowledgeIds.includes(k.id)}
                  onChange={() => toggleKnowledge(k.id)}
                  style={{ marginTop: 3 }}
                />
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                    {k.title}
                    {k.domain && (
                      <span className="tag category" style={{ marginLeft: 6, fontSize: 10 }}>
                        {k.domain}
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-muted truncate">{k.content}</div>
                </div>
              </label>
            ))}
          </div>
        )}
      </SectionCard>
    </div>
  );
}

/* ---- Collapsible Section Card ---- */
function SectionCard({
  title,
  icon,
  expanded,
  onToggle,
  actions,
  children,
}: {
  title: string;
  icon: string;
  expanded: boolean;
  onToggle: () => void;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="card mb-24">
      <div
        className="card-header"
        style={{ cursor: "pointer" }}
        onClick={onToggle}
      >
        <div className="flex items-center gap-10">
          <span style={{ fontSize: 18 }} dangerouslySetInnerHTML={{ __html: icon }} />
          <h3>{title}</h3>
          <span
            className="text-muted"
            style={{
              fontSize: 11,
              transition: "transform 0.2s var(--ease-out)",
              transform: expanded ? "rotate(180deg)" : "none",
              display: "inline-block",
            }}
          >
            &#x25BC;
          </span>
        </div>
        {actions && (
          <div onClick={e => e.stopPropagation()}>
            {actions}
          </div>
        )}
      </div>
      {expanded && (
        <div className="card-body" style={{ animation: "slideUp 0.2s var(--ease-out)" }}>
          {children}
        </div>
      )}
    </div>
  );
}
