import { useState, useEffect } from "react";
import {
  ArrowUpRight,
  BookOpen,
  Search,
  Plus,
  FileText,
  NotebookPen,
  ArrowRight,
  UploadCloud,
  GraduationCap,
  Trash2,
} from "lucide-react";
import { Concept, Course, Space, archive, mean, status } from "./model";
import {
  generateCourse,
  uploadRemote,
  syncNote,
  ApiError,
  retryRemote,
  exportMemory,
  refreshMemory,
  type TeachingPurpose,
} from "./api";
import { Dialog, relationLabel } from "./ui";
type Props = {
  online: boolean;
  space: Space;
  save: (fn: (s: Space) => Space) => void;
  start: (c: Course, n?: Concept) => void;
  guide: (c: Course, n?: Concept) => void;
  starting: boolean;
  canLearn: (purpose: TeachingPurpose) => Promise<boolean>;
  modelRequired: (error: unknown) => boolean;
  uploadTick?: number;
  onOpenStudio?: (id: string) => void;
  onOpenTutor?: (id: string) => void;
  onOpenGraph?: (courseId: string) => void;
  onDelete?: (c: Course) => void;
};
export default function Library({
  online,
  space,
  save,
  start,
  guide,
  starting,
  canLearn,
  modelRequired,
  uploadTick = 0,
  onOpenStudio,
  onOpenTutor,
  onOpenGraph,
  onDelete,
}: Props) {
  const [query, setQuery] = useState(""),
    [subject, setSubject] = useState(""),
    [upload, setUpload] = useState(false),
    [detail, setDetail] = useState(""),
    [file, setFile] = useState<File>(),
    [tag, setTag] = useState(""),
    [error, setError] = useState(""),
    [failedId, setFailedId] = useState<number>(),
    [busy, setBusy] = useState(false);
  const [topicOpen, setTopicOpen] = useState(false);
  const [topic, setTopic] = useState("");
  const [topicSubject, setTopicSubject] = useState("");
  const [dragging, setDragging] = useState(false);
  async function openUploadModal() {
    if (!(await canLearn("extract"))) return;
    setError("");
    setDragging(false);
    setUpload(true);
  }
  async function openTopicModal() {
    if (await canLearn("extract")) { setError(""); setTopicOpen(true); }
  }
  useEffect(() => {
    if (uploadTick > 0) void openUploadModal();
  }, [uploadTick]);
  function chooseFile(next?: File) {
    setFile(next);
    setFailedId(undefined);
    setError("");
  }
  async function generate() {
    if (!topic.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const course = await generateCourse(topic.trim(), topicSubject.trim());
      save(s => ({ ...s, courses: [course, ...s.courses] }));
      setTopicOpen(false);
      setTopic("");
      setTopicSubject("");
      setQuery("");
      setSubject("");
    } catch (e) {
      if (!modelRequired(e)) setError((e as Error).message);
    } finally { setBusy(false); }
  }
  const courses = space.courses,
    nodes = courses.flatMap((c) => c.nodes),
    doc = courses.find((c) => c.id === detail),
    visible = courses.filter(
      (c) =>
        c.title.toLowerCase().includes(query.toLowerCase()) &&
        (!subject || c.subject === subject),
    );
  const weakConcepts = courses.flatMap((c) =>
    c.nodes
      .filter((n) => status(n, space.logs) === "待补漏" || n.score < 60)
      .map((n) => ({ course: c, node: n, label: status(n, space.logs) }))
  ).sort((a, b) => a.node.score - b.node.score).slice(0, 5);
  const activePractice = space.sessions.find((s) => !s.guided && !s.closed);
  const activeGuide = space.sessions.find((s) => s.guided && !s.closed);
  useEffect(() => {
    if (!online || !doc) return;
    const ctrl = new AbortController();
    refreshMemory(doc, ctrl.signal)
      .then(({ course, logs }) => {
        if (!ctrl.signal.aborted)
          save((s) => ({
            ...s,
            courses: s.courses.map((c) => (c.id === course.id ? course : c)),
            logs: [...s.logs.filter((w) => w.doc !== course.id), ...logs],
          }));
      })
      .catch((e) => {
        if (!ctrl.signal.aborted) setError(e.message);
      });
    return () => ctrl.abort();
  }, [online, detail]);
  async function submit() {
    if (!online || !file || busy) return;
    setBusy(true);
    setError("");
    try {
      if (
        !/\.(md|txt|docx|pdf)$/i.test(file.name) ||
        file.size > 20 * 1024 * 1024
      )
        throw Error("请选择 20 MB 以内的 PDF、DOCX、MD 或 TXT 文件。");
      const c = failedId
        ? await retryRemote(failedId)
        : await uploadRemote(file, tag);
      setFailedId(undefined);
      save((s) => ({ ...s, courses: [c, ...s.courses] }));
      setUpload(false);
      setDetail(c.id);
      setFile(undefined);
      setTag("");
    } catch (e) {
      if (modelRequired(e)) return;
      if (e instanceof ApiError) setFailedId(e.docId);
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="library-page" aria-label="学习资料库">
      <div className="pagehead">
        <h1>学习资料库</h1>
        <div className="tools">
        <button onClick={() => void openTopicModal()}><NotebookPen aria-hidden="true" />输入知识点生成</button>
        <button
          className="primary"
          onClick={() => void openUploadModal()}
        >
          <Plus />
          上传资料
        </button>
        </div>
      </div>
      <div className="library-layout">
        <div className="library-main">
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
          {!upload && !topicOpen && !doc && error && <p role="alert">{error}</p>}
          <div className="cards">
            {visible.map((c) => {
              const avg = mean(c.nodes.map((n) => n.score));
              return (
                <article className="card" key={c.id}>
                  <div className="cardbody">
                    <div className="row between meta">
                      <span className="subject-tag"><BookOpen aria-hidden="true" />{c.subject}</span>
                      <div className="card-meta-actions">
                        <span className="format-mark"><FileText aria-hidden="true" />{c.format}</span>
                        {onDelete && (
                          <button
                            type="button"
                            className="icon-button card-delete"
                            aria-label="删除资料"
                            title={"删除「" + c.title + "」"}
                            disabled={c.parseStatus === "processing"}
                            onClick={(e) => {
                              e.stopPropagation();
                              onDelete(c);
                            }}
                          >
                            <Trash2 aria-hidden="true" />
                          </button>
                        )}
                      </div>
                    </div>
                    <h2>{c.title}</h2>
                    <p className="meta">
                      {c.nodes.length} 个知识点 ·{" "}
                      {c.parseStatus === "failed"
                        ? c.error || "解析失败"
                        : !c.parseStatus || c.parseStatus === "completed"
                          ? "已解析"
                          : "解析中"}
                    </p>
                    <div className="row between meta">
                      <span>掌握度</span>
                      <b>{avg}%</b>
                    </div>
                    <progress aria-label="资料掌握度" value={avg} max="100" />
                    <footer>
                      {c.parseStatus === "failed" ? (
                        <button
                          disabled={busy}
                          onClick={async () => {
                            setBusy(true);
                            try {
                              const course = await retryRemote(c.id);
                              save((s) => ({
                                ...s,
                                courses: s.courses.map((d) =>
                                  d.id === course.id ? course : d,
                                ),
                              }));
                            } catch (e) {
                              if (!modelRequired(e)) setError((e as Error).message);
                            } finally {
                              setBusy(false);
                            }
                          }}
                        >
                          重新解析
                        </button>
                      ) : (
                        <>
                          <button className="primary" disabled={starting || !c.nodes.some((n) => n.score < 80)}
                            onClick={() => guide(c)}>
                            <GraduationCap /> 先听 AI 讲解
                          </button>
                          <button disabled={starting || !c.nodes.length} onClick={() => start(c)}>
                            <BookOpen /> 开讲
                          </button>
                        </>
                      )}
                      <button
                        disabled={!c.nodes.length}
                        className="quiet"
                        onClick={() => {
                          setError("");
                          setDetail(c.id);
                        }}
                      >
                        记忆档案
                        <ArrowUpRight />
                      </button>
                    </footer>
                  </div>
                </article>
              );
            })}
            {visible.length > 0 && (
              <article className="card card-add-placeholder" aria-label="添加学习资料">
                <div className="cardbody">
                  <span className="placeholder-icon" aria-hidden="true"><Plus /></span>
                  <h3>添加学习资料</h3>
                  <p className="meta">支持上传 PDF、DOCX、MD、TXT，或按知识点直接生成认知文档。</p>
                  <div className="tools">
                    <button type="button" onClick={() => void openUploadModal()}>
                      <UploadCloud aria-hidden="true" /> 上传文件
                    </button>
                    <button type="button" onClick={() => void openTopicModal()}>
                      <NotebookPen aria-hidden="true" /> 按知识点生成
                    </button>
                  </div>
                </div>
              </article>
            )}
          </div>
          {!visible.length && (
            <div className="box empty">
              <BookOpen aria-hidden="true" />
              <h3>{query || subject ? "没有符合条件的资料" : "还没有学习资料"}</h3>
              <p>{query || subject ? "尝试更换关键词或清除学科筛选条件。" : "上传一份资料，或输入知识点自动生成认知文档。"}</p>
              {(query || subject) && (
                <button onClick={() => { setQuery(""); setSubject(""); }}>清除筛选</button>
              )}
            </div>
          )}
        </div>
        <aside className="library-aside" aria-label="学习概览">
          <div className="aside-section">
            <h3>学习概览</h3>
            <dl className="library-metrics" aria-label="资料库统计">
              <div><dt>学习资料</dt><dd>{courses.length}<small>份</small></dd></div>
              <div><dt>知识节点</dt><dd>{nodes.length}<small>个</small></dd></div>
              <div><dt>平均掌握度</dt><dd>{mean(nodes.map(n => n.score))}<small>%</small></dd></div>
            </dl>
          </div>
          {(activePractice || activeGuide || courses[0]) && (
            <div className="aside-section">
              <h3>最近学习入口</h3>
              <div className="aside-actions">
                {activePractice && onOpenStudio && (
                  <button className="aside-link" onClick={() => onOpenStudio(activePractice.id)}>
                    <span>继续费曼教学 · {courses.find(d => d.id === activePractice.doc)?.title || "当前会话"}</span>
                    <ArrowRight aria-hidden="true" />
                  </button>
                )}
                {activeGuide && onOpenTutor && (
                  <button className="aside-link" onClick={() => onOpenTutor(activeGuide.id)}>
                    <span>继续 AI 讲解 · {courses.find(d => d.id === activeGuide.doc)?.title || "当前讲解"}</span>
                    <ArrowRight aria-hidden="true" />
                  </button>
                )}
                {courses[0] && onOpenGraph && (
                  <button className="aside-link" onClick={() => onOpenGraph(courses[0].id)}>
                    <span>查看知识图谱 · {courses[0].title}</span>
                    <ArrowUpRight aria-hidden="true" />
                  </button>
                )}
              </div>
            </div>
          )}
          <div className="aside-section">
            <h3>薄弱知识点速览</h3>
            {weakConcepts.length ? (
              <ul className="aside-weak-list">
                {weakConcepts.map(({ course, node, label }) => (
                  <li key={node.id} className="aside-weak-item">
                    <div className="aside-weak-info">
                      <strong>{node.name}</strong>
                      <small>{course.title} · {label} · {node.score}%</small>
                    </div>
                    <div className="aside-weak-btns">
                      <button className="quiet" disabled={starting} onClick={() => guide(course, node)} title="听 AI 讲解">讲解</button>
                      <button className="quiet" disabled={starting} onClick={() => start(course, node)} title="去双窗开讲">开讲</button>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="meta">暂无明显薄弱知识点，继续保持巩固节奏。</p>
            )}
          </div>
        </aside>
      </div>
      {topicOpen && <Dialog title="按知识点生成认知文档" close={() => { if (!busy) setTopicOpen(false); }}>
        <form onSubmit={e => { e.preventDefault(); void generate(); }}>
          <label>知识点名称<input required data-initial-focus maxLength={150} value={topic}
            placeholder="例如：二分查找" onChange={e => setTopic(e.target.value)} /></label>
          <label>学科（选填）<input maxLength={100} value={topicSubject}
            onChange={e => setTopicSubject(e.target.value)} /></label>
          {error && <p role="alert">{error}</p>}
          <button className="primary" disabled={busy || !topic.trim()}>
            {busy ? "正在生成认知文档…" : "生成认知文档"}
          </button>
        </form>
      </Dialog>}
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
            <label className={"filepick" + (dragging ? " dragging" : "")}
              onDragOver={(e) => {
                if (!e.dataTransfer.types.includes("Files")) return;
                e.preventDefault();
                if (!busy) setDragging(true);
              }}
              onDragLeave={(e) => {
                if (!(e.relatedTarget instanceof Node) || !e.currentTarget.contains(e.relatedTarget)) setDragging(false);
              }}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                if (!busy && e.dataTransfer.files.length) chooseFile(e.dataTransfer.files[0]);
              }}>
              <input className="visually-hidden" type="file" accept=".pdf,.docx,.md,.txt"
                aria-label="选择本地文件" required={!file} disabled={busy}
                onChange={(e) => {
                  chooseFile(e.currentTarget.files?.[0]);
                  e.currentTarget.value = "";
                }} />
              <span className="upload-icon">{file ? <FileText /> : <UploadCloud />}</span>
              <strong aria-live="polite">{file?.name || "点击选择或拖入学习资料"}</strong>
              {file
                ? <span>{file.size < 1024 * 1024 ? Math.max(1, Math.ceil(file.size / 1024)) + " KB" : (file.size / 1024 / 1024).toFixed(1) + " MB"} · <u>重新选择</u></span>
                : <span>PDF、DOCX、MD、TXT · 最大 20 MB</span>}
            </label>
            <label>
              学科标签
              <input
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                placeholder="选填，如数据结构"
              />
            </label>
            <p className="meta">上传后由模型解析资料。</p>
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
              <button
                key={v}
                onClick={async () => {
                  try {
                    if (online) await exportMemory(doc, i ? "json" : "md");
                    else archive(doc, space.logs, !!i);
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
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
              {n.points?.length ? (
                <ul>
                  {n.points.map((p, i) => (
                    <li key={i}>{p}</li>
                  ))}
                </ul>
              ) : null}
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
                      const value = n.note;
                      await syncNote(doc.id, n.id, value);
                      save((s) => ({
                        ...s,
                        courses: s.courses.map((d) =>
                          d.id !== doc.id
                            ? d
                            : {
                                ...d,
                                nodes: d.nodes.map((x) =>
                                  x.id !== n.id ? x : { ...x, syncedNote: value },
                                ),
                              },
                        ),
                      }));
                      setError("笔记已同步。");
                    } catch (e) {
                      setError((e as Error).message);
                    }
                  }}
                >
                  {n.syncedNote !== undefined && n.note !== n.syncedNote
                    ? "同步笔记（未保存）"
                    : "同步笔记"}
                </button>
              )}
            </details>
          ))}
          <h3>概念关联</h3>
          {doc.links.map((l) => (
            <p className="relation-row" key={JSON.stringify([l.from, l.to, l.label])}>
              <span>{doc.nodes.find((n) => n.id === l.from)?.name}</span>
              <span className="relation-tag">{relationLabel(l.label)} <ArrowRight aria-hidden="true" /></span>
              <span>{doc.nodes.find((n) => n.id === l.to)?.name}</span>
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
    </section>
  );
}
