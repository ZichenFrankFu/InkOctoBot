/**
 * Publishing-platform catalog + per-platform creative profiles.
 *
 * The platform a project targets (起点 / 番茄 / ...) is not just a label —
 * platforms reward different chapter lengths, pacing and opening strategies.
 * `PLATFORM_PROFILES` captures those norms; the generation backend reads the
 * mirrored `analysis/feature_extraction/platform_profiles.py` to steer the AI
 * accordingly.
 */

export interface PlatformProfile {
  id: string;
  label: string;
  aliases: string[];
  chapterWordTarget: [number, number];
  pacing: string;
  openingNote: string;
  generationHint: string;
}

export const PLATFORM_PROFILES: PlatformProfile[] = [
  {
    id: "qidian",
    label: "起点中文网",
    aliases: ["起点", "qidian", "起点中文"],
    chapterWordTarget: [2500, 4000],
    pacing: "中速",
    openingNote: "黄金三章内建立核心冲突即可，允许适度铺垫",
    generationHint:
      "起点读者接受较长章节与世界观铺陈，单章 2500-4000 字，节奏中速；重视设定深度与文笔。",
  },
  {
    id: "fanqie",
    label: "番茄小说",
    aliases: ["番茄", "fanqie", "tomato"],
    chapterWordTarget: [1500, 2500],
    pacing: "快",
    openingNote: "首章即抛出爽点或核心钩子，强开局",
    generationHint:
      "番茄读者偏好短章快节奏，单章 1500-2500 字；情节推进密集，爽点前置，少铺垫多冲突。",
  },
  {
    id: "other",
    label: "其他",
    aliases: ["other"],
    chapterWordTarget: [2000, 3000],
    pacing: "中速",
    openingNote: "",
    generationHint: "",
  },
];

/** Platform option list for <select> controls. */
export const PLATFORMS: string[] = PLATFORM_PROFILES.map((p) => p.label);

/** Fuzzy-resolve a stored (free-text) platform string to its profile. */
export function platformProfile(name: string | undefined | null): PlatformProfile {
  const fallback = PLATFORM_PROFILES[PLATFORM_PROFILES.length - 1];
  const norm = (name || "").trim().toLowerCase();
  if (!norm) return fallback;
  for (const p of PLATFORM_PROFILES) {
    if (p.label.toLowerCase() === norm || p.id === norm) return p;
    if (p.aliases.some((a) => a.toLowerCase() === norm)) return p;
  }
  for (const p of PLATFORM_PROFILES) {
    if (p.id === "other") continue;
    if (norm.includes(p.label.toLowerCase())) return p;
    if (p.aliases.some((a) => a && norm.includes(a.toLowerCase()))) return p;
  }
  return fallback;
}
