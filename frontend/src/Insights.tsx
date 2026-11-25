import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BaseEdge,
  Background,
  Controls,
  EdgeLabelRenderer,
  MarkerType,
  Position,
  ReactFlow,
  getBezierPath,
  useInternalNode,
  useNodesInitialized,
  useNodesState,
  useReactFlow,
  useStore,
  type Edge,
  type EdgeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import * as echarts from "echarts/core";
import { RadarChart } from "echarts/charts";
import { RadarComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { Course, Concept, Link, Report, Space, download, mean, status } from "./model";
import { dimensions } from "./model";
import { relationLabel, WorkspaceToolbar, type ColorTheme } from "./ui";
import { CourseText } from "./LearningMessage";
import "./insights.css";
import { ArrowRight, GraduationCap, LayoutGrid, RotateCcw, Search, Trash2 } from "lucide-react";
import { MasteryLegend, MasteryMeter, MasteryProgress, masteryForScore } from "./MasteryProgress";
echarts.use([RadarChart, RadarComponent, CanvasRenderer]);
const graphFit = { padding: 0.18, minZoom: 0.025, maxZoom: 1.2 };
const rowSpacing = 168;
const colSpacing = 264;

function nodeBox(node: ReturnType<typeof useInternalNode>) {
  const w = node?.measured?.width ?? 164;
  const h = node?.measured?.height ?? 78;
  const x = node?.internals.positionAbsolute.x ?? 0;
  const y = node?.internals.positionAbsolute.y ?? 0;
  return { x, y, w, h, cx: x + w / 2, cy: y + h / 2 };
}

function edgeAttachment(
  from: ReturnType<typeof nodeBox>,
  to: ReturnType<typeof nodeBox>,
): { x: number; y: number; position: Position } {
  const dx = to.cx - from.cx;
  const dy = to.cy - from.cy;
  if (!dx && !dy) return { x: from.cx, y: from.cy + from.h / 2, position: Position.Bottom };
  const hw = from.w / 2 + 2;
  const hh = from.h / 2 + 2;
  const rx = Math.abs(dx) / hw;
  const ry = Math.abs(dy) / hh;
  const scale = 1 / Math.max(rx, ry);
  const position = rx >= ry
    ? (dx >= 0 ? Position.Right : Position.Left)
    : (dy >= 0 ? Position.Bottom : Position.Top);
  return { x: from.cx + dx * scale, y: from.cy + dy * scale, position };
}

type FloatingEdgeData = {
  relation: string;
  highlighted: boolean;
  dimmed: boolean;
  bypass?: number;
};

function FloatingEdge({ id, source, target, markerEnd, label, data }: EdgeProps<Edge<FloatingEdgeData>>) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  if (!sourceNode || !targetNode) return null;

  const sBox = nodeBox(sourceNode);
  const tBox = nodeBox(targetNode);
  const bypass = data?.bypass ?? 0;

  let edgePath = "";
  let labelX = 0;
  let labelY = 0;
  if (bypass !== 0) {
    const goRight = bypass > 0;
    const sx = goRight ? sBox.x + sBox.w + 2 : sBox.x - 2;
    const tx = goRight ? tBox.x + tBox.w + 2 : tBox.x - 2;
    const sy = sBox.cy;
    const ty = tBox.cy;
    const arcX = (goRight ? Math.max(sBox.x + sBox.w, tBox.x + tBox.w) : Math.min(sBox.x, tBox.x)) + bypass * 92;
    const arcY = (sy + ty) / 2;
    edgePath = `M ${sx} ${sy} Q ${arcX} ${arcY} ${tx} ${ty}`;
    labelX = 0.25 * sx + 0.5 * arcX + 0.25 * tx;
    labelY = 0.25 * sy + 0.5 * arcY + 0.25 * ty;
  } else {
    const sAnchor = edgeAttachment(sBox, tBox);
    const tAnchor = edgeAttachment(tBox, sBox);
    [edgePath, labelX, labelY] = getBezierPath({
      sourceX: sAnchor.x,
      sourceY: sAnchor.y,
      sourcePosition: sAnchor.position,
      targetX: tAnchor.x,
      targetY: tAnchor.y,
      targetPosition: tAnchor.position,
      curvature: 0.24,
    });
  }

  const relation = data?.relation || "";
  const highlighted = Boolean(data?.highlighted);
  const dimmed = Boolean(data?.dimmed);
  const dashArray = relation === "applies_to" || relation === "contains"
    ? "4 3"
    : relation === "contrast"
      ? "2 3"
      : undefined;

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke: highlighted ? "var(--accent)" : "var(--edge)",
          strokeWidth: highlighted ? 1.9 : 1.25,
          strokeDasharray: dashArray,
          opacity: highlighted ? 1 : dimmed ? 0.16 : 0.62,
          transition: "stroke 150ms ease, stroke-width 150ms ease, opacity 150ms ease",
        }}
      />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="graph-edge-label react-flow__edge-text"
            data-active={highlighted ? "true" : "false"}
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const edgeTypes = { floating: FloatingEdge };

