import React, { useEffect, useState, useCallback } from "react";
import { apiGet, apiPost } from "../api/client";
import type { SkillInfo, SkillExecuteResult } from "../api/types";

const DOMAIN_LABELS: Record<string, { label: string; color: string }> = {
  planner: { label: "Planner", color: "var(--indigo)" },
  production: { label: "Production", color: "var(--gold)" },
  evaluation: { label: "Evaluation", color: "var(--accent)" },
  analysis: { label: "Analysis", color: "var(--jade)" },
  learned_skills: { label: "Learned", color: "var(--purple)" },
  unknown: { label: "Other", color: "var(--text-secondary)" },
};

export default function SkillsPage() {
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

  const loadSkills = useCallback(() => {
    setLoading(true);
    Promise.all([
      apiGet<{ skills: SkillInfo[]; total: number }>("/api/skills"),
      apiGet<{ tags: string[] }>("/api/skills/tags"),
    ])
      .then(([skillsResp, tagsResp]) => {
        setSkills(skillsResp.skills || []);
        setAllTags(tagsResp.tags || []);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadSkills(); }, [loadSkills]);

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
                      }}
                    >
                      <div style={{ flex: 1 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
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
                        </div>
                        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
                          {skill.description}
                        </div>
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
