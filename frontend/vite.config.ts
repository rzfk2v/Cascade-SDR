import { defineConfig } from "vite";

// During dev, the Vite server (5173) proxies API + WebSocket to the FastAPI
// backend (8000) so the browser talks to a single origin.
// Production builds use a *relative* base, so one build works served directly
// by the backend (http://host:8000) AND behind any reverse-proxy subpath
// (e.g. an nginx location block at /sdr) — the runtime WS/API paths are
// derived from location.pathname (see ws.ts appBase()).
export default defineConfig({
  base: "./",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/dab": "http://localhost:7979",
    },
  },
});
