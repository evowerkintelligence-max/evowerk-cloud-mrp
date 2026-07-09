import { defineConfig } from "vite";
import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";

// All API prefixes the SPA talks to, proxied to the backend in dev.
const apiPrefixes = [
  "/auth",
  "/orgs",
  "/items",
  "/boms",
  "/suppliers",
  "/purchase-orders",
  "/lots",
  "/inventory",
  "/locations",
  "/mrp",
  "/forecasts",
  "/datasets",
  "/analytics",
];

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
    proxy: Object.fromEntries(
      apiPrefixes.map((prefix) => [prefix, `http://localhost:${backendPort}`]),
    ),
  },
});
