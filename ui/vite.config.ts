import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Built to dashboard/static/, which FastAPI serves. No Node process at runtime:
// this is a local dashboard, so SSR and a second server would be cost without
// benefit. `npm run dev` proxies the API so the React app talks to the real
// Python backend rather than to fixtures.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../dashboard/static", emptyOutDir: true },
  server: {
    port: 5273,
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
