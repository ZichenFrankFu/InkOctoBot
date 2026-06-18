import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, apiDelete } from "../../api/client";
import { useToast } from "../shared/Toast";

// 多作品学习 (spec 参考数据库功能10)：选取多个参考作品 + 关注维度 →
// LLM 提取共通点并总结为自学习 skill，供以后章节生成时按相关度召回。

interface WorkLite { ref_id: string; title: string; creator?: string; }
interface LearnedSkill {
  skill_id: string; display_name: string; description: string;
  body_snippet: string; updated_at?: string;
}

const DIMENSIONS: [string, string][] = [
  ["overall", "整体"], ["plot", "剧情大纲"], ["characters", "角色塑造"],
  ["settings", "世界观设定"], ["rhythm", "叙事节奏"], ["style", "语言风格"],
];

export default function CommonPatternLearningPanel({ works }: { works: WorkLite[] }) {
  const { toast } = useToast();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dimension, setDimension] = useState("overall");
  const [learning, setLearning] = useState(false);
  const [skills, setSkills] = useState<LearnedSkill[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const r = await apiGet<{ items: LearnedSkill[] }>("/api/references/learned-skills");
      setSkills(r.items || []);
    } catch { setSkills([]); }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const toggle = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const run = async () => {
    if (selected.size < 2) { toast("至少选择 2 部参考作品", "error"); return; }
    setLearning(true);
    try {
      const r = await apiPost<any>("/api/references/works/learn-common", {
        ref_ids: Array.from(selected), dimension,
      }, { timeoutMs: 300000 });
      toast(`学习完成：「${r.display_name}」已存为自学习 skill（${r.patterns_count} 条共通点）`, "success");
      setExpandedId(r.skill_id);
      await reload();
    } catch (e: any) { toast(e.message || "学习失败", "error"); }
    finally { setLearning(false); }
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", marginBottom: 16, flexWrap: "wrap" }}>
        <div style={{ flex: 1, minWidth: 280 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
            选择参考作品（已选 {selected.size}）
          </div>
          <div style={{ maxHeight: 220, overflowY: "auto", border: "1px solid var(--border-subtle)", borderRadius: 6, padding: "4px 8px" }}>
            {works.length === 0 && <div style={{ fontSize: 11, color: "var(--text-tertiary)", padding: 8 }}>参考库暂无作品。</div>}
            {works.map(w => (
              <label key={w.ref_id} style={{ display: "flex", gap: 8, alignItems: "center", padding: "4px 0", fontSize: 12, cursor: "pointer", borderBottom: "1px solid var(--border-subtle)" }}>
                <input type="checkbox" checked={selected.has(w.ref_id)} onChange={() => toggle(w.ref_id)} />
                <span style={{ fontWeight: 600 }}>{w.title}</span>
                {w.creator && <span style={{ color: "var(--text-tertiary)", fontSize: 10 }}>{w.creator}</span>}
              </label>
            ))}
          </div>
        </div>
        <div style={{ width: 220 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>关注维度</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {DIMENSIONS.map(([k, label]) => (
              <button key={k} className="btn" style={{
                fontSize: 11, padding: "4px 12px", textAlign: "center",
                color: dimension === k ? "var(--accent)" : undefined,
                borderColor: dimension === k ? "var(--accent)" : undefined,
              }} onClick={() => setDimension(k)}>{label}</button>
            ))}
          </div>
          <button className="btn-primary" style={{ width: "100%", marginTop: 10, fontSize: 12, padding: "6px 0" }}
            disabled={learning || selected.size < 2} onClick={run}>
            {learning ? "学习中..." : "提取共通点"}
          </button>
        </div>
      </div>

      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
        自学习成果（{skills.length}）
      </div>
      {skills.length === 0 && <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无自学习 skill。</div>}
      {skills.map(s => (
        <div key={s.skill_id} style={{ borderTop: "1px solid var(--border-subtle)", padding: "8px 0" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ fontSize: 12, fontWeight: 600 }}>{s.display_name}</span>
            <span style={{ fontSize: 10, color: "var(--text-tertiary)", flex: 1 }}>{s.description}</span>
            <button className="btn" style={{ fontSize: 10, padding: "1px 8px" }}
              onClick={() => setExpandedId(expandedId === s.skill_id ? null : s.skill_id)}>
              {expandedId === s.skill_id ? "收起" : "查看"}
            </button>
            <button className="btn" style={{ fontSize: 10, padding: "1px 8px", color: "var(--error)" }}
              onClick={async () => {
                try { await apiDelete(`/api/references/learned-skills/${s.skill_id}`); reload(); }
                catch (e: any) { toast(e.message || "删除失败", "error"); }
              }}>删除</button>
          </div>
          {expandedId === s.skill_id && (
            <pre style={{ marginTop: 6, fontSize: 11, whiteSpace: "pre-wrap", color: "var(--text-secondary)", background: "var(--bg-secondary)", borderRadius: 6, padding: "8px 10px", maxHeight: 280, overflow: "auto" }}>
              {s.body_snippet}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}
