// 界面演示入口：接口用内置的假数据应答，不连后台，只用来看界面效果。
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "../App";
import "../styles.css";
import { applyTheme, loadTheme } from "../theme";
import { installMock } from "./mock";

installMock();
applyTheme(loadTheme());

try {
  if (!localStorage.getItem("operator")) localStorage.setItem("operator", "李四");
} catch {
  /* 浏览器禁用存储时忽略 */
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
