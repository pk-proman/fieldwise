// Copies the web app into ./www and rewrites the few absolute paths, so it runs from inside the native app.
import { cpSync, rmSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const src = join(root, "..", "frontend");
const out = join(root, "www");
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
for (const d of ["css", "js"]) cpSync(join(src, d), join(out, d), { recursive: true });
cpSync(join(src, "icon.svg"), join(out, "icon.svg"));
let html = readFileSync(join(src, "index.html"), "utf8")
  .replace('<link rel="manifest" href="/manifest.webmanifest">\n', "")
  .replaceAll("/static/", "")
  .replace('<meta name="theme-color" content="#0f766e">', '<meta name="theme-color" content="#0f766e"><meta name="color-scheme" content="light dark">');
writeFileSync(join(out, "index.html"), html);
console.log("www built:", out);
