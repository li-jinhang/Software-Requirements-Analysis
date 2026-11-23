import { useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  Search,
  Plus,
  ArrowRight,
  Layers3,
  Target,
  LibraryBig,
  UploadCloud,
} from "lucide-react";
import { Course, Space, archive, mean, parseFile, status } from "./model";
import { uploadRemote, syncNote } from "./api";
import { Dialog, Diagram } from "./ui";
type Props = {
  online: boolean;
  space: Space;
  save: (fn: (s: Space) => Space) => void;
  start: (c: Course) => void;
};
export default function Library({ online, space, save, start }: Props) {
  const [query, setQuery] = useState(""),
    [subject, setSubject] = useState(""),
    [upload, setUpload] = useState(false),
    [detail, setDetail] = useState(""),
    [file, setFile] = useState<File>(),
    [tag, setTag] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const courses = space.courses,
    nodes = courses.flatMap((c) => c.nodes),
    doc = courses.find((c) => c.id === detail),
    visible = courses.filter(
      (c) =>
        c.title.toLowerCase().includes(query.toLowerCase()) &&
        (!subject || c.subject === subject),
    );
  async function submit() {
    if (!file || busy) return;
    setBusy(true);
    setError("");
    try {
      if (
        !/\.(md|txt|docx|pdf)$/i.test(file.name) ||
        file.size > 20 * 1024 * 1024
      )
        throw Error("请选择 20 MB 以内的 PDF、DOCX、MD 或 TXT 文件。");
      const c = await (online ? uploadRemote : parseFile)(file, tag);
      save((s) => ({ ...s, courses: [c, ...s.courses] }));
      setUpload(false);
      setDetail(c.id);
      setFile(undefined);
      setTag("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="pagehead">
        <div>
          <h1>学习资料库</h1>
          <p>整理课程资料，选择一份开始费曼讲解。</p>
        </div>
        <button
          className="primary"
          onClick={() => {
            setError("");
            setUpload(true);
          }}
        >
          <Plus />
          上传资料
        </button>
      </div>
      <div className="stats">
        {[
          [LibraryBig, "学习资料", courses.length, "份"],
          [Layers3, "知识节点", nodes.length, "个"],
          [Target, "平均掌握度", mean(nodes.map((n) => n.score)), "%"],
        ].map(([I, label, num, unit]) => {
          const Icon = I as typeof Target;
          return (
            <div className="stat" key={String(label)}>
              <span>
                <Icon />
                {String(label)}
              </span>
              <strong>
                {String(num)}
                <small>{String(unit)}</small>
              </strong>
            </div>
          );
        })}
      </div>
      <div className="sectionhead">
        <h2>
          全部资料 <small>{visible.length}</small>
        </h2>
        <div className="tools">
          <label className="search">
            <Search />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索资料…"
              aria-label="搜索资料"
            />
          </label>
          <select
            aria-label="学科筛选"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          >
            <option value="">全部学科</option>
            {[...new Set(courses.map((c) => c.subject))].map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="cards">
        {visible.map((c) => {
          const avg = mean(c.nodes.map((n) => n.score));
          return (
            <article className="card" key={c.id}>
              <Diagram type={c.cover} />
              <div className="cardbody">
                <div className="row between meta">
                  <span>{c.subject}</span>
                  <code>{c.format}</code>
                </div>
                <h2>{c.title}</h2>
                <p className="meta">{c.nodes.length} 个知识点 · 已解析</p>
                <div className="row between meta">
                  <span>掌握度</span>
                  <b>{avg}%</b>
                </div>
                <progress value={avg} max="100" />
                <footer>
                  <button onClick={() => start(c)}>
                    <BookOpen />
                    开讲
                  </button>
                  <button className="quiet" onClick={() => setDetail(c.id)}>
                    记忆档案
                    <ArrowUpRight />
                  </button>
                </footer>
              </div>
            </article>
          );
        })}
      </div>
      {!visible.length && <p className="empty">没有符合条件的资料。</p>}
      {upload && (
        <Dialog
          title="上传学习资料"
          close={() => {
            if (!busy) setUpload(false);
          }}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit();
            }}
          >
            <label className="filepick">
              <UploadCloud size={32} />
              <strong>{file?.name || "选择本地文件"}</strong>
              <span>PDF、DOCX、MD、TXT · 最大 20 MB</span>
              <input
                type="file"
                required={!file}
                accept=".pdf,.docx,.md,.txt"
                onChange={(e) => setFile(e.target.files?.[0])}
              />
            </label>
            <label>
              学科标签
              <input
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                placeholder="选填，如数据结构"
              />
            </label>
            <p className="meta">
              {online
                ? "上传后将等待服务端完成解析。"
                : "本地演示按原文分段建立记忆档案。"}
            </p>
            <p role="alert">{error}</p>
            <button className="primary" disabled={!file || busy}>
              {busy ? "正在解析…" : error ? "重新解析" : "解析资料"}
              <ArrowRight />
            </button>
          </form>
        </Dialog>
      )}
      {doc && (
        <Dialog drawer title="记忆档案" close={() => setDetail("")}>
          <h2>{doc.title}</h2>
          <div className="tools">
            {["Markdown", "JSON"].map((v, i) => (
              <button key={v} onClick={() => archive(doc, space.logs, !!i)}>
                导出 {v}
              </button>
            ))}
          </div>
          {error && <p role="status">{error}</p>}
          <h3>核心概念</h3>
          {doc.nodes.map((n) => (
            <details key={n.id}>
              <summary>
                {n.name}{" "}
                <small>
                  {status(n, space.logs)} · {n.score}%
                </small>
              </summary>
              <p>{n.summary}</p>
              <label>
                个人笔记
                <textarea
                  value={n.note}
                  onChange={(e) =>
                    save((s) => ({
                      ...s,
                      courses: s.courses.map((c) =>
                        c.id !== doc.id
                          ? c
                          : {
                              ...c,
                              nodes: c.nodes.map((x) =>
                                x.id !== n.id
                                  ? x
                                  : { ...x, note: e.target.value },
                              ),
                            },
                      ),
                    }))
                  }
                />
              </label>
              {online && (
                <button
                  onClick={async () => {
                    try {
                      await syncNote(doc.id, n.id, n.note);
                      setError("笔记已同步。");
                    } catch (e) {
                      setError((e as Error).message);
                    }
                  }}
                >
                  同步笔记
                </button>
              )}
            </details>
          ))}
          <h3>概念关联</h3>
          {doc.links.map((l) => (
            <p key={l.from + l.to}>
              {doc.nodes.find((n) => n.id === l.from)?.name} →{" "}
              {doc.nodes.find((n) => n.id === l.to)?.name} · {l.label}
            </p>
          ))}
          {!doc.links.length && <p>暂无关联。</p>}
          <h3>薄弱点记录</h3>
          {space.logs
            .filter((w) => w.doc === doc.id)
            .map((w) => (
              <p key={w.id}>
                {w.detail} · {w.resolved ? "已解决" : "待复讲"}
              </p>
            ))}
          {!space.logs.some((w) => w.doc === doc.id) && <p>暂无记录。</p>}
        </Dialog>
      )}
    </>
  );
}
