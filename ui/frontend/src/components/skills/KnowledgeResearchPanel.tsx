import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../../api/client";
import { useToast } from "../shared/Toast";

// 专业知识自学习 (spec 4.2)：用户确认领域后联网研究（机制1），结果直接
// 编译为 kind=knowledge 的 skill（机制2，标注 AI编译/非权威 机制3），
// 生成时由 skills loader 按章节相关度召回注入（机制4）。

interface KnowledgeSkill {
  skill_id: string;
  display_name: string;
  description: string;
  body_snippet: string;
  updated_at?: string;
}

export default function KnowledgeResearchPanel({ projectId }: { projectId?: string }) {
  const { toast } = useToast();
  const [items, setItems] = useState<KnowledgeSkill[]>([]);
  const [domain, setDomain] = useState("");
  const [context, setContext] = useState("");
  const [researching, setResearching] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingBody, setEditingBody] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const r = await apiGet<{ items: KnowledgeSkill[] }>("/api/knowledge/skills");
      setItems(r.items || []);
    } catch { setItems([]); }
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const runResearch = async () => {
    if (!domain.trim()) { toast("请填写要研究的专业领域", "error"); return; }
    setResearching(true);
    try {
      const r = await apiPost<any>("/api/knowledge/research", {
        project_id: projectId || "", domain: domain.trim(), context,
      }, { timeoutMs: 300000 });
      toast(
        r.used_web_search
          ? `「${domain}」研究完成，已编译为知识 skill`
          : `「${domain}」已编译（当前模型不支持联网，使用模型内置知识）`,
        "success",
      );
      setDomain(""); setContext("");
      await reload();
    } catch (e: any) {
      toast(e.message || "研究失败", "error");
    } finally { setResearching(false); }
  };

  const saveEdit = async (id: string) => {
    try {
      await apiPut(`/api/knowledge/skills/${id}`, { body: editingBody });
      setEditingId(null);
      await reload();
      toast("已保存修改", "success");
    } catch (e: any) { toast(e.message || "保存失败", "error"); }
  };

  const remove = async (id: string) => {
    try {
      await apiDelete(`/api/knowledge/skills/${id}`);
      setConfirmDeleteId(null);
      await reload();
    } catch (e: any) { toast(e.message || "删除失败", "error"); }
  };

  return (
    <div className="card mb-20">
      <div className="card-header">
        <h3>联网研究（AI 编译知识 skill）</h3>
      </div>
      <div className="card-body">
        <div style={{ padding: "8px 12px", background: "var(--bg-secondary)", borderRadius: 6, marginBottom: 12, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.6 }}>
          输入一个专业领域（如 中医、法医、航海），AI 将联网检索并编译为写作参考知识，
          标注「AI编译 / 非权威」后直接入库；后续章节生成时按相关度自动注入。
          系统也会在每 5 章的批量学习中检测正文涉及的领域并通知你。
        </div>
        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          <input className="input" placeholder="专业领域，如：刑侦" value={domain}
            onChange={e => setDomain(e.target.value)} style={{ width: 200 }} />
          <input className="input" placeholder="聚焦点（选填，如：90年代县城刑侦流程）" value={context}
            onChange={e => setContext(e.target.value)} style={{ flex: 1 }} />
          <button className="btn-primary" style={{ flexShrink: 0 }}
            onClick={runResearch} disabled={researching}>
            {researching ? "研究中..." : "联网研究"}
          </button>
        </div>
        {items.length === 0 && (
          <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无已编译的知识 skill。</div>
        )}
        {items.map(k => (
          <div key={k.skill_id} style={{ borderTop: "1px solid var(--border-subtle)", padding: "8px 0" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 12, fontWeight: 600 }}>{k.display_name}</span>
              <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 8, background: "var(--gold-subtle, rgba(212,168,83,0.12))", color: "var(--gold)" }}>
                AI编译 / 非权威
              </span>
              <span style={{ fontSize: 9, color: "var(--text-disabled)", marginLeft: "auto" }}>{k.updated_at || ""}</span>
              <button className="btn" style={{ fontSize: 10, padding: "1px 8px" }}
                onClick={() => setExpandedId(expandedId === k.skill_id ? null : k.skill_id)}>
                {expandedId === k.skill_id ? "收起" : "查看"}
              </button>
              <button className="btn" style={{ fontSize: 10, padding: "1px 8px" }}
                onClick={() => { setEditingId(k.skill_id); setEditingBody(k.body_snippet); setExpandedId(k.skill_id); }}>
                编辑
              </button>
              {confirmDeleteId === k.skill_id ? (
                <>
                  <button className="btn" style={{ fontSize: 10, padding: "1px 8px" }} onClick={() => setConfirmDeleteId(null)}>取消</button>
                  <button className="btn" style={{ fontSize: 10, padding: "1px 8px", color: "var(--error)", borderColor: "var(--error)" }} onClick={() => remove(k.skill_id)}>确认删除</button>
                </>
              ) : (
                <button className="btn" style={{ fontSize: 10, padding: "1px 8px", color: "var(--error)" }}
                  onClick={() => setConfirmDeleteId(k.skill_id)}>删除</button>
              )}
            </div>
            {expandedId === k.skill_id && (
              editingId === k.skill_id ? (
                <div style={{ marginTop: 6 }}>
                  <textarea className="input" value={editingBody}
                    onChange={e => setEditingBody(e.target.value)} rows={10}
                    style={{ width: "100%", boxSizing: "border-box", fontSize: 11, lineHeight: 1.6 }} />
                  <div style={{ display: "flex", gap: 6, marginTop: 4 }}>
                    <button className="btn-primary" style={{ fontSize: 10, padding: "2px 10px" }} onClick={() => saveEdit(k.skill_id)}>保存</button>
                    <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }} onClick={() => setEditingId(null)}>取消</button>
                  </div>
                </div>
              ) : (
                <pre style={{ marginTop: 6, fontSize: 11, whiteSpace: "pre-wrap", color: "var(--text-secondary)", background: "var(--bg-secondary)", borderRadius: 6, padding: "8px 10px", maxHeight: 300, overflow: "auto" }}>
                  {k.body_snippet}
                </pre>
              )
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
