// Theme configuration for InkOctoBot
// CSS variables are defined in global.css and toggled via data-theme attribute.
// This module is kept for compatibility with imports.

export const THEME_COLORS = {
  accent: "#e05545",
  jade: "#34c77b",
  gold: "#e8b84a",
  indigo: "#6b8aff",
  purple: "#a78bfa",
} as const;

export type ThemeMode = "dark" | "light";

export const themeConfig = {
  defaultTheme: "dark" as ThemeMode,
  storageKey: "theme",
};

export default themeConfig;
