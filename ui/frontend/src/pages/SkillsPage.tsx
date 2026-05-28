import React, { useEffect, useState, useCallback } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import type { SkillInfo, Project, WritingKnowledgeEntry } from "../api/types";
import { useToast } from "../components/shared/Toast";
// Two new tabs surface Part A (Edit Learning) + Part B (Domain Knowledge)
// — replaced the standalone "学习反馈" sidebar group per the new IA.
import PreferencesPage from "./PreferencesPage";
import DomainLearningPage from "./DomainLearningPage";

const SECTION_COLORS: Record<string, string> = {
  feature_extraction: "var(--cyan)",
  planner: "var(--indigo)",
  evaluation: "var(--accent)",
  production: "var(--gold)",
};

// Curated feature-extraction set, used to build a fallback view if the
// /api/skills/agents endpoint is unavailable.
const FEATURE_EXTRACTION_SKILLS = [
  "chronicle_outline_extract", "character_profile", "narrative_extract",
  "style_extract", "hook_extract", "info_density_judge",
  "opening_pattern_judge", "payoff_judge",
];

const KNOWLEDGE_DOMAINS = ["科学", "历史", "地理", "军事", "法律", "民俗", "其他"];
const FALLBACK_SECTIONS: { domain: string; label: string; description: string }[] = [
  { domain: "feature_extraction", label: "特征提取", description: "编年史、角色、叙事、风格、钩子、信息密度、开篇模式、爽点等特征抽取技能" },
  { domain: "planner", label: "规划", description: "故事规划与架构设计" },
  { domain: "evaluation", label: "评估", description: "质量评估与一致性检查" },
  { domain: "production", label: "生产", description: "内容创作与场景执行" },
];

interface LearningLogEntry {
  id: string;
  skill_name: string;
  display_name: string;
  trigger: string;
  need_description: string;
  project_id: string;
  created_at: string;
}

interface ExtractedMemory {
  id: string;
  content: string;
  source: string;
  timestamp: string;
}

interface PrefEntry {
  id: string;
  timestamp: string;
  action: string;
  detail: string;
  scope?: string;
  role?: string;
}

interface SkillSection {
  domain: string;
  label: string;
  description: string;
  skills: string[];
}

interface Props {
  projects: Project[];
  activeProject: string;
}

