import assert from "node:assert/strict";
import { test } from "node:test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
const { outputFiles } = await build({ entryPoints: [fileURLToPath(new URL("../src/slash.ts", import.meta.url))], bundle: true, write: false, format: "esm" });
const { slashQuery, filterSlash, parseCommand, commands } = await import("data:text/javascript;base64," + Buffer.from(outputFiles[0].text).toString("base64"));
test("slash menu opens only at input start and leaves normal text/escaped slashes alone", () => {
  assert.equal(slashQuery("/"), ""); assert.equal(slashQuery("/数学"), "数学");
  for (const text of ["讲一下 /role", "//literal", "https://example.com", "/lookup 学习率"]) assert.equal(slashQuery(text), null);
});
test("search supports Chinese, command aliases, descriptions and unavailable skills", () => {
  assert.equal(filterSlash(commands, "听众")[0].id, "role");
  assert.ok(filterSlash(commands, "/rev").some(c => c.id === "review"));
  const skill = { id: "x", name: "数学建模", command: "/math", type: "Skills", description: "求解", available: false };
  assert.equal(filterSlash([...commands, skill], "数学")[0].available, false);
  assert.deepEqual(filterSlash(commands, "definitely-unknown"), []);
});
test("command parameters preserve newlines and do not classify regular speech", () => {
  assert.deepEqual(parseCommand("/lookup 学习率\n为什么震荡"), { command: "lookup", argument: "学习率\n为什么震荡" });
  assert.deepEqual(parseCommand("/ROLE examiner"), { command: "role", argument: "examiner" });
  assert.equal(parseCommand("正常讲解"), null); assert.equal(parseCommand("//literal"), null);
});
