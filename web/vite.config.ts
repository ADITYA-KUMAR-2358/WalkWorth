import { defineConfig } from "vite";

export default defineConfig({
  server: {
    // Fort packs live in ../fortpacks and are bundled into the app.
    fs: { allow: [".."] },
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/files": "http://127.0.0.1:8000",
      "/journal": "http://127.0.0.1:8000",
      "/journal-assets": "http://127.0.0.1:8000",
    },
  },
});
