import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Relative base so the built folder works both locally and at
// https://oleggrigorev131313.github.io/put-k-sebe/peresmotr/
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: {
    outDir: "../peresmotr",
    emptyOutDir: true,
    sourcemap: false,
    assetsDir: "assets",
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
  },
});
