import { defineConfig } from "vite";

// During dev, the Vite server (5173) proxies API + WebSocket to the FastAPI
// backend (8000) so the browser talks to a single origin.
// Production builds set base="/sdr/" so the app can sit behind an nginx
// location block at /sdr on a shared domain (e.g. cascade.engfors.net/sdr).
export default defineConfig({
  base: process.env.NODE_ENV === "production" ? "/sdr/" : "/",
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
      "/dab": "http://localhost:7979",
    },
  },
});
