import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const { outputFiles } = await build({
  entryPoints: [fileURLToPath(new URL("../src/graphNeighborhood.ts", import.meta.url))],
  bundle: true, write: false, format: "esm",
});
const { graphNeighborhood, neighborhoodPositions } = await import(
  "data:text/javascript;base64," + Buffer.from(outputFiles[0].text).toString("base64")
);
const node = (id) => ({ id });
const edge = (from, to, label = "prerequisite") => ({ from, to, label });

test("node focus includes only valid incoming and outgoing connections", () => {
  const nodes = ["before", "center", "after", "two-hops", "isolated"].map(node);
  const incident = [edge("before", "center"), edge("center", "after"), edge("center", "after", "contrast")];
  const links = [...incident, edge("after", "two-hops"), edge("before", "after"), edge("center", "missing")];
  const result = graphNeighborhood(nodes, links, "center");
  assert.deepEqual(result.nodes.map(({ id }) => id), ["before", "center", "after"]);
  assert.deepEqual(result.edges, incident);
  assert.deepEqual(graphNeighborhood(nodes, links, "isolated"), { nodes: [node("isolated")], edges: [] });
});

test("focused layout keeps the anchor in place and separates its neighbors", () => {
  const anchor = { x: -260, y: 170 };
  for (const count of [0, 1, 2, 3, 4, 8, 16, 40]) {
    const nodes = [node("center"), ...Array.from({ length: count }, (_, index) => node(String(index)))];
    const positions = neighborhoodPositions(nodes, "center", anchor);
    assert.equal(positions.size, count + 1);
    assert.deepEqual(positions.get("center"), anchor);
    const points = [...positions.values()];
    for (let i = 0; i < points.length; i++) {
      for (const other of points.slice(i + 1)) {
        assert.ok(Math.abs(points[i].x - other.x) >= 164 || Math.abs(points[i].y - other.y) >= 105);
      }
    }
  }
});
