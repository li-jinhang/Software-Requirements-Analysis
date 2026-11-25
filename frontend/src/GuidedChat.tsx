import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, BookOpen, GraduationCap, MessagesSquare, Workflow } from "lucide-react";
import { ApiError, refreshMemory, remoteReport, sessionHistory, setPersona, stream } from "./api";
import { CorrectionDetails, CourseText, MessageSources } from "./LearningMessage";
import { roles, type Course, type Message, type Session, type Space } from "./model";

type Pending = { kind: "lesson" | "ask" | "opening" | "retell"; prompt?: string; text: string };

const ChatMessage = memo(function ChatMessage({ message }: { message: Message }) {
  if (message.who === "系统") return <p className="chat-system">{message.text}</p>;
  const mine = message.who === "我";
  const listener = message.who === "听众";
  const correction = message.correction;
  return (
    <article className={"chat-message" + (mine ? " chat-user" : "")}>
      {!mine && (
        <span className={"chat-avatar" + (listener ? " listener" : "")}>
          {listener ? <MessagesSquare aria-hidden="true" /> : <GraduationCap aria-hidden="true" />}
        </span>
      )}
      <div className="chat-message-body">
        <b className="chat-speaker">{mine ? "你" : listener ? "费曼听众" : "AI 老师"}</b>
        <div className="chat-markdown">
          {correction ? (
            <details className="chat-correction">
              <summary>纠偏 · {correction.concept_name} · {correction.label}</summary>
              <CorrectionDetails card={correction} />
            </details>
          ) : <CourseText text={message.text} />}
        </div>
        {message.badge && <small className="pill">{message.badge}</small>}
        {!!message.citations?.length && <MessageSources citations={message.citations} />}
      </div>
    </article>
  );
});

