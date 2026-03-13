import React, { useEffect, useState, useCallback } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import type { SkillInfo, SkillExecuteResult, Project } from "../api/types";

const DOMAIN_LABELS: Record<string, { label: string; color: string }> = {
  planner: { label: "Planner", color: "var(--indigo)" },
  production: { label: "Production", color: "var(--gold)" },
  evaluation: { label: "Evaluation", color: "var(--accent)" },
  analysis: { label: "Analysis", color: "var(--jade)" },
  learned_skills: { label: "Learned", color: "var(--purple)" },
  unknown: { label: "Other", color: "var(--text-secondary)" },
};

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

interface Props {
  projects: Project[];
  activeProject: string;
}

export default function SkillsPage({ projects, activeProject }: Props) {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [filterTag, setFilterTag] = useState("");
  const [allTags, setAllTags] = useState<string[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [testSkill, setTestSkill] = useState<string | null>(null);
  const [testInput, setTestInput] = useState("{}");
  const [testResult, setTestResult] = useState<SkillExecuteResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState("");
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

  const loadSkills = useCallback(() => {
    setLoading(true);
    Promise.all([
      apiGet<{ skills: SkillInfo[]; total: number }>("/api/skills"),
      apiGet<{ tags: string[] }>("/api/skills/tags"),
      apiGet<{ entries: LearningLogEntry[] }>("/api/skills/learning-log"),
    ])
      .then(([skillsResp, tagsResp, logResp]) => {
        setSkills(skillsResp.skills || []);
        setAllTags(tagsResp.tags || []);
        setLearningLog(logResp.entries || []);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

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
    } catch { /* ignore */ }
    setPrefLoading(false);
  };

  const removeExtractedMemory = (id: string) => {
    setExtractedMemories(prev => prev.filter(m => m.id !== id));
    apiDelete(`/api/data/preferences/memory/${id}?project_id=${prefProject}`).catch(() => {});
  };

  const updateExtractedMemory = (id: string, newContent: string) => {
    setExtractedMemories(prev => prev.map(m => m.id === id ? { ...m, content: newContent } : m));
    setEditingMemoryId(null);
    apiPut(`/api/data/preferences/memory/${id}`, { project_id: prefProject, content: newContent }).catch(() => {});
  };

  const removePrefEntry = (id: string) => {
    setPrefEntries(prev => prev.filter(e => e.id !== id));
    apiDelete(`/api/data/preferences/${id}?project_id=${prefProject}`).catch(() => {});
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
    } catch (e: any) {
      alert(e?.message || "Failed to create skill");
    }
    setCreating(false);
  };

  const handleToggleSkill = async (name: string) => {
    try {
      const resp = await apiPost<{ active: boolean }>(`/api/skills/${name}/toggle`, {});
      setSkills(prev => prev.map(s => s.name === name ? { ...s, active: resp.active } : s));
    } catch (e: any) {
      alert(e?.message || "Failed to toggle skill");
    }
  };

  const handleDeleteSkill = async (name: string) => {
    try {
      await apiDelete(`/api/skills/${name}`);
      setSkills(prev => prev.filter(s => s.name !== name));
      setConfirmDelete(null);
      if (expanded === name) setExpanded(null);
      if (editingSkill === name) setEditingSkill(null);
    } catch (e: any) {
      alert(e?.message || "Failed to delete skill");
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
    } catch (e: any) {
      alert(e?.message || "Failed to update skill");
    }
    setSaving(false);
  };

  const filtered = skills.filter((s) => {
    if (search && !s.name.includes(search) && !s.display_name.includes(search) && !s.description.includes(search)) return false;
    if (filterTag && !s.tags.includes(filterTag)) return false;
    return true;
  });

  // Group by domain
  const grouped: Record<string, SkillInfo[]> = {};
  for (const s of filtered) {
    const domain = s.agent_domain || "unknown";
    if (!grouped[domain]) grouped[domain] = [];
    grouped[domain].push(s);
  }

  const domainOrder = ["planner", "production", "evaluation", "analysis", "learned_skills", "unknown"];
  const sortedDomains = Object.keys(grouped).sort(
    (a, b) => domainOrder.indexOf(a) - domainOrder.indexOf(b)
  );

  const handleTest = async (skillName: string) => {
    setTesting(true);
    setTestResult(null);
    setTestError("");
    try {
      const inputs = JSON.parse(testInput);
      const resp = await apiPost<SkillExecuteResult>("/api/skills/execute", {
        name: skillName,
        inputs,
      });
      setTestResult(resp);
    } catch (e: any) {
      setTestError(e?.message || String(e));
    }
    setTesting(false);
  };

  const projectName = (pid: string) => projects.find(p => p.id === pid)?.name || pid;

  if (loading) {
    return (
      <div className="loading" style={{ paddingTop: 120 }}>
        <div className="loading-spinner" />
        Loading skills...
      </div>
    );
  }

  return (
    <div className="page-container" style={{ maxWidth: 1100 }}>
      {/* Header */}
      <div style={{ marginBottom: 24, display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
        <div>
          <h2 style={{ fontFamily: "var(--font-serif)", fontSize: 22, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
            Skill Registry
          </h2>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
            {skills.length} skills registered across {Object.keys(grouped).length} domains
          </p>
        </div>
        <button className="btn-primary" style={{ fontSize: 12 }} onClick={() => setShowCreate(!showCreate)}>
          + New Skill
        </button>
      </div>

      {/* ════════════════════════════════════════════════════════════════
          自学习成果 (Self-Learning Results)
          ════════════════════════════════════════════════════════════════ */}
      <div className="card" style={{ marginBottom: 24, borderLeft: "3px solid var(--purple)" }}>
        <div className="card-header" style={{ background: "var(--purple-subtle, rgba(147,51,234,0.06))" }}>
          <h3 style={{ fontSize: 15, margin: 0, color: "var(--purple, #9333ea)" }}>自学习成果</h3>
          <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            EditAnalyzer · 偏好记忆 · 自动习得技能
          </span>
        </div>
        <div className="card-body" style={{ padding: 0 }}>
          {/* ── Skill Learning Log ── */}
          <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border-subtle)" }}>
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
                {learningLog.map(entry => (
                  <div key={entry.id} style={{
                    padding: "10px 14px", background: "var(--bg-surface)", borderRadius: 8,
                    border: "1px solid var(--border-subtle)",
                  }}>
                    <div className="flex items-center gap-8" style={{ marginBottom: 4 }}>
                      <span style={{ fontSize: 13, fontWeight: 600, color: "var(--purple, #9333ea)" }}>
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
                    {/* Actions for learned skills */}
                    {(() => {
                      const matchedSkill = skills.find(s => s.name === entry.skill_name);
                      if (!matchedSkill) return null;
                      return (
                        <div style={{ display: "flex", gap: 6, marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border-subtle)" }}>
                          <button
                            className="btn"
                            style={{ fontSize: 10, padding: "3px 10px" }}
                            onClick={() => handleToggleSkill(entry.skill_name)}
                          >
                            {matchedSkill.active === false ? "启用" : "停用"}
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
                          {matchedSkill.active === false && (
                            <span style={{ fontSize: 10, color: "var(--text-disabled)", lineHeight: "22px", marginLeft: 4 }}>已停用</span>
                          )}
                        </div>
                      );
                    })()}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* ── Per-Project Preference Memories ── */}
          <div style={{ padding: "16px 20px" }}>
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

            {/* Summary */}
            {prefSummary && (
              <div style={{ padding: "8px 12px", background: "var(--bg-surface)", borderRadius: 6, marginBottom: 12, fontSize: 12, color: "var(--text-secondary)" }}>
                {prefSummary}
              </div>
            )}

            {/* Extracted memories */}
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

            {/* User interaction entries */}
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

      {/* Create skill form */}
      {showCreate && (
        <div className="card mb-20" style={{ animation: "slideUp 0.2s var(--ease-out)" }}>
          <div className="card-header"><h3>Create Learned Skill</h3></div>
          <div className="card-body">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
              <div className="field">
                <label className="label">Skill Name (snake_case)</label>
                <input className="input" value={newSkill.name} onChange={e => setNewSkill(prev => ({ ...prev, name: e.target.value }))} placeholder="my_custom_skill" />
              </div>
              <div className="field">
                <label className="label">Display Name</label>
                <input className="input" value={newSkill.display_name} onChange={e => setNewSkill(prev => ({ ...prev, display_name: e.target.value }))} placeholder="My Custom Skill" />
              </div>
            </div>
            <div className="field mb-12">
              <label className="label">Description</label>
              <input className="input" value={newSkill.description} onChange={e => setNewSkill(prev => ({ ...prev, description: e.target.value }))} placeholder="What this skill does..." />
            </div>
            <div className="field mb-12">
              <label className="label">Tags (comma separated)</label>
              <input className="input" value={newSkill.tags} onChange={e => setNewSkill(prev => ({ ...prev, tags: e.target.value }))} placeholder="custom, writing, analysis" />
            </div>
            <div className="field mb-12">
              <label className="label">Prompt Template</label>
              <textarea className="input" value={newSkill.prompt_template} onChange={e => setNewSkill(prev => ({ ...prev, prompt_template: e.target.value }))}
                placeholder="请根据以下输入生成内容：\n\n{text}" rows={3} style={{ fontFamily: "var(--font-mono)", fontSize: 12 }} />
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setShowCreate(false)}>Cancel</button>
              <button className="btn-primary" onClick={handleCreateSkill} disabled={creating || !newSkill.name.trim()}>
                {creating ? "Creating..." : "Create Skill"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Stats bar */}
      <div style={{ display: "flex", gap: 12, marginBottom: 20, flexWrap: "wrap" }}>
        {domainOrder.map((d) => {
          const count = grouped[d]?.length || 0;
          if (!count) return null;
          const meta = DOMAIN_LABELS[d] || DOMAIN_LABELS.unknown;
          return (
            <div
              key={d}
              style={{
                padding: "6px 14px",
                borderRadius: 8,
                background: "var(--bg-surface)",
                border: `1px solid ${meta.color}`,
                fontSize: 12,
                color: meta.color,
                fontWeight: 600,
              }}
            >
              {meta.label}: {count}
            </div>
          );
        })}
      </div>

      {/* Search + filter */}
      <div style={{ display: "flex", gap: 12, marginBottom: 20 }}>
        <input
          className="input"
          placeholder="Search skills..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ flex: 1, maxWidth: 300 }}
        />
        <select
          className="select"
          value={filterTag}
          onChange={(e) => setFilterTag(e.target.value)}
          style={{ minWidth: 140 }}
        >
          <option value="">All tags</option>
          {allTags.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </div>

      {/* Skill groups */}
      {sortedDomains.map((domain) => {
        const domainSkills = grouped[domain];
        const meta = DOMAIN_LABELS[domain] || DOMAIN_LABELS.unknown;
        return (
          <div key={domain} className="card" style={{ marginBottom: 16 }}>
            <div className="card-header" style={{ borderLeft: `3px solid ${meta.color}` }}>
              <h3 style={{ fontSize: 14, margin: 0, color: meta.color }}>{meta.label}</h3>
              <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                {domainSkills.length} skill{domainSkills.length > 1 ? "s" : ""}
              </span>
            </div>
            <div className="card-body" style={{ padding: 0 }}>
              {domainSkills.map((skill, idx) => {
                const isExpanded = expanded === skill.name;
                const isTesting = testSkill === skill.name;
                return (
                  <div
                    key={skill.name}
                    style={{
                      borderBottom: idx < domainSkills.length - 1 ? "1px solid var(--border-subtle)" : "none",
                    }}
                  >
                    {/* Skill row */}
                    <div
                      onClick={() => setExpanded(isExpanded ? null : skill.name)}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 12,
                        padding: "14px 20px",
                        cursor: "pointer",
                        transition: "background 0.1s",
                        opacity: skill.active === false ? 0.5 : 1,
                      }}
                    >
                      <div style={{ flex: 1 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)", textDecoration: skill.active === false ? "line-through" : "none" }}>
                            {skill.display_name}
                          </span>
                          <code style={{ fontSize: 11, color: "var(--text-tertiary)", background: "var(--bg-secondary)", padding: "1px 6px", borderRadius: 4 }}>
                            {skill.name}
                          </code>
                          <span style={{ fontSize: 10, color: "var(--text-disabled)" }}>v{skill.version}</span>
                          {skill.is_learned && (
                            <span style={{ fontSize: 10, padding: "1px 8px", borderRadius: 10, background: "var(--purple-subtle, rgba(147,51,234,0.1))", color: "var(--purple, #9333ea)", fontWeight: 600 }}>
                              自学习
                            </span>
                          )}
                          {skill.active === false && (
                            <span style={{ fontSize: 10, padding: "1px 8px", borderRadius: 10, background: "var(--bg-secondary)", color: "var(--text-disabled)", fontWeight: 600 }}>
                              已停用
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
                          {skill.description}
                        </div>
                      </div>
                      {/* Action buttons */}
                      <div style={{ display: "flex", gap: 4, flexShrink: 0 }} onClick={e => e.stopPropagation()}>
                        <button
                          className="btn"
                          style={{ fontSize: 10, padding: "3px 8px" }}
                          title={skill.active === false ? "启用" : "停用"}
                          onClick={() => handleToggleSkill(skill.name)}
                        >
                          {skill.active === false ? "启用" : "停用"}
                        </button>
                        {skill.is_learned && (
                          <>
                            <button
                              className="btn"
                              style={{ fontSize: 10, padding: "3px 8px" }}
                              title="修改"
                              onClick={() => handleEditSkill(skill)}
                            >
                              修改
                            </button>
                            <button
                              className="btn"
                              style={{ fontSize: 10, padding: "3px 8px", color: "var(--error)" }}
                              title="删除"
                              onClick={() => setConfirmDelete(skill.name)}
                            >
                              删除
                            </button>
                          </>
                        )}
                      </div>
                      <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
                        {skill.tags.slice(0, 3).map((t) => (
                          <span
                            key={t}
                            style={{
                              fontSize: 10,
                              padding: "2px 8px",
                              borderRadius: 10,
                              background: "var(--bg-secondary)",
                              color: "var(--text-secondary)",
                            }}
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                      <div style={{
                        fontSize: 11,
                        color: "var(--text-tertiary)",
                        minWidth: 80,
                        textAlign: "right",
                      }}>
                        role: {skill.model_role}
                      </div>
                      <span style={{ fontSize: 12, color: "var(--text-disabled)", transition: "transform 0.2s", transform: isExpanded ? "rotate(90deg)" : "none" }}>
                        &#9654;
                      </span>
                    </div>

                    {/* Delete confirmation */}
                    {confirmDelete === skill.name && (
                      <div style={{ padding: "12px 20px", background: "rgba(239,68,68,0.06)", borderTop: "1px solid var(--border-subtle)", display: "flex", alignItems: "center", gap: 12 }}>
                        <span style={{ fontSize: 12, color: "var(--error)", flex: 1 }}>
                          确认删除技能 "{skill.display_name}"？此操作不可撤销。
                        </span>
                        <button className="btn" style={{ fontSize: 11, padding: "4px 12px" }} onClick={() => setConfirmDelete(null)}>取消</button>
                        <button className="btn-primary" style={{ fontSize: 11, padding: "4px 12px", background: "var(--error)", borderColor: "var(--error)" }} onClick={() => handleDeleteSkill(skill.name)}>确认删除</button>
                      </div>
                    )}

                    {/* Edit form */}
                    {editingSkill === skill.name && (
                      <div style={{ padding: "16px 20px", background: "var(--bg-secondary)", borderTop: "1px solid var(--border-subtle)" }}>
                        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", marginBottom: 12 }}>修改技能</div>
                        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
                          <div className="field">
                            <label className="label">Display Name</label>
                            <input className="input" value={editForm.display_name} onChange={e => setEditForm(prev => ({ ...prev, display_name: e.target.value }))} />
                          </div>
                          <div className="field">
                            <label className="label">Tags (逗号分隔)</label>
                            <input className="input" value={editForm.tags} onChange={e => setEditForm(prev => ({ ...prev, tags: e.target.value }))} />
                          </div>
                        </div>
                        <div className="field mb-12">
                          <label className="label">Description</label>
                          <input className="input" value={editForm.description} onChange={e => setEditForm(prev => ({ ...prev, description: e.target.value }))} />
                        </div>
                        <div className="field mb-12">
                          <label className="label">Prompt Template (留空保持不变)</label>
                          <textarea className="input" value={editForm.prompt_template} onChange={e => setEditForm(prev => ({ ...prev, prompt_template: e.target.value }))}
                            placeholder="留空则保持原有模板" rows={3} style={{ fontFamily: "var(--font-mono)", fontSize: 12 }} />
                        </div>
                        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                          <button className="btn" onClick={() => setEditingSkill(null)}>取消</button>
                          <button className="btn-primary" onClick={handleSaveEdit} disabled={saving}>
                            {saving ? "保存中..." : "保存修改"}
                          </button>
                        </div>
                      </div>
                    )}

                    {/* Expanded detail */}
                    {isExpanded && (
                      <div style={{ padding: "0 20px 16px", background: "var(--bg-secondary)", borderTop: "1px solid var(--border-subtle)" }}>
                        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 12 }}>
                          <div>
                            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4 }}>Input Schema</div>
                            <pre style={{ fontSize: 11, background: "var(--bg-surface)", padding: 8, borderRadius: 6, overflow: "auto", maxHeight: 200, margin: 0 }}>
                              {JSON.stringify(skill.input_schema, null, 2)}
                            </pre>
                          </div>
                          <div>
                            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4 }}>Output Schema</div>
                            <pre style={{ fontSize: 11, background: "var(--bg-surface)", padding: 8, borderRadius: 6, overflow: "auto", maxHeight: 200, margin: 0 }}>
                              {JSON.stringify(skill.output_schema, null, 2)}
                            </pre>
                          </div>
                        </div>

                        <div style={{ display: "flex", gap: 12, marginTop: 12, fontSize: 12 }}>
                          <span style={{ color: "var(--text-secondary)" }}>
                            Temperature: <strong>{skill.temperature}</strong>
                          </span>
                          <span style={{ color: "var(--text-secondary)" }}>
                            Max tokens: <strong>{skill.max_tokens}</strong>
                          </span>
                          <span style={{ color: "var(--text-secondary)" }}>
                            Permissions: <strong>{skill.permissions.join(", ") || "none"}</strong>
                          </span>
                          {skill.is_learned && (
                            <span style={{ color: "var(--purple)", fontWeight: 600 }}>Learned Skill</span>
                          )}
                        </div>

                        {/* Test panel */}
                        <div style={{ marginTop: 16, padding: 12, background: "var(--bg-surface)", borderRadius: 8, border: "1px solid var(--border)" }}>
                          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                            <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Test Skill</span>
                            <button
                              className="btn-primary"
                              onClick={() => {
                                setTestSkill(skill.name);
                                handleTest(skill.name);
                              }}
                              disabled={testing && isTesting}
                              style={{ fontSize: 11, padding: "4px 12px" }}
                            >
                              {testing && isTesting ? "Running..." : "Execute"}
                            </button>
                          </div>
                          <textarea
                            value={isTesting || testSkill === skill.name ? testInput : "{}"}
                            onChange={(e) => {
                              setTestSkill(skill.name);
                              setTestInput(e.target.value);
                            }}
                            placeholder='{"text": "sample input..."}'
                            style={{
                              width: "100%",
                              minHeight: 60,
                              fontFamily: "var(--font-mono)",
                              fontSize: 11,
                              padding: 8,
                              borderRadius: 6,
                              border: "1px solid var(--border)",
                              background: "var(--bg-secondary)",
                              color: "var(--text-primary)",
                              resize: "vertical",
                            }}
                          />
                          {testResult && isTesting && (
                            <div style={{ marginTop: 8 }}>
                              <div style={{ fontSize: 11, color: "var(--jade)", marginBottom: 4 }}>
                                Completed in {testResult.execution_time_ms}ms
                              </div>
                              <pre style={{ fontSize: 11, background: "var(--bg-secondary)", padding: 8, borderRadius: 6, overflow: "auto", maxHeight: 300, margin: 0 }}>
                                {JSON.stringify(testResult.result, null, 2)}
                              </pre>
                            </div>
                          )}
                          {testError && isTesting && (
                            <div style={{ marginTop: 8, fontSize: 11, color: "var(--error)" }}>
                              {testError}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      {filtered.length === 0 && (
        <div style={{ textAlign: "center", padding: 60, color: "var(--text-tertiary)", fontSize: 14 }}>
          No skills found matching your criteria.
        </div>
      )}
    </div>
  );
}
