import { useEffect, useState } from "react";
import { spaces, write, userCount } from "./storage";
import { download, Space, mean } from "./model";
import {
  serverStats,
  serverExport,
  reviewRows,
  removeRow,
  ReviewRow,
  ReviewTable,
} from "./api";
import { Dialog } from "./ui";
const tables = ["文档", "节点", "日志", "报告"];
function rows(data: Record<string, Space>) {
  return Object.entries(data).flatMap(([owner, s]) => [
    ...s.courses.map((c) => ({
      owner,
      tab: 0,
      id: c.id,
      name: c.title,
      detail: c.subject,
    })),
    ...s.courses.flatMap((c) =>
      c.nodes.map((n) => ({
        owner,
        tab: 1,
        id: n.id,
        name: n.name,
        detail: n.score + "%",
      })),
    ),
    ...s.logs.map((l) => ({
      owner,
      tab: 2,
      id: l.id,
      name: l.detail,
      detail: l.resolved ? "已解决" : "待复讲",
    })),
    ...s.reports.map((r) => ({
      owner,
      tab: 3,
      id: r.id,
      name: r.title,
      detail: new Date(r.date).toLocaleString(),
    })),
  ]);
}
type Row = ReturnType<typeof rows>[number];
function clean(s: Space, r: Row) {
  if (r.tab === 0)
    return {
      ...s,
      courses: s.courses.filter((c) => c.id !== r.id),
      sessions: s.sessions.filter((v) => v.doc !== r.id),
      reports: s.reports.filter((v) => v.doc !== r.id),
      logs: s.logs.filter((v) => v.doc !== r.id),
    };
  if (r.tab === 1) {
    const c = s.courses.find((c) => c.nodes.some((n) => n.id === r.id))!;
    if (c.nodes.length === 1)
      throw Error("这是最后一个知识点，请改为清理整份文档。");
    return {
      ...s,
      courses: s.courses.map((d) =>
        d !== c
          ? d
          : {
              ...d,
              nodes: d.nodes.filter((n) => n.id !== r.id),
              links: d.links.filter((l) => l.from !== r.id && l.to !== r.id),
            },
      ),
      logs: s.logs.filter((w) => w.node !== r.id),
      sessions: s.sessions.map((v) =>
        v.focus !== r.id
          ? v
          : { ...v, focus: c.nodes.find((n) => n.id !== r.id)!.id },
      ),
    };
  }
  return r.tab === 2
    ? { ...s, logs: s.logs.filter((w) => w.id !== r.id) }
    : { ...s, reports: s.reports.filter((v) => v.id !== r.id) };
}
function ServerReview() {
  const names: ReviewTable[] = [
    "documents",
    "knowledge_nodes",
    "weakness_logs",
    "diagnostic_reports",
  ];
  const [tab, setTab] = useState(0),
    [query, setQuery] = useState(""),
    [page, setPage] = useState(1),
    [revision, setRevision] = useState(0),
    [result, setResult] = useState<{ items: ReviewRow[]; total: number }>({
      items: [],
      total: 0,
    }),
    [error, setError] = useState(""),
    [pending, setPending] = useState<ReviewRow>(),
    [busy, setBusy] = useState(false);
  const table = names[tab];
  useEffect(() => {
    const ctrl = new AbortController();
    setError("");
    setResult({ items: [], total: 0 });
    reviewRows(table, query, page, ctrl.signal)
      .then((r) => {
        if (!ctrl.signal.aborted) setResult(r);
      })
      .catch((e) => {
        if (!ctrl.signal.aborted) setError(e.message);
      });
    return () => ctrl.abort();
  }, [table, page, query, revision]);
  return (
    <section className="box">
      <h2>数据表审阅</h2>
      <div className="tools">
        {tables.map((v, i) => (
          <button
            key={v}
            aria-pressed={tab === i}
            onClick={() => {
              setPage(1);
              setTab(i);
            }}
          >
            {v}
          </button>
        ))}
        <input
          aria-label="搜索记录"
          placeholder="搜索记录…"
          value={query}
          onChange={(e) => {
            setPage(1);
            setQuery(e.target.value);
          }}
        />
        <button onClick={() => setRevision((v) => v + 1)}>刷新</button>
      </div>
      {result.items.map((r) => (
        <div key={r.id} className="record">
          <code>{r.id}</code>
          <strong>{r.name}</strong>
          <small>
            {r.status} {r.updated_at}
          </small>
          <button className="quiet record-clean" onClick={() => setPending(r)}>清理</button>
        </div>
      ))}
      <div className="tools">
        <small>共 {result.total} 条</small>
        <button disabled={page <= 1} onClick={() => setPage(page - 1)}>
          上一页
        </button>
        <button
          disabled={page * 10 >= result.total}
          onClick={() => setPage(page + 1)}
        >
          下一页
        </button>
      </div>
      <p role="alert">{error}</p>
      {pending && (
        <Dialog
          title="确认清理记录"
          close={() => {
            if (!busy) setPending(undefined);
          }}
        >
          <p>清理「{pending.name}」及关联记录？</p>
          <button
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await removeRow(table, pending.id);
                setPage(1);
                setRevision((v) => v + 1);
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setPending(undefined);
                setBusy(false);
              }
            }}
          >
            确认清理
          </button>
        </Dialog>
      )}
    </section>
  );
}
function OnlineAdmin() {
  const [stats, setStats] = useState<Record<string, unknown>>({}),
    [error, setError] = useState("");
  async function refresh() {
    try {
      setStats(await serverStats());
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    void refresh();
  }, []);
  async function exportFile(format: string) {
    try {
      download(
        "教学数据." + (format === "sqlite" ? "db" : format),
        await serverExport(format),
      );
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <>
      <div className="pagehead">
        <h1>后台数据中心</h1>
        <button onClick={refresh}>刷新</button>
      </div>
      <div className="stats adminstats">
        {[
          ["total_users", "用户"],
          ["total_docs", "文档"],
          ["total_nodes", "知识点"],
          ["total_sessions", "会话"],
          ["avg_mastery", "平均掌握度"],
        ].map(([key, name]) => (
          <div className="stat" key={key}>
            <span>{name}</span>
            <strong>{String(stats[key] ?? "—")}</strong>
          </div>
        ))}
      </div>
      <div className="box">
        <h2>高频薄弱知识点</h2>
        {Array.isArray(stats.top_weaknesses) &&
          stats.top_weaknesses.slice(0, 10).map((v, i) => (
            <p key={i}>
              {v.concept_name} · {v.count} 次
            </p>
          ))}
        <div className="tools">
          <button onClick={() => exportFile("json")}>导出全部 JSON</button>
          <button onClick={() => exportFile("sqlite")}>下载 SQLite</button>
          <a href={import.meta.env.VITE_BACKEND_URL + "/docs"}>
            开放接口文档 ↗
          </a>
        </div>
      </div>
      <ServerReview />
      <p role="alert">{error}</p>
    </>
  );
}
export default function Admin({ online }: { online: boolean }) {
  return <div className="admin-page">{online ? <OnlineAdmin /> : <LocalAdmin />}</div>;
}
function LocalAdmin() {
  const [data, setData] = useState(spaces),
    [tab, setTab] = useState(0),
    [q, setQ] = useState(""),
    [page, setPage] = useState(0),
    [pending, setPending] = useState<Row>(),
    [error, setError] = useState("");
  const records = rows(data),
    visible = records.filter((r) => r.tab === tab && r.name.includes(q)),
    all = Object.values(data),
    nodes = all.flatMap((s) => s.courses.flatMap((c) => c.nodes)),
    freq = new Map<string, number>();
  for (const s of all)
    for (const w of s.logs) {
      const k = nodes.find((n) => n.id === w.node)?.name || "已归档";
      freq.set(k, (freq.get(k) || 0) + 1);
    }
  async function sqlite() {
    try {
      const init = (await import("sql.js")).default;
      const SQL = await init({
        useOldExtensionFunctions: false,
        locateFile: () => wasm,
      });
      const db = new SQL.Database();
      try {
        db.run(
          "CREATE TABLE workspaces(owner TEXT PRIMARY KEY, contents TEXT NOT NULL)",
        );
        Object.entries(data).forEach(([id, s]) =>
          db.run("INSERT INTO workspaces VALUES (?,?)", [
            id,
            JSON.stringify(s),
          ]),
        );
        download(
          "教学数据.db",
          new Uint8Array(db.export()).buffer,
          "application/vnd.sqlite3",
        );
      } finally {
        db.close();
      }
    } catch (e) {
      setError(String(e));
    }
  }
  return (
    <>
      <div className="pagehead">
        <div>
          <h1>后台数据中心</h1>
          <p>本机演示空间的教学统计与记录维护。</p>
        </div>
      </div>
      <div className="tools">
        <button
          onClick={() =>
            download(
              "教学数据.json",
              JSON.stringify(data, null, 2),
              "application/json",
            )
          }
        >
          导出全部 JSON
        </button>
        <button onClick={sqlite}>下载 SQLite</button>
        {import.meta.env.VITE_BACKEND_URL ? (
          <a
            href={import.meta.env.VITE_BACKEND_URL + "/docs"}
            target="_blank"
            rel="noreferrer"
          >
            开放接口文档 ↗
          </a>
        ) : (
          <small>配置后端地址后可访问 Swagger 文档</small>
        )}
      </div>
      <div className="stats adminstats">
        {[
          ["用户", userCount()],
          ["文档", records.filter((r) => r.tab === 0).length],
          ["知识点", nodes.length],
          ["会话", all.reduce((a, s) => a + s.sessions.length, 0)],
          ["平均掌握度", mean(nodes.map((n) => n.score)) + "%"],
        ].map(([k, v]) => (
          <div className="stat" key={k}>
            <span>{k}</span>
            <strong>{v}</strong>
          </div>
        ))}
      </div>
      <div className="box">
        <h2>高频薄弱知识点</h2>
        {Array.from(freq)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 10)
          .map(([k, v]) => (
            <p key={k}>
              {k} · {v} 次
              <progress value={v} max={Math.max(...freq.values())} />
            </p>
          ))}
        {!freq.size && <p>暂无薄弱点日志。</p>}
      </div>
      <div className="box">
        <h2>数据表审阅</h2>
        <div className="tools">
          {tables.map((t, i) => (
            <button
              aria-pressed={i === tab}
              onClick={() => {
                setTab(i);
                setPage(0);
              }}
              key={t}
            >
              {t}
            </button>
          ))}
          <input
            aria-label="搜索记录"
            placeholder="搜索记录…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(0);
            }}
          />
          <button
            onClick={() => {
              setData(spaces());
              setPage(0);
            }}
          >
            刷新
          </button>
        </div>
        {visible.slice(page * 10, page * 10 + 10).map((r) => (
          <div className="record" key={r.id}>
            <code title={r.id}>{r.id.slice(0, 6)}</code>
            <strong>{r.name}</strong>
            <small>{r.detail}</small>
            <button className="quiet record-clean" onClick={() => setPending(r)}>清理</button>
          </div>
        ))}
        <div className="tools">
          <small>共 {visible.length} 条</small>
          <button disabled={!page} onClick={() => setPage(page - 1)}>
            上一页
          </button>
          <button
            disabled={(page + 1) * 10 >= visible.length}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </button>
        </div>
      </div>
      <p role="alert">{error}</p>
      {pending && (
        <Dialog title="确认清理记录" close={() => setPending(undefined)}>
          <p>将清理「{pending.name}」及必要的关联记录。</p>
          <button
            className="primary"
            onClick={() => {
              try {
                const current = spaces();
                write(pending.owner, clean(current[pending.owner], pending));
                setData(spaces());
                setPage(0);
                setError("");
              } catch (e) {
                setError((e as Error).message);
              }
              setPending(undefined);
            }}
          >
            确认清理
          </button>
        </Dialog>
      )}
    </>
  );
}
import wasm from "sql.js/dist/sql-wasm.wasm?url";
