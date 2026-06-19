/**
 * i18n — minimal in-house language toggle (zh / en).
 *
 * The user wants a hard switch between Chinese and English UI text
 * across the entire app, with Chinese as the default. Proper nouns
 * (embedding, AI, API, etc.) stay as-is in both languages.
 *
 * Design choice — flat dictionary keyed by Chinese string:
 *  - In zh mode, ``t("章节")`` returns "章节".
 *  - In en mode, ``t("章节")`` looks up "章节" → "Chapter".
 *  - Missing en translations fall back to the zh string so partial
 *    coverage is harmless.
 *
 * This avoids the up-front cost of refactoring every JSX literal into
 * a key constant. Callers wrap their literal in ``t()``; the dict
 * grows organically. The toggle persists in localStorage.
 */
import { useSyncExternalStore } from "react";


type Lang = "zh" | "en";


const STORAGE_KEY = "inkoctobot_lang";


// ── store ──

let _lang: Lang = (() => {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    return v === "en" ? "en" : "zh";
  } catch { return "zh"; }
})();
const _listeners = new Set<() => void>();

function _subscribe(l: () => void) { _listeners.add(l); return () => _listeners.delete(l); }
function _snapshot(): Lang { return _lang; }


export function setLang(l: Lang): void {
  if (l !== _lang) {
    _lang = l;
    try { localStorage.setItem(STORAGE_KEY, l); } catch { /* ignore */ }
    document.documentElement.setAttribute("lang", l);
    _listeners.forEach(fn => fn());
  }
}


export function getLang(): Lang { return _lang; }


/** React hook — components subscribe to language changes. */
export function useLang(): Lang {
  return useSyncExternalStore(_subscribe, _snapshot, _snapshot);
}


// ── dictionary ──

// Chinese-first keyed dict. Missing English fallback returns the key.
const DICT: Record<string, string> = {
  // Generic UI verbs / nouns
  "刷新":         "Refresh",
  "保存":         "Save",
  "取消":         "Cancel",
  "确认":         "Confirm",
  "删除":         "Delete",
  "编辑":         "Edit",
  "提交":         "Submit",
  "关闭":         "Close",
  "加载中...":    "Loading...",
  "搜索":         "Search",
  "搜索中...":    "Searching...",
  "无":           "(none)",
  "暂无":         "(empty)",
  "全部":         "All",
  "项目":         "Project",
  "章节":         "Chapter",
  "卷":           "Volume",
  "作品":         "Work",

  // Nav sections
  "首页":         "Home",
  "市场数据库":   "Market DB",
  "市场总览":     "Market Overview",
  "市场作品搜索": "Search Market Works",
  "市场特征提取": "Market Feature Extraction",
  "参考数据库":   "Reference DB",
  "参考总览":     "Reference Overview",
  "数据库概览":   "DB Overview",
  "参考作品特征提取": "Reference Feature Extraction",
  "参考数据库工具":   "Reference DB Tools",
  "灵感数据库":   "Inspiration DB",
  "灵感总览":     "Inspiration Overview",
  "灵感搜索":     "Inspiration Search",
  "灵感库":       "Inspiration Library",
  "创作":         "Authoring",
  "开书":         "Projects",
  "角色卡":       "Characters",
  "世界书":       "Worldbook",
  "编辑器":       "Editor",
  "剧情线":       "Storyline",
  "Truth 审阅":   "Truth Review",
  "智能体与设置": "Agents & Settings",
  "智能体":       "Agents",
  "粘贴收件箱":   "Paste Inbox",
  "设置":         "Settings",
  "开发者控制台": "Developer Console",

  // SkillsPage tabs
  "智能体 & Skills": "Agents & Skills",
  "自学习成果":   "Learned Skills",
  "写作知识":     "Writing Knowledge",
  "写作偏好":     "Writing Preferences",
  "领域知识":     "Domain Knowledge",

  // MarketOverviewPage
  "榜单":         "Rankings",
  "分析面板":     "Analysis",
  "AI 总结开篇技巧（基于已爬取的开篇章节正文）":
    "AI summary of opening techniques (from crawled opening chapters)",
  "AI 总结开篇技巧": "AI Opening Summary",
  "生成 prompt 并打开对话框": "Build prompt & open dialog",
  "清空结果":     "Clear",

  // MarketFeatureExtractionPage
  "选平台 + 榜单 → API 启动 / 手动模式 → 看任务进度、代表作提取、平台 profile":
    "Pick platform + ranking → API or Manual → watch jobs / works / profile",
  "启动新任务":   "Launch a new job",
  "平台":         "Platform",
  "榜单 / 类别":  "Ranking / Category",
  "API 模式: 启动 LLM 提取": "API mode: launch LLM extraction",
  "手动模式: 复制 prompt 跑网页 LLM": "Manual mode: copy prompt to web LLM",
  "任务":         "Jobs",
  "平台 Profile": "Platform Profile",
  "代表作":       "Representative works",
  "预览":         "Preview",
  "状态":         "Status",
  "进度":         "Progress",
  "开始":         "Started",
  "结束":         "Finished",
  "综述":         "Summary",
  "风格基线":     "Style baseline",
  "特征设备":     "Signature devices",
  "节奏指南":     "Pacing guidance",
  "章节特征":     "Chapter features",
  "新词":         "Neologisms",
  "查看提取结果": "View extraction",
  "样本":         "samples",
  "置信度":       "confidence",

  // MarketSearchPage
  "市场作品搜索 page sub": "search-market-page-sub",
  "按标题 / 作者 / 简介关键词搜索市场数据库，选中后查看历史榜单 snapshot 与首章内容":
    "Search market DB by title / author / blurb; click a hit to see rank history + opening chapters",
  "输入标题 / 作者 / 关键词（如：诡秘 / 乌贼 / 修仙）":
    "Title / author / keyword (e.g. 诡秘 / Wuzei / xianxia)",
  "全部平台":     "All platforms",
  "起点":         "Qidian",
  "番茄":         "Fanqie",
  "纵横":         "Zongheng",
  "搜索结果":     "Results",
  "输入关键词后回车，或选择平台筛选后再点搜索":
    "Type a keyword + Enter, or pick a platform and click Search",
  "万字":         "K chars",

  // Reference overview
  "参考作品库":   "Reference Library",

  // InspirationOverview / Library
  "灵感库 (Library)": "Inspiration Library",
  "跨项目共享的灵感片段库。生成时按相关性自动召回。":
    "Cross-project inspiration snippets, retrieved automatically by relevance during generation.",
  "灵感总数":     "Total inspirations",
  "已生成 embedding": "With embedding",
  "已被章节使用过":   "Used by chapters",
  "分类数":       "Categories",
  "按类别分布":   "By category",
  "灵感在参考作品中被使用过": "Inspirations used in reference works",
  "在参考作品中找相似片段":  "Find similar fragments in reference works",

  // Settings
  "语言":         "Language",
  "中文":         "中文",
  "English":      "English",
  "中文/英文":    "Chinese / English",

  // EditorPage / paste inbox
  "Manual Paste 收件箱":  "Manual Paste Inbox",
  "已复制到剪贴板":       "Copied to clipboard",
  "复制失败，请手动选中": "Copy failed — select manually",
  "提交中...":    "Submitting...",
  "保存中...":    "Saving...",
};


