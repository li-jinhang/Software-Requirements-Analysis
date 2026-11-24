import { useEffect, useState } from "react";
import { reviews } from "./api";
import { Dialog } from "./ui";
import type { CognitiveDoc, ReviewLists } from "./model";

type Props = {
  doc: string;
  rubric: CognitiveDoc | null;
  online: boolean;
  revision: number;
  busy: boolean;
  start: (ids: number[]) => Promise<void>;
  reportError: (message: string) => void;
};
const labels = {
  mastered: "已掌握",
  wrong: "理解偏差",
  partial: "待补全",
  untested: "待考核",
};

export function ReviewTools({
  doc,
  rubric,
  online,
  revision,
  busy,
  start,
  reportError,
}: Props) {
  const [open, setOpen] = useState(false);
  const [queue, setQueue] = useState<ReviewLists>({
    due_items: [],
    all_reviewable_items: [],
  });
  const [selected, setSelected] = useState<number[]>([]);
  const [saving, setSaving] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!online) return;
    const ctrl = new AbortController();
    reviews
      .get(doc, ctrl.signal)
      .then((data) => {
        if (!ctrl.signal.aborted) setQueue(data);
      })
      .catch((e) => {
        if (!ctrl.signal.aborted) reportError(e.message);
      });
    return () => ctrl.abort();
  }, [doc, online, revision, refresh, reportError]);
  async function change(action: () => Promise<ReviewLists>) {
    setSaving(true);
    try {
      setQueue(await action());
      setRefresh((n) => n + 1);
    } catch (e) {
      reportError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  const due = queue.due_items[0];
  const weak = queue.all_reviewable_items.filter(
    (i) => i.status === "wrong" || i.status === "partial",
  );
  return (
    <>
      <button
        onClick={() => {
          setSelected([]);
          setOpen(true);
        }}
      >
        自主复习
      </button>
      {online && (
        <button
          className="quiet"
          disabled={saving || busy}
          onClick={() => change(() => reviews.advance(doc))}
        >
          模拟 +1 天
        </button>
      )}
      {due && (
        <aside className="due-reminder" aria-label="记忆曲线复习提醒">
          <strong>记忆曲线复习提醒 · {queue.due_items.length} 条到期</strong>
          <span>
            {due.section_title} · {due.statement}
          </span>
          <div>
            <button disabled={saving || busy} onClick={() => start([due.id])}>
              跟他复习
            </button>
            <button
              disabled={saving || busy}
              onClick={() => change(() => reviews.skip(doc, due.id))}
            >
              跳过
            </button>
          </div>
        </aside>
      )}
      {open && (
        <Dialog drawer title="自主复习面板" close={() => setOpen(false)}>
          {!online && <p>登录在线学习账号可复习自己的认知条目。</p>}
          <div className="tools">
            <button
              disabled={busy || !weak.length}
              onClick={() => start(weak.map((i) => i.id))}
            >
              复习全部薄弱项
            </button>
            <button
              disabled={busy || !queue.all_reviewable_items.length}
              onClick={() => start(queue.all_reviewable_items.map((i) => i.id))}
            >
              复习全部已学项
            </button>
          </div>
          {rubric?.sections.map((g) => (
            <section key={g.title}>
              <h3>{g.title}</h3>
              {g.items.map((i) => (
                <label key={i.id} className="review-option">
                  <input
                    type="checkbox"
                    checked={selected.includes(i.id)}
                    onChange={(e) =>
                      setSelected((ids) =>
                        e.target.checked
                          ? [...ids, i.id]
                          : ids.filter((id) => id !== i.id),
                      )
                    }
                  />
                  <span>
                    {i.statement}
                    <small>
                      {labels[i.status]} · 阶段 {i.review_stage} · 已评估{" "}
                      {i.review_count} 次
                    </small>
                  </span>
                </label>
              ))}
            </section>
          ))}
          <button
            className="primary"
            disabled={busy || !selected.length}
            onClick={() => start(selected)}
          >
            开始复习选中项
          </button>
        </Dialog>
      )}
    </>
  );
}
