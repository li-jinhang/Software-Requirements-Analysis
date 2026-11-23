import React, { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import gfm from "remark-gfm";
import math from "remark-math";
import katex from "rehype-katex";
import highlight from "rehype-highlight";
import "katex/dist/katex.min.css";
import "highlight.js/styles/github.css";
import {
  ArrowUp,
  ArrowRight,
  BookOpen,
  MessagesSquare,
  PanelRightClose,
  PanelRightOpen,
} from "lucide-react";
import { stream, remoteReport } from "./api";
import {
  opening,
  Message,
  Session,
  Space,
  mean,
  roles,
  status,
  uid,
} from "./model";
export default function Workbench({
  online,
  space,
  save,
  session,
  choose,
  graph,
  report,
}: {
  online: boolean;
  space: Space;
  save: (fn: (s: Space) => Space) => void;
  session: Session;
  choose: () => void;
  graph: () => void;
  report: (id: string) => void;
}) {
  const c = space.courses.find((x) => x.id === session.doc)!,
    n = c.nodes.find((x) => x.id === session.focus) || c.nodes[0];
  const [ratio, setRatio] = useState(55),
    [collapsed, setCollapsed] = useState(false),
    [notice, setNotice] = useState(""),
    [mobile, setMobile] = useState(0);
  const [pending, setPending] = useState(["", ""]),
    [finishing, setFinishing] = useState(false);
  const streams = useRef<(AbortController | null)[]>([null, null]);
  useEffect(() => () => streams.current.forEach((ctrl) => ctrl?.abort()), []);
  const wrap = useRef<HTMLDivElement>(null),
    fields = useRef<(HTMLTextAreaElement | null)[]>([]),
    feeds = useRef<(HTMLDivElement | null)[]>([]);
  useEffect(() => {
    feeds.current.forEach((el) => el?.scrollTo(0, el.scrollHeight));
  }, [session.left.length, session.right.length, mobile, collapsed, pending]);
  function patch(change: Partial<Session>) {
    save((s) => ({
      ...s,
      sessions: s.sessions.map((v) =>
        v.id === session.id ? { ...v, ...change } : v,
      ),
    }));
  }
  function focus(side: number) {
    if (side === 1) setCollapsed(false);
    setMobile(side);
    requestAnimationFrame(() => fields.current[side]?.focus());
  }
  async function onlineSend(side: number, text: string, sourceId?: string) {
    if (streams.current[side]) return;
    const ctrl = new AbortController();
    streams.current[side] = ctrl;
    const key = side ? "right" : "left",
      topic = sourceId || session.focus;
    let answer = "",
      excerpt = "",
      citation = "",
      badge = "",
      weakness = "",
      node = topic;
    const scores = new Map<string, number>();
    const show = (value: string) =>
      setPending((v) => v.map((old, i) => (i === side ? value : old)));
    show("正在回复…");
    setNotice("");
    try {
      await stream(
        session.id,
        side,
        text,
        topic,
        session.role,
        ctrl.signal,
        (kind, e) => {
          if (kind === "token") {
            answer += e.text ?? e.token ?? "";
            show(answer || "正在回复…");
          }
          if (kind === "citation" && e.excerpt)
            excerpt += (excerpt ? "\n\n" : "") + e.excerpt;
          if (kind === "citation")
            citation = [
              citation,
              [
                e.doc_title,
                e.chunk_index !== undefined ? "片段 #" + e.chunk_index : "",
              ]
                .filter(Boolean)
                .join(" · "),
            ]
              .filter(Boolean)
              .join(" / ");
          if (kind === "memory_update") {
            node = String(e.node_id ?? topic);
            if (typeof e.mastery_score === "number")
              scores.set(node, Math.min(100, Math.max(0, e.mastery_score)));
            badge = e.assessment || badge;
            weakness = e.weakness || weakness;
          }
        },
      );
      if (ctrl.signal.aborted) return;
      const reply: Message = {
        who: side ? "百科" : "听众",
        text: answer,
        excerpt,
        node,
        badge,
        citation,
      };
      save((s) => ({
        ...s,
        courses: s.courses.map((d) =>
          d.id !== c.id
            ? d
            : {
                ...d,
                nodes: d.nodes.map((x) =>
                  scores.has(x.id) ? { ...x, score: scores.get(x.id)! } : x,
                ),
              },
        ),
        logs:
          weakness || (side && citation)
            ? [
                ...s.logs,
                {
                  id: uid(),
                  doc: c.id,
                  node,
                  detail: weakness || "查证：" + text,
                  resolved: false,
                  session: session.id,
                },
              ]
            : s.logs,
        sessions: s.sessions.map((v) => {
          if (v.id !== session.id) return v;
          const drafts: [string, string] = [...v.drafts];
          if (drafts[side].trim() === text) drafts[side] = "";
          return {
            ...v,
            drafts,
            [key]: [...v[key], { who: "我", text }, reply],
          };
        }),
      }));
    } catch (e) {
      if (!ctrl.signal.aborted) {
        setNotice((e as Error).message);
        save((s) => ({
          ...s,
          sessions: s.sessions.map((v) => {
            if (v.id !== session.id || v.drafts[side]) return v;
            const drafts: [string, string] = [...v.drafts];
            drafts[side] = text;
            return { ...v, drafts };
          }),
        }));
      }
    } finally {
      streams.current[side] = null;
      if (!ctrl.signal.aborted) show("");
    }
  }
  function send(side: number, text: string, sourceId?: string) {
    text = text.trim();
    if (!text || session.closed) return;
    if (online) {
      void onlineSend(side, text, sourceId);
      return;
    }
    setNotice("");
    const match =
      c.nodes.find((x) => x.id === sourceId) ||
      c.nodes.find((x) => text.includes(x.name));
    const topic = match || n;
    let reply: Message;
    let updated = topic;
    let logs = space.logs;
    if (side === 1) {
      reply = match
        ? {
            who: "百科",
            text: topic.summary,
            node: topic.id,
            citation: c.title + " · 片段 #" + (c.nodes.indexOf(topic) + 1),
          }
        : {
            who: "百科",
            text:
              "当前资料中未定位到直接对应的概念。可尝试输入知识点名称，例如「" +
              n.name +
              "」。",
          };
      if (match)
        logs = [
          ...logs,
          {
            id: uid(),
            doc: c.id,
            node: topic.id,
            detail: "查证「" + topic.name + "」：" + text,
            resolved: false,
            session: session.id,
          },
        ];
    } else {
      const good = text.length >= 22 && !/不会|卡壳|不懂/.test(text) && !!match;
      const streak = good ? topic.streak + 1 : 0,
        score = good
          ? Math.min(100, Math.max(streak >= 2 ? 80 : 0, topic.score + 12))
          : topic.score;
      updated = { ...topic, streak, score };
      reply = {
        who: "听众",
        node: topic.id,
        text: good
          ? opening(topic.name, session.role) +
            " 如果改变一个条件，结果还一样吗？"
          : /不会|卡壳/.test(text)
            ? "我们先看一段原文。查证「" +
              topic.name +
              "」后，试着用自己的话再讲一次。"
            : "回到「" + topic.name + "」，你能具体说明它在什么条件下成立吗？",
        badge: good
          ? "演示评估 · +" + (score - topic.score)
          : "待补漏 · 适用条件",
      };
      if (streak >= 2)
        logs = logs.map((w) =>
          w.node === topic.id ? { ...w, resolved: true } : w,
        );
      if (!good)
        logs = [
          ...logs,
          {
            id: uid(),
            doc: c.id,
            node: topic.id,
            detail: topic.name + " · 需补充解释或适用条件",
            resolved: false,
            session: session.id,
          },
        ];
    }
    const key = side === 0 ? "left" : "right",
      drafts: [string, string] = [...session.drafts];
    drafts[side] = "";
    save((s) => ({
      ...s,
      logs,
      courses: s.courses.map((d) =>
        d.id === c.id
          ? {
              ...d,
              nodes: d.nodes.map((x) => (x.id === updated.id ? updated : x)),
            }
          : d,
      ),
      sessions: s.sessions.map((v) =>
        v.id === session.id
          ? {
              ...v,
              focus: topic.id,
              drafts,
              [key]: [...v[key], { who: "我", text }, reply],
            }
          : v,
      ),
    }));
  }
  async function finish() {
    const turns = session.left.filter((m) => m.who === "我");
    if (turns.length < 2) {
      setNotice("至少完成两轮讲解后即可生成报告。");
      return;
    }
    const weaknesses = [
      ...new Set(
        space.logs
          .filter((w) => w.session === session.id && !w.resolved)
          .map((w) => w.detail),
      ),
    ];
    const answers = session.left.filter((m) => m.badge);
    const scores = [
      Math.round(
        (new Set(answers.map((m) => m.node)).size / c.nodes.length) * 100,
      ),
      Math.round(
        (answers.filter((m) => m.badge?.includes("+")).length / turns.length) *
          100,
      ),
      Math.round(
        (turns.filter((m) => /例如|比如|就像|好比/.test(m.text)).length /
          turns.length) *
          100,
      ),
      Math.round(
        (turns.filter((m) => /因为|所以|如果|因此|条件/.test(m.text)).length /
          turns.length) *
          100,
      ),
    ];
    setFinishing(true);
    try {
      const r = online
        ? await remoteReport(session.id, c.id, c.title)
        : {
            id: session.id,
            doc: c.id,
            title: c.title,
            date: new Date().toISOString(),
            scores,
            weaknesses,
            plan: weaknesses.length
              ? weaknesses.map((t) => "结合原文重新讲解：" + t)
              : ["用一个新的例子复讲本轮知识点。"],
          };
      save((s) => ({
        ...s,
        sessions: s.sessions.map((v) =>
          v.id === session.id ? { ...v, closed: true } : v,
        ),
        reports: [r, ...s.reports],
      }));
      report(r.id);
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setFinishing(false);
    }
  }
  return (
    <>
      <div className="pagehead compact">
        <div>
          <h1>双窗工作台</h1>
          <button className="quiet" onClick={choose}>
            {c.title} · 更换资料
          </button>
        </div>
        <div className="tools">
          <button onClick={graph}>
            知识图谱 · {mean(c.nodes.map((x) => x.score))}%
          </button>
          <button
            className="primary"
            disabled={finishing || pending.some(Boolean)}
            onClick={finish}
          >
            {finishing ? "生成报告中…" : "结束并生成报告"}
          </button>
        </div>
      </div>
      {notice && <p role="status">{notice}</p>}
      <div className="mobiletabs">
        {["费曼讲解", "百科查证"].map((t, i) => (
          <button aria-pressed={mobile === i} onClick={() => focus(i)} key={t}>
            {t}
          </button>
        ))}
      </div>
      <div
        className={"duo " + (collapsed ? "fold" : "")}
        ref={wrap}
        style={{ "--ratio": ratio + "%" } as React.CSSProperties}
      >
        {[0, 1].map((i) => (
          <React.Fragment key={i}>
            {i === 1 && !collapsed && (
              <div
                className="splitter"
                role="separator"
                aria-label="调整双窗宽度"
                aria-valuenow={ratio}
                aria-orientation="vertical"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (["ArrowLeft", "ArrowRight"].includes(e.key)) {
                    e.preventDefault();
                    setRatio(
                      Math.max(
                        32,
                        Math.min(68, ratio + (e.key === "ArrowRight" ? 2 : -2)),
                      ),
                    );
                  }
                }}
                onPointerDown={(e) =>
                  e.currentTarget.setPointerCapture(e.pointerId)
                }
                onPointerMove={(e) => {
                  if (e.currentTarget.hasPointerCapture(e.pointerId)) {
                    const rect = wrap.current!.getBoundingClientRect();
                    setRatio(
                      Math.min(
                        68,
                        Math.max(
                          32,
                          ((e.clientX - rect.left) / rect.width) * 100,
                        ),
                      ),
                    );
                  }
                }}
              />
            )}
            <section className={"pane p" + i + (mobile === i ? " active" : "")}>
              <header>
                <div className="row between">
                  <h2>
                    {i ? <BookOpen /> : <MessagesSquare />}
                    {i ? "百科溯源助教" : "费曼听众"}
                  </h2>
                  {i === 0 ? (
                    <select
                      aria-label="听众角色"
                      value={session.role}
                      onChange={(e) =>
                        patch({
                          role: e.target.value,
                          left: [
                            ...session.left,
                            {
                              who: "系统",
                              text: "听众已切换为" + e.target.value,
                            },
                          ],
                        })
                      }
                    >
                      {roles.map((r) => (
                        <option key={r}>{r}</option>
                      ))}
                    </select>
                  ) : (
                    <button
                      onClick={() => setCollapsed(true)}
                      className="foldbtn"
                      aria-label="收起百科"
                    >
                      <PanelRightClose />
                    </button>
                  )}
                </div>
                <small>
                  {i
                    ? "优先查证当前资料"
                    : ["已掌握", "巩固中", "待补漏"]
                        .map(
                          (v) =>
                            v +
                            " " +
                            c.nodes.filter((x) => status(x, space.logs) === v)
                              .length,
                        )
                        .join("　")}
                </small>
              </header>
              <div
                className="feed"
                ref={(el) => {
                  feeds.current[i] = el;
                }}
              >
                {!(i ? session.right : session.left).length && (
                  <div className="empty">
                    <BookOpen />
                    <h3>查证课程原文</h3>
                    <p>输入概念名称，或从左窗带入一个追问。</p>
                  </div>
                )}
                {(i ? session.right : session.left).map((m, j) => (
                  <article
                    className={"msg " + (m.who === "我" ? "mine" : "")}
                    key={j}
                  >
                    <small>{m.who}</small>
                    <Markdown
                      remarkPlugins={[gfm, math]}
                      rehypePlugins={[katex, highlight]}
                    >
                      {m.text}
                    </Markdown>
                    {m.badge && <small className="pill">{m.badge}</small>}
                    {m.citation && (
                      <details>
                        <summary>{m.citation}</summary>
                        {m.excerpt ||
                          (online
                            ? "服务端仅返回了出处位置。"
                            : c.nodes.find((x) => x.id === m.node)?.summary)}
                      </details>
                    )}
                    {i === 1 && m.node && (
                      <small>
                        关联概念 ·{" "}
                        {c.links
                          .filter((l) => l.to === m.node || l.from === m.node)
                          .map(
                            (l) =>
                              c.nodes.find(
                                (x) =>
                                  x.id === (l.from === m.node ? l.to : l.from),
                              )?.name,
                          )
                          .join("、") || "暂无"}
                      </small>
                    )}
                    {m.who === "听众" && (
                      <button
                        className="quiet"
                        onClick={() => {
                          send(1, m.text, m.node);
                          focus(1);
                        }}
                      >
                        去右侧查证
                        <ArrowRight />
                      </button>
                    )}
                    {m.who === "百科" && (
                      <button className="quiet" onClick={() => focus(0)}>
                        回左窗继续讲
                      </button>
                    )}
                  </article>
                ))}
                {pending[i] && (
                  <article className="msg" role="status">
                    {pending[i]}
                  </article>
                )}
              </div>
              <div className="composer">
                <textarea
                  aria-label={i ? "百科问题" : "讲解内容"}
                  ref={(el) => {
                    fields.current[i] = el;
                  }}
                  value={session.drafts[i]}
                  onChange={(e) => {
                    const drafts: [string, string] = [...session.drafts];
                    drafts[i] = e.target.value;
                    patch({ drafts });
                  }}
                  placeholder={i ? "向百科助教提问…" : "用自己的话讲一讲…"}
                  onKeyDown={(e) => {
                    if ((e.ctrlKey || e.metaKey) && e.key === "Enter")
                      send(i, session.drafts[i]);
                  }}
                />
                <button
                  className="send"
                  aria-label={i ? "提交查证" : "发送讲解"}
                  disabled={
                    finishing || !!pending[i] || !session.drafts[i].trim()
                  }
                  onClick={() => send(i, session.drafts[i])}
                >
                  <ArrowUp />
                </button>
              </div>
            </section>
          </React.Fragment>
        ))}
        {collapsed && (
          <button
            className="unfold"
            onClick={() => setCollapsed(false)}
            aria-label="展开百科"
          >
            <PanelRightOpen />
          </button>
        )}
      </div>
    </>
  );
}
