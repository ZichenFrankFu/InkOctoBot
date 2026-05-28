/**
 * PromptInspector — show what's actually in the prompt (P1).
 *
 * Solves the recurring "did worldbook actually inject? did special_req
 * make it in?" debugging cycle. Calls quick-generate with
 * ``prompt_only=true`` to get the rendered prompt back without
 * burning an LLM call, then parses it into the labelled sections
 * the user can see at a glance.
 */
import React, { useCallback, useEffect, useState } from "react";
import { apiPost } from "../../api/client";
import { useToast } from "./Toast";


interface Props {
  projectId: string;
  chapterId: string;
  chapterNum: number;
  synopsis?: string;
  characters?: string[];
}


// Heuristic section parser: the single_agent template uses ``## title``
// headers + ``### subtitle``. Group by top-level ``##``.
function parseSections(prompt: string): { title: string; body: string }[] {
  if (!prompt) return [];
  const out: { title: string; body: string }[] = [];
  const lines = prompt.split("\n");
  let current: { title: string; body: string } | null = null;
  for (const line of lines) {
    const m = line.match(/^##\s+(.+?)\s*$/);
    if (m) {
      if (current) out.push(current);
      current = { title: m[1], body: "" };
    } else if (current) {
      current.body += line + "\n";
    }
  }
  if (current) out.push(current);
  return out;
}


// Expected loader sections (whether they should appear). Empty body
// means "absent" — explicitly flagged so user can see what's missing.
const EXPECTED_SECTIONS = [
  { title: "用户特别要求", source: "user_special_requirements" },
  { title: "本章大纲",     source: "chapter_outline" },
  { title: "时间与地点",   source: "time_location" },
  { title: "本章出场角色", source: "characters_block" },
  { title: "出场角色档案", source: "character_cards" },
  { title: "世界观设定",   source: "worldbook" },
  { title: "关联参考",     source: "reference" },
  { title: "用户写作偏好", source: "user_preferences" },
  { title: "未回收伏笔",   source: "foreshadowing" },
  { title: "故事线",       source: "subplots" },
  { title: "相关灵感",     source: "inspiration" },
  { title: "客观状态",     source: "storyland_state" },
  { title: "读者视角记忆", source: "reader_memory" },
  { title: "已有正文",     source: "existing_content / current_chapter_draft" },
  { title: "创作技能",     source: "skills" },
  { title: "平台风格",     source: "platform_directive" },
];


export default function PromptInspector({
  projectId, chapterId, chapterNum, synopsis = "", characters = [],
}: Props) {
  const { toast } = useToast();
  const [prompt, setPrompt] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [showFull, setShowFull] = useState(false);

  const fetch = useCallback(async () => {
    if (!projectId || !chapterId) return;
    setLoading(true);
    try {
      const res = await apiPost<{ status: string; prompt: string }>(
        "/api/generation/quick-generate",
        {
          project_id: projectId,
          chapter_id: chapterId,
          chapter_num: chapterNum,
          synopsis,
          characters,
          prompt_only: true,
        },
      );
      setPrompt(res.prompt || "");
    } catch (e: any) {
      toast(`获取 prompt 失败: ${e.message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [projectId, chapterId, chapterNum, synopsis, characters, toast]);

  useEffect(() => { fetch(); }, [fetch]);

  const sections = parseSections(prompt);
  const sectionsByTitle = new Map(sections.map(s => [s.title.trim(), s]));

  const copyAll = () => {
    navigator.clipboard.writeText(prompt).then(
      () => toast("已复制完整 prompt", "success"),
      () => toast("复制失败", "error"),
    );
  };

  return (
    <div style={{ padding: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 14 }}>📋 Prompt 透视</h3>
          <p style={{ fontSize: 11, color: "var(--text-tertiary)", margin: "2px 0 0" }}>
            14 个 loader 的实际注入状态 · {prompt.length} 字总长
          </p>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button className="btn-sm" onClick={fetch} disabled={loading}>
            {loading ? "..." : "刷新"}
          </button>
          <button className="btn-sm" onClick={copyAll} disabled={!prompt}>📋 复制</button>
          <button className="btn-sm" onClick={() => setShowFull(s => !s)}>
            {showFull ? "收起" : "看完整"}
          </button>
        </div>
      </div>

      {/* Section status grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 4, fontSize: 12 }}>
        {EXPECTED_SECTIONS.map(expected => {
          const s = sectionsByTitle.get(expected.title) ||
                    [...sectionsByTitle.values()].find(x => x.title.startsWith(expected.title));
          const present = !!s && s.body.trim().length > 0;
          const chars = s ? s.body.trim().length : 0;
          return (
            <details
              key={expected.title}
              style={{
                padding: "4px 8px",
                background: present ? "var(--bg-surface-2)" : "var(--bg-surface)",
                borderLeft: `3px solid ${present ? "var(--success)" : "var(--text-disabled)"}`,
                borderRadius: 4,
              }}
            >
              <summary style={{ cursor: present ? "pointer" : "default", color: present ? undefined : "var(--text-tertiary)" }}>
                {present ? "✅" : "⏸"} <strong>{expected.title}</strong>
                <span style={{ color: "var(--text-tertiary)", marginLeft: 8, fontSize: 11 }}>
                  ({expected.source}) {present ? `· ${chars} 字` : "· 未注入"}
                </span>
              </summary>
              {present && (
                <pre style={{
                  marginTop: 4, padding: 6, background: "var(--bg-surface)",
                  fontSize: 11, fontFamily: "var(--font-mono)",
                  maxHeight: 180, overflow: "auto", borderRadius: 3,
                  whiteSpace: "pre-wrap", wordBreak: "break-word",
                }}>{s!.body.trim()}</pre>
              )}
            </details>
          );
        })}

        {/* Surface unexpected sections (loaders we didn't pre-declare) */}
        {sections.filter(s => {
          const t = s.title.trim();
          return !EXPECTED_SECTIONS.some(e => t === e.title || t.startsWith(e.title));
        }).map(s => (
          <div key={s.title} style={{
            padding: "4px 8px", borderLeft: "3px solid var(--accent)",
            background: "var(--bg-surface-2)", fontSize: 11, color: "var(--text-tertiary)",
          }}>
            ?  额外段: <strong>{s.title}</strong>  ({s.body.trim().length} 字)
          </div>
        ))}
      </div>

      {showFull && (
        <details open style={{ marginTop: 12 }}>
          <summary style={{ cursor: "pointer", fontWeight: 600 }}>完整 prompt</summary>
          <pre style={{
            marginTop: 8, padding: 10, background: "var(--bg-surface-2)",
            fontSize: 11, fontFamily: "var(--font-mono)",
            maxHeight: 400, overflow: "auto", borderRadius: 4,
            whiteSpace: "pre-wrap",
          }}>{prompt}</pre>
        </details>
      )}
    </div>
  );
}
