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

export default function CompareWorksPanel({
  onSaved,
  selectedWorks: controlledSelected,
  hideWorkPicker = false,
}: {
  onSaved?: () => void;
  /** When provided, the panel uses this controlled selection instead of
   *  its own internal state (and hides the work picker if
   *  hideWorkPicker=true). The parent is responsible for sourcing works
   *  and managing the selection set. */
  selectedWorks?: { ref_id: string; title: string; creator?: string }[];
  hideWorkPicker?: boolean;
}) {
  const { toast } = useToast();
  const [works, setWorks] = useState<WorkRow[]>([]);
  const [internalSelected, setInternalSelected] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string>("all");
  const [instruction, setInstruction] = useState("");
  const [searching, setSearching] = useState("");
  // Processing-mode lives in a modal now; `modalOpen` drives it.
  const [modalOpen, setModalOpen] = useState(false);
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
    if (controlledSelected) return;  // parent supplies works via selection
    apiGet<{ items: WorkRow[]; total: number }>("/api/references/works?limit=500")
      .then(r => setWorks(r.items || []))
      .catch(() => setWorks([]));
  }, [controlledSelected]);

  // 当父组件托管选择时，使用受控数据；否则用内部状态。
  const selected = controlledSelected
    ? new Set(controlledSelected.map(w => w.ref_id))
    : internalSelected;
  const selectedAsWorks = controlledSelected
    ?? Array.from(internalSelected).map(rid => {
      const w = works.find(x => x.ref_id === rid);
      return { ref_id: rid, title: w?.title || rid };
    });

  const filtered = works.filter(w => {
    if (!searching.trim()) return true;
    const q = searching.toLowerCase();
    return (w.title || "").toLowerCase().includes(q) ||
           (w.creator || "").toLowerCase().includes(q);
  });

  const toggleWork = (refId: string) => {
    setInternalSelected(prev => {
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
      setModalOpen(false);
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
      setSourceWorks(selectedAsWorks.map(w => ({ ref_id: w.ref_id, title: w.title })));
      setModalOpen(false);
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
      if (!controlledSelected) setInternalSelected(new Set());
      setPromptText("");
      setPasteText("");
    } catch (e: any) {
      toast(`保存失败: ${e?.message || e}`, "error");
    } finally { setSaving(false); }
  };

  return (
    <div>
      {/* Body: optional work picker (standalone use) + inline settings.
          In controlled mode, the picker is hidden and only the form is shown. */}
      {!hideWorkPicker && (
        <div className="card" style={{ marginBottom: 14 }}>
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
      )}

      {/* 共通点学习 settings — flat (no inner card-in-card nesting). */}
      <div className="card">
        <div className="card-header">
          <h3 style={{ margin: 0 }}>共通点学习</h3>
        </div>
        <div className="card-body">
          {/* 关注维度 — direct button selection (no dropdown) */}
          <div className="field" style={{ marginBottom: 14 }}>
            <label className="label">关注维度</label>
            <div className="flex" style={{ gap: 6, flexWrap: "wrap" }}>
              {FOCUS_OPTIONS.map(o => {
                const on = focus === o.value;
                return (
                  <button
                    key={o.value}
                    onClick={() => setFocus(o.value)}
                    style={{
                      padding: "5px 14px", fontSize: 12,
                      fontWeight: on ? 700 : 500,
                      color: on ? "var(--accent)" : "var(--text-secondary)",
                      background: on ? "var(--bg-surface-2)" : "var(--bg-surface)",
                      border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
                      borderRadius: "var(--radius-sm)",
                      cursor: "pointer",
                      transition: "all 0.15s",
                    }}>{o.label}</button>
                );
              })}
            </div>
          </div>

          <div className="field" style={{ marginBottom: 14 }}>
            <label className="label">额外指示（可选）</label>
            <textarea
              className="input" rows={2}
              placeholder="例如：把对比结果包装成一个能指导章节导演选择 hook 类型的技能"
              value={instruction}
              onChange={e => setInstruction(e.target.value)}
            />
          </div>

          <div className="flex" style={{ justifyContent: "flex-end" }}>
            <button
              className="btn-primary"
              onClick={() => setModalOpen(true)}
              disabled={selected.size < 2}
              title={selected.size < 2 ? "请先选择至少 2 部作品" : ""}
            >
              提取共通点（{selected.size} 部已选）
            </button>
          </div>
        </div>
      </div>

      {/* 处理方式弹窗 — API 或 网页版，统一与其他大模型交互一致 */}
      {modalOpen && (
        <ProcessingModeModal
          mode={mode}
          onChangeMode={setMode}
          generating={generating}
          loadingPrompt={loadingPrompt}
          promptText={promptText}
          pasteText={pasteText}
          onChangePaste={setPasteText}
          onRunApi={generate}
          onLoadPrompt={loadPrompt}
          onParsePaste={parsePasted}
          onClose={() => setModalOpen(false)}
        />
      )}

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

/** Modal for picking the LLM processing mode (API vs 网页版). Matches
 *  the pattern used by 纯设定作品 / 参考作品特征提取 — a slider toggle
 *  at the top + the relevant action below. */
function ProcessingModeModal({
  mode, onChangeMode,
  generating, loadingPrompt, promptText, pasteText,
  onChangePaste, onRunApi, onLoadPrompt, onParsePaste, onClose,
}: {
  mode: "ai" | "manual";
  onChangeMode: (m: "ai" | "manual") => void;
  generating: boolean;
  loadingPrompt: boolean;
  promptText: string;
  pasteText: string;
  onChangePaste: (s: string) => void;
  onRunApi: () => void;
  onLoadPrompt: () => void;
  onParsePaste: () => void;
  onClose: () => void;
}) {
  return (
    <>
      <div className="side-panel-overlay" onClick={onClose} />
      <div style={{
        position: "fixed", inset: 0, zIndex: 100,
        display: "flex", alignItems: "center", justifyContent: "center",
        pointerEvents: "none",
      }}>
        <div className="card" style={{
          width: 720, maxHeight: "85vh", overflow: "auto",
          pointerEvents: "auto", boxShadow: "var(--shadow-lg)",
        }}>
          <div className="card-header">
            <h3>处理方式</h3>
            <button className="btn-icon" onClick={onClose}
                    style={{ width: 28, height: 28, fontSize: 18 }}>×</button>
          </div>
          <div className="card-body">
            {/* Mode toggle — same slider style used elsewhere */}
            <div style={{
              display: "inline-flex",
              background: "var(--bg-surface)",
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border)",
              padding: 3, marginBottom: 14,
            }}>
              {([["ai", "使用大模型 API"], ["manual", "使用大模型网页版"]] as const).map(([k, lbl]) => (
                <button key={k}
                        onClick={() => onChangeMode(k)}
                        style={{
                          padding: "6px 14px", fontSize: 12,
                          fontWeight: 600,
                          color: mode === k ? "white" : "var(--text-secondary)",
                          background: mode === k ? "var(--accent)" : "transparent",
                          border: "none",
                          borderRadius: 4,
                          cursor: "pointer",
                          transition: "all 0.15s",
                        }}>{lbl}</button>
              ))}
            </div>

            {mode === "ai" ? (
              <>
                <div className="text-xs" style={{
                  color: "var(--text-tertiary)", marginBottom: 12, lineHeight: 1.7,
                }}>
                  使用 UI 设置页面里配置好的模型 API，根据已选作品的提取数据生成草稿技能。
                </div>
                <button className="btn-primary"
                        onClick={onRunApi}
                        disabled={generating}>
                  {generating ? "AI 对比生成中…" : "调用 API 生成草稿"}
                </button>
              </>
            ) : (
              <>
                <div className="text-xs" style={{
                  color: "var(--text-tertiary)", marginBottom: 12, lineHeight: 1.7,
                }}>
                  生成 prompt 复制到大模型网页版（ChatGPT / Claude.ai 等），
                  把返回的 JSON 粘贴回下方输入框，系统自动解析为草稿技能。
                </div>
                <button className="btn" onClick={onLoadPrompt}
                        disabled={loadingPrompt}
                        style={{ marginBottom: 10 }}>
                  {loadingPrompt ? "生成中…" : "生成并复制 prompt"}
                </button>
                {promptText && (
                  <textarea className="input font-mono" rows={4} readOnly
                            value={promptText}
                            style={{
                              fontSize: 11, lineHeight: 1.5,
                              marginBottom: 10,
                              background: "var(--bg-app)",
                            }} />
                )}
                <label className="label" style={{
                  fontSize: 11, fontWeight: 600,
                  color: "var(--text-tertiary)",
                  textTransform: "uppercase", letterSpacing: 0.5,
                  marginBottom: 4,
                }}>粘贴网页 LLM 返回的 JSON</label>
                <textarea className="input font-mono" rows={6}
                          value={pasteText}
                          onChange={e => onChangePaste(e.target.value)}
                          placeholder='{"name": "...", "display_name": "...", "description": "...", "prompt_template": "...", "tags": [...]}'
                          style={{
                            fontSize: 11, lineHeight: 1.5,
                            marginBottom: 10,
                            background: "var(--bg-app)",
                          }} />
                <button className="btn-primary"
                        onClick={onParsePaste}
                        disabled={!pasteText.trim()}>
                  解析为草稿技能
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
