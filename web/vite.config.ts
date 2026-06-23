import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import os from "node:os";
import path from "node:path";

// https://vite.dev/config/
export default defineConfig({
  base: "./",
  // Dep-optimization cache MUST live outside the project tree: inside Dropbox it
  // races Vite's atomic rename (EBUSY/EPERM → white screen). Default to the OS temp
  // dir (outside Dropbox everywhere, regenerable, no hardcoded path); override with
  // OPENVOICE_VITE_CACHE if needed.
  cacheDir: process.env.OPENVOICE_VITE_CACHE || path.join(os.tmpdir(), "openvoice-vite-cache"),
  plugins: [react()],
  server: {
    host: true,          // bind all interfaces so other LAN devices can load it
    allowedHosts: true,  // internal/trusted LAN — accept any Host header (LAN hostname or IP)
    port: 5600,
    proxy: {
      // The UI talks to the FastAPI backend on :5601.
      "/api": "http://127.0.0.1:5601",
    },
  },
  build: {
    // Deployed boxes build into the repo's web/dist (served single-port by the
    // backend). On the dev box the repo is inside Dropbox, which races Vite's
    // atomic rename (EPERM) — set OPENVOICE_DIST to an external path when building
    // there. The backend reads the same OPENVOICE_DIST to locate the built UI.
    outDir: process.env.OPENVOICE_DIST || "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
