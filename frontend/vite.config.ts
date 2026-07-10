import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/auth": "http://localhost:8000",
      "/orgs": "http://localhost:8000",
      "/items": "http://localhost:8000",
      "/boms": "http://localhost:8000",
      "/suppliers": "http://localhost:8000",
      "/purchase-orders": "http://localhost:8000",
      "/lots": "http://localhost:8000",
      "/datasets": "http://localhost:8000",
    },
  },
});
