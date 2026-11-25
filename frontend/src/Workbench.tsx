import React, { useEffect, useRef, useState } from "react";
import { lookupQuestion } from "./lookup";
import CommandComposer from "./CommandComposer";
import { commands } from "./slash";
import { animateCorrection, scrollToLatestMessage } from "./correctionFlight";
import { Dialog, WorkspaceToolbar } from "./ui";
import "./cognitive.css";
import { CourseText, CorrectionDetails, MessageSources } from "./LearningMessage";
import { MasteryMeter } from "./MasteryProgress";
import {
  History,
  FileCheck2,
  Workflow,
  ListChecks,
  ChevronsUpDown,
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
  capabilitiesApi, type CapabilityRun,
} from "./api";
import {
  CognitiveDoc, CognitiveItem, CognitiveUpdate, Correction,
  Course,
  Message,
  Session,
  Space,
  mean,
  roles,
  status,
  download,
} from "./model";

type PendingTurn = { question: string; reply: string };

const markLabels = { mastered: "✓ 已掌握", partial: "◐ 待补全", wrong: "× 理解偏差", untested: "○ 待考核" };

function Rubric({ doc, close, assess, busy }: { doc: CognitiveDoc; close: () => void; assess: (ids: number[]) => void; busy: boolean }) {
  return <Dialog drawer className="rubric-drawer" title="Agent 认知文档" close={close} summary={
    <div className="rubric-overview">
      <div className="row between"><span>总体掌握度 <strong>{doc.mastery_score}%</strong></span>
        <small>已掌握 {doc.mastered_count}/{doc.total}</small></div>
      <MasteryMeter score={doc.mastery_score} label="认知文档掌握度" compact />
    </div>
  }>
    <div className="rubric-intro"><CourseText text={doc.overview} /></div>
    {doc.sections.map(group => <section key={group.title}>
      <h3>{group.title}</h3>
      {group.items.map(item => <article key={item.id} className={"rubric-item " + item.status} data-item-id={item.id}>
        <header><b>{markLabels[item.status]}</b>
          {item.id === doc.current_item_id && <small>当前提问</small>}
          <button className="assess-item" disabled={busy} onClick={() => assess([item.id])}>考核此条</button></header>
        <CourseText text={item.statement} />
        <details><summary>判定标准</summary><CourseText text={item.key_criteria} /></details>
        {item.user_evidence && <p className="rubric-note"><b>回答依据</b> {item.user_evidence}</p>}
        {item.issue_note && <p className="rubric-note"><b>待纠正</b> {item.issue_note}</p>}
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
    <CorrectionDetails card={card} />
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
  modelRequired, enterAssessment, openCapabilities, openModels, openReview,
}: {
  openCapabilities: (tab?: "skills" | "mcp") => void;
  openModels: () => void;
  openReview: () => void;
  enterAssessment: (id: string, course: Course) => void;
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
  const focusNode = session.targeted && rubric
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
  const [startingAssessment, setStartingAssessment] = useState(false);
  const [capBusy, setCapBusy] = useState(false), [help, setHelp] = useState(false), [rolePicker, setRolePicker] = useState(false), [reportConfirm, setReportConfirm] = useState(false);
  const requests = useRef(new Map<string, string>());
  const [runLog, setRunLog] = useState<Awaited<ReturnType<typeof capabilitiesApi.runs>> | null>(null);
  async function showRuns() { try { setRunLog(await capabilitiesApi.runs(session.id)); } catch (e) { setNotice((e as Error).message); } }
  async function startAssessment(ids: number[]) {
    if (startingAssessment || pending.some(Boolean)) return;
    setStartingAssessment(true);
    try {
      if (await canLearn("feynman")) enterAssessment(await createRemote(c.id, session.role, undefined, ids), c);
    } catch (e) { if (!modelRequired(e)) setNotice((e as Error).message); }
    finally { setStartingAssessment(false); }
  }
  function applyMarks(update: CognitiveUpdate) {
    if (update.review_completed) patch({assessmentCompleted: true});
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
  const [pendingLeft, pendingRight] = pending;
  useEffect(() => {
    const feed = feeds.current[0];
    if (feed) scrollToLatestMessage(feed);
  }, [session.left.length, pendingLeft, mobile, collapsed]);
  useEffect(() => {
    const feed = feeds.current[1];
    if (feed) scrollToLatestMessage(feed);
  }, [session.right.length, pendingRight, mobile, collapsed]);
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
    capBusy ||
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
    if (!text || locked || (!side && session.assessmentCompleted)) return;
    if (online) void onlineSend(side, text);
    else void canLearn(side ? "encyclopedia" : "feynman");
  }
  async function executeCapability(side: number, body: Pick<CapabilityRun, "kind" | "capability_id" | "tool" | "arguments" | "content" | "confirmed">) {
    if (!online || capBusy || pending.some(Boolean)) { setNotice("请登录账号，并等待当前回复完成。"); return false; }
    if (body.kind === "skill" && !(await canLearn("encyclopedia"))) return false;
    const key = JSON.stringify([side, body]);
    const request_id = requests.current.get(key) || crypto.randomUUID();
    requests.current.set(key, request_id);
    setCapBusy(true); setNotice("正在执行能力，请等待结果保存…");
    try {
      setPending(v => v.map((old, index) => index === side ? { question: body.content, reply: "" } : old));
      await capabilitiesApi.stream({ ...body, request_id, session_id: Number(session.id), window: side ? "encyclopedia" : "feynman" },
        reply => setPending(v => v.map((old, index) => index === side ? { question: body.content, reply } : old)));
      const history = await sessionHistory(session.id);
      save(s => ({ ...s, sessions: s.sessions.map(v => {
        if (v.id !== session.id) return v;
        const drafts: [string, string] = [...v.drafts];
        if (drafts[side] === session.drafts[side] && (drafts[side].trim() === body.content.trim() || drafts[side].startsWith("/"))) drafts[side] = "";
        return { ...v, ...history, drafts };
      }) }));
      requests.current.delete(key); setNotice("已完成并保存。本次工具或技能操作不计入讲解评分。");
      return true;
    } catch (e) { if (!modelRequired(e)) setNotice((e as Error).message + " 可刷新历史核对结果；请勿重复执行不确定的外部操作。"); return false; }
    finally { setCapBusy(false); setPending(v => v.map((old, index) => index === side ? null : old)); }
  }
  function runCommand(side: number, name: string, argument: string) {
    if (capBusy || pending.some(Boolean)) return;
    const clear = () => { const drafts: [string, string] = [...session.drafts]; drafts[side] = ""; patch({ drafts }); };
    if (name === "skills" || name === "mcp") { openCapabilities(name); clear(); }
    else if (["help", "tools"].includes(name)) { setHelp(true); clear(); }
    else if (name === "model") { openModels(); clear(); }
    else if (name === "graph") { graph(); clear(); }
    else if (name === "review") { openReview(); clear(); }
    else if (name === "report") setReportConfirm(true);
    else if (name === "role") {
      const labels: Record<string, string> = { child: roles[0], 小学生: roles[0], peer: roles[1], 同窗: roles[1], examiner: roles[2], 考官: roles[2] };
      if (argument && (labels[argument] || roles.includes(argument))) { void changeRole(labels[argument] || argument); clear(); }
      else { setRolePicker(true); clear(); }
    } else if (name === "lookup") {
      if (!argument) { setNotice("用法：/lookup 你想查证的问题"); return; }
      focus(1); void executeCapability(1, { kind: "lookup", content: argument }).then(ok => { if (ok && side === 0) clear(); });
    } else if (name === "progress" || name === "history") void executeCapability(side, { kind: name, content: "/" + name });
  }
  async function finish() {
    setFinishing(true);
    try {
      if (!(await canLearn("report"))) return;
      if (session.left.filter((m) => m.who === "我" && !m.operation).length < 2) {
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
      <WorkspaceToolbar>
        <button className="course-switch quiet" onClick={choose} aria-label={c.title + " · 更换资料"} title={c.title + " · 更换资料"}>
          <span className="truncate">{c.title}</span><span className="change-label">更换资料</span>
          <ChevronsUpDown aria-hidden="true" />
        </button>
        <div className="toolbar-actions">
          {online && (
            <button className="compact-command" title="能力运行记录" onClick={() => void showRuns()}>
              <History aria-hidden="true" /><span>能力运行记录</span>
            </button>
          )}
          {online && rubric && <button className="rubric-trigger compact-command" onClick={() => setRubricOpen(true)}
            title={`Agent 认知文档（已掌握 ${rubric.mastered_count}/${rubric.total}）`}>
            <ListChecks aria-hidden="true" /><span>Agent 认知文档（已掌握 {rubric.mastered_count}/{rubric.total}）</span>
          </button>}
          <button className="compact-command" onClick={graph} title="知识图谱">
            <Workflow aria-hidden="true" /><span>知识图谱 · {mean(c.nodes.map((x) => x.score))}%</span>
          </button>
          <button className="primary compact-command" disabled={locked || pending.some(Boolean)} onClick={finish}
            title={session.closed ? "教学已结束" : "结束并生成报告"}>
            <FileCheck2 aria-hidden="true" /><span>{session.closed ? "教学已结束" : finishing ? "生成报告中…" : "结束并生成报告"}</span>
          </button>
        </div>
      </WorkspaceToolbar>
      {session.targeted && <p className="assessment-state">{session.assessmentCompleted ? "本轮定向考核已完成" : "正在考核选定条目"}</p>}
      {rubricOpen && rubric && <Rubric doc={rubric} assess={startAssessment} busy={startingAssessment || pending.some(Boolean)} close={() => setRubricOpen(false)} />}
      {notice && <p role="status">{notice}</p>}
      {runLog && <Dialog title="能力运行记录" close={() => setRunLog(null)}>{!runLog.length && <p>本轮尚未运行工具或技能。</p>}{runLog.map(run => <p key={run.id}><strong>{run.kind}</strong> · {run.status === "completed" ? "已完成" : run.status === "running" ? "执行中 / 待核对" : "失败或结果待核对"} · {run.created_at}<br /><small>{run.id}</small></p>)}</Dialog>}
      {help && <Dialog title="命令与工具" close={() => setHelp(false)}><p>输入 / 搜索，↑ ↓ 选择、Enter 确认，Shift+Enter 换行。Skills 选择后再输入任务；工具操作不作为讲解评分。</p>{commands.map(c => <p key={c.id}><strong>{c.command} · {c.name}</strong>　{c.description}</p>)}<button onClick={() => { setHelp(false); openCapabilities(); }}>打开能力中心</button></Dialog>}
      {rolePicker && <Dialog title="选择听众" close={() => setRolePicker(false)}><div className="tools">{roles.map(role => <button key={role} onClick={() => { void changeRole(role); setRolePicker(false); }}>{role}</button>)}</div></Dialog>}
      {reportConfirm && <Dialog title="结束并生成报告" close={() => setReportConfirm(false)}><p>生成诊断报告后，本轮教学会话将结束。</p><button onClick={() => { setReportConfirm(false); void finish(); }}>确认生成报告</button></Dialog>}
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
              <header className="pane-header">
                <h2>{i ? <BookOpen aria-hidden="true" /> : <MessagesSquare aria-hidden="true" />}
                  {i ? "百科溯源助教" : "费曼听众"}</h2>
                {i === 0 ? <>
                  <select aria-label="听众角色" value={session.role}
                    disabled={!online || locked || pending.some(Boolean)}
                    onChange={(e) => void changeRole(e.target.value)}>
                    {roles.map((r) => <option key={r}>{r}</option>)}
                  </select>
                  <small className="mastery-counts">
                    {(online ? ["未学习", "已掌握", "巩固中", "待补漏"] : ["已掌握", "巩固中", "待补漏"]).map(label => {
                      const count = c.nodes.filter(x => status(x, space.logs) === label).length;
                      return <span key={label} title={label + " " + count} data-status={label}>
                        <i aria-hidden="true" /><span className="count-label">{label} </span><b>{count}</b>
                      </span>;
                    })}
                  </small>
                </> : <>
                  {pushCount > 0 ? <small className="push-notice" role="status">已弹出 {pushCount} 条实时纠偏</small>
                    : <small>优先查证当前资料</small>}
                  <button onClick={() => setCollapsed(true)} className="icon-button foldbtn"
                    aria-label="收起百科" title="收起百科"><PanelRightClose /></button>
                </>}
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
                    {m.operation && m.who !== "我" && <button className="quiet" onClick={() => download(c.title + "-能力结果.md", m.text, "text/markdown")}>下载结果</button>}
                    {m.badge && (
                      <small className="pill">{displayBadge(m)}</small>
                    )}
                    {m.citations?.length ? (
                      <MessageSources citations={m.citations} />
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
              <CommandComposer side={i} online={online} revision={reload} locked={locked || !!pending[i]}
                context={{ title: c.title, content: [...session.left, ...session.right].filter(m => m.who !== "我").sort((a,b) => (a.id || 0) - (b.id || 0)).at(-1)?.text || "", date: new Date(Date.now() + 86400000).toISOString().slice(0,10) }}
                readOnly={session.closed || (i === 0 && session.assessmentCompleted) || !!pending[i]}
                value={pending[i]?.question === session.drafts[i].trim() ? "" : session.drafts[i]}
                change={value => { const drafts: [string, string] = [...session.drafts]; drafts[i] = value; patch({ drafts }); }}
                send={text => send(i, text)} command={(name, argument) => runCommand(i, name, argument)}
                execute={body => executeCapability(i, body)} manager={openCapabilities}
                onSwitchPane={() => focus(i ? 0 : 1)}
                field={el => { fields.current[i] = el; }} />
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