// Rank the visible graph topologically and sort each layer by parent barycenter to minimize crossings.
function graphPositions(concepts: Concept[], links: Link[], groupByModule: boolean) {
  const positions = new Map<string, { x: number; y: number }>();
  if (groupByModule) {
    const containment = links.filter(link => link.label === "contains");
    const modules = [...new Set(containment.map(link => link.from))];
    modules.forEach((id, column) => {
      const x = (column - (modules.length - 1) / 2) * colSpacing;
      positions.set(id, { x, y: 0 });
      containment.filter(link => link.from === id).forEach((link, row) => {
        positions.set(link.to, { x, y: (row + 1) * rowSpacing });
      });
    });
    if (concepts.every(concept => positions.has(concept.id))) return positions;
    positions.clear();
  }
  const remaining = new Set(concepts.map(n => n.id));
  let row = 0;
  while (remaining.size) {
    const layer = concepts.filter(n => remaining.has(n.id) &&
      !links.some(l => l.to === n.id && remaining.has(l.from)));
    if (!layer.length) layer.push(concepts.find(n => remaining.has(n.id))!);
    layer.sort((a, b) => {
      const parentAvg = (id: string) => {
        const xs = links
          .filter(l => l.to === id && positions.has(l.from))
          .map(l => positions.get(l.from)!.x);
        return xs.length ? xs.reduce((sum, x) => sum + x, 0) / xs.length : Number.POSITIVE_INFINITY;
      };
      const pa = parentAvg(a.id);
      const pb = parentAvg(b.id);
      if (pa !== pb) return pa - pb;
      return b.importance - a.importance;
    });
    layer.forEach((n, i) => {
      const rowSize = Math.min(4, layer.length - Math.floor(i / 4) * 4);
      positions.set(n.id, { x: (i % 4 - (rowSize - 1) / 2) * colSpacing, y: (row + Math.floor(i / 4)) * rowSpacing });
      remaining.delete(n.id);
    });
    row += Math.ceil(layer.length / 4);
  }
  return positions;
}

function FitLoadedGraph({ layout }: { layout: string }) {
  const ready = useNodesInitialized();
  const { fitView } = useReactFlow();
  const width = useStore((state) => state.width);
  const height = useStore((state) => state.height);
  useEffect(() => {
    if (!ready || !width || !height) return;
    const reduced = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    let inner = 0;
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => void fitView({ ...graphFit, duration: reduced ? 0 : 200 }));
    });
    return () => {
      cancelAnimationFrame(outer);
      cancelAnimationFrame(inner);
    };
  }, [ready, layout, fitView, width, height]);
  return null;
}

