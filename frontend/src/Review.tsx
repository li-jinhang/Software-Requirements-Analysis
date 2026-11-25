import { useEffect, useRef, useState } from "react";
import { Clock, Layers3, RotateCcw } from "lucide-react";
import { reviews } from "./api";
import { CourseText } from "./LearningMessage";
import type { Course, ReviewLists } from "./model";
import "./review.css";

const formatTime = new Intl.DateTimeFormat("zh-CN", {
  month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
});

function ReviewDeck({ course, assess }: { course: Course; assess: () => void }) {
  const [queue, setQueue] = useState<ReviewLists | null>(null);
  const [scope, setScope] = useState<"due" | "all">("due");
  const [selected, setSelected] = useState<number>();
  const [revealed, setRevealed] = useState<number>();
  const [handled, setHandled] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const request = useRef<AbortController | null>(null);

  async function update(action: (signal: AbortSignal) => Promise<ReviewLists>, handledId?: number, message = "") {
    if (request.current) return;
    const ctrl = new AbortController();
    request.current = ctrl;
    setLoading(true);
    setError("");
    setNotice("");
    try {
      const data = await action(ctrl.signal);
      if (ctrl.signal.aborted) return;
      setQueue(data);
      if (handledId !== undefined) {
        setHandled((ids) => [...ids, handledId]);
        setSelected(undefined);
        setRevealed(undefined);
      }
      setNotice(message);
    } catch (e) {
      if (!ctrl.signal.aborted) setError((e as Error).message);
    } finally {
      request.current = null;
      if (!ctrl.signal.aborted) setLoading(false);
    }
  }

  function reload() {
    void update((signal) => reviews.get(course.id, signal));
  }

  useEffect(() => {
    reload();
    return () => request.current?.abort();
  }, [course.id]);

  const due = queue?.due_items ?? [];
  const all = queue?.all_reviewable_items ?? [];
  const cards = (scope === "due" ? due : all).filter((card) => !handled.includes(card.id));
  const card = cards.find((item) => item.id === selected) || cards[0];
  const answerShown = card && revealed === card.id;
  const dueNow = card && due.some((item) => item.id === card.id);
  const date = card?.next_review_at;
  const utcDate = date && (date.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(date) ? date : date + "Z");
  const points = all.filter((item) => item.node_id === card?.node_id).sort((a, b) => a.item_order - b.item_order);

  function record(outcome: "remembered" | "again") {
    if (!card || !answerShown) return;
    void update((signal) => reviews.record(course.id, card, outcome, signal), card.id,
      outcome === "remembered" ? "已记录，复习计划已更新。" : "已安排 10 分钟后再复习。" );
  }

  return (
    <div className="review-deck">
      {queue && (
        <div className="review-toolbar">
          <div className="tools" role="group" aria-label="复习范围">
            <button aria-pressed={scope === "due"} onClick={() => setScope("due")}><Clock aria-hidden="true" /> 到期 {due.length}</button>
            <button aria-pressed={scope === "all"} onClick={() => setScope("all")}><Layers3 aria-hidden="true" /> 全部 {all.length}</button>
          </div>
          {cards.length > 1 && (
            <select aria-label="选择复习条目" value={card?.id} onChange={(e) => {
              setSelected(Number(e.target.value));
              setRevealed(undefined);
            }}>
              {cards.map((item, index) => <option key={item.id} value={item.id}>{item.section_title} · 卡片 {index + 1}</option>)}
            </select>
          )}
        </div>
      )}
      {error && <div className="review-feedback"><p role="alert">{error}</p><button onClick={reload}>重新加载</button></div>}
      {notice && <p className="review-feedback" role="status">{notice}</p>}
      {!queue && !error && <p role="status">正在加载复习计划…</p>}
      {card ? (
        <article className="review-card" aria-busy={loading}>
          <header>
            <small>{dueNow ? "已到期" : "提前复习"}</small>
            <span className="review-mastery">掌握度 {card.mastery_score}%</span>
          </header>
          <h2>{card.section_title}</h2>
          <p>{points.length > 1 ? `回忆这个知识点的要点，再核对第 ${points.findIndex((item) => item.id === card.id) + 1} 条。` : "先用自己的话回忆这个知识点，再查看参考答案。"}</p>
          {answerShown && (
            <div className="review-answer" id="current-review-answer">
              <small>参考答案</small>
              <CourseText text={card.statement} />
            </div>
          )}
          <footer>
            <div className="tools">
              {answerShown ? (
                <>
                  <button className="primary" disabled={loading} onClick={() => record("remembered")}>记得</button>
                  <button disabled={loading} onClick={() => record("again")}>需巩固</button>
                </>
              ) : <button className="primary" disabled={loading} onClick={() => setRevealed(card.id)}>查看答案</button>}
              {dueNow && <button className="quiet" disabled={loading} onClick={() => void update(
                (signal) => reviews.skip(course.id, card.id, signal), card.id, "已延到明天。",
              )}>明天再复习</button>}
            </div>
            {utcDate && <small>下次复习 <time dateTime={utcDate}>{formatTime.format(new Date(utcDate))}</time></small>}
          </footer>
        </article>
      ) : queue && (
        <div className="box empty review-empty">
          <RotateCcw aria-hidden="true" />
          <h2>{!all.length ? "还没有可复习的知识点" : scope === "due" ? "当前没有待复习的到期卡片" : "本轮复习已完成"}</h2>
          {!all.length ? (
            <><p>通过费曼考核达到 80% 掌握度后，相应的已掌握条目会进入复习计划。</p><button onClick={assess}>开始费曼练习</button></>
          ) : scope === "due" && all.some((item) => !handled.includes(item.id)) ? (
            <button onClick={() => setScope("all")}>查看全部复习卡片</button>
          ) : <p>到期提醒会按新的复习计划更新。</p>}
        </div>
      )}
    </div>
  );
}

export default function Review({ courses, course, online, select, assess, account, library }: {
  courses: Course[];
  course?: Course;
  online: boolean;
  select: (id: string) => void;
  assess: (course: Course) => void;
  account: () => void;
  library: () => void;
}) {
  const eligibleCourses = courses.filter((item) => item.nodes.length && (!item.parseStatus || item.parseStatus === "completed"));
  const current = eligibleCourses.find((item) => item.id === course?.id) || eligibleCourses[0];
  return (
    <>
      <div className="pagehead">
        <div><h1>复习</h1><p>回忆已掌握的知识点，按记忆情况安排下一次复习。</p></div>
        {online && current && <select aria-label="复习资料" value={current.id} onChange={(e) => select(e.target.value)}>
          {eligibleCourses.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select>}
      </div>
      {!online ? <div className="box empty"><p>选择本机账号，查看自己的复习计划。</p><button onClick={account}>选择本机账号</button></div>
        : current ? <ReviewDeck key={current.id} course={current} assess={() => assess(current)} />
          : <div className="box empty"><p>先在资料库添加学习资料。</p><button onClick={library}>前往资料库</button></div>}
    </>
  );
}
