import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ImageViewerProvider } from "./ImageViewer";
import "./styles.css";
import { applyTheme, loadTheme } from "./theme";

applyTheme(loadTheme()); // 先套上风格再渲染，避免闪一下

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ImageViewerProvider>
      <App />
    </ImageViewerProvider>
  </StrictMode>,
);
