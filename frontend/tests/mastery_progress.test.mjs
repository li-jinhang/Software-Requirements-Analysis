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
const { masteryForScore, MasteryMeter, MasteryProgress } = await import(
  "data:text/javascript;base64," + Buffer.from(outputFiles[0].text).toString("base64")
);

const stages = [
  [0, 0, "empty", "未激活"],
  [1, 20, "start", "初启"],
  [21, 40, "ice", "冰封"],
  [41, 60, "water", "流动"],
  [61, 75, "growth", "生长"],
  [76, 90, "charge", "冲刺"],
  [91, 100, "fire", "燃烧完成"],
];

test("mastery progress follows the seven bands at each boundary", () => {
  for (const [min, max, tone, name] of stages) {
    for (let score = min; score <= max; score++) {
      assert.equal(masteryForScore(score).tone, tone);
      assert.equal(masteryForScore(score).name, name);
    }
  }
  assert.equal(masteryForScore(-1).value, 0);
  assert.equal(masteryForScore(101).value, 100);
  assert.equal(masteryForScore(NaN).value, 0);
});

test("accessible meters preserve the exact score in each band", () => {
  for (const [score, , tone, name] of stages) {
    const html = renderToStaticMarkup(createElement(MasteryProgress, { score, status: "待补漏" }));
    assert.match(html, new RegExp(`data-mastery="${tone}"`));
    assert.match(html, /role="progressbar"/);
    assert.ok(html.includes(`aria-valuenow="${score}"`));
    assert.ok(html.includes(`aria-valuetext="待补漏 · ${score}%"`));
    assert.ok(!html.includes(name), `Unexpected phase label in progress bar: ${name}`);
    assert.ok(html.includes(`--mastery-value:${score}%`));
    assert.equal(html.includes('class="mastery-fill"'), score > 0);
  }
  const compact = renderToStaticMarkup(createElement(MasteryMeter, {
    score: 83, label: "课程掌握度", compact: true, motion: "focus",
  }));
  assert.ok(compact.includes('class="mastery-meter is-compact"'));
  assert.ok(compact.includes('data-mastery="charge" data-motion="focus"'));
  assert.ok(compact.includes('aria-label="课程掌握度"'));
});

test("mastery effects have a reduced-motion rule", async () => {
  const css = await readFile(new URL("../src/mastery-progress.css", import.meta.url), "utf8");
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*animation:\s*none/);
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]*transition:\s*none/);
  for (const animation of ["mist", "frost", "wave", "veins", "electric", "flare"]) {
    assert.ok(css.includes(`@keyframes mastery-${animation}`));
    assert.ok(css.includes(`--mastery-fill-animation: mastery-${animation} `));
  }
});

test("every active progress effect loops while its meter is animating", async () => {
  const css = await readFile(new URL("../src/mastery-progress.css", import.meta.url), "utf8");
  const effects = [...css.matchAll(/--mastery-(?:fill|tip|particle)-animation:\s*([^;]+);/g)];
  assert.ok(effects.length > 0);
  for (const [, effect] of effects) assert.match(effect, /\binfinite\b/, effect);
});