export default function SkillsPage({ projects, activeProject }: Props) {
  const { toast } = useToast();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newSkill, setNewSkill] = useState({ name: "", display_name: "", description: "", tags: "", prompt_template: "" });
  const [creating, setCreating] = useState(false);
  const [editingSkill, setEditingSkill] = useState<string | null>(null);
  const [editForm, setEditForm] = useState({ display_name: "", description: "", tags: "", prompt_template: "" });
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // Self-learning section state
  const [learningLog, setLearningLog] = useState<LearningLogEntry[]>([]);
  const [prefProject, setPrefProject] = useState(activeProject || "default");
  const [prefEntries, setPrefEntries] = useState<PrefEntry[]>([]);
  const [prefSummary, setPrefSummary] = useState("");
  const [extractedMemories, setExtractedMemories] = useState<ExtractedMemory[]>([]);
  const [prefLoading, setPrefLoading] = useState(false);
  const [editingMemoryId, setEditingMemoryId] = useState<string | null>(null);
  const [editingMemoryText, setEditingMemoryText] = useState("");

  // Skill sections (智能体 & skills tab)
  const [sections, setSections] = useState<SkillSection[]>([]);

  // Track deactivated state for learning log entries not in registry
  const [logDeactivated, setLogDeactivated] = useState<Set<string>>(new Set());

  const [activeTab, setActiveTab] = useState<"agents" | "learning" | "knowledge" | "preferences" | "domain">("agents");
  // Expanded section in the agents tab
  const [expandedDomain, setExpandedDomain] = useState<string | null>(null);

  // ── Writing-knowledge library (R4) ──
  const [knowledgeList, setKnowledgeList] = useState<WritingKnowledgeEntry[]>([]);
  const [knForm, setKnForm] = useState({ id: "", title: "", domain: "科学", content: "", tags: "", source: "" });
  const [knEditing, setKnEditing] = useState(false);
  const [knShowForm, setKnShowForm] = useState(false);
  const [knSaving, setKnSaving] = useState(false);
  const [knConfirmDelete, setKnConfirmDelete] = useState<string | null>(null);
  const [knSearch, setKnSearch] = useState("");

  const loadKnowledge = useCallback(async () => {
    try {
      const r = await apiGet<{ items: WritingKnowledgeEntry[] }>("/api/data/writing_knowledge");
      setKnowledgeList(r.items || []);
    } catch { /* ignore */ }
  }, []);
  useEffect(() => { loadKnowledge(); }, [loadKnowledge]);

  const openKnCreate = () => {
    setKnForm({ id: "", title: "", domain: "科学", content: "", tags: "", source: "" });
    setKnEditing(false); setKnShowForm(true);
  };
  const openKnEdit = (k: WritingKnowledgeEntry) => {
    setKnForm({
      id: k.id, title: k.title, domain: k.domain || "其他",
      content: k.content, tags: (k.tags || []).join(", "), source: k.source || "",
    });
    setKnEditing(true); setKnShowForm(true);
  };
  const saveKnowledge = async () => {
    if (!knForm.title.trim() || !knForm.content.trim()) {
      toast("标题和内容不能为空", "error"); return;
    }
    setKnSaving(true);
    const body = {
      title: knForm.title.trim(), domain: knForm.domain, content: knForm.content.trim(),
      tags: knForm.tags.split(/[,，]/).map(t => t.trim()).filter(Boolean),
      source: knForm.source.trim(),
    };
    try {
      if (knEditing) {
        const orig = knowledgeList.find(k => k.id === knForm.id);
        await apiPut(`/api/data/writing_knowledge/${knForm.id}`, { ...orig, ...body, id: knForm.id });
      } else {
        await apiPost("/api/data/writing_knowledge", body);
      }
      setKnShowForm(false);
      toast(knEditing ? "写作知识已更新" : "写作知识已创建", "success");
      loadKnowledge();
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    }
    setKnSaving(false);
  };
  const deleteKnowledge = async (id: string) => {
    try {
      await apiDelete(`/api/data/writing_knowledge/${id}`);
      setKnConfirmDelete(null);
      toast("写作知识已删除", "success");
      loadKnowledge();
    } catch (e: any) {
      toast(e?.message || "删除失败", "error");
    }
  };

  const buildFallbackSections = useCallback((skillsList: SkillInfo[]): SkillSection[] => {
    const names = new Set(skillsList.map(s => s.name));
    const byDomain: Record<string, string[]> = {};
    for (const s of skillsList) {
      const d = (s as any).agent_domain || "unknown";
      (byDomain[d] ||= []).push(s.name);
    }
    return FALLBACK_SECTIONS.map(sec => ({
      ...sec,
      skills: sec.domain === "feature_extraction"
        ? FEATURE_EXTRACTION_SKILLS.filter(n => names.has(n))
        : (byDomain[sec.domain] || []).slice().sort(),
    }));
  }, []);

  const loadSkills = useCallback(() => {
    setLoading(true);
    const skillsP = apiGet<{ skills: SkillInfo[]; total: number }>("/api/skills").catch(() => ({ skills: [] as SkillInfo[], total: 0 }));
    const logP = apiGet<{ entries: LearningLogEntry[] }>("/api/skills/learning-log").catch(() => ({ entries: [] as LearningLogEntry[] }));
    const sectionsP = apiGet<{ sections: SkillSection[] }>("/api/skills/agents").catch(() => ({ sections: [] as SkillSection[] }));

    Promise.all([skillsP, logP, sectionsP])
      .then(([skillsResp, logResp, sectionsResp]) => {
        const fetchedSkills = skillsResp.skills || [];
        setSkills(fetchedSkills);
        setLearningLog(logResp.entries || []);
        const secs = sectionsResp.sections || [];
        setSections(secs.length > 0 ? secs : buildFallbackSections(fetchedSkills));
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [buildFallbackSections]);

  useEffect(() => { loadSkills(); }, [loadSkills]);

  // Load preferences when project selection changes
  useEffect(() => {
    if (!prefProject) return;
    apiGet<{ entries: PrefEntry[]; summary: string; extracted_memories?: ExtractedMemory[] }>(
      `/api/data/preferences?project_id=${prefProject}`
    )
      .then(r => {
        setPrefEntries(r.entries || []);
        setPrefSummary(r.summary || "");
        setExtractedMemories(r.extracted_memories || []);
      })
      .catch(() => { setPrefEntries([]); setPrefSummary(""); setExtractedMemories([]); });
  }, [prefProject]);

  const analyzePreferences = async () => {
    setPrefLoading(true);
    try {
      const resp = await apiPost<{ summary: string; entries: PrefEntry[]; extracted_memories?: ExtractedMemory[] }>(
        "/api/data/preferences/analyze", { project_id: prefProject }
      );
      if (resp.summary) setPrefSummary(resp.summary);
      if (resp.entries) setPrefEntries(resp.entries);
      if (resp.extracted_memories) setExtractedMemories(resp.extracted_memories);
    } catch (e: any) {
      toast(e?.message || "操作失败", "error");
    }
    setPrefLoading(false);
  };

  const removeExtractedMemory = (id: string) => {
    setExtractedMemories(prev => prev.filter(m => m.id !== id));
    apiDelete(`/api/data/preferences/memory/${id}?project_id=${prefProject}`).catch((e) => toast(e.message || "操作失败", "error"));
  };

  const updateExtractedMemory = (id: string, newContent: string) => {
    setExtractedMemories(prev => prev.map(m => m.id === id ? { ...m, content: newContent } : m));
    setEditingMemoryId(null);
    apiPut(`/api/data/preferences/memory/${id}`, { project_id: prefProject, content: newContent }).catch((e) => toast(e.message || "操作失败", "error"));
  };

  const removePrefEntry = (id: string) => {
    setPrefEntries(prev => prev.filter(e => e.id !== id));
    apiDelete(`/api/data/preferences/${id}?project_id=${prefProject}`).catch((e) => toast(e.message || "操作失败", "error"));
  };

  const handleCreateSkill = async () => {
    if (!newSkill.name.trim()) return;
    setCreating(true);
    try {
      await apiPost("/api/skills/create", {
        name: newSkill.name.trim().replace(/\s+/g, "_").toLowerCase(),
        display_name: newSkill.display_name.trim(),
        description: newSkill.description.trim(),
        tags: newSkill.tags.split(",").map(s => s.trim()).filter(Boolean),
        prompt_template: newSkill.prompt_template.trim(),
      });
      setShowCreate(false);
      setNewSkill({ name: "", display_name: "", description: "", tags: "", prompt_template: "" });
      loadSkills();
      toast("技能创建成功", "success");
    } catch (e: any) {
      toast(e?.message || "创建技能失败", "error");
    }
    setCreating(false);
  };

  const handleToggleSkill = async (name: string) => {
    try {
      const resp = await apiPost<{ active: boolean }>(`/api/skills/${name}/toggle`, {});
      setSkills(prev => prev.map(s => s.name === name ? { ...s, active: resp.active } : s));
      setLogDeactivated(prev => {
        const next = new Set(prev);
        if (resp.active) next.delete(name); else next.add(name);
        return next;
      });
      toast(resp.active ? "技能已启用" : "技能已停用", "success");
    } catch (e: any) {
      setLogDeactivated(prev => {
        const next = new Set(prev);
        if (next.has(name)) next.delete(name); else next.add(name);
        return next;
      });
      toast(e?.message || "操作失败", "error");
    }
  };

  const handleDeleteSkill = async (name: string) => {
    try {
      await apiDelete(`/api/skills/${name}`);
      setSkills(prev => prev.filter(s => s.name !== name));
      setLearningLog(prev => prev.filter(e => e.skill_name !== name));
      setConfirmDelete(null);
      if (expanded === name) setExpanded(null);
      if (editingSkill === name) setEditingSkill(null);
      toast("技能已删除", "success");
    } catch (e: any) {
      toast(e?.message || "删除技能失败", "error");
    }
  };

  const handleEditSkill = (skill: SkillInfo) => {
    setEditingSkill(skill.name);
    setEditForm({
      display_name: skill.display_name,
      description: skill.description,
      tags: skill.tags.join(", "),
      prompt_template: "",
    });
  };

  const handleSaveEdit = async () => {
    if (!editingSkill) return;
    setSaving(true);
    try {
      await apiPut(`/api/skills/${editingSkill}`, {
        display_name: editForm.display_name.trim(),
        description: editForm.description.trim(),
        tags: editForm.tags.split(",").map(s => s.trim()).filter(Boolean),
        prompt_template: editForm.prompt_template.trim() || undefined,
      });
      setEditingSkill(null);
      loadSkills();
      toast("技能修改已保存", "success");
    } catch (e: any) {
      toast(e?.message || "保存修改失败", "error");
    }
    setSaving(false);
  };

  const matchesSearch = (s: SkillInfo): boolean => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return s.name.toLowerCase().includes(q)
      || (s.display_name || "").toLowerCase().includes(q)
      || (s.description || "").toLowerCase().includes(q);
  };

  const projectName = (pid: string) => projects.find(p => p.id === pid)?.name || pid;

  const isSkillActive = (name: string): boolean => {
    const registrySkill = skills.find(s => s.name === name);
    if (registrySkill) return registrySkill.active !== false;
    return !logDeactivated.has(name);
  };

  // Basic (built-in) skills are read-only; only learned skills are editable.
  const isBasicSkill = (skill: SkillInfo): boolean => skill.is_basic ?? !skill.is_learned;

  if (loading) {
    return (
      <div className="loading" style={{ paddingTop: 120 }}>
        <div className="loading-spinner" />
        Loading skills...
      </div>
    );
  }

  const learnedSkills = skills.filter(s => s.is_learned);
  const learnedCount = learnedSkills.length;
  const sectionSkillTotal = sections.reduce((n, sec) => n + sec.skills.length, 0);

  // Render a skill row with expand / edit / delete capabilities
  const renderSkillRow = (skill: SkillInfo, idx: number, total: number) => {
    const isExp = expanded === skill.name;
    const basic = isBasicSkill(skill);
    return (
      <div key={skill.name} style={{ borderBottom: idx < total - 1 ? "1px solid var(--border-subtle)" : "none" }}>
        <div
          onClick={() => setExpanded(isExp ? null : skill.name)}
          style={{
            display: "flex", alignItems: "center", gap: 10, padding: "10px 16px",
            cursor: "pointer", transition: "background 0.1s",
            opacity: skill.active === false ? 0.5 : 1,
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", textDecoration: skill.active === false ? "line-through" : "none" }}>
                {skill.display_name}
              </span>
              <code style={{ fontSize: 10, color: "var(--text-tertiary)", background: "var(--bg-secondary)", padding: "1px 5px", borderRadius: 4 }}>
                {skill.name}
              </code>
              {skill.is_learned && (
                <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 10, background: "var(--purple-subtle, rgba(147,51,234,0.1))", color: "var(--purple, #9333ea)", fontWeight: 600 }}>自学习</span>
              )}
              {skill.active === false && (
                <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 10, background: "var(--bg-secondary)", color: "var(--text-disabled)", fontWeight: 600 }}>已停用</span>
              )}
            </div>
            <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {skill.description}
            </div>
          </div>
          <div style={{ display: "flex", gap: 4, flexShrink: 0 }} onClick={e => e.stopPropagation()}>
            <button className="btn" style={{ fontSize: 10, padding: "2px 8px" }} onClick={() => handleToggleSkill(skill.name)}>
              {skill.active === false ? "启用" : "停用"}
            </button>
            {basic ? (
              <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 10, background: "var(--bg-secondary)", color: "var(--text-disabled)", lineHeight: "18px" }} title="基础技能不可修改或删除">基础</span>
            ) : (
              <>
                <button className="btn" style={{ fontSize: 10, padding: "2px 8px" }} onClick={() => handleEditSkill(skill)}>修改</button>
                <button className="btn" style={{ fontSize: 10, padding: "2px 8px", color: "var(--error)" }} onClick={() => setConfirmDelete(skill.name)}>删除</button>
              </>
            )}
          </div>
          <span style={{ fontSize: 11, color: "var(--text-disabled)", transition: "transform 0.2s", transform: isExp ? "rotate(90deg)" : "none" }}>&#9654;</span>
        </div>
        {confirmDelete === skill.name && (
          <div style={{ padding: "10px 16px", background: "rgba(239,68,68,0.06)", borderTop: "1px solid var(--border-subtle)", display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ fontSize: 12, color: "var(--error)", flex: 1 }}>确认删除技能 &ldquo;{skill.display_name}&rdquo;？此操作不可撤销。</span>
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={() => setConfirmDelete(null)}>取消</button>
            <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px", background: "var(--error)", borderColor: "var(--error)" }} onClick={() => handleDeleteSkill(skill.name)}>确认删除</button>
          </div>
        )}
        {editingSkill === skill.name && (
          <div style={{ padding: "14px 16px", background: "var(--bg-secondary)", borderTop: "1px solid var(--border-subtle)" }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", marginBottom: 10 }}>修改技能</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
              <div className="field">
                <label className="label">显示名</label>
                <input className="input" value={editForm.display_name} onChange={e => setEditForm(prev => ({ ...prev, display_name: e.target.value }))} />
              </div>
              <div className="field">
                <label className="label">标签（逗号分隔）</label>
                <input className="input" value={editForm.tags} onChange={e => setEditForm(prev => ({ ...prev, tags: e.target.value }))} />
              </div>
            </div>
            <div className="field mb-12">
              <label className="label">描述</label>
              <input className="input" value={editForm.description} onChange={e => setEditForm(prev => ({ ...prev, description: e.target.value }))} />
            </div>
            <div className="field mb-12">
              <label className="label">Prompt 模板（留空保持不变）</label>
              <textarea className="input" value={editForm.prompt_template} onChange={e => setEditForm(prev => ({ ...prev, prompt_template: e.target.value }))}
                placeholder="留空则保持原有模板" rows={3} style={{ fontFamily: "var(--font-mono)", fontSize: 12 }} />
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setEditingSkill(null)}>取消</button>
              <button className="btn-primary" onClick={handleSaveEdit} disabled={saving}>{saving ? "保存中..." : "保存修改"}</button>
            </div>
          </div>
        )}
        {isExp && (
          <div style={{ padding: "0 16px 14px", background: "var(--bg-secondary)", borderTop: "1px solid var(--border-subtle)" }}>
            {skill.skill_md && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4 }}>
                  SKILL.md &middot; Claude 技能格式
                </div>
                <pre style={{ fontSize: 11, background: "var(--bg-surface)", padding: 8, borderRadius: 6, overflow: "auto", maxHeight: 260, margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.55 }}>
                  {skill.skill_md}
                </pre>
              </div>
            )}
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4 }}>输出 Schema</div>
              <pre style={{ fontSize: 11, background: "var(--bg-surface)", padding: 8, borderRadius: 6, overflow: "auto", maxHeight: 220, margin: 0 }}>
                {JSON.stringify(skill.output_schema, null, 2)}
              </pre>
            </div>
            <div style={{ display: "flex", gap: 12, marginTop: 10, fontSize: 11, flexWrap: "wrap" }}>
              <span style={{ color: "var(--text-secondary)" }}>Temperature: <strong>{skill.temperature}</strong></span>
              <span style={{ color: "var(--text-secondary)" }}>Max tokens: <strong>{skill.max_tokens}</strong></span>
              <span style={{ color: "var(--text-secondary)" }}>Role: <strong>{skill.model_role}</strong></span>
              {basic && <span style={{ color: "var(--text-disabled)", fontStyle: "italic" }}>基础技能（不可修改 / 删除）</span>}
            </div>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="page-container" style={{ maxWidth: 1100 }}>
      {/* Header */}
      <div style={{ marginBottom: 20, display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h2 style={{ fontFamily: "var(--font-serif)", fontSize: 22, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
            智能体管理
          </h2>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
            {skills.length} 个技能 &middot; {learnedCount} 个自学习技能
          </p>
        </div>
        <button className="btn-primary" style={{ fontSize: 12 }} onClick={() => { setActiveTab("learning"); setShowCreate(true); }}>
          + 新建技能
        </button>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", gap: 0, marginBottom: 20, borderBottom: "2px solid var(--border-subtle)" }}>
        {([
          { key: "agents" as const, label: "智能体 & Skills", count: sectionSkillTotal },
          { key: "learning" as const, label: "自学习成果", count: learnedCount },
          { key: "knowledge" as const, label: "写作知识", count: knowledgeList.length },
          // Promoted into here from the removed 学习反馈 sidebar group:
          { key: "preferences" as const, label: " 写作偏好", count: 0 },
          { key: "domain" as const, label: " 领域知识", count: 0 },
        ]).map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            style={{
              padding: "10px 20px",
              fontSize: 13,
              fontWeight: activeTab === tab.key ? 700 : 400,
              color: activeTab === tab.key ? "var(--accent)" : "var(--text-secondary)",
              background: "none",
              border: "none",
              borderBottom: activeTab === tab.key ? "2px solid var(--accent)" : "2px solid transparent",
              cursor: "pointer",
              marginBottom: -2,
              transition: "all 0.15s",
            }}
          >
            {tab.label}
            {tab.count > 0 && <span style={{ fontSize: 10, marginLeft: 6, color: "var(--text-tertiary)" }}>({tab.count})</span>}
          </button>
        ))}
      </div>

      {/* ═══════════════════════ TAB: Agents & Skills ═══════════════════════ */}
      {activeTab === "agents" && (
        <>
          <div style={{ marginBottom: 16 }}>
            <input
              className="input"
              placeholder="搜索技能..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ maxWidth: 320 }}
            />
          </div>

          {sections.length === 0 && (
            <div className="card" style={{ padding: 24, textAlign: "center" }}>
              <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
                暂无注册的技能。请检查后端服务是否正常运行。
              </div>
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {sections.map(section => {
              const color = SECTION_COLORS[section.domain] || "var(--text-secondary)";
              const sectionSkills = section.skills
                .map(name => skills.find(s => s.name === name))
                .filter((s): s is SkillInfo => !!s);
              const visibleSkills = sectionSkills.filter(matchesSearch);
              const isOpen = expandedDomain === section.domain;
              return (
                <div key={section.domain} className="card">
                  <div
                    className="card-header"
                    style={{ borderLeft: `3px solid ${color}`, padding: "10px 16px", cursor: "pointer" }}
                    onClick={() => setExpandedDomain(isOpen ? null : section.domain)}
                  >
                    <div style={{ flex: 1 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <h3 style={{ fontSize: 14, margin: 0, color }}>{section.label}</h3>
                        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                          {sectionSkills.length} 个技能
                        </span>
                      </div>
                      <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>{section.description}</div>
                    </div>
                    <span style={{ fontSize: 12, color: "var(--text-disabled)", transition: "transform 0.2s", transform: isOpen ? "rotate(90deg)" : "none" }}>&#9654;</span>
                  </div>

                  {isOpen && (
                    <div className="card-body" style={{ padding: 0 }}>
                      {visibleSkills.length === 0 ? (
                        <div style={{ padding: "12px 16px", fontSize: 12, color: "var(--text-tertiary)" }}>
                          {sectionSkills.length === 0 ? "暂无技能" : "无匹配技能"}
                        </div>
                      ) : (
                        visibleSkills.map((skill, idx) => renderSkillRow(skill, idx, visibleSkills.length))
                      )}
                    </div>
                  )}

                  {!isOpen && sectionSkills.length > 0 && (
                    <div className="card-body" style={{ padding: "8px 16px 12px" }}>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                        {sectionSkills.map(s => (
                          <div key={s.name} style={{
                            padding: "3px 10px", borderRadius: 6,
                            background: s.active !== false ? "var(--bg-surface)" : "var(--bg-secondary)",
                            border: "1px solid var(--border-subtle)", fontSize: 11,
                            color: s.active !== false ? "var(--text-primary)" : "var(--text-disabled)",
                            textDecoration: s.active !== false ? "none" : "line-through",
                            cursor: "pointer",
                          }} onClick={() => setExpandedDomain(section.domain)}>
                            {s.display_name || s.name}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* ═══════════════════════ TAB: Self-Learning ═══════════════════════ */}
      {activeTab === "learning" && (
        <>
          {showCreate && (
            <div className="card mb-20" style={{ animation: "slideUp 0.2s var(--ease-out)" }}>
              <div className="card-header"><h3>新建自学习技能</h3></div>
              <div className="card-body">
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
                  <div className="field">
                    <label className="label">技能名（snake_case）</label>
                    <input className="input" value={newSkill.name} onChange={e => setNewSkill(prev => ({ ...prev, name: e.target.value }))} placeholder="my_custom_skill" />
                  </div>
                  <div className="field">
                    <label className="label">显示名</label>
                    <input className="input" value={newSkill.display_name} onChange={e => setNewSkill(prev => ({ ...prev, display_name: e.target.value }))} placeholder="我的技能" />
                  </div>
                </div>
                <div className="field mb-12">
                  <label className="label">描述</label>
                  <input className="input" value={newSkill.description} onChange={e => setNewSkill(prev => ({ ...prev, description: e.target.value }))} placeholder="这个技能做什么..." />
                </div>
                <div className="field mb-12">
                  <label className="label">标签（逗号分隔）</label>
                  <input className="input" value={newSkill.tags} onChange={e => setNewSkill(prev => ({ ...prev, tags: e.target.value }))} placeholder="custom, writing" />
                </div>
                <div className="field mb-12">
                  <label className="label">Prompt 模板</label>
                  <textarea className="input" value={newSkill.prompt_template} onChange={e => setNewSkill(prev => ({ ...prev, prompt_template: e.target.value }))}
                    placeholder="请根据以下输入生成内容：{text}" rows={3} style={{ fontFamily: "var(--font-mono)", fontSize: 12 }} />
                </div>
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                  <button className="btn" onClick={() => setShowCreate(false)}>取消</button>
                  <button className="btn-primary" onClick={handleCreateSkill} disabled={creating || !newSkill.name.trim()}>
                    {creating ? "创建中..." : "创建技能"}
                  </button>
                </div>
              </div>
            </div>
          )}

          <div className="card" style={{ borderLeft: "3px solid var(--purple)" }}>
            <div className="card-header" style={{ background: "var(--purple-subtle, rgba(147,51,234,0.06))" }}>
              <h3 style={{ fontSize: 15, margin: 0, color: "var(--purple, #9333ea)" }}>自学习成果</h3>
              <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                自学习技能 &middot; 习得记录 &middot; 偏好记忆
              </span>
            </div>
            <div className="card-body" style={{ padding: 0 }}>
              {/* ── Learned skills ── */}
              <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border-subtle)" }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", marginBottom: 10 }}>
                  自学习技能
                  <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-tertiary)", marginLeft: 8 }}>
                    {learnedCount} 个
                  </span>
                </div>
                {learnedSkills.length === 0 ? (
                  <div style={{ fontSize: 12, color: "var(--text-tertiary)", padding: "8px 0" }}>
                    暂无自学习技能。可通过上方「新建技能」创建，或在「灵感搜索」页的「作品对比」生成。
                  </div>
                ) : (
                  <div style={{ border: "1px solid var(--border-subtle)", borderRadius: 8, overflow: "hidden" }}>
                    {learnedSkills.map((skill, idx) => renderSkillRow(skill, idx, learnedSkills.length))}
                  </div>
                )}
              </div>

              {/* ── Skill Learning Log ── */}
              <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--border-subtle)" }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", marginBottom: 10 }}>
                  技能习得记录
                  <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-tertiary)", marginLeft: 8 }}>
                    {learningLog.length} 条
                  </span>
                </div>
                {learningLog.length === 0 ? (
                  <div style={{ fontSize: 12, color: "var(--text-tertiary)", padding: "8px 0" }}>
                    暂无自学习记录。当系统检测到重复的用户修改模式或评估失败时，将自动生成新技能。
                  </div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {learningLog.map(entry => {
                      const active = isSkillActive(entry.skill_name);
                      return (
                        <div key={entry.id} style={{
                          padding: "10px 14px", background: "var(--bg-surface)", borderRadius: 8,
                          border: "1px solid var(--border-subtle)",
                          opacity: active ? 1 : 0.6,
                        }}>
                          <div className="flex items-center gap-8" style={{ marginBottom: 4 }}>
                            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--purple, #9333ea)", textDecoration: active ? "none" : "line-through" }}>
                              {entry.display_name || entry.skill_name}
                            </span>
                            <code style={{ fontSize: 10, color: "var(--text-tertiary)", background: "var(--bg-secondary)", padding: "1px 6px", borderRadius: 4 }}>
                              {entry.skill_name}
                            </code>
                            {entry.project_id && (
                              <span style={{ fontSize: 10, padding: "1px 8px", borderRadius: 10, background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>
                                {projectName(entry.project_id)}
                              </span>
                            )}
                            {!active && (
                              <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 10, background: "var(--bg-secondary)", color: "var(--text-disabled)" }}>已停用</span>
                            )}
                            <span style={{ fontSize: 10, color: "var(--text-disabled)", marginLeft: "auto" }}>
                              {entry.created_at}
                            </span>
                          </div>
                          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 2 }}>
                            <span style={{ color: "var(--gold)", fontWeight: 600 }}>触发：</span>{entry.trigger}
                          </div>
                          <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                            <span style={{ color: "var(--jade)", fontWeight: 600 }}>用途：</span>{entry.need_description}
                          </div>
                          <div style={{ display: "flex", gap: 6, marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border-subtle)" }}>
                            <button
                              className="btn"
                              style={{ fontSize: 10, padding: "3px 10px" }}
                              onClick={() => handleToggleSkill(entry.skill_name)}
                            >
                              {active ? "停用" : "启用"}
                            </button>
                            {confirmDelete === entry.skill_name ? (
                              <>
                                <span style={{ fontSize: 10, color: "var(--error)", lineHeight: "22px" }}>确认删除？</span>
                                <button className="btn" style={{ fontSize: 10, padding: "3px 8px" }} onClick={() => setConfirmDelete(null)}>取消</button>
                                <button className="btn" style={{ fontSize: 10, padding: "3px 8px", color: "var(--error)", borderColor: "var(--error)" }} onClick={() => handleDeleteSkill(entry.skill_name)}>确认</button>
                              </>
                            ) : (
                              <button
                                className="btn"
                                style={{ fontSize: 10, padding: "3px 10px", color: "var(--error)" }}
                                onClick={() => setConfirmDelete(entry.skill_name)}
                              >
                                删除
                              </button>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* ── Per-Project Preference Memories ── */}
              <div style={{ padding: "14px 16px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>偏好记忆</div>
                  <select className="select" style={{ fontSize: 11, minWidth: 140 }}
                    value={prefProject} onChange={e => setPrefProject(e.target.value)}>
                    {projects.length === 0 && <option value="default">默认项目</option>}
                    {projects.map(p => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                  <button className="btn" style={{ fontSize: 11, padding: "3px 10px", marginLeft: "auto" }}
                    onClick={analyzePreferences} disabled={prefLoading}>
                    {prefLoading ? "分析中..." : prefEntries.length > 0 ? "刷新" : "收集交互记录"}
                  </button>
                </div>

                <div style={{ padding: "8px 12px", background: "var(--purple-subtle, rgba(147,51,234,0.06))", borderRadius: 6, marginBottom: 12, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.6 }}>
                  根据该项目内所有AI对话交互记录，自动提取创作偏好。各项目的偏好记忆独立存储，用于改进AI生成质量。
                </div>

                {prefSummary && (
                  <div style={{ padding: "8px 12px", background: "var(--bg-surface)", borderRadius: 6, marginBottom: 12, fontSize: 12, color: "var(--text-secondary)" }}>
                    {prefSummary}
                  </div>
                )}

                {extractedMemories.length > 0 && (
                  <div style={{ marginBottom: 12 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>
                      提取的创作偏好
                      <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-tertiary)", marginLeft: 6 }}>
                        {extractedMemories.length} 条
                      </span>
                    </div>
                    {extractedMemories.map(mem => (
                      <div key={mem.id} style={{
                        padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)",
                        display: "flex", alignItems: "flex-start", gap: 8,
                        borderLeft: "3px solid var(--indigo)",
                      }}>
                        <div style={{
                          width: 20, height: 20, borderRadius: "50%", flexShrink: 0, marginTop: 1,
                          background: "var(--indigo-subtle)", border: "1.5px solid var(--indigo)",
                          display: "flex", alignItems: "center", justifyContent: "center", fontSize: 9,
                          color: "var(--indigo)",
                        }}>M</div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div className="flex items-center gap-8" style={{ marginBottom: 2 }}>
                            <span style={{ fontSize: 10, color: "var(--text-disabled)" }}>{mem.timestamp}</span>
                            <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 8, background: "var(--indigo-subtle)", color: "var(--indigo)" }}>{mem.source}</span>
                          </div>
                          {editingMemoryId === mem.id ? (
                            <div>
                              <textarea className="input" value={editingMemoryText}
                                onChange={e => setEditingMemoryText(e.target.value)}
                                rows={2} style={{ fontSize: 11, width: "100%", boxSizing: "border-box", marginBottom: 4 }} />
                              <div className="flex gap-4">
                                <button className="btn-primary" style={{ fontSize: 10, padding: "2px 8px" }}
                                  onClick={() => updateExtractedMemory(mem.id, editingMemoryText)}>保存</button>
                                <button className="btn" style={{ fontSize: 10, padding: "2px 8px" }}
                                  onClick={() => setEditingMemoryId(null)}>取消</button>
                              </div>
                            </div>
                          ) : (
                            <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                              {mem.content}
                            </div>
                          )}
                        </div>
                        <div className="flex gap-2" style={{ flexShrink: 0 }}>
                          <button className="btn-icon" style={{ fontSize: 10 }}
                            onClick={() => { setEditingMemoryId(mem.id); setEditingMemoryText(mem.content); }}
                            title="编辑">&#9998;</button>
                          <button className="btn-icon" style={{ fontSize: 11 }}
                            onClick={() => removeExtractedMemory(mem.id)}
                            title="删除">&times;</button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {prefEntries.length > 0 && (
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>
                      用户交互记录
                      <span style={{ fontSize: 10, fontWeight: 400, color: "var(--text-tertiary)", marginLeft: 6 }}>
                        {prefEntries.length} 条
                      </span>
                    </div>
                    <div style={{ maxHeight: 300, overflowY: "auto" }}>
                      {prefEntries.map(entry => (
                        <div key={entry.id} style={{
                          padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)",
                          display: "flex", alignItems: "flex-start", gap: 8,
                          borderLeft: "3px solid var(--purple, #9333ea)",
                        }}>
                          <div style={{
                            width: 20, height: 20, borderRadius: "50%", flexShrink: 0, marginTop: 1,
                            background: "var(--purple-subtle)", border: "1.5px solid var(--purple, #9333ea)",
                            display: "flex", alignItems: "center", justifyContent: "center", fontSize: 9,
                          }}>U</div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div className="flex items-center gap-8" style={{ marginBottom: 2 }}>
                              <span style={{ fontSize: 10, color: "var(--text-disabled)" }}>{entry.timestamp}</span>
                              <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 8, background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>{entry.action}</span>
                            </div>
                            <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.5, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                              {entry.detail.length > 300 ? entry.detail.slice(0, 300) + "..." : entry.detail}
                            </div>
                          </div>
                          <button className="btn-icon" style={{ fontSize: 11, flexShrink: 0 }}
                            onClick={() => removePrefEntry(entry.id)}>&times;</button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {prefEntries.length === 0 && !prefSummary && extractedMemories.length === 0 && (
                  <div style={{ textAlign: "center", padding: "16px 0", color: "var(--text-tertiary)", fontSize: 12 }}>
                    点击「收集交互记录」按钮来分析该项目的AI对话历史
                  </div>
                )}
              </div>
            </div>
          </div>
        </>
      )}

      {/* ═══════════════════════ TAB: Writing Knowledge ═══════════════════════ */}
      {activeTab === "knowledge" && (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
            <input className="input" placeholder="搜索写作知识..." value={knSearch}
              onChange={e => setKnSearch(e.target.value)} style={{ maxWidth: 280 }} />
            <button className="btn-primary" style={{ fontSize: 12, marginLeft: "auto" }} onClick={openKnCreate}>
              + 新建写作知识
            </button>
          </div>

          {knShowForm && (
            <div className="card mb-20" style={{ animation: "slideUp 0.2s var(--ease-out)" }}>
              <div className="card-header"><h3>{knEditing ? "编辑写作知识" : "新建写作知识"}</h3></div>
              <div className="card-body">
                <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 12, marginBottom: 12 }}>
                  <div className="field">
                    <label className="label">标题</label>
                    <input className="input" value={knForm.title}
                      onChange={e => setKnForm(p => ({ ...p, title: e.target.value }))}
                      placeholder="例：冷兵器时代的攻城战术" />
                  </div>
                  <div className="field">
                    <label className="label">领域</label>
                    <select className="select" value={knForm.domain}
                      onChange={e => setKnForm(p => ({ ...p, domain: e.target.value }))} style={{ width: "100%" }}>
                      {KNOWLEDGE_DOMAINS.map(d => <option key={d} value={d}>{d}</option>)}
                    </select>
                  </div>
                </div>
                <div className="field mb-12">
                  <label className="label">内容</label>
                  <textarea className="input" value={knForm.content}
                    onChange={e => setKnForm(p => ({ ...p, content: e.target.value }))}
                    rows={5} placeholder="详细描述该专业知识，AI 创作时会据此保持设定严谨..."
                    style={{ lineHeight: 1.7 }} />
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
                  <div className="field">
                    <label className="label">标签（逗号分隔）</label>
                    <input className="input" value={knForm.tags}
                      onChange={e => setKnForm(p => ({ ...p, tags: e.target.value }))} placeholder="军事, 古代" />
                  </div>
                  <div className="field">
                    <label className="label">来源（选填）</label>
                    <input className="input" value={knForm.source}
                      onChange={e => setKnForm(p => ({ ...p, source: e.target.value }))} placeholder="资料出处" />
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                  <button className="btn" onClick={() => setKnShowForm(false)}>取消</button>
                  <button className="btn-primary" onClick={saveKnowledge} disabled={knSaving || !knForm.title.trim()}>
                    {knSaving ? "保存中..." : (knEditing ? "保存修改" : "创建")}
                  </button>
                </div>
              </div>
            </div>
          )}

          {knowledgeList.length === 0 ? (
            <div className="card" style={{ padding: 24, textAlign: "center" }}>
              <div style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
                知识库暂无条目。点击「新建写作知识」添加专业领域知识。
              </div>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {knowledgeList
                .filter(k => {
                  const q = knSearch.trim().toLowerCase();
                  if (!q) return true;
                  return (k.title || "").toLowerCase().includes(q)
                    || (k.content || "").toLowerCase().includes(q)
                    || (k.domain || "").toLowerCase().includes(q)
                    || (k.tags || []).some(t => t.toLowerCase().includes(q));
                })
                .map(k => (
                  <div key={k.id} className="card" style={{ padding: "12px 16px" }}>
                    <div className="flex items-center gap-8" style={{ marginBottom: 4 }}>
                      <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>{k.title}</span>
                      {k.domain && <span className="tag category" style={{ fontSize: 10 }}>{k.domain}</span>}
                      <div className="flex gap-4" style={{ marginLeft: "auto" }}>
                        <button className="btn" style={{ fontSize: 10, padding: "3px 10px" }} onClick={() => openKnEdit(k)}>编辑</button>
                        {knConfirmDelete === k.id ? (
                          <>
                            <button className="btn" style={{ fontSize: 10, padding: "3px 8px" }} onClick={() => setKnConfirmDelete(null)}>取消</button>
                            <button className="btn" style={{ fontSize: 10, padding: "3px 8px", color: "var(--error)", borderColor: "var(--error)" }} onClick={() => deleteKnowledge(k.id)}>确认删除</button>
                          </>
                        ) : (
                          <button className="btn" style={{ fontSize: 10, padding: "3px 10px", color: "var(--error)" }} onClick={() => setKnConfirmDelete(k.id)}>删除</button>
                        )}
                      </div>
                    </div>
                    <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {k.content}
                    </div>
                    {k.tags && k.tags.length > 0 && (
                      <div className="flex gap-4" style={{ flexWrap: "wrap", marginTop: 6 }}>
                        {k.tags.map(t => <span key={t} className="tag" style={{ fontSize: 9 }}>{t}</span>)}
                      </div>
                    )}
                    {k.source && <div className="text-xs text-muted" style={{ marginTop: 4 }}>来源：{k.source}</div>}
                  </div>
                ))}
            </div>
          )}
        </>
      )}

      {/* ═══════════════════════ TAB: 写作偏好 (Part A) ═══════════════════════ */}
      {activeTab === "preferences" && <PreferencesPage projectId={activeProject} />}

      {/* ═══════════════════════ TAB: 领域知识 (Part B) ═══════════════════════ */}
      {activeTab === "domain" && <DomainLearningPage projectId={activeProject} />}
    </div>
  );
}
