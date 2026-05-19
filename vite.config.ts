import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri 2 dev server — must match tauri.conf.json build.devUrl port.
const host = process.env.TAURI_DEV_HOST;
const port = 1420;

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port,
    strictPort: true,
    host: host ?? false,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: process.env.TAURI_PLATFORM === "windows" ? "chrome105" : "safari13",
    minify: !process.env.TAURI_DEBUG ? "esbuild" : false,
    sourcemap: !!process.env.TAURI_DEBUG,
  },
});
