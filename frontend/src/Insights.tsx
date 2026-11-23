import { useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useNodesInitialized,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import * as echarts from "echarts/core";
import { RadarChart } from "echarts/charts";
import { RadarComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { Course, Concept, Report, Space, download, status } from "./model";
import { dimensions } from "./model";
echarts.use([RadarChart, RadarComponent, CanvasRenderer]);
const graphFit = { padding: 0.22, minZoom: 0.1, maxZoom: 1.3 };
const relationNames: Record<string, string> = {
  prerequisite: "前置依赖",
  contains: "包含",
  contrast: "对比",
  applies_to: "应用于",
};
function FitLoadedGraph({ layout }: { layout: string }) {
  const ready = useNodesInitialized();
  const { fitView } = useReactFlow();
  useEffect(() => {
    if (!ready) return;
    const frame = requestAnimationFrame(() => void fitView(graphFit));
    return () => cancelAnimationFrame(frame);
  }, [ready, layout, fitView]);
  return null;
}
export function Graph({
  space,
  c,
  setCourse,
  start,
}: {
  space: Space;
  c: Course;
  setCourse: (id: string) => void;
  start: (c: Course, n: Concept) => void;
}) {
  const [query, setQuery] = useState(""),
    [selected, setSelected] = useState(c.nodes[0]?.id);
  const matches = useMemo(
    () =>
      c.nodes
        .filter((n) => n.name.includes(query))
        .sort((a, b) => b.importance - a.importance)
        .slice(0, 40),
    [c, query],
  );
  const initial = useMemo(
    () =>
      matches.map((n, i) => ({
        id: n.id,
        position: {
          x: (i % 3) * 180,
          y: Math.floor(i / 3) * 130 + (i % 3 === 1 ? 50 : 0),
        },
        data: { label: n.name + " · " + n.score + "%" },
        className:
          "node " +
          { 未学习: "", 已掌握: "green", 巩固中: "amber", 待补漏: "red" }[
            status(n, space.logs)
          ],
      })),
    [matches, space.logs],
  );
  const [nodes, setNodes, change] = useNodesState(initial);
  useEffect(() => setNodes(initial), [initial, setNodes]);
  const current = matches.find((n) => n.id === selected) || matches[0];
  return (
    <>
      <div className="pagehead">
        <div>
          <h1>知识记忆图谱</h1>
          <p>沿概念关联查看掌握进度，选择节点进入专项讲解。</p>
        </div>
        <select
          aria-label="图谱资料"
          value={c.id}
          onChange={(e) => setCourse(e.target.value)}
        >
          {space.courses.map((x) => (
            <option key={x.id} value={x.id}>
              {x.title}
            </option>
          ))}
        </select>
      </div>
      <div className="sectionhead">
        <input
          aria-label="搜索知识点"
          placeholder="搜索知识点…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <small>已掌握 ≥80　巩固中 40–79　待补漏 &lt;40 或有薄弱记录</small>
      </div>
      <div className="graphlayout">
        <div className="graph">
          <ReactFlow
            key={c.id + query}
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
                label: relationNames[l.label] || l.label,
                labelShowBg: true,
                labelBgStyle: { fill: "#eef2ff", fillOpacity: 0.96 },
                labelBgPadding: [5, 3],
                labelBgBorderRadius: 4,
              }))}
            onNodesChange={change}
            onNodeClick={(_, node) => setSelected(node.id)}
            fitView
            fitViewOptions={graphFit}
            minZoom={0.1}
          >
            <FitLoadedGraph layout={JSON.stringify(nodes.map((n) => n.id))} />
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
        <aside className="box">
          {current ? (
            <>
              <small>节点详情</small>
              <h2>{current.name}</h2>
              <b>
                {status(current, space.logs)} · {current.score}%
              </b>
              <progress value={current.score} max={100} />
              <p>{current.summary}</p>
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
              <button className="primary" onClick={() => start(c, current)}>
                专项费曼考核
              </button>
            </>
          ) : (
            <p>没有匹配的知识点。</p>
          )}
        </aside>
      </div>
    </>
  );
}
function Radar({ r }: { r: Report }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const chart = echarts.init(ref.current!);
    chart.setOption({
      animation: false,
      radar: {
        radius: "59%",
        indicator: dimensions.map((name) => ({ name, max: 100 })),
        axisName: { color: "#535353" },
        splitArea: { areaStyle: { color: ["#fff", "#f5f5f5"] } },
      },
      series: [
        {
          type: "radar",
          data: [{ value: r.scores }],
          lineStyle: { color: "#444" },
          itemStyle: { color: "#444" },
          areaStyle: { color: "#686868", opacity: 0.16 },
        },
      ],
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current!);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [r]);
  return <div className="radar" ref={ref} aria-hidden="true" />;
}
export function Reports({
  space,
  id,
  select,
  history,
  go,
}: {
  space: Space;
  id: string;
  select: (id: string) => void;
  history?: (id: string) => void;
  go: () => void;
}) {
  const r = space.reports.find((x) => x.id === id) || space.reports[0];
  return (
    <>
      <div className="pagehead">
        <div>
          <h1>历史诊断报告</h1>
          <p>回看每轮教学的四维评分与复习计划。</p>
        </div>
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
      </div>
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
            <button onClick={() => history(r.sessionId!)}>查看双窗历史</button>
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
                    "## 复习计划",
                    r.plan.map((p) => "- " + p).join("\n"),
                  ].join("\n\n"),
                )
              }
            >
              导出 Markdown
            </button>
            <button onClick={() => window.print()}>打印 / 存 PDF</button>
          </div>
          <Radar r={r} />
          <div className="scores">
            {dimensions.map((d, i) => (
              <div key={d}>
                <small>{d}</small>
                <strong>{r.scores[i]}</strong>
              </div>
            ))}
          </div>
          {r.comment && <p>{r.comment}</p>}
          <h3>盲区复盘</h3>
          {(r.weaknesses.length
            ? r.weaknesses
            : ["本轮没有未解决的薄弱记录。"]
          ).map((x) => (
            <p key={x}>{x}</p>
          ))}
          <h3>复习清单</h3>
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
