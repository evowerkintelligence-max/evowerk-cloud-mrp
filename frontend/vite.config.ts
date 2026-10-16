import { coverageConfigDefaults, defineConfig } from "vitest/config";
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
    // Docker Desktop's Windows bind mount doesn't propagate inotify events,
    // so chokidar never sees host-side edits without polling.
    watch: {
      usePolling: true,
    },
    proxy: {
      // One regex over every backend router prefix (see app/main.py). The old
      // entry-per-route allowlist silently dropped any router added later:
      // /forecasts, /mrp, /inventory, /locations and /ai were all missing, so
      // Planning, Inventory and Ask never reached the API in dev. Same lesson
      // hit again later - computer-vision/data-chat/predictive/rca-cases/
      // scenarios were added to main.py but never added here. And again:
      // machines/work-centers/routings/cbm were missing here AND from
      // nginx.conf.template - cbm backs the Predictive Maintenance
      // fleet/machine-detail views, so that page was silently broken.
      //
      // nginx.conf.template dropped its equivalent allowlist entirely (now
      // dispatches on try_files $uri, which checks for a real built static
      // file before falling back to the API - a check with no ambiguity in
      // a production image where every asset already exists on disk). This
      // dev proxy can't do the same: vite's proxy matcher has no "is this a
      // real file" primitive, so matching every path here would also swallow
      // vite's own module/asset requests (/@vite/client, /src/*.tsx,
      // /node_modules/.vite/*) before vite's own middleware ever saw them.
      // An explicit list is still the right call here - just remember to
      // update this one when a new backend router ships.
      //
      // Several of these prefixes are ALSO SPA routes (/rca-cases, /analytics,
      // /bi, /reports, /workflows, /automl, /alerts, /integrations, /realtime,
      // /simulation, /predictive - see App.tsx). Client-side nav
      // never hits this proxy, but a hard refresh or a direct URL open does,
      // and used to get proxied straight to the backend (a bare JSON 404)
      // instead of index.html. bypass() tells the difference: a page
      // navigation's Accept header leads with text/html; apiFetch's fetch()
      // calls never send that, so only real API calls get proxied.
      "^/(auth|orgs|items|boms|suppliers|purchase-orders|customers|sales-orders|lots|locations|inventory|forecasts|mrp|datasets|analytics|ai|reports|bi|workflows|automl|kpis|kpi-dashboards|integrations|sync-schedules|simulation|health|alerts|computer-vision|data-chat|downtime-tracking|predictive|rca-cases|pace|machines|work-centers|routings|cbm|admin|staff|planning|work-orders|operators|checkin|client-errors)(/|$|\\?)":
        {
          target: `http://localhost:${backendPort}`,
          bypass(req) {
            if (req.headers.accept?.includes("text/html")) return req.url;
          },
        },
    },
  },
  test: {
    environment: "node",
    // jsdom interaction tests type into forms; 5s (vitest's default) is too
    // tight on a loaded CI runner.
    testTimeout: 15_000,
    coverage: {
      // The shared test harness is test code, not app code.
      exclude: [...coverageConfigDefaults.exclude, "src/test/**"],
      // Floor a few points under current (98.8% lines, 91.6% branches, 93.9%
      // functions over all of src): pages are covered by jsdom interaction
      // tests (*.interaction.test.tsx). Ratchet up as coverage grows; CI fails
      // below.
      thresholds: { lines: 95, functions: 90, branches: 88 },
    },
  },
});
