import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // One regex over every backend router prefix (see app/main.py). The old
      // entry-per-route allowlist silently dropped any router added later:
      // /forecasts, /mrp, /inventory, /locations and /ai were all missing, so
      // Planning, Inventory and Ask never reached the API in dev.
      "^/(auth|orgs|items|boms|suppliers|purchase-orders|lots|locations|inventory|forecasts|mrp|datasets|ai|reports|health)(/|$|\\?)":
        "http://localhost:8000",
    },
  },
});
