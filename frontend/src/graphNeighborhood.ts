import type { Concept, Link } from "./model";

export function graphNeighborhood(concepts: Concept[], links: Link[], centerId: string) {
  const available = new Set(concepts.map((node) => node.id));
  const neighbors = new Set<string>([centerId]);
  const edges = links.filter(({ from, to }) => {
    if ((from !== centerId && to !== centerId) || !available.has(from) || !available.has(to)) return false;
    neighbors.add(from);
    neighbors.add(to);
    return true;
  });
  return { nodes: concepts.filter((node) => neighbors.has(node.id)), edges };
}

export function neighborhoodPositions(concepts: Concept[], centerId: string, anchor: { x: number; y: number }) {
  const positions = new Map([[centerId, anchor]]);
  const neighbors = concepts.filter((node) => node.id !== centerId);
  const radiusX = Math.max(244, neighbors.length * 45);
  const radiusY = Math.max(154, neighbors.length * 31);
  neighbors.forEach((node, index) => {
    const angle = (2 * Math.PI * index) / neighbors.length - (neighbors.length > 2 ? Math.PI / 2 : 0);
    positions.set(node.id, {
      x: anchor.x + Math.cos(angle) * radiusX,
      y: anchor.y + Math.sin(angle) * radiusY,
    });
  });
  return positions;
}
