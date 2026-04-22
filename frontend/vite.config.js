import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const configFile = fileURLToPath(import.meta.url);
const configDir = dirname(configFile);
const rootDir = fs.realpathSync(configDir);

export default defineConfig({
  root: rootDir,
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true
      }
    }
  },
  resolve: {
    preserveSymlinks: false
  },
  build: {
    outDir: resolve(rootDir, "dist"),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        app: resolve(rootDir, "index.html")
      }
    }
  }
});