export default function GuidedChat({ session, course, historyOptions, select, graph, save, modelRequired, showReport }: {
  session: Session;
  course: Course;
  historyOptions: { id: string; title: string }[];
  select: (id: string) => void;
  graph: () => void;
  save: (update: (space: Space) => Space) => void;
  modelRequired: (error: unknown) => boolean;
  showReport: (id: string) => void;
}) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(true);
  const [ready, setReady] = useState(false);
  const [mode, setMode] = useState<0 | 1>(1);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const operation = useRef<AbortController | null>(null);
  const feed = useRef<HTMLDivElement>(null);
  const field = useRef<HTMLTextAreaElement>(null);
  const followBottom = useRef(true);
  const target = course.nodes.find((n) => n.id === session.focus);
  const lessonReady = session.right.some((message) => message.lesson);
  const retellStarted = session.left.some((message) => message.who === "听众");
  const enoughPractice = session.left.filter((message) => message.who === "我").length >= 2;
  const messages = useMemo(() => [...session.left, ...session.right].sort((a, b) => (a.id ?? 0) - (b.id ?? 0)),
    [session.left, session.right]);

  function updateSession(update: (current: Session) => Session) {
    save((space) => ({ ...space, sessions: space.sessions.map((row) => row.id === session.id ? update(row) : row) }));
  }

  async function run(work: (signal: AbortSignal) => Promise<void>) {
    if (operation.current) return;
    const ctrl = new AbortController();
    operation.current = ctrl;
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await work(ctrl.signal);
    } catch (e) {
      if (!ctrl.signal.aborted && !modelRequired(e)) setError((e as Error).message);
    } finally {
      operation.current = null;
      if (!ctrl.signal.aborted) {
        setBusy(false);
        setPending(null);
      }
    }
  }

  async function loadHistory(signal: AbortSignal) {
    const history = await sessionHistory(session.id, signal);
    if (!signal.aborted) updateSession((current) => ({ ...current, ...history }));
    return history;
  }

  function restore() {
    return run(async (signal) => {
      const history = await loadHistory(signal);
      if (signal.aborted) return;
      if (!history.closed && !history.right.some((message) => message.lesson)) {
        setPending({ kind: "lesson", text: "" });
        const { reply } = await stream(session.id, 1, "", signal, (text) => setPending({ kind: "lesson", text }), "lesson");
        if (signal.aborted) return;
        updateSession((current) => ({ ...current, right: [...current.right, reply] }));
      }
      if (history.left.some((message) => message.who === "听众")) setMode(0);
      setReady(true);
    });
  }

  useEffect(() => {
    void restore();
    return () => operation.current?.abort();
  }, [session.id]);

  useEffect(() => {
    if (followBottom.current && (messages.length > 1 || pending?.prompt || pending?.kind === "opening")) {
      feed.current?.scrollTo(0, feed.current.scrollHeight);
    }
  }, [messages, pending]);

  function chooseMode(side: 0 | 1) {
    if (side === 1 || retellStarted) {
      setMode(side);
      field.current?.focus();
      return;
    }
    void run(async (signal) => {
      followBottom.current = true;
      setPending({ kind: "opening", text: "" });
      const { reply } = await stream(session.id, 0, "", signal, () => {}, true);
      if (signal.aborted) return;
      updateSession((current) => current.left.some((m) => m.id === reply.id)
        ? current : { ...current, left: [...current.left, reply] });
      setMode(0);
      requestAnimationFrame(() => field.current?.focus());
    });
  }

  function writeDraft(text: string) {
    updateSession((current) => {
      const drafts: Session["drafts"] = [...current.drafts];
      drafts[mode] = text;
      return { ...current, drafts };
    });
  }

  function send() {
    const text = session.drafts[mode].trim();
    if (!text || busy || !ready || !lessonReady || session.closed) return;
    const side = mode;
    const window = side ? "right" : "left";
    const kind = side ? "ask" : "retell";
    void run(async (signal) => {
      followBottom.current = true;
      setPending({ kind, prompt: text, text: "" });
      try {
        const { scores } = await stream(session.id, side, text, signal,
          (preview) => setPending({ kind, prompt: text, text: preview }));
        if (signal.aborted) return;
        if (scores.size) save((space) => ({ ...space, courses: space.courses.map((doc) => doc.id !== course.id ? doc : {
          ...doc, nodes: doc.nodes.map((node) => ({ ...node, ...scores.get(node.id) })),
        }) }));
      } catch (e) {
        if (signal.aborted || (e instanceof ApiError && e.code === "model_required")) throw e;
        const history = await loadHistory(signal).catch(() => null);
        if (signal.aborted) return;
        if (!history) {
          setReady(false);
          throw Error((e as Error).message + " 请先恢复会话。");
        }
        const lastId = session[window].at(-1)?.id ?? 0;
        if (!history[window].some((m) => (m.id ?? 0) > lastId && m.who === "我" && m.text === text)) throw e;
        writeDraft("");
        if (!side) {
          const memory = await refreshMemory(course, signal);
          if (!signal.aborted) save((space) => ({ ...space,
            courses: space.courses.map((doc) => doc.id === course.id ? memory.course : doc),
            logs: [...space.logs.filter((log) => log.doc !== course.id), ...memory.logs],
          }));
        }
        setNotice("已恢复此轮的已保存消息。");
        return;
      }
      writeDraft("");
      try {
        await loadHistory(signal);
      } catch (e) {
        if (signal.aborted) return;
        setReady(false);
        throw Error("本轮已保存。请恢复会话后查看回复。");
      }
    });
  }

  function finish() {
    void run(async (signal) => {
      const report = await remoteReport(session.id, course.id, course.title, "POST", signal);
      if (signal.aborted) return;
      save((space) => ({ ...space,
        sessions: space.sessions.map((row) => row.id === session.id ? { ...row, closed: true } : row),
        reports: [report, ...space.reports.filter((row) => row.id !== report.id)],
      }));
      showReport(report.id);
    });
  }

  const disabled = busy || !ready || !lessonReady || session.closed;
  const inputLabel = mode ? "向 AI 老师提问" : "费曼复述内容";
  const pendingRole = pending?.kind === "opening" || pending?.kind === "retell" ? "听众" : "百科";

  return (
    <section className="guided-chat" aria-label="AI 讲解会话">
      <header className="chat-header">
        <div className="chat-heading">
          <small><BookOpen aria-hidden="true" /> {course.title}</small>
          <h1>{target?.name || "AI 讲解"}</h1>
        </div>
        <div className="chat-actions">
          {historyOptions.length > 1 && <select aria-label="切换讲解会话" value={session.id} onChange={(e) => select(e.target.value)}>
            {historyOptions.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}
          </select>}
          <button onClick={graph}><Workflow aria-hidden="true" /> 选知识点</button>
          {enoughPractice && !session.closed && <button disabled={disabled} onClick={finish}>生成报告</button>}
        </div>
      </header>

      <div className="chat-feed" tabIndex={0} aria-label="讲解消息" ref={feed} onScroll={(e) => {
        const area = e.currentTarget;
        followBottom.current = area.scrollHeight - area.scrollTop - area.clientHeight < 96;
      }}>
        <div className="chat-thread">
          {!messages.length && !pending && busy && <p className="chat-system" role="status">正在恢复讲解会话…</p>}
          {messages.map((message) => <ChatMessage key={message.id} message={message} />)}
          {pending?.prompt && <ChatMessage message={{ who: "我", text: pending.prompt }} />}
          {pending && (
            <div aria-busy="true">
              {pending.text
                ? <ChatMessage message={{ who: pendingRole, text: pending.text }} />
                : <p className="chat-thinking" role="status">{pending.kind === "lesson" ? "正在准备入门讲解…" : "正在回复…"}</p>}
            </div>
          )}
        </div>
      </div>

      <div className="chat-footer">
        {error && <p className="chat-error" role="alert">{error}</p>}
        {notice && <p className="chat-system" role="status">{notice}</p>}
        {!ready && !busy && <button onClick={restore}>{lessonReady ? "恢复会话" : "重试讲解"}</button>}
        {session.closed ? <p className="chat-system">会话已结束</p> : (
          <form className="chat-composer" onSubmit={(event) => { event.preventDefault(); send(); }}>
            <textarea ref={field} aria-label={inputLabel} placeholder={mode ? "关于这一课，你还想问什么？" : "用自己的话讲一讲…"}
              value={pending?.prompt ? "" : session.drafts[mode]} rows={2} readOnly={disabled}
              onChange={(event) => writeDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter" || event.shiftKey || event.altKey || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
                event.preventDefault();
                if (!event.repeat) send();
              }} />
            <div className="chat-composer-tools">
              <div className="chat-modes" role="group" aria-label="发言方式">
                <button type="button" aria-pressed={mode === 1} disabled={disabled} onClick={() => chooseMode(1)}>提问</button>
                <button type="button" aria-pressed={mode === 0} disabled={disabled} onClick={() => chooseMode(0)}>
                  {retellStarted ? "费曼复述" : "开始费曼复述"}
                </button>
                {mode === 0 && <select value={session.role} aria-label="听众角色" disabled={disabled} onChange={(e) => {
                  const role = e.target.value;
                  void run(async (signal) => {
                    await setPersona(session.id, role);
                    if (!signal.aborted) await loadHistory(signal);
                  });
                }}>
                  {roles.map((role) => <option key={role}>{role}</option>)}
                </select>}
              </div>
              <button className="primary chat-send" type="submit" aria-label="发送" disabled={disabled || !session.drafts[mode].trim()}>
                <ArrowUp aria-hidden="true" />
              </button>
            </div>
          </form>
        )}
      </div>
    </section>
  );
}
