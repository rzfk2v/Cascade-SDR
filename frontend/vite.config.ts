import { defineConfig } from "vite";

// During dev, the Vite server (5173) proxies API + WebSocket to the FastAPI
// backend (8000) so the browser talks to a single origin.
export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
