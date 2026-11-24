import { roles } from "./model";
import type { Concept, Course, Message, Report, Session, SourceQuote, Space, Weak } from "./model";
import type { User } from "./storage";

export const backendUrl = String(import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "");
export const connected = Boolean(backendUrl);
export function token(value?: string) {
  if (value !== undefined) sessionStorage.setItem("fm.jwt:" + backendUrl, value);
  return sessionStorage.getItem("fm.jwt:" + backendUrl) || "";
}

export class ApiError extends Error {
  constructor(message: string, public status: number, public docId?: number) {
    super(message);
  }
}
function errorText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((e) => `${e.loc?.slice(1).join(".") || "输入"}：${e.msg}`).join("；");
  if (detail && typeof detail === "object" && "message" in detail) return String(detail.message);
  return "请求失败，请稍后重试。";
}
async function request(path: string, opts: RequestInit = {}) {
  const headers = new Headers(opts.headers);
  if (token()) headers.set("Authorization", "Bearer " + token());
  if (opts.body && !(opts.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(backendUrl + "/api/v1" + path, { ...opts, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    if (res.status === 401 && token() && !["/auth/login", "/auth/register"].includes(path)) {
      token("");
      window.dispatchEvent(new Event("fm:auth-expired"));
    }
    throw new ApiError(errorText(body.detail), res.status, body.detail?.doc_id);
  }
  return res;
}
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });
const json = async <T>(path: string, signal?: AbortSignal): Promise<T> => (await request(path, { signal })).json();
const utc = (value: string) => /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : value + "Z";
type RawUser = { username: string; nickname: string; role: string };
function userFrom(u: RawUser): User {
  return { id: `api:${backendUrl}:${u.username}`, name: u.nickname, admin: u.role === "admin" };
}
export async function signIn(username: string, password: string, nickname?: string) {
  if (nickname !== undefined) await request("/auth/register", post({ username, password, nickname }));
  const a = await (await request("/auth/login", post({ username, password }))).json();
  if (!a.access_token) throw Error("登录响应缺少访问令牌。");
  token(a.access_token);
  return userFrom(a.user);
}
export async function currentUser(signal?: AbortSignal) {
  return userFrom(await json<RawUser>("/auth/me", signal));
}

type RawNode = {
  id: number; concept_name: string; summary: string; importance: number;
  mastery_score: number; user_note: string; status: Concept["state"];
  key_points: string[]; lookup_count: number;
};
type RawEdge = { source_node_id: number; target_node_id: number; relation_type: string };
type RawDoc = { id: number; title: string; subject: string; file_type: string; parse_status: string };
type RawWeak = { id: number; session_id: number; node_id: number; detail: string; is_resolved: boolean };
type RawMemory = { nodes: RawNode[]; edges: RawEdge[]; weaknesses: RawWeak[] };
type RawSession = { id: number; doc_id: number; target_node_id: number | null; persona_type: string; status: string };
type RawMessage = {
  id: number; window_type: string; sender_role: string; content: string; related_node_ids: number[];
  meta_json: { citations?: SourceQuote[]; updates?: NodeChange[]; mode?: "offline" | "live" };
};
type RawReport = {
  id: number; session_id: number; created_at: string; coverage_score: number; accuracy_score: number;
  clarity_score: number; logic_score: number; overall_comment: string; weakness_summary: string[]; review_plan: string;
};
function courseFrom(d: RawDoc, memory: RawMemory, draft?: Course): Course {
  return {
    id: String(d.id), title: d.title, subject: d.subject || "未分类", format: d.file_type.toUpperCase(), cover: 3,
    nodes: memory.nodes.map((n) => {
      const local = draft?.nodes.find((v) => v.id === String(n.id));
      return { id: String(n.id), name: n.concept_name, summary: n.summary,
        importance: n.importance, score: n.mastery_score,
        note: local?.syncedNote !== undefined && local.note !== local.syncedNote ? local.note : n.user_note || "",
        syncedNote: n.user_note || "", streak: 0, state: n.status, keyPoints: n.key_points, lookupCount: n.lookup_count };
    }),
    links: memory.edges.map((e) => ({ from: String(e.source_node_id), to: String(e.target_node_id), label: e.relation_type })),
  };
}
function logsFrom(doc: string, rows: RawWeak[]): Weak[] {
  return rows.map((w) => ({ id: String(w.id), doc, node: String(w.node_id), detail: w.detail,
    resolved: w.is_resolved, session: String(w.session_id) }));
}
function sessionFrom(s: RawSession, c: Course, cached?: Session): Session {
  return { id: String(s.id), doc: c.id, focus: String(s.target_node_id || c.nodes[0]?.id || ""),
    role: roles[["child", "peer", "examiner"].indexOf(s.persona_type)] || roles[1],
    left: cached?.left || [], right: cached?.right || [], drafts: cached?.drafts || ["", ""],
    closed: s.status === "completed" };
}
function messageFrom(m: RawMessage): Message {
  return { id: String(m.id), who: m.sender_role === "user" ? "我" : m.sender_role === "system" ? "系统"
      : m.window_type === "feynman" ? "听众" : "百科",
    text: m.content, node: m.related_node_ids[0] == null ? undefined : String(m.related_node_ids[0]),
    badge: m.meta_json.updates?.map((u) => u.tag).join("；"), sources: m.meta_json.citations,
    mode: m.meta_json.mode };
}
function reportFrom(r: RawReport, c: Course): Report {
  return { id: String(r.session_id), doc: c.id, title: c.title, date: utc(r.created_at),
    scores: [r.coverage_score, r.accuracy_score, r.clarity_score, r.logic_score],
    comment: r.overall_comment, weaknesses: r.weakness_summary, plan: r.review_plan.split(/\n/).filter(Boolean) };
}

export async function loadWorkspace(cached: Space, signal?: AbortSignal): Promise<Space> {
  const [docs, sessions] = await Promise.all([
    json<RawDoc[]>("/documents", signal), json<RawSession[]>("/sessions", signal),
  ]);
  const materials = await Promise.all(docs.filter((d) => d.parse_status === "completed").map(async (d) => {
    const memory = await json<RawMemory>(`/documents/${d.id}/memory`, signal);
    return { course: courseFrom(d, memory, cached.courses.find((c) => c.id === String(d.id))), logs: logsFrom(String(d.id), memory.weaknesses) };
  }));
  const courses = materials.map((m) => m.course).filter((c) => c.nodes.length);
  const available = sessions.filter((s) => courses.some((c) => c.id === String(s.doc_id)));
  const reports = await Promise.all(available.filter((s) => s.status === "completed").map(async (s) => {
    try {
      return reportFrom(await json<RawReport>(`/sessions/${s.id}/report`, signal), courses.find((c) => c.id === String(s.doc_id))!);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  }));
  return { courses, logs: materials.flatMap((m) => m.logs), reports: reports.filter((r): r is Report => r !== null),
    sessions: available.map((s) => sessionFrom(s, courses.find((c) => c.id === String(s.doc_id))!, cached.sessions.find((v) => v.id === String(s.id)))) };
}
export type SessionSnapshot = { session: Session; course: Course; logs: Weak[] };
export async function loadSession(session: Session, course: Course, signal?: AbortSignal): Promise<SessionSnapshot> {
  // History waits for an accepted turn to settle before memory is reloaded.
  const messages = await json<RawMessage[]>(`/sessions/${session.id}/messages`, signal);
  const [memory, sessions] = await Promise.all([
    json<RawMemory>(`/documents/${course.id}/memory`, signal), json<RawSession[]>("/sessions", signal),
  ]);
  const raw = sessions.find((s) => String(s.id) === session.id);
  if (!raw) throw new ApiError("会话已被删除，请重新选择资料。", 404);
  return { session: { ...sessionFrom(raw, course, session),
      left: messages.filter((m) => m.window_type === "feynman").map(messageFrom),
      right: messages.filter((m) => m.window_type === "encyclopedia").map(messageFrom) },
    course: { ...course, ...courseFrom({ id: Number(course.id), title: course.title, subject: course.subject,
      file_type: course.format, parse_status: "completed" }, memory, course) }, logs: logsFrom(course.id, memory.weaknesses) };
}
export async function uploadRemote(file: File, subject: string): Promise<Course> {
  const data = new FormData(); data.append("file", file); data.append("subject", subject);
  const doc: RawDoc = await (await request("/documents/upload", { method: "POST", body: data })).json();
  return courseFrom(doc, await json<RawMemory>(`/documents/${doc.id}/memory`));
}
export async function retryRemote(id: number): Promise<Course> {
  const doc: RawDoc = await (await request(`/documents/${id}/retry`, { method: "POST" })).json();
  return courseFrom(doc, await json<RawMemory>(`/documents/${doc.id}/memory`));
}
export const persona = (role: string) => ["child", "peer", "examiner"][roles.indexOf(role)];
export async function createRemote(doc: string, node: string, role: string) {
  const s = await (await request("/sessions", post({ doc_id: Number(doc), node_id: Number(node), persona_type: persona(role) }))).json();
  return String(s.id);
}
export async function changePersona(id: string, role: string, signal?: AbortSignal) {
  await request(`/sessions/${id}/persona`, { method: "PATCH", body: JSON.stringify({ persona_type: persona(role) }), signal });
}
export type NodeChange = { node_id: number; concept_name: string; mastery_score: number; delta: number; status: Concept["state"]; tag: string };
export type StreamEvent =
  | { type: "token"; delta: string }
  | { type: "citation"; citations: SourceQuote[] }
  | { type: "memory_update"; updates: NodeChange[] }
  | { type: "done"; message_id: number };

export async function consumeStream(response: Response, consume: (event: StreamEvent) => void) {
  if (!response.body || !response.headers.get("content-type")?.includes("text/event-stream")) throw Error("服务未返回 SSE 消息流。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "", finished = false;
  function dispatch(frame: string) {
    const lines = frame.split(/\r?\n/);
    const type = lines.find((l) => l.startsWith("event:"))?.slice(6).trim();
    const body = lines.filter((l) => l.startsWith("data:")).map((l) => l.slice(5).replace(/^ /, "")).join("\n");
    if (!body || !type) return;
    const data = JSON.parse(body);
    if (!data || typeof data !== "object" || Array.isArray(data)) throw Error("回复数据格式有误，请刷新对话后重试。");
    if (type === "error") throw Error(errorText(data.detail));
    const valid = type === "token" ? typeof data.delta === "string"
      : type === "citation" ? Array.isArray(data.citations) && data.citations.every((c: SourceQuote) =>
        c && typeof c.doc_title === "string" && typeof c.snippet === "string" && Number.isInteger(c.chunk_index)
        && c.chunk_index >= 0 && Number.isFinite(c.score) && c.score >= 0 && c.score <= 1)
      : type === "memory_update" ? Array.isArray(data.updates) && data.updates.every((u: NodeChange) =>
        u && Number.isInteger(u.node_id) && Number.isFinite(u.mastery_score) && u.mastery_score >= 0 && u.mastery_score <= 100
        && typeof u.tag === "string" && ["unlearned", "weak", "learning", "mastered"].includes(u.status || ""))
      : type === "done" ? Number.isInteger(data.message_id) && data.message_id > 0 : false;
    if (!valid) throw Error("回复数据格式有误，请刷新对话后重试。");
    consume({ ...data, type } as StreamEvent);
    if (type === "done") finished = true;
  }
  try {
    while (!finished) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary;
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        dispatch(buffer.slice(0, boundary.index));
        buffer = buffer.slice(boundary.index + boundary[0].length);
        if (finished) break;
      }
      if (done) {
        if (!finished && buffer.trim()) dispatch(buffer);
        if (!finished) throw Error("回复中断，正在核对已保存的对话。请保留草稿。");
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
export async function stream(id: string, side: number, content: string, signal: AbortSignal, consume: (e: StreamEvent) => void) {
  await consumeStream(await request(`/sessions/${id}/${side ? "encyclopedia" : "feynman"}/stream`,
    { ...post({ content }), headers: { Accept: "text/event-stream" }, signal }), consume);
}
export async function openingRemote(id: string, signal: AbortSignal, consume: (e: StreamEvent) => void) {
  await consumeStream(await request(`/sessions/${id}/opening/stream`,
    { method: "POST", headers: { Accept: "text/event-stream" }, signal }), consume);
}
export async function remoteReport(id: string, doc: string, title: string): Promise<Report> {
  return reportFrom(await (await request(`/sessions/${id}/report`, { method: "POST" })).json(), { id: doc, title } as Course);
}
export type ReviewTable = "documents" | "knowledge_nodes" | "weakness_logs" | "diagnostic_reports";
export type ReviewRow = { id: number | string; name: string; status?: string; updated_at?: string };
export async function reviewRows(table: ReviewTable, query: string, page: number, signal: AbortSignal): Promise<{ items: ReviewRow[]; total: number }> {
  const params = new URLSearchParams({ q: query, page: String(page), page_size: "10" });
  const result = await (await request(`/admin/tables/${table}?${params}`, { signal })).json();
  return { total: result.total, items: result.items.map((r: Record<string, string | number | boolean>) => ({
    id: r.id, name: r.title || r.concept_name || r.detail || r.overall_comment || `会话 ${r.session_id}`,
    status: r.parse_status || r.status || (typeof r.is_resolved === "boolean" ? r.is_resolved ? "已解决" : "待复讲" : ""),
    updated_at: r.updated_at || r.created_at,
  })) };
}
export async function removeRow(table: ReviewTable, id: string | number) {
  await request(`/admin/tables/${table}/${encodeURIComponent(id)}`, { method: "DELETE" });
}
export async function syncNote(doc: string, node: string, user_note: string) {
  await request(`/documents/${doc}/nodes/${node}/note`, { method: "PATCH", body: JSON.stringify({ user_note }) });
}
export async function memoryExport(doc: string, format: "md" | "json") {
  return (await request(`/documents/${doc}/memory/export?format=${format}`)).blob();
}
export async function serverStats() { return json<Record<string, unknown>>("/admin/stats"); }
export async function serverExport(format: string) { return (await request(`/admin/export/${format === "sqlite" ? "db" : "json"}`)).blob(); }
