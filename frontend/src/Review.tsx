import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Clock,
  FileText,
  Layers3,
  ListChecks,
  RotateCcw,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { reviews } from "./api";
import { WorkspaceToolbar } from "./ui";
import { CourseText } from "./LearningMessage";
import type {
  Concept,
  Course,
  ReviewCard,
  ReviewChallenge,
  ReviewLists,
  ReviewVerifyResult,
} from "./model";
import { MasteryMeter } from "./MasteryProgress";
import "./review.css";

const formatTime = new Intl.DateTimeFormat("zh-CN", {
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const STAGE_RETENTION = [64, 75, 84, 91, 96, 98];

function toUtcDate(date?: string | null) {
  if (!date) return "";
  return date.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(date) ? date : date + "Z";
}

function summarizeStatement(text: string) {
  const cleaned = text
    .replace(/\$\$?[\s\S]*?\$\$?/g, " ")
    .replace(/[#>*`]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return "认知核验条目";
  const first = cleaned.split(/[。！？!?]/)[0]?.trim() || cleaned;
  return first.length > 34 ? first.slice(0, 34) + "…" : first;
}

function stageDays(stage: number) {
  if (stage <= 1) return 1;
  if (stage === 2) return 3;
  if (stage === 3) return 7;
  return 15;
}

function nextDaysFor(card: ReviewCard) {
  if (card.next_remembered_days) return card.next_remembered_days;
  const nextStage = Math.min(5, (card.review_stage || 0) + 1);
  return stageDays(nextStage);
}

function localCue(card: ReviewCard) {
  const parts: string[] = [];
  if (card.key_criteria?.trim()) parts.push(`核验判据：${card.key_criteria.trim()}`);
  if (card.issue_note?.trim()) parts.push(`曾有盲区：留意「${card.issue_note.trim()}」`);
  if (!parts.length) {
    parts.push(`试着用自己的话讲清「${card.concept_name || card.section_title}」的原理机制与边界条件。`);
  }
  return parts.join(" · ");
}

function ReviewSteps() {
  return (
    <ol className="review-steps" aria-label="复习三步">
      <li>
        <BookOpen aria-hidden="true" />
        <strong>听AI讲或讲给AI</strong>
        <small>用自己的话讲清概念</small>
      </li>
      <li>
        <ListChecks aria-hidden="true" />
        <strong>掌握度 ≥80%</strong>
        <small>已掌握条目自动进入复习库</small>
      </li>
      <li>
        <Clock aria-hidden="true" />
        <strong>间隔回忆巩固</strong>
        <small>按艾宾浩斯间隔复习</small>
      </li>
    </ol>
  );
}

function EbbinghausStrip({
  all,
  dueRemaining,
  dueDoneCount,
}: {
  all: ReviewCard[];
  dueRemaining: number;
  dueDoneCount: number;
}) {
  const stages = useMemo(() => {
    let s1 = 0;
    let s2 = 0;
    let s3 = 0;
    let s4 = 0;
    let retentionSum = 0;
    for (const item of all) {
      const st = item.review_stage ?? 0;
      if (st <= 1) s1 += 1;
      else if (st === 2) s2 += 1;
      else if (st === 3) s3 += 1;
      else s4 += 1;
      const base = STAGE_RETENTION[Math.min(5, Math.max(0, st))];
      retentionSum += Math.round(base * 0.7 + item.mastery_score * 0.3);
    }
    return {
      buckets: [
        { label: "第1阶 1d", count: s1 },
        { label: "第2阶 3d", count: s2 },
        { label: "第3阶 7d", count: s3 },
        { label: "长期 15d", count: s4 },
      ],
      consolidation: all.length ? Math.round(retentionSum / all.length) : 0,
    };
  }, [all]);

  const dueTotal = dueRemaining + dueDoneCount;
  const dueProgress = dueTotal ? Math.round((dueDoneCount / dueTotal) * 100) : 100;

  return (
    <section className="ebbinghaus-bar" aria-label="艾宾浩斯记忆阶梯概览">
      <div className="ebbinghaus-stages">
        {stages.buckets.map((b) => (
          <div className="ebbinghaus-pill" key={b.label}>
            <span>{b.label}</span>
            <strong>{b.count}</strong>
          </div>
        ))}
      </div>
      <div className="ebbinghaus-progress">
        <div className="ebbinghaus-metrics">
          <span>
            预计巩固度 <b>{stages.consolidation}%</b>
          </span>
          <span>
            今日到期 <b>{dueDoneCount}/{dueTotal || 0}</b>
          </span>
        </div>
        <div className="ebbinghaus-track" role="progressbar" aria-label="今日到期复习进度" aria-valuenow={dueProgress} aria-valuemin={0} aria-valuemax={100}>
          <span style={{ width: `${dueProgress}%` }} />
        </div>
      </div>
    </section>
  );
}

function ReviewDeck({
  course,
  assess,
  openGraph,
}: {
  course: Course;
  assess: (concept?: Concept) => void;
  openGraph?: () => void;
}) {
  const [queue, setQueue] = useState<ReviewLists | null>(null);
  const [scope, setScope] = useState<"due" | "all">("due");
  const [selected, setSelected] = useState<number>();
  const [revealed, setRevealed] = useState<number>();
  const [handled, setHandled] = useState<number[]>([]);
  const [dueHandled, setDueHandled] = useState<number[]>([]);
  const [sessionStats, setSessionStats] = useState({ remembered: 0, again: 0, skipped: 0, promoted: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [challenges, setChallenges] = useState<Record<number, ReviewChallenge>>({});
  const [verifies, setVerifies] = useState<Record<number, ReviewVerifyResult>>({});
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [challengingId, setChallengingId] = useState<number>();
  const [verifyingId, setVerifyingId] = useState<number>();

  const request = useRef<AbortController | null>(null);

  async function update(
    action: (signal: AbortSignal) => Promise<ReviewLists>,
    handledCard?: { id: number; wasDue: boolean; kind: "remembered" | "again" | "skip"; promoted?: boolean },
    message = "",
  ) {
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
      if (handledCard !== undefined) {
        setHandled((ids) => (ids.includes(handledCard.id) ? ids : [...ids, handledCard.id]));
        if (handledCard.wasDue) {
          setDueHandled((ids) => (ids.includes(handledCard.id) ? ids : [...ids, handledCard.id]));
        }
        setSessionStats((prev) => ({
          remembered: prev.remembered + (handledCard.kind === "remembered" ? 1 : 0),
          again: prev.again + (handledCard.kind === "again" ? 1 : 0),
          skipped: prev.skipped + (handledCard.kind === "skip" ? 1 : 0),
          promoted: prev.promoted + (handledCard.promoted ? 1 : 0),
        }));
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
  const cards = (scope === "due" ? due : all).filter((item) => !handled.includes(item.id));
  const card = cards.find((item) => item.id === selected) || cards[0];
  const answerShown = Boolean(card && revealed === card.id);
  const dueNow = Boolean(card && due.some((item) => item.id === card.id));
  const utcDate = toUtcDate(card?.next_review_at);
  const points = card
    ? all.filter((item) => item.node_id === card.node_id).sort((a, b) => a.item_order - b.item_order)
    : [];
  const linkedConcept = card
    ? course.nodes.find((n) => Number(n.id) === card.node_id || n.name === card.concept_name)
    : undefined;

  const activeChallenge = card ? challenges[card.id] : undefined;
  const activeVerify = card ? verifies[card.id] : undefined;
  const draftAnswer = card ? drafts[card.id] ?? "" : "";
  const nextDays = card ? nextDaysFor(card) : 1;

  function record(outcome: "remembered" | "again") {
    if (!card || !answerShown) return;
    const promoted = outcome === "remembered" && (card.review_stage ?? 0) < 5;
    void update(
      (signal) => reviews.record(course.id, card, outcome, signal),
      { id: card.id, wasDue: dueNow, kind: outcome, promoted },
      outcome === "remembered" ? "已记录，复习计划已更新。" : "已安排 10 分钟后再复习。",
    );
  }

  async function requestChallenge() {
    if (!card || challengingId === card.id) return;
    const targetId = card.id;
    setChallengingId(targetId);
    setError("");
    try {
      const res = await reviews.aiChallenge(course.id, targetId);
      setChallenges((prev) => ({ ...prev, [targetId]: res }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setChallengingId((curr) => (curr === targetId ? undefined : curr));
    }
  }

  async function submitQuickVerify(event: React.FormEvent) {
    event.preventDefault();
    if (!card || verifyingId === card.id) return;
    const answer = draftAnswer.trim();
    if (!answer) return;
    const targetId = card.id;
    setVerifyingId(targetId);
    setError("");
    try {
      const res = await reviews.aiVerify(course.id, targetId, answer);
      setVerifies((prev) => ({ ...prev, [targetId]: res }));
      setRevealed(targetId);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setVerifyingId((curr) => (curr === targetId ? undefined : curr));
    }
  }

  const closestNodes = [...course.nodes].sort((a, b) => b.score - a.score).slice(0, 6);
  const remainingUnreviewedAll = all.filter((item) => !handled.includes(item.id));
  const earliestNextUtc = useMemo(() => {
    const sorted = [...all]
      .map((item) => toUtcDate(item.next_review_at))
      .filter(Boolean)
      .sort((a, b) => Date.parse(a) - Date.parse(b));
    return sorted[0] || "";
  }, [all]);

  const hasTraceArchive = Boolean(
    card &&
      (card.user_evidence?.trim() ||
        card.issue_note?.trim() ||
        card.user_note?.trim() ||
        (card.source_snippets && card.source_snippets.length > 0)),
  );
  const needsDeepReview = Boolean(
    activeVerify && (activeVerify.verdict !== "pass" || activeVerify.missing_points.length > 0),
  );

  return (
    <div className="review-layout">
      {queue && (
        <WorkspaceToolbar>
          <div className="review-toolbar toolbar-actions">
            <div className="tools" role="group" aria-label="复习范围">
              <button aria-pressed={scope === "due"} onClick={() => setScope("due")}>
                <Clock aria-hidden="true" /> 到期 {due.length}
              </button>
              <button aria-pressed={scope === "all"} onClick={() => setScope("all")}>
                <Layers3 aria-hidden="true" /> 全部 {all.length}
              </button>
            </div>
            {cards.length > 1 && (
              <select
                aria-label="选择复习条目"
                value={card?.id}
                onChange={(e) => {
                  setSelected(Number(e.target.value));
                  setRevealed(undefined);
                }}
              >
                {cards.map((item, index) => (
                  <option key={item.id} value={item.id}>
                    {(item.concept_name || item.section_title)} · 卡片 {index + 1}
                  </option>
                ))}
              </select>
            )}
          </div>
        </WorkspaceToolbar>
      )}
      <aside className="review-queue-rail" aria-label="复习队列与解锁进度">
        <div className="review-rail-head">
          <h3>{cards.length ? "复习队列" : "待解锁进度"}</h3>
          {queue && (
            <div className="review-rail-stats">
              <span>
                到期 <b>{due.length}</b>
              </span>
              <span>
                已解锁 <b>{all.length}</b>
              </span>
              <span>
                已完成 <b>{handled.length}</b>
              </span>
            </div>
          )}
        </div>
        {cards.length ? (
          <ul className="review-queue-list">
            {cards.map((item, index) => {
              const stage = item.review_stage ?? 0;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    aria-current={item.id === card?.id ? "true" : undefined}
                    onClick={() => {
                      setSelected(item.id);
                      setRevealed(undefined);
                    }}
                  >
                    <span className="review-queue-idx">{String(index + 1).padStart(2, "0")}</span>
                    <div className="review-queue-body">
                      <strong>{summarizeStatement(item.statement)}</strong>
                      <div className="review-queue-meta">
                        <span className="review-node-badge">{item.concept_name || item.section_title}</span>
                        <small>第 {stage} 阶 · {stageDays(stage)}天</small>
                        {item.issue_note?.trim() && <span className="review-issue-flag">曾有盲区</span>}
                      </div>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <div className="review-unlock-list">
            <p className="meta">知识点掌握度达到 80% 后将自动进入艾宾浩斯复习队列。</p>
            {closestNodes.map((n) => (
              <button type="button" className="review-unlock-item" key={n.id} onClick={() => assess(n)}>
                <div className="row between">
                  <strong>{n.name}</strong>
                  <span>{n.score}%</span>
                </div>
                <MasteryMeter score={n.score} label={n.name + "掌握度"} compact motion="focus" />
                <small>
                  {n.score >= 80 ? "去完成认知条目" : `还差 ${80 - n.score}% 解锁`} <ArrowRight aria-hidden="true" />
                </small>
              </button>
            ))}
          </div>
        )}
      </aside>
      <div className="review-stage">
        {queue && all.length > 0 && (
          <EbbinghausStrip all={all} dueRemaining={due.length} dueDoneCount={dueHandled.length} />
        )}
        {error && (
          <div className="review-feedback">
            <p role="alert">{error}</p>
            <button onClick={reload}>重新加载</button>
          </div>
        )}
        {notice && (
          <p className="review-feedback" role="status">
            {notice}
          </p>
        )}
        {!queue && !error && <p role="status">正在加载复习计划…</p>}
        {card ? (
          <article className="review-card" aria-busy={loading}>
            <header>
              <div className="review-card-tags">
                <small>{dueNow ? "已到期" : "提前复习"}</small>
                <span className="review-section-tag">{card.section_title}</span>
                <span className="review-stage-chip">
                  第 {card.review_stage ?? 0} 阶 · 记得推迟 {nextDays} 天
                </span>
              </div>
              <span className="review-mastery">掌握度 {card.mastery_score}%</span>
            </header>

            <div className="review-concept-head">
              <h2>{card.concept_name || card.section_title}</h2>
              <div className="review-meter-row">
                <MasteryMeter
                  score={card.mastery_score}
                  label={(card.concept_name || card.section_title) + "掌握度"}
                  compact
                  motion="focus"
                />
                <small>
                  {points.length > 1
                    ? `条目 ${points.findIndex((item) => item.id === card.id) + 1} / ${points.length}`
                    : "核心认知条目"}
                </small>
              </div>
            </div>

            <div className="review-cue-box">
              <div className="review-cue-head">
                <span className="review-cue-title">
                  <Sparkles aria-hidden="true" />
                  {activeChallenge?.available && activeChallenge.challenge ? "AI 变式情景追问" : "启发式回忆线索"}
                </span>
                <button
                  type="button"
                  className="quiet review-challenge-btn"
                  disabled={loading || challengingId === card.id}
                  onClick={() => void requestChallenge()}
                >
                  <Sparkles aria-hidden="true" />
                  {challengingId === card.id ? "生成中…" : activeChallenge ? "换一道追问" : "AI 出变式追问"}
                </button>
              </div>
              {activeChallenge?.challenge ? (
                <div className="review-challenge-body">
                  <p className="review-challenge-question">{activeChallenge.challenge}</p>
                  <p className="review-cue-text">线索：{activeChallenge.hint}</p>
                </div>
              ) : (
                <p className="review-cue-text">
                  {activeChallenge?.hint || localCue(card)}
                </p>
              )}
              {activeChallenge?.notice && (
                <small className="review-cue-notice">{activeChallenge.notice}</small>
              )}
            </div>

            <form className="review-quick-form" onSubmit={(e) => void submitQuickVerify(e)}>
              <input
                type="text"
                aria-label="一句话费曼速测"
                placeholder="用一句话复述核心逻辑，回车或点击 AI 核验…"
                value={draftAnswer}
                disabled={loading || verifyingId === card.id}
                onChange={(e) => {
                  const val = e.target.value;
                  setDrafts((prev) => ({ ...prev, [card.id]: val }));
                }}
              />
              <button
                type="submit"
                disabled={loading || verifyingId === card.id || !draftAnswer.trim()}
              >
                {verifyingId === card.id ? "核验中…" : "AI 核验"}
              </button>
            </form>

            {answerShown && (
              <div className="review-answer" id="current-review-answer">
                {activeVerify && (
                  <div className={"review-verify-card verdict-" + activeVerify.verdict}>
                    <div className="review-verify-head">
                      <strong>
                        {activeVerify.verdict === "pass" ? (
                          <CheckCircle2 aria-hidden="true" />
                        ) : (
                          <TriangleAlert aria-hidden="true" />
                        )}
                        {!activeVerify.available
                          ? "本地核验提示"
                          : activeVerify.verdict === "pass"
                            ? "复述通过 · 建议点「记得」"
                            : activeVerify.verdict === "partial"
                              ? "部分命中 · 建议强化巩固"
                              : "存在偏差 · 建议再来一次"}
                      </strong>
                      {activeVerify.available && (
                        <span className="review-score-hint">
                          推荐：{activeVerify.score_hint === "remembered" ? `记得（+${nextDays}d）` : "需巩固（10m）"}
                        </span>
                      )}
                    </div>
                    <p>{activeVerify.feedback}</p>
                    {(activeVerify.matched_criteria.length > 0 || activeVerify.missing_points.length > 0) && (
                      <div className="review-verify-points">
                        {activeVerify.matched_criteria.map((pt, i) => (
                          <span className="verify-chip matched" key={"m" + i}>
                            ✓ {pt}
                          </span>
                        ))}
                        {activeVerify.missing_points.map((pt, i) => (
                          <span className="verify-chip missing" key={"x" + i}>
                            缺：{pt}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                <div className="review-standard-block">
                  <small>标准核心表述</small>
                  <CourseText text={card.statement} />
                  {card.key_criteria?.trim() && (
                    <div className="review-criteria">
                      <span>核验判据</span>
                      <p>{card.key_criteria}</p>
                    </div>
                  )}
                </div>

                {hasTraceArchive && (
                  <section className="review-trace-panel" aria-label="智溯记忆档案">
                    <div className="review-trace-head">
                      <FileText aria-hidden="true" />
                      <span>智溯记忆档案</span>
                    </div>
                    <div className="review-trace-grid">
                      {card.user_evidence?.trim() && (
                        <div className="review-trace-item">
                          <small>你上次掌握时的原话</small>
                          <p>{card.user_evidence}</p>
                        </div>
                      )}
                      {(card.issue_note?.trim() || card.user_note?.trim()) && (
                        <div className="review-trace-item">
                          <small>历史踩坑与个人笔记</small>
                          {card.issue_note?.trim() && <p className="trace-issue">纠偏记录：{card.issue_note}</p>}
                          {card.user_note?.trim() && <p>个人笔记：{card.user_note}</p>}
                        </div>
                      )}
                      {card.source_snippets && card.source_snippets.length > 0 && (
                        <div className="review-trace-item trace-snippets">
                          <small>课程原文依据</small>
                          {card.source_snippets.map((sn, i) => (
                            <blockquote key={i}>
                              <cite>
                                {sn.doc_title || course.title} · 片段 #{sn.chunk_index + 1}
                              </cite>
                              <span>{sn.snippet}</span>
                            </blockquote>
                          ))}
                        </div>
                      )}
                    </div>
                  </section>
                )}
              </div>
            )}

            <footer>
              <div className="tools">
                {answerShown ? (
                  <>
                    <button
                      type="button"
                      className="primary"
                      aria-label="记得"
                      title={`记得 · 推迟至 ${nextDays} 天后`}
                      disabled={loading}
                      onClick={() => record("remembered")}
                    >
                      记得 · 推迟至 {nextDays} 天后
                    </button>
                    <button
                      type="button"
                      className="review-again"
                      aria-label="需巩固"
                      title="再来一次 · 10 分钟后强化"
                      disabled={loading}
                      onClick={() => record("again")}
                    >
                      再来一次 · 10 分钟后强化
                    </button>
                  </>
                ) : (
                  <button
                    type="button"
                    className="primary"
                    disabled={loading}
                    onClick={() => setRevealed(card.id)}
                  >
                    查看答案
                  </button>
                )}
                {dueNow && (
                  <button
                    type="button"
                    className="quiet"
                    aria-label="明天再复习"
                    title="跳过本次，顺延 1 天再复习"
                    disabled={loading}
                    onClick={() =>
                      void update(
                        (signal) => reviews.skip(course.id, card.id, signal),
                        { id: card.id, wasDue: true, kind: "skip" },
                        "已延到明天。",
                      )
                    }
                  >
                    跳过（顺延 1 天）
                  </button>
                )}
                {answerShown && needsDeepReview && (
                  <button
                    type="button"
                    className="quiet review-deep-link"
                    onClick={() => assess(linkedConcept)}
                  >
                    去双窗工作台深度补漏 →
                  </button>
                )}
              </div>
              {utcDate && (
                <small>
                  下次复习 <time dateTime={utcDate}>{formatTime.format(new Date(utcDate))}</time>
                </small>
              )}
            </footer>
          </article>
        ) : (
          queue && (
            <div className="box empty review-empty review-victory">
              <RotateCcw aria-hidden="true" />
              <h2>
                {!all.length
                  ? "还没有可复习的知识点"
                  : handled.length > 0
                    ? "今日记忆巩固战报"
                    : scope === "due"
                      ? "当前没有待复习的到期卡片"
                      : "本轮复习已完成"}
              </h2>
              {!all.length ? (
                <>
                  <ReviewSteps />
                  <button className="primary" onClick={() => assess()}>
                    开始费曼练习
                  </button>
                </>
              ) : (
                <>
                  {handled.length > 0 ? (
                    <div className="victory-metrics">
                      <div>
                        <span>本次巩固</span>
                        <strong>{handled.length} 条</strong>
                        <small>
                          记得 {sessionStats.remembered} · 强化 {sessionStats.again}
                        </small>
                      </div>
                      <div>
                        <span>记忆阶数跃升</span>
                        <strong>{sessionStats.promoted} 阶</strong>
                        <small>进入更长遗忘间隔</small>
                      </div>
                      <div>
                        <span>最早下次复习</span>
                        <strong>
                          {earliestNextUtc ? formatTime.format(new Date(earliestNextUtc)) : "待安排"}
                        </strong>
                        <small>已同步更新日程</small>
                      </div>
                    </div>
                  ) : (
                    <p>到期提醒会按新的复习计划更新。</p>
                  )}
                  <div className="tools" style={{ justifyContent: "center" }}>
                    {scope === "due" && remainingUnreviewedAll.length > 0 && (
                      <button className="primary" onClick={() => setScope("all")}>
                        查看全部复习卡片
                      </button>
                    )}
                    {openGraph && (
                      <button type="button" onClick={openGraph}>
                        前往知识图谱
                      </button>
                    )}
                    <button type="button" className="quiet" onClick={() => assess()}>
                      继续费曼练习
                    </button>
                  </div>
                </>
              )}
            </div>
          )
        )}
      </div>
    </div>
  );
}

export default function Review({
  courses,
  course,
  online,
  select,
  assess,
  openGraph,
  account,
  library,
}: {
  courses: Course[];
  course?: Course;
  online: boolean;
  select: (id: string) => void;
  assess: (course: Course, concept?: Concept) => void;
  openGraph?: (course: Course) => void;
  account: () => void;
  library: () => void;
}) {
  const eligibleCourses = courses.filter(
    (item) => item.nodes.length && (!item.parseStatus || item.parseStatus === "completed"),
  );
  const current = eligibleCourses.find((item) => item.id === course?.id) || eligibleCourses[0];
  return (
    <>
      <WorkspaceToolbar>
        {online && current && (
          <select aria-label="复习资料" value={current.id} onChange={(e) => select(e.target.value)}>
            {eligibleCourses.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </select>
        )}
      </WorkspaceToolbar>
      {!online ? (
        <div className="box empty review-empty">
          <h2>选择本机账号，查看自己的复习计划。</h2>
          <ReviewSteps />
          <button className="primary" onClick={account}>
            选择本机账号
          </button>
        </div>
      ) : current ? (
        <ReviewDeck
          key={current.id}
          course={current}
          assess={(concept) => assess(current, concept)}
          openGraph={openGraph ? () => openGraph(current) : undefined}
        />
      ) : (
        <div className="box empty review-empty">
          <h2>先在资料库添加学习资料。</h2>
          <ReviewSteps />
          <button className="primary" onClick={library}>
            前往资料库
          </button>
        </div>
      )}
    </>
  );
}
