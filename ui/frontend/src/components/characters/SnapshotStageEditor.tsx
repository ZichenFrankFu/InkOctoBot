import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";

// 转变档位编辑器 (spec 角色卡·机制2)：列出角色的关键帧快照（SQL
// character_snapshots，生成链路实际使用的 canonical 存储），对过渡窗口
// 内的每个推进章节设置 动摇 / 试探 / 倾向 三档位；未设置的章节按窗口
// 进度自动推导。

interface Snapshot {
  snapshot_id: string;
  snapshot_order: number;
  trigger_description: string;
  bound_chapters: number[];
  transition_complete_chapter: number | null;
  transition_stages: Record<string, string>;
}

const STAGES: [string, string, string][] = [
  ["wavering", "动摇", "对原状态产生怀疑，行为仍以旧状态为主"],
  ["probing", "试探", "开始尝试新状态的行为方式"],
  ["leaning", "倾向", "多数行为已偏向新状态，尚未完全切换"],
];

export default function SnapshotStageEditor({ characterId, characterName }: {
  characterId: string; characterName?: string;
}) {
  const { toast } = useToast();
  const [snaps, setSnaps] = useState<Snapshot[]>([]);
  const [loaded, setLoaded] = useState(false);

  const reload = useCallback(async () => {
    if (!characterId) return;
    try {
      const r = await apiGet<{ items: Snapshot[] }>(
        `/api/snapshots?character_id=${encodeURIComponent(characterId)}`);
      setSnaps(r.items || []);
    } catch { setSnaps([]); }
    finally { setLoaded(true); }
  }, [characterId]);

  useEffect(() => { reload(); }, [reload]);

  const setStage = async (snapshotId: string, chapter: number, stage: string | null) => {
    try {
      await apiPost(`/api/snapshots/${snapshotId}/set-stage`,
        { chapter_num: chapter, stage });
      await reload();
    } catch (e: any) { toast(e.message || "设置失败", "error"); }
  };

  if (!loaded) return null;
  if (snaps.length === 0) {
    return (
      <div style={{ fontSize: 11, color: "var(--text-tertiary)", padding: "6px 0" }}>
        {characterName || "该角色"}暂无关键帧快照（生成链路使用的 canonical 快照）。
        提交章节后由快照检测器自动建档，或通过 API 创建。
      </div>
    );
  }
  return (
    <div>
      {snaps.map(s => {
        const bound = (s.bound_chapters || []).slice().sort((a, b) => a - b);
        const comp = s.transition_complete_chapter;
        // 过渡窗口 (机制1: 关键帧之间都是过渡期): 首个推进章 → 完成章前一章
        const winStart = bound.length ? bound[0] : null;
        const winEnd = comp !== null ? comp - 1 : (bound.length ? bound[bound.length - 1] : null);
        const chapters: number[] = [];
        if (winStart !== null && winEnd !== null) {
          for (let c = winStart; c <= winEnd; c++) chapters.push(c);
        }
        return (
          <div key={s.snapshot_id} style={{
            border: "1px solid var(--border-subtle)", borderRadius: 6,
            padding: "8px 10px", marginBottom: 8, fontSize: 11,
          }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
              <span style={{ fontWeight: 600 }}>关键帧 {s.snapshot_order}</span>
              <span style={{ color: "var(--text-tertiary)", flex: 1 }}>{s.trigger_description || ""}</span>
              <span style={{ color: "var(--text-disabled)" }}>
                推进章 {bound.join("、") || "未绑定"}
                {comp !== null ? ` · 第${comp}章完成` : " · 未设完成章"}
              </span>
            </div>
            {chapters.length === 0 && (
              <div style={{ color: "var(--text-tertiary)" }}>
                绑定推进章节并设置完成章后，可对过渡期各章设置档位。
              </div>
            )}
            {chapters.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {chapters.map(ch => {
                  const explicit = (s.transition_stages || {})[String(ch)] || "";
                  return (
                    <div key={ch} style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <span style={{ width: 56, color: "var(--text-secondary)" }}>第{ch}章</span>
                      {STAGES.map(([key, label, hint]) => (
                        <button key={key} className="btn" title={hint} style={{
                          fontSize: 10, padding: "1px 10px",
                          color: explicit === key ? "var(--accent)" : "var(--text-tertiary)",
                          borderColor: explicit === key ? "var(--accent)" : "var(--border-subtle)",
                        }} onClick={() => setStage(s.snapshot_id, ch, explicit === key ? null : key)}>
                          {label}
                        </button>
                      ))}
                      <span style={{ fontSize: 9, color: "var(--text-disabled)" }}>
                        {explicit ? "用户设定" : "未设置（按进度自动推导）"}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
