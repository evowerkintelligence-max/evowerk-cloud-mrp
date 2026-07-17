import { defineConfig } from "vitest/config";
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";

// Overridable so the SPA can point at a backend on a non-default port (e.g. when
// something else already holds 8000 locally) without editing this file.
const backendPort = process.env.BACKEND_PORT || "8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      // One regex over every backend router prefix (see app/main.py). The old
      // entry-per-route allowlist silently dropped any router added later:
      // /forecasts, /mrp, /inventory, /locations and /ai were all missing, so
      // Planning, Inventory and Ask never reached the API in dev. Same lesson
      // hit again later - computer-vision/data-chat/predictive/rca-cases/
      // scenarios were added to main.py but never added here.
      "^/(auth|orgs|items|boms|suppliers|purchase-orders|lots|locations|inventory|forecasts|mrp|datasets|analytics|ai|reports|bi|workflows|automl|kpis|kpi-dashboards|integrations|realtime|simulation|health|alerts|computer-vision|data-chat|downtime-tracking|predictive|rca-cases|scenarios|pace)(/|$|\\?)":
        `http://localhost:${backendPort}`,
    },
  },
  test: {
    environment: "node",
  },
});
