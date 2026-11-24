import React, { useEffect, useRef, useState } from "react";
import { lookupQuestion } from "./lookup";
import { ReviewTools } from "./ReviewTools";
import { animateCorrection } from "./correctionFlight";
import { Dialog } from "./ui";
import "./cognitive.css";
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
import {
  stream,
  remoteReport,
  sessionHistory,
  setPersona,
  cognitiveDoc, refreshMemory, createRemote,
  type TeachingPurpose,
} from "./api";
import {
  CognitiveDoc, CognitiveItem, CognitiveUpdate, Correction,
  Course, CourseCitation,
  Message,
  Session,
  Space,
  mean,
  roles,
  status,
} from "./model";

type PendingTurn = { question: string; reply: string };

function CourseText({ text }: { text: string }) {
  return (
    <Markdown remarkPlugins={[gfm, math]} rehypePlugins={[katex, highlight]}>
      {text}
    </Markdown>
  );
}

function SourceCard({ hit }: { hit: CourseCitation }) {
  const [expanded, setExpanded] = useState(false);
  const long = hit.snippet.length > 200 || hit.snippet.split("\n").length > 7;
  const preview = hit.snippet
    .split(/\n\s*\n|(?<=[。！？])\s*/u)
    .find((part) => part.trim() && !/^\s*#{1,6}\s+[^\n]*$/.test(part));
  return (
    <section className="source-card">
      <header>
        <strong>
          {hit.doc_title} · 片段 #{hit.chunk_index}
        </strong>
        <small>
          {hit.score === undefined
            ? "本地课件"
            : "相关度 " + hit.score.toFixed(2)}
        </small>
      </header>
      <div className={long && !expanded ? "source-preview" : ""}>
        <CourseText
          text={long && !expanded ? preview || hit.snippet : hit.snippet}
        />
      </div>
      {long && (
        <button
          type="button"
          className="quiet"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起片段" : "展开全文"}
        </button>
      )}
    </section>
  );
}
const markLabels = { mastered: "✓ 已掌握", partial: "◐ 待补全", wrong: "× 理解偏差", untested: "○ 待考核" };

function Rubric({ doc, close, review, busy }: { doc: CognitiveDoc; close: () => void; review: (ids: number[]) => void; busy: boolean }) {
  return <Dialog drawer title="Agent 认知文档" close={close}>
    <CourseText text={doc.overview} />
    <p>总体掌握度 <strong>{doc.mastery_score}%</strong> · 已掌握 {doc.mastered_count}/{doc.total}</p>
    <progress max={100} value={doc.mastery_score} aria-label="认知文档掌握度" />
    {doc.sections.map(group => <section key={group.title}>
      <h3>{group.title}</h3>
      {group.items.map(item => <article key={item.id} className={"rubric-item " + item.status} data-item-id={item.id}>
        <header><b>{markLabels[item.status]}</b>
          {item.id === doc.current_item_id && <small>当前提问</small>}
          <button className="quiet" disabled={busy} onClick={() => review([item.id])}>复习此条</button></header>
        <CourseText text={item.statement} />
        <details><summary>判定标准</summary><CourseText text={item.key_criteria} /></details>
        {item.user_evidence && <p>回答依据 · {item.user_evidence}</p>}
        {item.issue_note && <p>待纠正 · {item.issue_note}</p>}
      </article>)}
    </section>)}
  </Dialog>;
}

function CorrectionCard({ card, back, messageId, arrivals }: {
  card: Correction;
  back: () => void;
  messageId: number;
  arrivals: Set<number>;
}) {
  const target = useRef<HTMLElement>(null);
  const [justPushed] = useState(() => arrivals.has(messageId));
  React.useLayoutEffect(() => {
    if (!justPushed || !target.current) return;
    arrivals.delete(messageId);
    return animateCorrection(target.current);
  }, [justPushed, arrivals, messageId]);
  return <article ref={target} className={"msg correction-card " + card.issue_type}>
    <header><strong>实时纠偏 · {card.label}</strong><small>{card.concept_name}</small></header>
    <h4>你的表述</h4><blockquote>{card.user_claim}</blockquote>
    <h4>Agent 认知文档标准解读与举例</h4><CourseText text={card.accurate_explanation} />
    <h4>参考依据</h4><CourseText text={card.item_statement} />
    {card.citations.map(hit => "kind" in hit
      ? <small key={"item-" + hit.item_id}>{hit.section_title} · 认知条目 #{hit.item_id}</small>
      : <SourceCard key={hit.chunk_index} hit={hit} />)}
    <button className="quiet" onClick={back}>我明白了，回左窗继续讲</button>
  </article>;
}

export default function Workbench({
  online,
  space,
  save,
  session,
  choose,
  graph,
  report,
  canLearn,
  modelRequired, enterReview,
}: {
  enterReview: (id: string, course: Course) => void;
  online: boolean;
  space: Space;
  save: (fn: (s: Space) => Space) => void;
  session: Session;
  choose: () => void;
  graph: () => void;
  report: (id: string) => void;
  canLearn: (purpose: TeachingPurpose) => Promise<boolean>;
  modelRequired: (error: unknown) => boolean;
}) {
  const c = space.courses.find((x) => x.id === session.doc)!;
  function displayBadge(message: Message) {
    const name =
      !online && c.nodes.find((node) => node.id === message.node)?.name;
    return name && message.badge && !message.badge.startsWith(name + " · ")
      ? name + " · " + message.badge
      : message.badge;
  }
  const [ratio, setRatio] = useState(55),
    [collapsed, setCollapsed] = useState(false),
    [notice, setNotice] = useState(""),
    [mobile, setMobile] = useState(0);
  const [pending, setPending] = useState<(PendingTurn | null)[]>([null, null]),
    [finishing, setFinishing] = useState(false);
  const streams = useRef<(AbortController | null)[]>([null, null]);
  useEffect(() => () => streams.current.forEach((ctrl) => ctrl?.abort()), []);
  const [rubric, setRubric] = useState<CognitiveDoc | null>(null);
  const focusNode = session.review && rubric
    ? rubric.sections.flatMap(section => section.items).find(item => item.id === rubric.current_item_id)?.node_id
    : undefined;
  const n = c.nodes.find(node => node.id === String(focusNode ?? session.focus)) || c.nodes[0];
  const [rubricOpen, setRubricOpen] = useState(false);
  const [pushCount, setPushCount] = useState(0);
  useEffect(() => {
    if (!pushCount) return;
    const timer = setTimeout(() => setPushCount(0), 4500);
    return () => clearTimeout(timer);
  }, [pushCount]);
  const arrivals = useRef(new Set<number>());
  const [revision, setRevision] = useState(0);
  const [startingReview, setStartingReview] = useState(false);
  async function startReview(ids: number[]) {
    if (startingReview || pending.some(Boolean)) return;
    setStartingReview(true);
    try {
      if (await canLearn("feynman")) enterReview(await createRemote(c.id, session.role, undefined, ids), c);
    } catch (e) { if (!modelRequired(e)) setNotice((e as Error).message); }
    finally { setStartingReview(false); }
  }
  function applyMarks(update: CognitiveUpdate) {
    setRevision(n => n + 1);
    if (update.review_completed) patch({reviewCompleted: true});
    const changes = new Map<number, CognitiveItem>(update.items.map(i => [i.id, i]));
    setRubric(doc => doc && ({ ...doc, current_item_id: update.current_item_id,
      mastery_score: update.mastery_score, mastered_count: update.mastered_count,
      sections: doc.sections.map(g => ({ ...g, items: g.items.map(i => changes.get(i.id) || i) })) }));
  }
  function pushCard(message: Message) {
    if (message.id) arrivals.current.add(message.id);
    setCollapsed(false);
    setMobile(1);
    setPushCount(n => n + 1);
    save(s => ({ ...s, sessions: s.sessions.map(v => v.id !== session.id || v.right.some(m => m.id === message.id)
      ? v : { ...v, right: [...v.right, message] }) }));
  }
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
  const [loadingHistory, setLoadingHistory] = useState(online),
    [historyError, setHistoryError] = useState(false),
    [changingRole, setChangingRole] = useState(false),
    [reload, setReload] = useState(0);
  useEffect(() => {
    if (!online) return;
    const ctrl = new AbortController();
    setLoadingHistory(true);
    setHistoryError(false);
    sessionHistory(session.id, ctrl.signal)
      .then(async (history) => {
        if (ctrl.signal.aborted) return;
        if (!history.left.length && !history.closed) {
          const { reply } = await stream(session.id, 0, "", ctrl.signal, () => {}, true);
          history.left.push(reply);
        }
        const doc = await cognitiveDoc(c.id, session.id, ctrl.signal);
        if (!ctrl.signal.aborted) { setRubric(doc); patch(history); }
      })
      .catch((e) => {
        if (!ctrl.signal.aborted) {
          setNotice(e.message);
          setHistoryError(true);
        }
      })
      .finally(() => {
        if (!ctrl.signal.aborted) setLoadingHistory(false);
      });
    return () => ctrl.abort();
  }, [online, session.id, reload]);
  const locked =
    session.closed ||
    changingRole ||
    finishing ||
    loadingHistory ||
    historyError;
  async function changeRole(role: string) {
    if (!online || locked || pending.some(Boolean)) return;
    setChangingRole(true);
    try {
      await setPersona(session.id, role);
      patch({ role, ...await sessionHistory(session.id) });
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setChangingRole(false);
    }
  }
  async function onlineSend(side: number, text: string) {
    if (locked || streams.current[side]) return;
    const ctrl = new AbortController();
    streams.current[side] = ctrl;
    const key = side ? "right" : "left";
    const show = (turn: PendingTurn | null) =>
      setPending((v) => v.map((old, i) => (side === i ? turn : old)));
    show({ question: text, reply: "" });
    setNotice("");
    try {
      const { reply, scores } = await stream(
        session.id,
        side,
        text,
        ctrl.signal,
        (reply) => show({ question: text, reply }), false, applyMarks, pushCard,
      );
      if (ctrl.signal.aborted) return;
      save((s) => ({
        ...s,
        courses: s.courses.map((d) =>
          d.id !== c.id
            ? d
            : {
                ...d,
                nodes: d.nodes.map((n) => ({ ...n, ...scores.get(n.id) })),
              },
        ),
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
      if (ctrl.signal.aborted || modelRequired(e)) return;
      const reason = (e as Error).message;
      try {
        const [history, memory, doc] = await Promise.all([
          sessionHistory(session.id, ctrl.signal),
          refreshMemory(c, ctrl.signal), cognitiveDoc(c.id, session.id, ctrl.signal),
        ]);
        if (ctrl.signal.aborted) return;
        setRubric(doc);
        const baseline =
          session[key]
            .filter((m) => m.who === "听众" || m.who === "百科")
            .at(-1)?.id || 0;
        const accepted = history[key].some(
          (m) => m.id && m.id > baseline && m.who === "我" && m.text === text,
        );
        save((s) => ({
          ...s,
          courses: s.courses.map((d) =>
            d.id === memory.course.id ? memory.course : d,
          ),
          logs: [
            ...s.logs.filter((w) => w.doc !== memory.course.id),
            ...memory.logs,
          ],
          sessions: s.sessions.map((v) => {
            if (v.id !== session.id) return v;
            const drafts: [string, string] = [...v.drafts];
            if (accepted && drafts[side].trim() === text) drafts[side] = "";
            else if (!accepted && !drafts[side]) drafts[side] = text;
            return { ...v, ...history, drafts };
          }),
        }));
        setNotice(
          accepted
            ? history[key].at(-1)?.who === "我"
              ? reason + " 回答与纠偏已保存，可继续复讲。"
              : "已从服务端恢复本轮回复。"
            : reason + " 已核对历史，请确认后再发送。",
        );
      } catch {
        if (!ctrl.signal.aborted) {
          setNotice(reason + " 请先刷新会话历史。");
          setHistoryError(true);
        }
      }
    } finally {
      streams.current[side] = null;
      if (!ctrl.signal.aborted) show(null);
    }
  }
  function send(side: number, text: string) {
    text = text.trim();
    if (!text || locked || (!side && session.reviewCompleted)) return;
    if (online) void onlineSend(side, text);
    else void canLearn(side ? "encyclopedia" : "feynman");
  }
  async function finish() {
    setFinishing(true);
    try {
      if (!(await canLearn("report"))) return;
      if (session.left.filter((m) => m.who === "我").length < 2) {
        setNotice("至少完成两轮讲解后即可生成报告。");
        return;
      }
      const r = await remoteReport(session.id, c.id, c.title);
      save((s) => ({
        ...s,
        sessions: s.sessions.map((v) =>
          v.id === session.id ? { ...v, closed: true } : v,
        ),
        reports: [r, ...s.reports],
      }));
      report(r.id);
    } catch (e) {
      if (!modelRequired(e)) setNotice((e as Error).message);
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
          <ReviewTools doc={c.id} rubric={rubric} online={online} revision={revision}
            busy={startingReview || pending.some(Boolean) || finishing || loadingHistory}
            start={startReview} reportError={setNotice} />
          {online && rubric && <button className="rubric-trigger" onClick={() => setRubricOpen(true)}>
            Agent 认知文档（已掌握 {rubric.mastered_count}/{rubric.total}）
          </button>}
          <button onClick={graph}>
            知识图谱 · {mean(c.nodes.map((x) => x.score))}%
          </button>
          <button
            className="primary"
            disabled={locked || pending.some(Boolean)}
            onClick={finish}
          >
            {session.closed
              ? "教学已结束"
              : finishing
                ? "生成报告中…"
                : "结束并生成报告"}
          </button>
        </div>
      </div>
      {session.review && <p className="review-state">{session.reviewCompleted ? "本轮复习已完成" : "正在复习选定条目"}</p>}
      {rubricOpen && rubric && <Rubric doc={rubric} review={startReview} busy={startingReview || pending.some(Boolean)} close={() => setRubricOpen(false)} />}
      {notice && <p role="status">{notice}</p>}
      {historyError && (
        <button onClick={() => setReload((v) => v + 1)}>刷新会话历史</button>
      )}
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
                      disabled={!online || locked || pending.some(Boolean)}
                      onChange={(e) => void changeRole(e.target.value)}
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
                {i === 1 && pushCount > 0 && <p className="push-notice" role="status">已弹出 {pushCount} 条实时纠偏</p>}
                <small>
                  {i
                    ? "优先查证当前资料"
                    : (online
                        ? ["未学习", "已掌握", "巩固中", "待补漏"]
                        : ["已掌握", "巩固中", "待补漏"]
                      )
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
                    <h3>
                      {i
                        ? "查证课程原文"
                        : loadingHistory
                          ? "正在恢复会话"
                          : "开始讲解"}
                    </h3>
                    <p>
                      {i
                        ? "输入概念名称，或从左窗带入一个追问。"
                        : n
                          ? "用自己的话讲解「" + n.name + "」。"
                          : "围绕当前资料进行讲解。"}
                    </p>
                  </div>
                )}
                {(i ? session.right : session.left).map((m, j) => m.correction ? (
                  <CorrectionCard key={"push-" + m.id} card={m.correction} messageId={m.id!} arrivals={arrivals.current} back={() => focus(0)} />
                ) : (
                  <article
                    className={"msg " + (m.who === "我" ? "mine" : "")}
                    key={j}
                  >
                    <small>{m.who}</small>
                    <CourseText text={m.text} />
                    {m.badge && (
                      <small className="pill">{displayBadge(m)}</small>
                    )}
                    {m.citations?.length ? (
                      <details className="source-list">
                        <summary>
                          参考资料片段（共 {m.citations.length} 条）
                        </summary>
                        {m.citations.map((hit) => (
                          <SourceCard key={hit.chunk_index} hit={hit} />
                        ))}
                      </details>
                    ) : (
                      m.citation && (
                        <details>
                          <summary>{m.citation}</summary>
                          <CourseText
                            text={
                              m.excerpt ||
                              (online
                                ? "服务端仅返回了出处位置。"
                                : c.nodes.find((x) => x.id === m.node)
                                    ?.summary || "")
                            }
                          />
                        </details>
                      )
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
                        disabled={locked || !!pending[1]}
                        onClick={() => {
                          send(1, lookupQuestion(session.left, j, c));
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
                  <>
                    <article className="msg mine">
                      <small>我</small>
                      <CourseText text={pending[i].question} />
                    </article>
                    <article className="msg" aria-busy="true">
                      <small>{i ? "百科" : "听众"}</small>
                      <CourseText text={pending[i].reply || "正在回复…"} />
                    </article>
                  </>
                )}
              </div>
              <div className="composer">
                <textarea
                  aria-label={i ? "百科问题" : "讲解内容"}
                  ref={(el) => {
                    fields.current[i] = el;
                  }}
                  readOnly={session.closed || (i === 0 && session.reviewCompleted) || !!pending[i]}
                  value={
                    pending[i]?.question === session.drafts[i].trim()
                      ? ""
                      : session.drafts[i]
                  }
                  onChange={(e) => {
                    const drafts: [string, string] = [...session.drafts];
                    drafts[i] = e.target.value;
                    patch({ drafts });
                  }}
                  placeholder={i ? "向百科助教提问…" : "用自己的话讲一讲…"}
                  onKeyDown={(e) => {
                    if (
                      e.key !== "Enter" ||
                      e.shiftKey ||
                      e.altKey ||
                      e.nativeEvent.isComposing ||
                      e.nativeEvent.keyCode === 229
                    )
                      return;
                    e.preventDefault();
                    if (!e.repeat) send(i, session.drafts[i]);
                  }}
                />
                <button
                  className="send"
                  aria-label={i ? "提交查证" : "发送讲解"}
                  disabled={locked || (i === 0 && session.reviewCompleted) || !!pending[i] || !session.drafts[i].trim()}
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
