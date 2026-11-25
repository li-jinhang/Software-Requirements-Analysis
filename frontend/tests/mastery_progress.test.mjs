import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const { outputFiles } = await build({
  entryPoints: [fileURLToPath(new URL("../src/MasteryProgress.tsx", import.meta.url))],
  bundle: true, write: false, format: "esm", jsx: "automatic", loader: { ".css": "empty" },
  define: { "process.env.NODE_ENV": '"production"' },
});
const { masteryForScore, MasteryLegend, MasteryProgress } = await import(
  "data:text/javascript;base64," + Buffer.from(outputFiles[0].text).toString("base64")
);

test("mastery progress selects the same bands at every cutoff", () => {
  const scores = [0, 1, 39, 40, 79, 80, 99, 100];
  assert.deepEqual(scores.map((score) => masteryForScore(score).tone),
    ["empty", "low", "low", "mid", "mid", "high", "high", "full"]);
  assert.equal(masteryForScore(-1).value, 0);
  assert.equal(masteryForScore(101).value, 100);
  assert.equal(masteryForScore(NaN).value, 0);
});

test("legend and accessible meters preserve the exact score in each band", () => {
  const legend = renderToStaticMarkup(createElement(MasteryLegend));
  for (const [score, tone] of [[0, "empty"], [24, "low"], [58, "mid"], [91, "high"], [100, "full"]]) {
    const html = renderToStaticMarkup(createElement(MasteryProgress, { score, status: "待补漏" }));
    assert.ok(legend.includes(`data-mastery="${tone}"`));
    assert.match(html, new RegExp(`data-mastery="${tone}"`));
    assert.match(html, /role="progressbar"/);
    assert.ok(html.includes(`aria-valuenow="${score}"`));
    assert.ok(html.includes(`aria-valuetext="待补漏 · ${score}%"`));
    assert.ok(html.includes(`--mastery-value:${score}%`));
  }
});

test("mastery effects have a reduced-motion rule", async () => {
  const css = await readFile(new URL("../src/mastery-progress.css", import.meta.url), "utf8");
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*animation:\s*none/);
});
