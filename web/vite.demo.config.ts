import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 界面演示：假数据、不连后台。npm run demo 本地看；npm run build:demo 打包到 dist-demo/
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { host: true, port: 5180, open: "/demo.html" },
  build: { outDir: "dist-demo", rollupOptions: { input: "demo.html" } },
});