export function Graph({
  theme,
  space,
  c,
  setCourse,
  start,
  guide,
  starting,
}: {
  theme: ColorTheme;
  space: Space;
  c: Course;
  setCourse: (id: string) => void;
  start: (c: Course, n: Concept) => void;
  guide: (c: Course, n?: Concept) => void;
  starting: boolean;
}) {
  const [query, setQuery] = useState(""),
    [includeNeighbors, setIncludeNeighbors] = useState(true),
    [selected, setSelected] = useState(c.nodes[0]?.id),
    [lockedNode, setLockedNode] = useState<string | null>(null),
    [hoveredNode, setHoveredNode] = useState<string | null>(null),
    [hoveredEdge, setHoveredEdge] = useState<string | null>(null),
    [layoutTick, setLayoutTick] = useState(0);

  const trimmedQuery = query.trim().toLowerCase();
  const isSearchActive = trimmedQuery.length > 0;

  const { matches, matchedIds, neighborIds } = useMemo(() => {
    if (!trimmedQuery) {
      return {
        matches: [...c.nodes].sort((a, b) => b.importance - a.importance).slice(0, 40),
        matchedIds: new Set<string>(),
        neighborIds: new Set<string>(),
      };
    }
    const direct = c.nodes.filter((n) => n.name.toLowerCase().includes(trimmedQuery));
    const mIds = new Set(direct.map((n) => n.id));
    const nIds = new Set<string>();
    if (includeNeighbors && direct.length) {
      for (const l of c.links) {
        if (mIds.has(l.from) && !mIds.has(l.to)) nIds.add(l.to);
        if (mIds.has(l.to) && !mIds.has(l.from)) nIds.add(l.from);
      }
    }
    const combined = c.nodes
      .filter((n) => mIds.has(n.id) || nIds.has(n.id))
      .sort((a, b) => {
        const aDirect = mIds.has(a.id) ? 1 : 0;
        const bDirect = mIds.has(b.id) ? 1 : 0;
        if (aDirect !== bDirect) return bDirect - aDirect;
        return b.importance - a.importance;
      })
      .slice(0, 40);
    return { matches: combined, matchedIds: mIds, neighborIds: nIds };
  }, [c.nodes, c.links, trimmedQuery, includeNeighbors]);

  const visibleEdges = useMemo(
    () =>
      c.links.filter(
        (l) => matches.some((n) => n.id === l.from) && matches.some((n) => n.id === l.to),
      ),
    [c.links, matches],
  );

  const activeFocusId = hoveredNode ?? lockedNode;
  const { highlightedEdges, connectedNodes, hasFocus } = useMemo(() => {
    const eSet = new Set<string>();
    const nSet = new Set<string>();
    if (activeFocusId) {
      nSet.add(activeFocusId);
      for (const l of visibleEdges) {
        if (l.from === activeFocusId || l.to === activeFocusId) {
          eSet.add(JSON.stringify([l.from, l.to, l.label]));
          nSet.add(l.from);
          nSet.add(l.to);
        }
      }
      return { highlightedEdges: eSet, connectedNodes: nSet, hasFocus: true };
    }
    if (hoveredEdge) {
      eSet.add(hoveredEdge);
      for (const l of visibleEdges) {
        if (JSON.stringify([l.from, l.to, l.label]) === hoveredEdge) {
          nSet.add(l.from);
          nSet.add(l.to);
        }
      }
      return { highlightedEdges: eSet, connectedNodes: nSet, hasFocus: true };
    }
    return { highlightedEdges: eSet, connectedNodes: nSet, hasFocus: false };
  }, [activeFocusId, hoveredEdge, visibleEdges]);

  const initialNodes = useMemo(() => {
    const positions = graphPositions(matches, c.links, !!c.isLibrary && !isSearchActive);
    return matches.map(n => {
      const label = status(n, space.logs);
      const isNeighbor = isSearchActive && neighborIds.has(n.id);
      return {
        id: n.id,
        position: positions.get(n.id)!,
        ariaLabel: n.name + (isNeighbor ? " · 关联节点" : "") + " · " + label + " " + n.score + "%",
        className: "node mastery-node mastery-" + masteryForScore(n.score).tone,
        data: { label: <div className="node-content">
          <div className="node-heading">
            <i aria-hidden="true" />
            <strong>{n.name}</strong>
            {isNeighbor && <span className="node-neighbor-tag">关联</span>}
          </div>
          <div className="node-mastery"><span>{label}</span><b>{n.score}%</b></div>
          <MasteryMeter score={n.score} label={n.name + "掌握度"} status={label} compact motion="focus" />
        </div> },
      };
    });
  }, [matches, c.links, c.isLibrary, space.logs, isSearchActive, neighborIds]);

  const [nodes, setNodes, change] = useNodesState(initialNodes);
  useEffect(() => {
    setNodes(initialNodes);
  }, [initialNodes, setNodes]);

  const styledNodes = useMemo(
    () =>
      nodes.map((n) => {
        const isDirect = isSearchActive && matchedIds.has(n.id);
        const isNeighbor = isSearchActive && neighborIds.has(n.id);
        return {
          ...n,
          className:
            n.className +
            (isDirect ? " is-search-match" : "") +
            (isNeighbor ? " is-search-neighbor" : "") +
            (hasFocus && !connectedNodes.has(n.id) ? " is-dimmed" : "") +
            (activeFocusId === n.id ? " is-focused" : ""),
        };
      }),
    [nodes, isSearchActive, matchedIds, neighborIds, hasFocus, connectedNodes, activeFocusId],
  );

  const flowEdges = useMemo(
    () => {
      const posMap = new Map(nodes.map((n) => [n.id, n.position]));
      let bypassDir = 1;
      return visibleEdges.map((l) => {
        const edgeId = JSON.stringify([l.from, l.to, l.label]);
        const searchHighlighted = isSearchActive && !hasFocus && (matchedIds.has(l.from) || matchedIds.has(l.to));
        const highlighted = (hasFocus && highlightedEdges.has(edgeId)) || searchHighlighted;
        const dimmed = hasFocus && !highlighted;
        const sp = posMap.get(l.from);
        const tp = posMap.get(l.to);
        let bypass = 0;
        if (sp && tp) {
          const minY = Math.min(sp.y, tp.y);
          const maxY = Math.max(sp.y, tp.y);
          const avgX = (sp.x + tp.x) / 2;
          const blocked = nodes.some(
            (other) =>
              other.id !== l.from &&
              other.id !== l.to &&
              other.position.y > minY + 20 &&
              other.position.y < maxY - 20 &&
              Math.abs(other.position.x - avgX) < 110,
          );
          if (blocked) {
            bypass = bypassDir;
            bypassDir = -bypassDir;
          }
        }
        return {
          id: edgeId,
          source: l.from,
          target: l.to,
          type: "floating",
          label: relationLabel(l.label),
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 13,
            height: 13,
            color: highlighted ? "var(--accent)" : "var(--edge)",
          },
          data: {
            relation: l.label,
            highlighted,
            dimmed,
            bypass,
          },
          zIndex: highlighted ? 10 : 0,
        };
      });
    },
    [visibleEdges, nodes, isSearchActive, hasFocus, highlightedEdges, matchedIds],
  );

  const resetLayout = useCallback(() => {
    setNodes(initialNodes);
    setLockedNode(null);
    setLayoutTick((t) => t + 1);
  }, [initialNodes, setNodes]);

  const current = matches.find((n) => n.id === selected) || matches.find((n) => matchedIds.has(n.id)) || matches[0];
  return (
    <>
      <WorkspaceToolbar>
        <select className="graph-course" aria-label="图谱资料" value={c.id} onChange={(e) => { setLockedNode(null); setCourse(e.target.value); }}>
          {space.courses.map((course) => <option key={course.id} value={course.id}>
            {course.libraryId
              ? `${space.courses.find(library => library.id === course.libraryId)?.title || "补充资料"} / ${course.title}`
              : course.title}
          </option>)}
        </select>
        <label className="search graph-search"><Search aria-hidden="true" />
          <input aria-label="搜索知识点" placeholder="搜索知识点…" value={query} onChange={(e) => { setLockedNode(null); setQuery(e.target.value); }} />
        </label>
        {isSearchActive && (
          <button
            type="button"
            className="graph-neighbor-toggle"
            aria-pressed={includeNeighbors}
            title={includeNeighbors ? "已包含 1 跳关联知识点，点击仅显示直接命中节点" : "仅显示直接命中节点，点击包含 1 跳关联"}
            onClick={() => setIncludeNeighbors((v) => !v)}
          >
            含关联
          </button>
        )}
        <div className="toolbar-actions">
          <button type="button" className="compact-command" onClick={resetLayout} title="整理布局">
            <LayoutGrid aria-hidden="true" /><span>整理布局</span>
          </button>
          <div className="graph-mastery-key">
            <MasteryLegend />
            <details>
              <summary aria-label="掌握进度分档图例">图例</summary>
              <MasteryLegend detailed />
            </details>
          </div>
          <button className="primary compact-command" disabled={starting || !c.nodes.some((n) => n.score < 80)}
            onClick={() => guide(c)} title="按图谱顺序学习">
            <GraduationCap aria-hidden="true" /><span>按图谱顺序学习</span>
          </button>
        </div>
      </WorkspaceToolbar>
      <div className="graphlayout">
        <div className="graph">
          {matches.length === 0 && (
            <div className="graph-empty">
              <p role="status">
                {query.trim() ? "没有匹配的知识点。" : "该资料暂无知识点。"}
              </p>
              {query.trim() && (
                <button onClick={() => setQuery("")}>清除搜索</button>
              )}
            </div>
          )}
          <ReactFlow
            colorMode={theme}
            nodes={styledNodes}
            edges={flowEdges}
            edgeTypes={edgeTypes}
            onNodesChange={change}
            onNodeClick={(_, node) => {
              setSelected(node.id);
              setLockedNode((prev) => (prev === node.id ? null : node.id));
            }}
            onNodeMouseEnter={(_, node) => setHoveredNode(node.id)}
            onNodeMouseLeave={() => setHoveredNode(null)}
            onEdgeMouseEnter={(_, edge) => setHoveredEdge(edge.id)}
            onEdgeMouseLeave={() => setHoveredEdge(null)}
            onPaneClick={() => setLockedNode(null)}
            fitView
            fitViewOptions={graphFit}
            minZoom={graphFit.minZoom}
          >
            <FitLoadedGraph layout={JSON.stringify(initialNodes.map((n) => [n.id, n.position.x, n.position.y])) + ":" + layoutTick} />
            <Background color="var(--dot-grid)" gap={18} size={1} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <aside className="node-detail">
          {current ? (
            <>
              <small>节点详情</small>
              <h2>{current.name}</h2>
              <MasteryProgress key={current.id + ":" + current.score} score={current.score}
                status={status(current, space.logs)} />
              <div className="node-summary"><CourseText text={current.summary} /></div>
              <h3>薄弱记录</h3>
              {space.logs
                .filter((l) => l.node === current.id)
                .map((w) => (
                  <p key={w.id}>
                    {w.detail} · {w.resolved ? "已解决" : "待复讲"}
                  </p>
                ))}
              {!space.logs.some((l) => l.node === current.id) && (
                <p>暂无薄弱记录。</p>
              )}
              <div className="tools graph-actions">
                {current.score < 80 && (
                  <button className="primary" disabled={starting} onClick={() => guide(c, current)}>
                    <GraduationCap /> 听讲解
                  </button>
                )}
                <button disabled={starting} onClick={() => start(c, current)}>专项费曼考核</button>
              </div>
            </>
          ) : (
            <p>选择图谱中的知识点查看详情。</p>
          )}
        </aside>
      </div>
    </>
  );
}
function Radar({ r, mode }: { r: Report; mode: ColorTheme }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const chart = echarts.init(ref.current!);
    const theme = getComputedStyle(ref.current!);
    const color = (token: string) => theme.getPropertyValue(token).trim();
    chart.setOption({
      animation: false,
      textStyle: { fontFamily: theme.fontFamily },
      radar: {
        radius: "59%",
        indicator: dimensions.map((name) => ({ name, max: 100 })),
        axisName: { color: color("--muted") },
        axisLine: { lineStyle: { color: color("--line") } },
        splitLine: { lineStyle: { color: color("--line") } },
        splitArea: { areaStyle: { color: [color("--card"), color("--surface")] } },
      },
      series: [
        {
          type: "radar",
          data: [{ value: r.scores }],
          lineStyle: { color: color("--accent") },
          itemStyle: { color: color("--accent") },
          areaStyle: { color: color("--accent"), opacity: 0.14 },
        },
      ],
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current!);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [r, mode]);
  return <div className="radar" ref={ref} aria-hidden="true" />;
}
function dimensionAdvice(index: number, score: number) {
  const high = [
    "核心要点已覆盖到位，可进一步补充跨概念联系。",
    "术语定义与核心结论准确，表述可信度高。",
    "语言通俗直观，能用清晰例子帮助听众理解。",
    "推导步骤衔接紧凑，边界条件与前提交代清楚。",
  ];
  const mid = [
    "主干框架已掌握，建议补充细节案例与对比场景。",
    "大方向正确，个别公式前提或概念区分可再精准些。",
    "整体能听懂，尝试用生活类比把抽象机制讲透。",
    "结论合理，建议显式说明「为什么成立」与边界限制。",
  ];
  const low = [
    "覆盖偏少，建议先对照认知文档梳理核心条目。",
    "存在概念混淆或偏差，优先听 AI 讲解校准定义。",
    "表述略显抽象生硬，试着向初学者口语化复述一遍。",
    "因果链条跳跃较多，建议补充前置条件与反例验证。",
  ];
  return (score >= 80 ? high : score >= 60 ? mid : low)[index] || "";
}

