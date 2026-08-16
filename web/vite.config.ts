import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // The API is served by `dramatis serve`; in development Vite proxies to it so the
    // client sees one origin and needs no CORS handling on either side.
    proxy: { "/api": "http://127.0.0.1:7373" },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
