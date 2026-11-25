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
  LibraryBig,
  Workflow,
  Download,
} from "lucide-react";
import { Concept, Course, Space, archive, mean, status } from "./model";
import {
  generateCourse,
  continueLibrarySetup,
  uploadRemote,
  syncNote,
  ApiError,
  retryRemote,
  exportMemory,
  importLibrary,
  exportLibrary,
  refreshMemory,
  type TeachingPurpose,
} from "./api";
import { Dialog, relationLabel } from "./ui";
import CourseLibrarySetup from "./CourseLibrarySetup";
import { MasteryMeter, MasteryProgress } from "./MasteryProgress";
import "./library.css";

const setupLabels: Record<string, string> = {
  processing: "创建中",
  pending: "待补充",
  failed: "生成失败",
};
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
  const [setupOpen, setSetupOpen] = useState<string>();
  const [libraryId, setLibraryId] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [archiveFile, setArchiveFile] = useState<File>();
  const [exporting, setExporting] = useState(false);

  function saveCourse(course: Course) {
    save(s => ({
      ...s,
      courses: s.courses.some(c => c.id === course.id)
        ? s.courses.map(c => c.id === course.id ? course : c)
        : [course, ...s.courses],
    }));
  }
  function created(course: Course) {
    saveCourse(course);
    setSetupOpen(undefined);
    setQuery("");
    setSubject("");
    if (course.parseStatus === "completed") setLibraryId(course.id);
  }
  async function importArchive() {
    if (!archiveFile || busy) return;
    setBusy(true);
    setError("");
    try {
      const courses = await importLibrary(archiveFile);
      const imported = new Set(courses.map(course => course.id));
      save(s => ({ ...s, courses: [...courses, ...s.courses.filter(course => !imported.has(course.id))] }));
      setImportOpen(false);
      setArchiveFile(undefined);
      setQuery("");
      setSubject("");
      setLibraryId(courses[0].id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function downloadArchive(course: Course) {
    if (busy || exporting) return;
    setExporting(true);
    setError("");
    try {
      await exportLibrary(course);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setExporting(false);
    }
  }
  async function retryCourse(course: Course) {
    setBusy(true);
    setError("");
    try {
      saveCourse(await (course.isLibrary ? continueLibrarySetup(course.id) : retryRemote(course.id)));
    } catch (e) {
      if (!modelRequired(e)) setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function openUploadModal() {
    if (!(await canLearn("extract"))) return;
    chooseFile();
    setTag("");
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
      saveCourse(course);
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
    library = courses.find(c => c.id === libraryId && c.isLibrary),
    setupCourse = courses.find(c => c.id === setupOpen);
  const sources = new Map<string, Course[]>();
  for (const course of courses) {
    if (!course.libraryId) continue;
    const siblings = sources.get(course.libraryId);
    if (siblings) siblings.push(course);
    else sources.set(course.libraryId, [course]);
  }
  const topLevel = courses.filter(c => !c.libraryId);
  const averageMastery = mean(nodes.map(n => n.score));
  const needle = query.trim().toLowerCase();
  const visible = topLevel.filter(
      (c) =>
        [c, ...(sources.get(c.id) || [])].some(d => d.title.toLowerCase().includes(needle)) &&
        (!subject || c.subject === subject),
    );
  const recentGraph = courses.find(c => c.nodes.length);
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
        : await uploadRemote(file, tag, library?.id);
      setFailedId(undefined);
      saveCourse(c);
      setUpload(false);
      if (!library) setDetail(c.id);
      setFile(undefined);
      setTag("");
    } catch (e) {
      if (modelRequired(e)) return;
      if (e instanceof ApiError && e.docId) {
        setFailedId(e.docId);
        const dot = file.name.lastIndexOf(".");
        saveCourse({
          id: String(e.docId), title: file.name.slice(0, dot),
          format: file.name.slice(dot + 1).toUpperCase(), cover: 3,
          subject: library?.subject || tag || "未分类", libraryId: library?.id,
          parseStatus: "failed", error: e.message, nodes: [], links: [],
        });
      }
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
          <button onClick={() => void openUploadModal()}><UploadCloud aria-hidden="true" /> 上传资料</button>
          <button disabled={!online || busy} onClick={() => { setError(""); setArchiveFile(undefined); setImportOpen(true); }}>
            <UploadCloud aria-hidden="true" /> 导入资料库
          </button>
          <button className="primary" disabled={!online} onClick={() => setSetupOpen("")}>
            <Plus aria-hidden="true" /> 新建资料库
          </button>
        </div>
      </div>
      <div className="library-layout">
        <div className="library-main">
          <div className="sectionhead">
            <h2>
              资料库与独立资料 <small>{visible.length}</small>
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
                {[...new Set(topLevel.map((c) => c.subject))].map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </div>
          </div>
          {!upload && !topicOpen && !importOpen && !doc && !library && error && <p role="alert">{error}</p>}
          <div className="cards">
            {visible.map((c) => {
              const avg = mean(c.nodes.map((n) => n.score));
              const setupStatus = c.isLibrary && setupLabels[c.parseStatus || ""];
              return (
                <article className="card" key={c.id}>
                  <div className="cardbody">
                    {!c.isLibrary ? (
                      <div className="row between meta">
                        <span className="subject-tag"><BookOpen aria-hidden="true" />{c.subject}</span>
                        <span className="format-mark"><FileText aria-hidden="true" />{c.format}</span>
                      </div>
                    ) : setupStatus && (
                      <div className="row meta">
                        <span className="format-mark" aria-live="polite">{setupStatus}</span>
                      </div>
                    )}
                    <div className="card-heading">
                      <h2>{c.title}</h2>
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
                    <p className="meta">
                      {setupStatus ? (
                        c.parseStatus === "processing" ? "可继续使用工作台，完成后会更新图谱。"
                          : c.parseStatus === "pending" ? "补充课程信息后继续生成。"
                            : c.error || "可以重新生成课程框架。"
                      ) : <>{c.nodes.length} 个知识点 ·{" "}
                      {c.isLibrary ? `${sources.get(c.id)?.length || 0} 份补充资料` : c.parseStatus === "failed"
                        ? c.error || "解析失败"
                        : !c.parseStatus || c.parseStatus === "completed"
                          ? "已解析"
                          : "解析中"}</>}
                    </p>
                    {c.nodes.length > 0 && <MasteryProgress score={avg} label="资料掌握度" />}
                    <footer>
                      {c.isLibrary ? (
                        <>
                          <button className="primary" onClick={() => { setError(""); setLibraryId(c.id); }}>
                            <LibraryBig aria-hidden="true" /> 打开资料库
                          </button>
                          {c.nodes.length > 0 && onOpenGraph &&
                            <button onClick={() => onOpenGraph(c.id)}><Workflow aria-hidden="true" /> 查看图谱</button>}
                          {c.parseStatus === "pending" && <button onClick={() => setSetupOpen(c.id)}>补充课程信息</button>}
                          {c.parseStatus === "failed" && <button disabled={busy} onClick={() => void retryCourse(c)}>重新生成</button>}
                        </>
                      ) : c.parseStatus === "failed" ? (
                        <button
                          disabled={busy}
                          onClick={() => void retryCourse(c)}
                        >
                          重新解析
                        </button>
                      ) : (
                        <>
                          <button className="primary" disabled={starting || !c.nodes.some((n) => n.score < 80)}
                            onClick={() => guide(c)}>
                            <GraduationCap /> 听AI讲
                          </button>
                          <button disabled={starting || !c.nodes.length} onClick={() => start(c)}>
                            <BookOpen /> 讲给AI
                          </button>
                        </>
                      )}
                      {!c.isLibrary && <button
                        disabled={!c.nodes.length}
                        className="quiet"
                        onClick={() => {
                          setError("");
                          setDetail(c.id);
                        }}
                      >
                        记忆档案
                        <ArrowUpRight />
                      </button>}
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
              <div>
                <dt>平均掌握度</dt>
                <dd>
                  {averageMastery}<small>%</small>
                  <MasteryMeter score={averageMastery} label="资料库平均掌握度" compact />
                </dd>
              </div>
            </dl>
          </div>
          {(activePractice || activeGuide || recentGraph) && (
            <div className="aside-section">
              <h3>最近学习</h3>
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
                {recentGraph && onOpenGraph && (
                  <button className="aside-link" onClick={() => onOpenGraph(recentGraph.id)}>
                    <span>查看知识图谱 · {recentGraph.title}</span>
                    <ArrowUpRight aria-hidden="true" />
                  </button>
                )}
              </div>
            </div>
          )}
          <div className="aside-section">
            <h3>薄弱知识</h3>
            {weakConcepts.length ? (
              <ul className="aside-weak-list">
                {weakConcepts.map(({ course, node, label }) => (
                  <li key={node.id} className="aside-weak-item">
                    <div className="aside-weak-info">
                      <strong>{node.name}</strong>
                      <small>{course.title} · {label} · {node.score}%</small>
                    </div>
                    <div className="aside-weak-btns">
                      <button className="quiet" disabled={starting} onClick={() => guide(course, node)}>听AI讲</button>
                      <button className="quiet" disabled={starting} onClick={() => start(course, node)}>讲给AI</button>
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
      {setupOpen !== undefined && <CourseLibrarySetup key={setupOpen} course={setupCourse}
        close={() => setSetupOpen(undefined)} created={created} modelRequired={modelRequired} />}
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
      {library && !upload && !doc && (
        <Dialog drawer title="课程资料库" close={() => { if (!busy) setLibraryId(""); }}>
          <h2 className="course-library-name">{library.title}</h2>
          <section className="course-framework" aria-label="课程框架">
            <div className="row between">
              <h3><Workflow aria-hidden="true" /> 课程框架</h3>
              {library.nodes.length > 0 && <small>{library.nodes.length} 个节点 · {library.links.length} 条关系</small>}
            </div>
            <p>{library.overview || (
              library.parseStatus === "processing" ? "课程框架正在后台创建，可以继续添加资料或前往其他页面。"
                : library.parseStatus === "pending" ? library.setupQuestion
                  : library.parseStatus === "failed" ? library.error || "课程框架生成失败。"
                    : "资料库已创建。添加课件或参考文件后，可以从对应资料开始学习。"
            )}</p>
            {library.parseStatus === "pending" && <button onClick={() => setSetupOpen(library.id)}>补充课程信息</button>}
            {library.parseStatus === "failed" && <button disabled={busy} onClick={() => void retryCourse(library)}>重新生成</button>}
            <div className="tools">
              {library.nodes.length > 0 && <>
                {onOpenGraph && <button className="primary" onClick={() => onOpenGraph(library.id)}>查看知识图谱</button>}
                <button disabled={starting || !library.nodes.some(n => n.score < 80)} onClick={() => guide(library)}>听AI讲</button>
                <button disabled={starting} onClick={() => start(library)}>讲给AI</button>
                <button onClick={() => { setError(""); setDetail(library.id); }}>记忆档案</button>
              </>}
              {online && <button disabled={busy || exporting || library.parseStatus !== "completed" || sources.get(library.id)?.some(s => s.parseStatus === "processing")}
                onClick={() => void downloadArchive(library)}>
                <Download aria-hidden="true" /> {exporting ? "正在导出…" : "导出资料库"}
              </button>}
            </div>
          </section>
          <section className="course-sources" aria-label="补充资料">
            <div className="row between">
              <h3>补充资料 <small>{sources.get(library.id)?.length || 0}</small></h3>
              <button disabled={busy} onClick={() => void openUploadModal()}><Plus aria-hidden="true" /> 添加资料</button>
            </div>
            {error && <p role="alert">{error}</p>}
            {sources.get(library.id)?.map(source => (
              <article key={source.id} className="course-source">
                <div className="row between">
                  <h4>{source.title}</h4>
                  {onDelete && <button className="icon-button card-delete" aria-label={"删除补充资料 " + source.title}
                    disabled={source.parseStatus === "processing"} onClick={() => onDelete(source)}>
                    <Trash2 aria-hidden="true" />
                  </button>}
                </div>
                <p className="meta">{source.format} · {source.parseStatus === "failed" ? source.error || "解析失败"
                  : source.parseStatus === "completed" ? `${source.nodes.length} 个知识点` : "解析中"}</p>
                <div className="tools">
                  {source.parseStatus === "failed" && <button disabled={busy} onClick={() => void retryCourse(source)}>重新解析</button>}
                  {source.nodes.length > 0 && <>
                    {onOpenGraph && <button onClick={() => onOpenGraph(source.id)}>查看图谱</button>}
                    <button disabled={starting || !source.nodes.some(n => n.score < 80)} onClick={() => guide(source)}>听AI讲</button>
                    <button disabled={starting} onClick={() => start(source)}>讲给AI</button>
                    <button onClick={() => { setError(""); setDetail(source.id); }}>记忆档案</button>
                  </>}
                </div>
              </article>
            ))}
            {!sources.get(library.id)?.length && <p className="meta">还没有补充资料。</p>}
          </section>
        </Dialog>
      )}
      {importOpen && (
        <Dialog title="导入资料库" close={() => { if (!busy) setImportOpen(false); }}>
          <form onSubmit={(e) => { e.preventDefault(); void importArchive(); }}>
            <label className="filepick">
              <input className="visually-hidden" type="file" accept=".zip,application/zip"
                aria-label="选择资料库 ZIP 文件" disabled={busy} onChange={(e) => {
                  setArchiveFile(e.currentTarget.files?.[0]);
                  setError("");
                  e.currentTarget.value = "";
                }} />
              <span className="upload-icon" aria-hidden="true"><UploadCloud /></span>
              <strong aria-live="polite">{archiveFile?.name || "选择资料库 ZIP 文件"}</strong>
              <span>最大 512 MB</span>
            </label>
            <p className="meta">导入后在当前账号新建一份资料库。</p>
            {error && <p role="alert">{error}</p>}
            <button className="primary" disabled={!archiveFile || busy}>
              {busy ? "正在导入…" : "导入"}
            </button>
          </form>
        </Dialog>
      )}
      {upload && (
        <Dialog
          title={library ? "添加补充资料" : "上传学习资料"}
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
            {library ? <p className="meta">添加到《{library.title}》</p> : <label>
              学科标签
              <input
                value={tag}
                onChange={(e) => setTag(e.target.value)}
                placeholder="选填，如数据结构"
              />
            </label>}
            <p className="meta">上传后由模型解析资料。</p>
            {error && <p role="alert">{error}</p>}
            <button className="primary" disabled={!file || busy}>
              {busy ? "正在解析…" : failedId ? "重新解析" : "解析资料"}
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
