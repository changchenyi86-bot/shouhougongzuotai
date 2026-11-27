// 界面演示入口：接口用内置的假数据应答，不连后台，只用来看界面效果。
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "../App";
import { ImageViewerProvider } from "../ImageViewer";
import "../styles.css";
import { applyTheme, loadTheme } from "../theme";
import { installMock } from "./mock";

installMock();
applyTheme(loadTheme());

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ImageViewerProvider>
      <App />
    </ImageViewerProvider>
  </StrictMode>,
);
