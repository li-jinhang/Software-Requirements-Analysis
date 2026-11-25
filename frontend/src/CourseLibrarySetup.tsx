import { useState } from "react";
import { ArrowRight, FolderPlus, Workflow } from "lucide-react";
import { continueLibrarySetup, createLibrary, startLibrarySetup } from "./api";
import type { Course } from "./model";
import { Dialog } from "./ui";

export default function CourseLibrarySetup({ course, close, created, modelRequired }: {
  course?: Course;
  close: () => void;
  created: (course: Course) => void;
  modelRequired: (error: unknown) => boolean;
}) {
  const [mode, setMode] = useState<"guided" | "empty">("guided");
  const [title, setTitle] = useState("");
  const [requirements, setRequirements] = useState("");
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const question = course?.setupQuestion;

  async function submit(useDefaults = false) {
    if (busy || (course ? !question || (!answer.trim() && !useDefaults) : !title.trim())) return;
    setBusy(true);
    setError("");
    try {
      const result = course && question
        ? await continueLibrarySetup(course.id, { question, answer: answer.trim(), use_defaults: useDefaults })
        : mode === "empty"
          ? await createLibrary(title.trim())
          : await startLibrarySetup(title.trim(), requirements.trim());
      created(result);
    } catch (e) {
      setError((e as Error).message);
      modelRequired(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog className="course-setup" title={course ? "补充课程信息" : "新建课程资料库"}
      close={() => { if (!busy) close(); }}>
      <form onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        {course ? (
          <>
            <h3 className="course-setup-title">{course.title}</h3>
            {!!course.setupAnswers?.length && (
              <dl className="course-answers">
                {course.setupAnswers.map(({ question, answer }, index) => (
                  <div key={index}><dt>{question}</dt><dd>{answer}</dd></div>
                ))}
              </dl>
            )}
            <label className="course-question">
              <strong>{question}</strong>
              <textarea aria-label="补充回答" data-initial-focus required rows={3} maxLength={1500} value={answer}
                disabled={busy} onChange={(e) => setAnswer(e.target.value)} placeholder="输入你的回答" />
            </label>
          </>
        ) : (
          <>
            <div className="course-modes" role="group" aria-label="建库方式">
              <button type="button" aria-pressed={mode === "guided"} disabled={busy}
                onClick={() => { setMode("guided"); setError(""); }}>
                <Workflow aria-hidden="true" />
                <span><strong>AI 搭建课程框架</strong><small>生成课程模块、知识点与图谱</small></span>
              </button>
              <button type="button" aria-pressed={mode === "empty"} disabled={busy}
                onClick={() => { setMode("empty"); setError(""); }}>
                <FolderPlus aria-hidden="true" />
                <span><strong>创建空资料库</strong><small>稍后添加课件和参考文件</small></span>
              </button>
            </div>
            <label>课程名称<input required data-initial-focus maxLength={100} value={title} disabled={busy}
              onChange={(e) => setTitle(e.target.value)} placeholder="例如：大学物理" /></label>
            {mode === "guided" && (
              <label>学习要求（选填）<textarea rows={3} maxLength={2000} value={requirements} disabled={busy}
                onChange={(e) => setRequirements(e.target.value)}
                placeholder="例如：从零学习上册的力学部分，重点掌握推导和典型题" /></label>
            )}
          </>
        )}
        {error && <p role="alert">{error}</p>}
        <div className="tools course-setup-actions">
          <button className="primary" disabled={busy || (course ? !answer.trim() : !title.trim())}>
            {busy ? "正在提交…" : course ? "提交回答" : mode === "empty" ? "创建资料库" : "生成课程资料库"}
            <ArrowRight aria-hidden="true" />
          </button>
          {course && <button type="button" disabled={busy} onClick={() => void submit(true)}>按通用范围生成</button>}
        </div>
      </form>
    </Dialog>
  );
}
