import { useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useNodesInitialized,
  useReactFlow,
  useStore,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import * as echarts from "echarts/core";
import { RadarChart } from "echarts/charts";
import { RadarComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { Course, Concept, Link, Report, Space, download, status } from "./model";
import { dimensions } from "./model";
import { relationLabel, WorkspaceToolbar, type ColorTheme } from "./ui";
import { CourseText } from "./LearningMessage";
import "./insights.css";
import { GraduationCap, Search } from "lucide-react";
import { MasteryLegend, MasteryProgress, masteryForScore } from "./MasteryProgress";
echarts.use([RadarChart, RadarComponent, CanvasRenderer]);
const graphFit = { padding: 0.20, minZoom: 0.025, maxZoom: 1.2 };
const rowSpacing = 144;

// Rank the visible graph for display; user dragging and the underlying learning order stay independent.
function graphPositions(concepts: Concept[], links: Link[]) {
  const remaining = new Set(concepts.map(n => n.id));
  const positions = new Map<string, { x: number; y: number }>();
  let row = 0;
  while (remaining.size) {
    const layer = concepts.filter(n => remaining.has(n.id) &&
      !links.some(l => l.to === n.id && remaining.has(l.from)));
    if (!layer.length) layer.push(concepts.find(n => remaining.has(n.id))!);
    layer.forEach((n, i) => {
      const rowSize = Math.min(4, layer.length - Math.floor(i / 4) * 4);
      positions.set(n.id, { x: (i % 4 - (rowSize - 1) / 2) * 236, y: (row + Math.floor(i / 4)) * rowSpacing });
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
    const frame = requestAnimationFrame(() => void fitView(graphFit));
    return () => cancelAnimationFrame(frame);
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
    [selected, setSelected] = useState(c.nodes[0]?.id);
  const matches = useMemo(
    () =>
      c.nodes
        .filter((n) => n.name.includes(query.trim()))
        .sort((a, b) => b.importance - a.importance)
        .slice(0, 40),
    [c, query],
  );
  const initial = useMemo(() => {
    const positions = graphPositions(matches, c.links);
    return matches.map(n => {
      const label = status(n, space.logs);
      return {
        id: n.id, position: positions.get(n.id)!, ariaLabel: n.name + " · " + label + " " + n.score + "%",
        className: "node mastery-node mastery-" + masteryForScore(n.score).tone,
        data: { label: <div className="node-content">
          <div className="node-heading"><i aria-hidden="true" /><strong>{n.name}</strong></div>
          <div className="node-mastery"><span>{label}</span><b>{n.score}%</b></div>
          <progress max={100} value={n.score} aria-label={n.name + "掌握度"} />
        </div> },
      };
    });
  }, [matches, c.links, space.logs]);
  const [nodes, setNodes, change] = useNodesState(initial);
  useEffect(() => setNodes(initial), [initial, setNodes]);
  const rowY = new Map(nodes.map(n => [n.id, n.position.y]));
  const current = matches.find((n) => n.id === selected) || matches[0];
  return (
    <>
      <WorkspaceToolbar>
        <select className="graph-course" aria-label="图谱资料" value={c.id} onChange={(e) => setCourse(e.target.value)}>
          {space.courses.map((x) => <option key={x.id} value={x.id}>{x.title}</option>)}
        </select>
        <label className="search graph-search"><Search aria-hidden="true" />
          <input aria-label="搜索知识点" placeholder="搜索知识点…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <div className="toolbar-actions">
          <div className="graph-mastery-key">
            <MasteryLegend />
            <details>
              <summary aria-label="掌握进度分档图例">图例</summary>
              <MasteryLegend />
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
            nodes={nodes}
            edges={c.links
              .filter(
                (l) =>
                  matches.some((n) => n.id === l.from) &&
                  matches.some((n) => n.id === l.to),
              )
              .map((l) => ({
                id: JSON.stringify([l.from, l.to, l.label]),
                source: l.from,
                target: l.to,
                // A link spanning several ranks places its caption in the first gap between rows.
                type: (rowY.get(l.to)! - rowY.get(l.from)!) > rowSpacing ? "smoothstep" : "default",
                pathOptions: { stepPosition: 0.11, offset: 18, borderRadius: 12 },
                label: relationLabel(l.label),
                labelShowBg: true,
                labelBgStyle: { fill: "var(--surface)", stroke: "var(--line)" },
                labelBgPadding: [6, 2],
                labelBgBorderRadius: 5,
              }))}
            onNodesChange={change}
            onNodeClick={(_, node) => setSelected(node.id)}
            fitView
            fitViewOptions={graphFit}
            minZoom={graphFit.minZoom}
          >
            <FitLoadedGraph layout={JSON.stringify(nodes.map((n) => n.id))} />
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
export function Reports({
  theme,
  space,
  id,
  select,
  history,
  go,
}: {
  theme: ColorTheme;
  space: Space;
  id: string;
  select: (id: string) => void;
  history?: (id: string) => void;
  go: () => void;
}) {
  const r = space.reports.find((x) => x.id === id) || space.reports[0];
  const guided = space.sessions.some((session) => session.id === r?.sessionId && session.guided);
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
          <h2>还没有诊断报告</h2>
          <p>完成两轮讲解后，即可在工作台结束教学并生成报告。</p>
          <button onClick={go}>前往工作台</button>
        </div>
      ) : (
        <article className="box report">
          <small>费曼学习诊断 · {new Date(r.date).toLocaleString()}</small>
          <h2>{r.title}</h2>
          {history && r.sessionId && (
            <button onClick={() => history(r.sessionId!)}>{guided ? "查看讲解会话" : "查看双窗历史"}</button>
          )}
          <div className="tools">
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
          </div>
          <div className="report-figure">
            <Radar r={r} mode={theme} />
            <div className="scores">
              {dimensions.map((d, i) => (
                <div key={d}>
                  <small>{d}</small>
                  <strong>{r.scores[i]}</strong>
                </div>
              ))}
            </div>
          </div>
          {r.comment && <p className="report-comment">{r.comment}</p>}
          <h3>盲区复盘</h3>
          <ul className="report-findings">{(r.weaknesses.length
            ? r.weaknesses
            : ["本轮没有未解决的薄弱记录。"]
          ).map((x) => (
            <li key={x}>{x}</li>
          ))}</ul>
          <h3>后续练习</h3>
          <ol className="review-plan">
            {r.plan.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ol>
        </article>
      )}
    </>
  );
}
