import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 8086,
    proxy: {
      "/api": "http://localhost:8093",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
