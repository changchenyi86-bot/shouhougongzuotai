// 界面风格：默认「简洁」，可切换到「渐变」，记在浏览器里
export type Theme = "clean" | "aurora";

export const THEMES: { key: Theme; label: string }[] = [
  { key: "clean", label: "简洁" },
  { key: "aurora", label: "渐变" },
];

const KEY = "theme";

export function loadTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === "aurora" ? "aurora" : "clean";
  } catch {
    return "clean";
  }
}

export function applyTheme(theme: Theme) {
  if (theme === "aurora") document.documentElement.dataset.theme = "aurora";
  else delete document.documentElement.dataset.theme;
}

export function saveTheme(theme: Theme) {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* 浏览器禁用存储时忽略 */
  }
}
