import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const apiTarget = loadEnv(mode, ".", "").API_PROXY_TARGET || "http://127.0.0.1:8000";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": apiTarget,
        "/media": apiTarget,
      },
    },
    build: { outDir: "dist", sourcemap: false },
  };
});
