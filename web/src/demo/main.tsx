// 界面演示入口：假数据 + 风格切换条，用来挑选界面方案，不连后台。
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "../App";
import "../styles.css";
import "./demo.css";
import { installMock } from "./mock";

installMock();

const THEMES = [
  { key: "", name: "方案 A", desc: "简洁浅色" },
  { key: "dark", name: "方案 B", desc: "深色科技" },
  { key: "aurora", name: "方案 C", desc: "明亮渐变" },
];

function initialTheme(): string {
  const fromUrl = new URLSearchParams(location.search).get("theme");
  if (fromUrl !== null && THEMES.some((t) => t.key === fromUrl)) return fromUrl;
  try {
    return localStorage.getItem("demo-theme") ?? "";
  } catch {
    return "";
  }
}

function Demo() {
  const [theme, setTheme] = useState(initialTheme);

  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    try {
      localStorage.setItem("demo-theme", theme);
      if (!localStorage.getItem("operator")) localStorage.setItem("operator", "李四");
    } catch {
      /* 浏览器禁用存储时忽略 */
    }
  }, [theme]);

  return (
    <>
      <App />
      <div className="demo-switch" role="radiogroup" aria-label="界面方案">
        <span className="demo-hint">界面方案预览（演示数据）</span>
        {THEMES.map((t) => (
          <button key={t.key} role="radio" aria-checked={theme === t.key} className={theme === t.key ? "on" : ""} onClick={() => setTheme(t.key)}>
            <b>{t.name}</b>
            {t.desc}
          </button>
        ))}
      </div>
    </>
  );
}

try {
  if (!localStorage.getItem("operator")) localStorage.setItem("operator", "李四");
} catch {
  /* 浏览器禁用存储时忽略 */
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Demo />
  </StrictMode>,
);
