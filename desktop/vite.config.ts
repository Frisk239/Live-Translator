import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

// 两个页面：控制面板 + 字幕窗，各自独立 WebView 窗口
export default defineConfig({
  clearScreen: false,
  server: { port: 5173, strictPort: true },
  build: {
    rollupOptions: {
      input: {
        panel: fileURLToPath(new URL("./panel.html", import.meta.url)),
        subtitle: fileURLToPath(new URL("./subtitle.html", import.meta.url)),
      },
    },
  },
});
