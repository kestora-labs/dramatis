/**
 * Render the manual to docs/manual.pdf.
 *
 * The diagrams live in diagrams.html so they can be edited without hunting through the
 * prose; they are inlined here into the placeholder comments in manual.html. Page size,
 * margins and the running footer are set here rather than in CSS because Chrome reserves
 * the footer space itself and ignores CSS margin boxes.
 *
 *   npm --prefix docs/manual install     (once — puppeteer-core only)
 *   node docs/manual/build.mjs
 */

import { readFile, writeFile, unlink } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import puppeteer from "puppeteer-core";

const here = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(here, "..", "manual.pdf");

/** Wherever Chrome is on this machine. */
const CANDIDATES = [
  process.env.CHROME_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].filter(Boolean);

async function chrome() {
  const { existsSync } = await import("node:fs");
  const found = CANDIDATES.find((candidate) => existsSync(candidate));
  if (!found) throw new Error("no Chrome found; set CHROME_PATH");
  return found;
}

/** Pull one <svg id="..."> … </svg> out of diagrams.html. */
function svg(source, id) {
  const start = source.indexOf(`<svg id="${id}"`);
  if (start === -1) throw new Error(`no diagram ${id}`);
  const end = source.indexOf("</svg>", start);
  return source.slice(start, end + "</svg>".length);
}

const diagrams = await readFile(path.join(here, "diagrams.html"), "utf8");
let html = await readFile(path.join(here, "manual.html"), "utf8");

for (const [marker, id] of [
  ["<!--PIPELINE-->", "fig-pipeline"],
  ["<!--AXES-->", "fig-axes"],
  ["<!--PROVENANCE-->", "fig-provenance"],
  ["<!--LIFECYCLE-->", "fig-lifecycle"],
]) {
  if (html.includes(marker)) html = html.replace(marker, svg(diagrams, id));
}

const built = path.join(here, ".built.html");
await writeFile(built, html, "utf8");

const browser = await puppeteer.launch({
  executablePath: await chrome(),
  headless: "new",
  args: ["--no-sandbox", "--font-render-hinting=none", "--force-color-profile=srgb"],
});
const page = await browser.newPage();
await page.goto("file:///" + built.replace(/\\/g, "/"), { waitUntil: "networkidle0" });
await new Promise((r) => setTimeout(r, 1500));

const footer = `
  <div style="width:100%;font-family:Helvetica,Arial,sans-serif;font-size:7pt;color:#a0aec0;
              padding:0 20mm;display:flex;justify-content:space-between;">
    <span>Dramatis — The Manual</span>
    <span class="pageNumber"></span>
  </div>`;

await page.pdf({
  path: OUT,
  format: "A4",
  printBackground: true,
  displayHeaderFooter: true,
  headerTemplate: "<span></span>",
  footerTemplate: footer,
  margin: { top: "18mm", bottom: "16mm", left: "20mm", right: "20mm" },
});

await browser.close();
await unlink(built);
console.log("wrote", OUT);
