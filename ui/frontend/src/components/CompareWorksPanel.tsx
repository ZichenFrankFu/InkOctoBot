/**
 * Compare-Works → Draft Skill. Pick 2-8 reference works, contrast their
 * extracted features, and turn the insight into a saveable learned skill.
 *
 * Two LLM entry points (consistent with 市场特征提取 / 高级特征提取):
 *   - 使用大模型 API  → open UniversalLLMDialog with editable prompt + API run
 *   - 使用大模型网页版 → open the same dialog with a paste-back surface
 *
 * Both modes share one prompt (loaded once via /compare_works/prompt), let
 * the user edit it before running, and converge on /compare_works/parse to
 * turn the raw response into a Skill draft.
 */
import React, { useEffect, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import { useToast } from "./shared/Toast";
import UniversalLLMDialog from "./shared/UniversalLLMDialog";

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
  const [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState<CompareDraft | null>(null);
  const [sourceWorks, setSourceWorks] = useState<{ ref_id: string; title: string }[]>([]);
  // Universal LLM dialog state — one prompt, two modes (api / manual).
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogMode, setDialogMode] = useState<"api" | "manual">("api");
  const [dialogPrompt, setDialogPrompt] = useState("");
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

  /** Build (or rebuild) the compare prompt with the current ref_ids /
   *  focus / instruction, open the UniversalLLMDialog in the chosen
   *  mode with that prompt pre-loaded and editable. */
  const openDialog = async (mode: "api" | "manual") => {
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
      setDialogPrompt(r.prompt || "");
      setDialogMode(mode);
      setDialogOpen(true);
    } catch (e: any) {
      toast(`生成 prompt 失败: ${e?.message || e}`, "error");
    } finally { setLoadingPrompt(false); }
  };

  /** API mode invocation — runs the (possibly user-edited) prompt
   *  through the configured LLM and returns the raw text. */
  const invokeApi = async (signal: AbortSignal, livePrompt: string): Promise<string> => {
    const res = await fetch("/api/skills/compare_works/invoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: livePrompt }),
      signal,
    });
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try { const e = await res.json(); msg = e.detail || msg; } catch { /* keep */ }
      throw new Error(msg);
    }
    const j = await res.json();
    return j.raw || "";
  };

  /** Parse the (possibly user-edited) raw response into the Skill
   *  draft via the backend (single source of truth for JSON cleanup +
   *  tag defaulting). Stash the draft into the preview card and close
   *  the dialog. Called by UniversalLLMDialog.onCommit. */
  const onDialogCommit = async (payload: { text: string }) => {
    try {
      const r = await apiPost<CompareResponse>(
        "/api/skills/compare_works/parse",
        { raw: payload.text, ref_ids: Array.from(selected), focus },
      );
      setDraft(r.draft);
      setSourceWorks(
        r.source_works?.length
          ? r.source_works
          : selectedAsWorks.map(w => ({ ref_id: w.ref_id, title: w.title })),
      );
      setDialogOpen(false);
      toast("已生成草稿", "success");
    } catch (e: any) {
      toast(`解析失败: ${e?.message || e}`, "error");
      throw e;  // let the dialog stay open so the user can fix the JSON
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

          {/* Two direct buttons — API vs 网页版 — matching the
              advanced extraction page in 市场特征提取. */}
          <div className="flex" style={{ gap: 10, justifyContent: "flex-end" }}>
            <button
              className="btn"
              onClick={() => openDialog("manual")}
              disabled={selected.size < 2 || loadingPrompt}
              title={selected.size < 2 ? "请先选择至少 2 部作品" : ""}
            >
              {loadingPrompt && dialogMode === "manual" ? "生成 prompt 中…" : "使用大模型网页版"}
            </button>
            <button
              className="btn-primary"
              onClick={() => openDialog("api")}
              disabled={selected.size < 2 || loadingPrompt}
              title={selected.size < 2 ? "请先选择至少 2 部作品" : ""}
            >
              {loadingPrompt && dialogMode === "api" ? "生成 prompt 中…" : "使用大模型 API"}
            </button>
          </div>
        </div>
      </div>

      {/* Universal LLM dialog — left = editable prompt, right = API run
          (start / progress / result) or 网页版 paste box. */}
      <UniversalLLMDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title={`共通点学习（${selected.size} 部已选）`}
        description={dialogMode === "api"
          ? "确认提示词后用大模型 API 自动运行；右侧显示进度与原始回复。"
          : "复制提示词到大模型网页版运行后，把返回内容粘回右侧。"}
        prompt={dialogPrompt}
        editablePrompt
        invokeApi={invokeApi}
        onCommit={onDialogCommit}
        minChars={20}
        initialMode={dialogMode === "manual" ? "manual_only" : "api_only"}
      />

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