/** Translate a Chinese key; falls back to the key itself when missing. */
export function t(zh: string): string {
  if (_lang === "zh") return zh;
  return DICT[zh] ?? zh;
}


/** Domain-specific lookup for inspiration categories. Values are stored
 *  in the DB as the raw English key ("scene", "plot_device", etc.) but
 *  the UI should always show the localised label. */
const INSPIRATION_CATEGORY: Record<string, { zh: string; en: string }> = {
  scene:         { zh: "场景",     en: "Scene" },
  plot_device:   { zh: "桥段",     en: "Plot device" },
  character:     { zh: "人物设计", en: "Character design" },
  worldbuilding: { zh: "设定",     en: "Worldbuilding" },
  other:         { zh: "其他",     en: "Other" },
};


/** Resolve an inspiration category key into the active language. Falls
 *  back to the raw key if it isn't a recognised inspiration category
 *  (e.g. legacy free-text values). */
export function tInspirationCategory(key: string): string {
  const meta = INSPIRATION_CATEGORY[key];
  if (!meta) return key;
  return _lang === "en" ? meta.en : meta.zh;
}


/** Platform keys are stored in the DB as the crawler's English slug
 *  (qidian / fanqie / zongheng / ciweimao / 17k). The UI should display
 *  the localised Chinese name in zh mode. */
const PLATFORM_NAMES: Record<string, { zh: string; en: string }> = {
  qidian:   { zh: "起点",   en: "Qidian" },
  fanqie:   { zh: "番茄",   en: "Fanqie" },
  zongheng: { zh: "纵横",   en: "Zongheng" },
  ciweimao: { zh: "刺猬猫", en: "Ciweimao" },
  "17k":    { zh: "17K",    en: "17K" },
};

export function tPlatform(key: string): string {
  const meta = PLATFORM_NAMES[(key || "").toLowerCase()];
  if (!meta) return key;
  return _lang === "en" ? meta.en : meta.zh;
}


// Initialize <html lang="..."> on first import.
try {
  document.documentElement.setAttribute("lang", _lang);
} catch { /* ignore during SSR */ }