function readTodos(reportId: string): Record<number, boolean> {
  try {
    return JSON.parse(localStorage.getItem("fm.report.todo:" + reportId) || "{}") as Record<number, boolean>;
  } catch {
    return {};
  }
}

export function Reports({
  theme,
  space,
  id,
  select,
  practice,
  guide,
  history,
  onDelete,
  go,
}: {
  theme: ColorTheme;
  space: Space;
  id: string;
  select: (id: string) => void;
  practice?: (c: Course, n?: Concept) => void;
  guide?: (c: Course, n?: Concept) => void;
  history?: (id: string) => void;
  onDelete?: (report: Report) => void;
  go: () => void;
}) {
  const r = space.reports.find((x) => x.id === id) || space.reports[0];
  const guided = space.sessions.some((session) => session.id === r?.sessionId && session.guided);
  const course = r ? space.courses.find((c) => c.id === r.doc || c.title === r.title) : undefined;
  const [todos, setTodos] = useState<Record<number, boolean>>(() => (r ? readTodos(r.id) : {}));
  useEffect(() => {
    setTodos(r ? readTodos(r.id) : {});
  }, [r?.id]);
  function toggleTodo(index: number) {
    if (!r) return;
    const next = { ...todos, [index]: !todos[index] };
    setTodos(next);
    try { localStorage.setItem("fm.report.todo:" + r.id, JSON.stringify(next)); } catch { /* ignore */ }
  }
  function matchConcept(text: string) {
    return course?.nodes.find((n) => text.includes(n.name));
  }
  const avgScore = r ? mean(r.scores) : 0;
  const maxScore = r ? Math.max(...r.scores) : 0;
  const minScore = r ? Math.min(...r.scores) : 0;
  const completedTodos = r ? r.plan.filter((_, i) => Boolean(todos[i])).length : 0;

  return (
    <>
      <WorkspaceToolbar>
        {r && (
          <select
            aria-label="历史报告"
            value={r.id}
            onChange={(e) => select(e.target.value)}
          >
            {space.reports.map((v) => (
              <option value={v.id} key={v.id}>
                {v.title} · {new Date(v.date).toLocaleString()}
              </option>
            ))}
          </select>
        )}
      </WorkspaceToolbar>
      {!r ? (
        <div className="box empty">
          <RotateCcw aria-hidden="true" />
          <h2>还没有诊断报告</h2>
          <p>完成两轮讲解后，即可在工作台结束教学并生成报告。</p>
          <button className="primary" onClick={go}>前往工作台</button>
        </div>
      ) : (
        <div className="reports-layout">
          <aside className="reports-rail" aria-label="历史报告列表">
            <div className="reports-rail-head">
              <h3>历史诊断记录</h3>
              <small>{space.reports.length} 份</small>
            </div>
            <ul className="reports-list">
              {space.reports.map((item) => {
                const score = mean(item.scores);
                return (
                  <li key={item.id} className="reports-list-row">
                    <button
                      type="button"
                      className="reports-list-item"
                      aria-current={item.id === r.id ? "true" : undefined}
                      onClick={() => select(item.id)}
                    >
                      <div className="reports-list-main">
                        <strong>{item.title}</strong>
                        <small>{new Date(item.date).toLocaleString()}</small>
                      </div>
                      <span className="reports-list-score">{score}</span>
                    </button>
                    {onDelete && (
                      <button
                        type="button"
                        className="icon-button reports-row-delete"
                        aria-label="删除报告"
                        title={"删除「" + item.title + "」报告"}
                        onClick={(e) => {
                          e.stopPropagation();
                          onDelete(item);
                        }}
                      >
                        <Trash2 aria-hidden="true" />
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          </aside>
          <article className="box report">
            <header className="report-head">
              <div>
                <small>费曼学习诊断 · {new Date(r.date).toLocaleString()}</small>
                <h2>{r.title}</h2>
              </div>
              <div className="tools">
                {history && r.sessionId && (
                  <button onClick={() => history(r.sessionId!)}>{guided ? "查看讲解会话" : "查看双窗历史"}</button>
                )}
                <button
                  onClick={() =>
                    download(
                      r.title + "-诊断.md",
                      [
                        "# " + r.title,
                        new Date(r.date).toLocaleString(),
                        ...dimensions.map((d, i) => d + " " + r.scores[i]),
                        r.comment || "",
                        "## 盲区复盘",
                        ...(r.weaknesses.length ? r.weaknesses : ["暂无记录"]),
                        "## 后续练习",
                        r.plan.map((p) => "- " + p).join("\n"),
                      ].join("\n\n"),
                    )
                  }
                >
                  导出 Markdown
                </button>
                <button onClick={() => window.print()}>打印 / 存 PDF</button>
                {onDelete && (
                  <button
                    type="button"
                    className="quiet report-delete-btn"
                    title="删除此诊断报告"
                    onClick={() => onDelete(r)}
                  >
                    <Trash2 aria-hidden="true" />
                    删除报告
                  </button>
                )}
              </div>
            </header>

            <section className="report-figure" aria-label="四维诊断">
              <div className="report-radar-wrap">
                <Radar r={r} mode={theme} />
                <div className="report-overall-badge">
                  <span>综合均分</span>
                  <strong>{avgScore}</strong>
                </div>
              </div>
              <div className="dimension-bars">
                {dimensions.map((d, i) => {
                  const score = r.scores[i] ?? 0;
                  const isStrength = maxScore > minScore && score === maxScore;
                  const isWeakness = maxScore > minScore && score === minScore;
                  return (
                    <div className="dimension-row" key={d}>
                      <div className="dimension-row-head">
                        <div className="dimension-title">
                          <strong>{d}</strong>
                          {isStrength && <span className="dim-tag strength">优势项</span>}
                          {isWeakness && <span className="dim-tag focus">优先补强</span>}
                        </div>
                        <b className="dimension-score">{score}</b>
                      </div>
                      <progress value={score} max={100} aria-label={`${d} ${score}分`} />
                      <p className="dimension-advice">{dimensionAdvice(i, score)}</p>
                    </div>
                  );
                })}
              </div>
            </section>

            {r.comment && (
              <section className="report-summary-card" aria-label="本轮复述核心结论">
                <header>
                  <h3>本轮复述核心结论</h3>
                  {course && (
                    <small>
                      关联资料：{course.title} · 当前掌握度 {mean(course.nodes.map((n) => n.score))}%
                    </small>
                  )}
                </header>
                <p className="report-comment">{r.comment}</p>
              </section>
            )}

            <section className="report-section">
              <h3>盲区复盘</h3>
              {r.weaknesses.length ? (
                <ul className="report-findings weakness-cards">
                  {r.weaknesses.map((x) => {
                    const matched = matchConcept(x);
                    return (
                      <li key={x} className="weakness-card">
                        <div className="weakness-card-body">
                          <span className="weakness-status">待补漏</span>
                          <p>{x}</p>
                        </div>
                        {course && (guide || practice) && (
                          <div className="weakness-actions">
                            {guide && (
                              <button type="button" onClick={() => guide(course, matched)}>
                                <GraduationCap aria-hidden="true" /> 听 AI 讲解
                              </button>
                            )}
                            {practice && (
                              <button type="button" onClick={() => practice(course, matched)}>
                                回双窗重讲
                              </button>
                            )}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <ul className="report-findings">
                  <li>本轮没有未解决的薄弱记录。</li>
                </ul>
              )}
            </section>

            <section className="report-section">
              <div className="sectionhead" style={{ margin: "0 0 8px" }}>
                <h3 style={{ margin: 0 }}>后续练习</h3>
                <small className="meta">已完成 {completedTodos} / {r.plan.length}</small>
              </div>
              <ol className="review-plan">
                {r.plan.map((p, i) => (
                  <li key={i} className={todos[i] ? "todo-done" : ""}>
                    <label className="todo-label">
                      <input type="checkbox" checked={Boolean(todos[i])} onChange={() => toggleTodo(i)} />
                      <span>{p}</span>
                    </label>
                    {course && practice && (
                      <button type="button" className="quiet todo-go" onClick={() => practice(course, matchConcept(p))}>
                        去双窗演练 <ArrowRight aria-hidden="true" />
                      </button>
                    )}
                  </li>
                ))}
              </ol>
            </section>

            {course && course.nodes.length > 0 && (
              <section className="report-section">
                <h3>关联知识点掌握现状</h3>
                <div className="report-concepts-grid">
                  {course.nodes.map((n) => {
                    const label = status(n, space.logs);
                    return (
                      <div key={n.id} className="report-concept-chip">
                        <div className="row between">
                          <strong>{n.name}</strong>
                          <small>{label} · {n.score}%</small>
                        </div>
                        <MasteryMeter score={n.score} status={label} label={n.name + "掌握度"} compact motion="focus" />
                        {n.score < 80 && practice && (
                          <button type="button" className="quiet" onClick={() => practice(course, n)}>
                            专项复讲 <ArrowRight aria-hidden="true" />
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            )}
          </article>
        </div>
      )}
    </>
  );
}
