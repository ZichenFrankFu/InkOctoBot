import React, { useEffect, useState, useCallback } from "react";
import { apiGet, apiPut } from "../api/client";
import { useToast } from "../components/shared/Toast";
import type { Project, Character, WorldBookEntry } from "../api/types";

interface SetupProps {
  projectId: string;
  onNavigate?: (page: string) => void;
}

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
  });

  // Editable fields
  const [worldSummary, setWorldSummary] = useState("");
  const [outlineText, setOutlineText] = useState("");
  const [constraints, setConstraints] = useState("");
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [proj, chars, wb] = await Promise.all([
        apiGet<Project>(`/api/data/projects/${projectId}`),
        apiGet<{ items: Character[] }>(`/api/data/projects/${projectId}/characters`).catch(() => ({ items: [] })),
        apiGet<{ items: WorldBookEntry[] }>(`/api/data/projects/${projectId}/worldbook`).catch(() => ({ items: [] })),
      ]);
      setProject(proj);
      setCharacters(chars.items || []);
      setWorldEntries(wb.items || []);
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
            <p>配置项目的四大创作维度与约束规则</p>
          </div>
          {dirty && (
            <button className="btn-primary" onClick={handleSave}>
              保存设置
            </button>
          )}
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
