import { copyFile } from "node:fs/promises";
import { build } from "esbuild";

await build({
  entryPoints: {
    content: "src/content.jsx",
    settings: "src/settings.jsx",
    worker: "src/worker.js",
  },
  outdir: "dist",
  bundle: true,
  format: "iife",
  target: "chrome120",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' },
  loader: { ".css": "text", ".png": "dataurl" },
  minify: true,
  legalComments: "linked",
});

await Promise.all([
  copyFile("manifest.json", "dist/manifest.json"),
  copyFile("settings.html", "dist/settings.html"),
  copyFile("assets/seed-button.png", "dist/seed-button.png"),
]);
