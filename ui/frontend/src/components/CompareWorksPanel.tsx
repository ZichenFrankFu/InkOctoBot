/**
 * Compare-Works → Draft Skill. Pick 2-8 reference works, contrast their
 * extracted features, and turn the insight into a saveable learned skill.
 * Rendered as a tab inside the 灵感搜索 page.
 */
import React, { useEffect, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import { useToast } from "./shared/Toast";

interface WorkRow {
  ref_id: string;
  title: string;
  creator?: string;
  media_type?: string;
}

interface CompareDraft {
  name: string;
  display_name: string;
  description: string;
  prompt_template: string;
  tags: string[];
}

interface CompareResponse {
  draft: CompareDraft;
  source_works: { ref_id: string; title: string }[];
  focus: string;
}

const FOCUS_OPTIONS: { value: string; label: string }[] = [
  { value: "all",        label: "整体" },
  { value: "plot",       label: "剧情大纲" },
  { value: "characters", label: "角色塑造" },
  { value: "settings",   label: "世界观设定" },
  { value: "rhythm",     label: "叙事节奏" },
  { value: "style",      label: "语言风格" },
];

export default function CompareWorksPanel({ onSaved }: { onSaved?: () => void }) {
  const { toast } = useToast();
  const [works, setWorks] = useState<WorkRow[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string>("all");
  const [instruction, setInstruction] = useState("");
  const [searching, setSearching] = useState("");
  const [mode, setMode] = useState<"ai" | "manual">("ai");
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<CompareDraft | null>(null);
  const [sourceWorks, setSourceWorks] = useState<{ ref_id: string; title: string }[]>([]);
  // Manual (copy-prompt / paste-result) mode
  const [promptText, setPromptText] = useState("");
  const [pasteText, setPasteText] = useState("");
  const [loadingPrompt, setLoadingPrompt] = useState(false);

  useEffect(() => {
    apiGet<{ items: WorkRow[]; total: number }>("/api/references/works?limit=500")
      .then(r => setWorks(r.items || []))
      .catch(() => setWorks([]));
  }, []);

  const filtered = works.filter(w => {
    if (!searching.trim()) return true;
    const q = searching.toLowerCase();
    return (w.title || "").toLowerCase().includes(q) ||
           (w.creator || "").toLowerCase().includes(q);
  });

  const toggleWork = (refId: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(refId)) next.delete(refId);
      else if (next.size < 8) next.add(refId);
      return next;
    });
  };

  const generate = async () => {
    if (selected.size < 2) {
      toast("请选择至少 2 部作品对比", "error");
      return;
    }
    setGenerating(true);
    setDraft(null);
    try {
      const r = await apiPost<CompareResponse>(
        "/api/skills/compare_works",
        { ref_ids: Array.from(selected), focus, instruction },
        { timeoutMs: 300_000 },
      );
      setDraft(r.draft);
      setSourceWorks(r.source_works || []);
    } catch (e: any) {
      toast(`生成失败: ${e?.message || e}`, "error");
    } finally { setGenerating(false); }
  };

  const loadPrompt = async () => {
    if (selected.size < 2) {
      toast("请选择至少 2 部作品对比", "error");
      return;
    }
    setLoadingPrompt(true);
    try {
      const r = await apiPost<{ prompt: string }>(
        "/api/skills/compare_works/prompt",
        { ref_ids: Array.from(selected), focus, instruction },
      );
      setPromptText(r.prompt || "");
      try {
        await navigator.clipboard.writeText(r.prompt || "");
        toast("Prompt 已生成并复制到剪贴板", "success");
      } catch {
        toast("Prompt 已生成（请手动复制下方文本）", "info");
      }
    } catch (e: any) {
      toast(`生成 Prompt 失败: ${e?.message || e}`, "error");
    } finally { setLoadingPrompt(false); }
  };

  const parsePasted = () => {
    let s = pasteText.trim();
    if (!s) { toast("请先粘贴网页 LLM 返回的结果", "error"); return; }
    const fence = s.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/);
    if (fence) s = fence[1].trim();
    const a = s.indexOf("{"), b = s.lastIndexOf("}");
    if (a >= 0 && b > a) s = s.slice(a, b + 1);
    try {
      const obj = JSON.parse(s);
      setDraft({
        name: String(obj.name || ""),
        display_name: String(obj.display_name || ""),
        description: String(obj.description || ""),
        prompt_template: String(obj.prompt_template || ""),
        tags: Array.isArray(obj.tags) ? obj.tags.map((t: any) => String(t)) : [],
      });
      setSourceWorks(works.filter(w => selected.has(w.ref_id)).map(w => ({ ref_id: w.ref_id, title: w.title })));
      toast("解析成功", "success");
    } catch {
      toast("解析失败：粘贴的内容不是合法 JSON", "error");
    }
  };

  const save = async () => {
    if (!draft) return;
    if (!draft.name.trim() || !draft.prompt_template.trim()) {
      toast("请填写技能名和 Prompt 模板", "error");
      return;
    }
    setSaving(true);
    try {
      await apiPost("/api/skills/create", {
        name: draft.name.trim(),
        display_name: draft.display_name.trim() || draft.name.trim(),
        description: draft.description.trim(),
        domain: "learned_skills",
        model_role: "default",
        tags: draft.tags,
        prompt_template: draft.prompt_template,
      });
      toast("已保存为自学习技能，可在「智能体」页面的「自学习成果」查看", "success");
      onSaved?.();
      setDraft(null);
      setSelected(new Set());
      setPromptText("");
      setPasteText("");
    } catch (e: any) {
      toast(`保存失败: ${e?.message || e}`, "error");
    } finally { setSaving(false); }
  };

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        {/* LEFT: work picker */}
        <div className="card">
          <div className="card-header">
            <h3>
              选择作品
              <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                {selected.size}/{Math.min(works.length, 8)} 已选 (最多 8)
              </span>
            </h3>
          </div>
          <div className="card-body">
            <input
              className="input"
              placeholder="搜索标题 / 作者..."
              value={searching}
              onChange={e => setSearching(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            <div style={{ maxHeight: 380, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
              {filtered.length === 0 ? (
                <div className="text-xs text-muted text-center" style={{ padding: 16 }}>
                  无匹配作品
                </div>
              ) : filtered.map(w => {
                const on = selected.has(w.ref_id);
                return (
                  <label key={w.ref_id} style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "8px 12px", cursor: "pointer",
                    borderBottom: "1px solid var(--border)",
                    background: on ? "var(--accent-subtle)" : "transparent",
                  }}>
                    <input
                      type="checkbox" checked={on}
                      onChange={() => toggleWork(w.ref_id)}
                      style={{ width: 14, height: 14 }}
                    />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="truncate" style={{ fontSize: 13, fontWeight: 600 }}>{w.title}</div>
                      {w.creator && (
                        <div className="text-xs text-muted truncate">{w.creator}</div>
                      )}
                    </div>
                  </label>
                );
              })}
            </div>
          </div>
        </div>

        {/* RIGHT: focus + instruction + run */}
        <div className="card">
          <div className="card-header"><h3>对比设置</h3></div>
          <div className="card-body">
            <div className="field" style={{ marginBottom: 12 }}>
              <label className="label">关注维度</label>
              <select className="select w-full" value={focus} onChange={e => setFocus(e.target.value)}>
                {FOCUS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </div>
            <div className="field" style={{ marginBottom: 12 }}>
              <label className="label">额外指示（可选）</label>
              <textarea
                className="input" rows={2}
                placeholder="例如：把对比结果包装成一个能指导章节导演选择 hook 类型的技能"
                value={instruction}
                onChange={e => setInstruction(e.target.value)}
              />
            </div>
            <div className="field" style={{ marginBottom: 12 }}>
              <label className="label">处理方式</label>
              <div style={{ display: "flex", gap: 6 }}>
                {([["ai", "AI大模型API处理"], ["manual", "AI大模型网页版"]] as const).map(([k, lbl]) => (
                  <button key={k} className={mode === k ? "btn-primary" : "btn"}
                    style={{
                      fontSize: 11, flex: 1, padding: "6px 4px",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      textAlign: "center",
                    }}
                    onClick={() => setMode(k)}>{lbl}</button>
                ))}
              </div>
            </div>

            {mode === "ai" ? (
              <button
                className="btn-primary w-full"
                onClick={generate}
                disabled={generating || selected.size < 2}
              >
                {generating ? "AI 对比生成中..." : `生成对比技能 (${selected.size} 部作品)`}
              </button>
            ) : (
              <div>
                <button
                  className="btn w-full"
                  onClick={loadPrompt}
                  disabled={loadingPrompt || selected.size < 2}
                  style={{ marginBottom: 8 }}
                >
                  {loadingPrompt ? "生成中..." : `复制对比 Prompt (${selected.size} 部作品)`}
                </button>
                {promptText && (
                  <textarea className="input font-mono" rows={4} readOnly value={promptText}
                    style={{ fontSize: 11, marginBottom: 8, width: "100%", boxSizing: "border-box" }} />
                )}
                <label className="label">粘贴网页 LLM 返回的结果</label>
                <textarea className="input font-mono" rows={4} value={pasteText}
                  onChange={e => setPasteText(e.target.value)}
                  placeholder='{"name": "...", "display_name": "...", "description": "...", "prompt_template": "...", "tags": [...]}'
                  style={{ fontSize: 11, marginBottom: 8, width: "100%", boxSizing: "border-box" }} />
                <button className="btn-primary w-full" onClick={parsePasted} disabled={!pasteText.trim()}>
                  解析为技能草稿
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Draft preview */}
      {draft && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-header">
            <h3>
              草稿技能
              {sourceWorks.length > 0 && (
                <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                  来源：{sourceWorks.map(w => w.title).join(" · ")}
                </span>
              )}
            </h3>
          </div>
          <div className="card-body">
            <div className="field" style={{ marginBottom: 10 }}>
              <label className="label">技能名（snake_case）</label>
              <input
                className="input"
                value={draft.name}
                onChange={e => setDraft({ ...draft, name: e.target.value })}
              />
            </div>
            <div className="field" style={{ marginBottom: 10 }}>
              <label className="label">显示名</label>
              <input
                className="input"
                value={draft.display_name}
                onChange={e => setDraft({ ...draft, display_name: e.target.value })}
              />
            </div>
            <div className="field" style={{ marginBottom: 10 }}>
              <label className="label">描述</label>
              <textarea
                className="input" rows={2}
                value={draft.description}
                onChange={e => setDraft({ ...draft, description: e.target.value })}
              />
            </div>
            <div className="field" style={{ marginBottom: 10 }}>
              <label className="label">标签（逗号分隔）</label>
              <input
                className="input"
                value={draft.tags.join("，")}
                onChange={e => setDraft({ ...draft, tags: e.target.value.split(/[,，]/).map(s => s.trim()).filter(Boolean) })}
              />
            </div>
            <div className="field" style={{ marginBottom: 14 }}>
              <label className="label">Prompt 模板</label>
              <textarea
                className="input font-mono" rows={12}
                style={{ fontSize: 12, lineHeight: 1.55 }}
                value={draft.prompt_template}
                onChange={e => setDraft({ ...draft, prompt_template: e.target.value })}
              />
            </div>
            <div className="flex gap-8" style={{ justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setDraft(null)} disabled={saving}>丢弃</button>
              <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "保存中..." : "保存为技能"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
