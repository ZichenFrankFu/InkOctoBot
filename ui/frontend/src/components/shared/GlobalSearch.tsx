import React, { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { apiGet } from "../../api/client";

interface GlobalSearchProps {
  open: boolean;
  onClose: () => void;
  onNavigate: (tab: string) => void;
  projects: { id: string; name: string; genre?: string }[];
  activeProject: string;
}

interface Character {
  id: string;
  name: string;
  role?: string;
}

interface WorldBookEntry {
  id: string;
  title: string;
  category?: string;
}

interface SearchItem {
  id: string;
  label: string;
  detail?: string;
  category: "nav" | "project" | "character" | "worldbook";
  action: () => void;
}

const NAV_ITEMS: { key: string; icon: string; label: string }[] = [
  { key: "dashboard", icon: "\u25A3", label: "首页" },
  { key: "rankings", icon: "\u2261", label: "市场数据库" },
  { key: "references", icon: "\u229E", label: "参考作品库" },
  { key: "analysis", icon: "\u2197", label: "分析面板" },
  { key: "projects", icon: "\u25A1", label: "开书" },
  { key: "characters", icon: "\u2662", label: "角色卡"},
  { key: "worldbook", icon: "\u2295", label: "世界书" },
  { key: "editor", icon: "\u270E", label: "编辑器" },
  { key: "storyline", icon: "\u2500", label: "故事线" },
  { key: "skills", icon: "\u2699", label: "智能体" },
  { key: "settings", icon: "\u2638", label: "设置" },
];

const CATEGORY_LABELS: Record<string, string> = {
  nav: "页面导航",
  project: "项目",
  character: "角色",
  worldbook: "世界书",
};

const CATEGORY_ICONS: Record<string, string> = {
  nav: "\u2192",
  project: "\u25A1",
  character: "\u2662",
  worldbook: "\u2295",
};

export default function GlobalSearch({
  open,
  onClose,
  onNavigate,
  projects,
  activeProject,
}: GlobalSearchProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [worldbookEntries, setWorldbookEntries] = useState<WorldBookEntry[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // Load character and world book data once when modal opens
  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActiveIndex(0);

    if (activeProject) {
      apiGet<{ items: Character[] }>(
        `/api/data/characters?project_id=${activeProject}`
      )
        .then((r) => setCharacters(r.items || []))
        .catch(() => setCharacters([]));

      apiGet<{ items: WorldBookEntry[] }>(
        `/api/data/worldbook?project_id=${activeProject}`
      )
        .then((r) => setWorldbookEntries(r.items || []))
        .catch(() => setWorldbookEntries([]));
    }
  }, [open, activeProject]);

  // Focus input when opened
  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Build all search items
  const allItems = useMemo<SearchItem[]>(() => {
    const items: SearchItem[] = [];

    // Nav items
    for (const nav of NAV_ITEMS) {
      items.push({
        id: `nav-${nav.key}`,
        label: nav.label,
        detail: nav.icon,
        category: "nav",
        action: () => {
          onNavigate(nav.key);
          onClose();
        },
      });
    }

    // Projects
    for (const p of projects) {
      items.push({
        id: `project-${p.id}`,
        label: p.name,
        detail: p.genre || "",
        category: "project",
        action: () => {
          onNavigate("projects");
          onClose();
        },
      });
    }

    // Characters
    for (const c of characters) {
      items.push({
        id: `char-${c.id}`,
        label: c.name,
        detail: c.role || "",
        category: "character",
        action: () => {
          onNavigate("characters");
          onClose();
        },
      });
    }

    // World book entries
    for (const w of worldbookEntries) {
      items.push({
        id: `wb-${w.id}`,
        label: w.title,
        detail: w.category || "",
        category: "worldbook",
        action: () => {
          onNavigate("worldbook");
          onClose();
        },
      });
    }

    return items;
  }, [projects, characters, worldbookEntries, onNavigate, onClose]);

  // Debounced filter
  const [debouncedQuery, setDebouncedQuery] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(query), 150);
    return () => clearTimeout(timer);
  }, [query]);

  const filtered = useMemo(() => {
    if (!debouncedQuery.trim()) return allItems;
    const q = debouncedQuery.toLowerCase();
    return allItems.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        (item.detail && item.detail.toLowerCase().includes(q))
    );
  }, [debouncedQuery, allItems]);

  // Group results by category
  const grouped = useMemo(() => {
    const groups: { category: string; items: SearchItem[] }[] = [];
    const catOrder: string[] = ["nav", "project", "character", "worldbook"];
    for (const cat of catOrder) {
      const items = filtered.filter((i) => i.category === cat);
      if (items.length > 0) groups.push({ category: cat, items });
    }
    return groups;
  }, [filtered]);

  // Flat list for keyboard navigation
  const flatList = useMemo(
    () => grouped.flatMap((g) => g.items),
    [grouped]
  );

  // Reset active index when results change
  useEffect(() => {
    setActiveIndex(0);
  }, [debouncedQuery]);

  // Scroll active item into view
  useEffect(() => {
    if (!listRef.current) return;
    const el = listRef.current.querySelector("[data-active='true']");
    if (el) el.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => Math.min(i + 1, flatList.length - 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (flatList[activeIndex]) flatList[activeIndex].action();
      } else if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    },
    [flatList, activeIndex, onClose]
  );

  if (!open) return null;

  let runningIndex = 0;

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "center",
        paddingTop: "15vh",
        background: "rgba(0, 0, 0, 0.6)",
        backdropFilter: "blur(6px)",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: 560,
          background: "var(--bg-surface)",
          border: "1px solid var(--border-hover)",
          borderRadius: "var(--radius-lg, 12px)",
          boxShadow: "0 24px 80px rgba(0, 0, 0, 0.6)",
          overflow: "hidden",
          display: "flex",
          flexDirection: "column",
          maxHeight: "60vh",
        }}
        onKeyDown={handleKeyDown}
      >
        {/* Search input */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "14px 18px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <span
            style={{
              color: "var(--text-tertiary)",
              fontSize: 16,
              flexShrink: 0,
            }}
          >
            /
          </span>
          <input
            ref={inputRef}
            type="text"
            placeholder="搜索页面、项目、角色、世界书..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{
              flex: 1,
              background: "transparent",
              border: "none",
              outline: "none",
              color: "var(--text-primary)",
              fontSize: 15,
              fontFamily: "var(--font-sans)",
            }}
          />
          <kbd
            style={{
              fontSize: 11,
              color: "var(--text-tertiary)",
              background: "var(--bg-surface-2)",
              border: "1px solid var(--border)",
              borderRadius: 4,
              padding: "2px 6px",
              fontFamily: "var(--font-mono)",
            }}
          >
            ESC
          </kbd>
        </div>

        {/* Results */}
        <div
          ref={listRef}
          style={{
            overflowY: "auto",
            padding: "6px 0",
          }}
        >
          {flatList.length === 0 && debouncedQuery.trim() !== "" && (
            <div
              style={{
                padding: "24px 18px",
                textAlign: "center",
                color: "var(--text-tertiary)",
                fontSize: 13,
              }}
            >
              没有找到匹配的结果
            </div>
          )}

          {grouped.map((group) => {
            const groupItems = group.items.map((item) => {
              const idx = runningIndex++;
              const isActive = idx === activeIndex;
              return (
                <div
                  key={item.id}
                  data-active={isActive}
                  onClick={() => item.action()}
                  onMouseEnter={() => setActiveIndex(idx)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    padding: "8px 18px",
                    cursor: "pointer",
                    background: isActive
                      ? "var(--bg-surface-hover)"
                      : "transparent",
                    transition: "background 0.1s",
                  }}
                >
                  <span
                    style={{
                      width: 22,
                      height: 22,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 13,
                      color: isActive
                        ? "var(--accent)"
                        : "var(--text-secondary)",
                      background: isActive
                        ? "var(--accent-subtle)"
                        : "var(--bg-surface-2)",
                      borderRadius: 4,
                      flexShrink: 0,
                    }}
                  >
                    {item.category === "nav"
                      ? item.detail
                      : CATEGORY_ICONS[item.category]}
                  </span>
                  <span
                    style={{
                      flex: 1,
                      fontSize: 13,
                      color: isActive
                        ? "var(--text-primary)"
                        : "var(--text-secondary)",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {item.label}
                  </span>
                  {item.category !== "nav" && item.detail && (
                    <span
                      style={{
                        fontSize: 11,
                        color: "var(--text-tertiary)",
                        flexShrink: 0,
                      }}
                    >
                      {item.detail}
                    </span>
                  )}
                </div>
              );
            });

            return (
              <div key={group.category}>
                <div
                  style={{
                    padding: "8px 18px 4px",
                    fontSize: 11,
                    fontWeight: 600,
                    color: "var(--text-tertiary)",
                    letterSpacing: "0.04em",
                    textTransform: "uppercase",
                  }}
                >
                  {CATEGORY_LABELS[group.category]}
                </div>
                {groupItems}
              </div>
            );
          })}
        </div>

        {/* Footer hint */}
        <div
          style={{
            padding: "8px 18px",
            borderTop: "1px solid var(--border)",
            display: "flex",
            gap: 16,
            fontSize: 11,
            color: "var(--text-tertiary)",
          }}
        >
          <span>
            <kbd
              style={{
                background: "var(--bg-surface-2)",
                border: "1px solid var(--border)",
                borderRadius: 3,
                padding: "1px 4px",
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                marginRight: 4,
              }}
            >
              ↑↓
            </kbd>
            导航
          </span>
          <span>
            <kbd
              style={{
                background: "var(--bg-surface-2)",
                border: "1px solid var(--border)",
                borderRadius: 3,
                padding: "1px 4px",
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                marginRight: 4,
              }}
            >
              ↵
            </kbd>
            选择
          </span>
          <span>
            <kbd
              style={{
                background: "var(--bg-surface-2)",
                border: "1px solid var(--border)",
                borderRadius: 3,
                padding: "1px 4px",
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                marginRight: 4,
              }}
            >
              esc
            </kbd>
            关闭
          </span>
        </div>
      </div>
    </div>
  );
}
