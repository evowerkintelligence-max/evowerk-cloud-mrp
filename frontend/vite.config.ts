import { defineConfig } from "vite";
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
      //
      // Several of these prefixes are ALSO SPA routes (/rca-cases, /analytics,
      // /bi, /reports, /workflows, /automl, /alerts, /integrations, /realtime,
      // /simulation, /scenarios, /predictive - see App.tsx). Client-side nav
      // never hits this proxy, but a hard refresh or a direct URL open does,
      // and used to get proxied straight to the backend (a bare JSON 404)
      // instead of index.html. bypass() tells the difference: a page
      // navigation's Accept header leads with text/html; apiFetch's fetch()
      // calls never send that, so only real API calls get proxied.
      "^/(auth|orgs|items|boms|suppliers|purchase-orders|lots|locations|inventory|forecasts|mrp|datasets|analytics|ai|reports|bi|workflows|automl|kpis|kpi-dashboards|integrations|realtime|simulation|health|alerts|computer-vision|data-chat|downtime-tracking|predictive|rca-cases|scenarios|pace)(/|$|\\?)":
        {
          target: `http://localhost:${backendPort}`,
          bypass(req) {
            if (req.headers.accept?.includes("text/html")) return req.url;
          },
        },
    },
  },
});
