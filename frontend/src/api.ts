import {
  Concept,
  Course,
  Message,
  Session,
  Space,
  Weak,
  Report,
  roles,
  download,
} from "./model";
const base = String(import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "");
export const currentService = (id: string) =>
  Boolean(base) && id.startsWith("api:" + base + ":");
export const connected = Boolean(base);
export function token(value?: string) {
  if (value !== undefined) sessionStorage.setItem("fm.jwt:" + base, value);
  return sessionStorage.getItem("fm.jwt:" + base) || "";
}
export class ApiError extends Error {
  constructor(
    message: string,
    readonly docId?: number,
    readonly status?: number,
  ) {
    super(message);
  }
}
async function request(path: string, opts: RequestInit = {}) {
  const headers = new Headers(opts.headers);
  if (token()) headers.set("Authorization", "Bearer " + token());
  if (opts.body && !(opts.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const res = await fetch(base + "/api/v1" + path, { ...opts, headers });
  if (!res.ok) {
    const msg = await res.json().catch(() => ({}));
    const detail = msg.detail;
    if (
      res.status === 401 &&
      !path.startsWith("/auth/login") &&
      headers.get("Authorization") === "Bearer " + token()
    ) {
      token("");
      window.dispatchEvent(new Event("fm:expired"));
    }
    throw new ApiError(
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((e) => e.loc.join(".") + " " + e.msg).join("；")
          : detail?.message || "请求失败（" + res.status + "）",
      detail?.doc_id,
      res.status,
    );
  }
  return res;
}
const post = (body: unknown) => ({
  method: "POST",
  body: JSON.stringify(body),
});
export async function signIn(
  username: string,
  password: string,
  nickname?: string,
) {
  if (nickname !== undefined)
    await request("/auth/register", post({ username, password, nickname }));
  const a = await (
    await request("/auth/login", post({ username, password }))
  ).json();
  if (!a.access_token) throw Error("登录响应缺少访问令牌。");
  const u = a.user || a;
  token(a.access_token);
  return {
    id: "api:" + base + ":" + username,
    name: u.nickname || username,
    admin: u.role === "admin",
  };
}
type RawNode = {
  id: string | number;
  concept_name: string;
  summary: string;
  importance: number;
  mastery_score: number;
  user_note?: string;
  status: Concept["mastery"];
  key_points: string[];
};
type RawEdge = {
  source_node_id: string | number;
  target_node_id: string | number;
  relation_type: string;
};
type DocumentRow = {
  id: number;
  title: string;
  subject: string;
  file_type: string;
  parse_status: string;
  error_message?: string;
};
type Memory = {
  nodes: RawNode[];
  edges: RawEdge[];
  weaknesses?: {
    id: number;
    node_id: number;
    detail: string;
    is_resolved: boolean;
    session_id: number;
  }[];
};
async function json<T>(path: string, options?: RequestInit): Promise<T> {
  return (await request(path, options)).json();
}
function mapCourse(doc: DocumentRow, data: Memory): Course {
  return {
    id: String(doc.id),
    title: doc.title,
    subject: doc.subject || "未分类",
    format: doc.file_type.toUpperCase(),
    cover: 3,
    parseStatus: doc.parse_status,
    error: doc.error_message,
    nodes: data.nodes.map((n) => ({
      id: String(n.id),
      name: n.concept_name,
      summary: n.summary,
      importance: n.importance,
      score: n.mastery_score,
      note: n.user_note || "",
      streak: 0,
      mastery: n.status,
      points: n.key_points || [],
    })),
    links: data.edges.map((e) => ({
      from: String(e.source_node_id),
      to: String(e.target_node_id),
      label: e.relation_type,
    })),
  };
}
async function uploaded(doc: DocumentRow) {
  return mapCourse(doc, await json<Memory>("/documents/" + doc.id + "/graph"));
}
export async function uploadRemote(file: File, subject: string) {
  const data = new FormData();
  data.append("file", file);
  data.append("subject", subject);
  return uploaded(
    await json<DocumentRow>("/documents/upload", {
      method: "POST",
      body: data,
    }),
  );
}
export async function retryRemote(id: number | string) {
  return uploaded(
    await json<DocumentRow>("/documents/" + id + "/retry", { method: "POST" }),
  );
}
export async function exportMemory(doc: Course, format: "json" | "md") {
  const res = await request(
    "/documents/" + doc.id + "/memory/export?format=" + format,
  );
  download(
    doc.title + "." + format,
    await res.blob(),
    res.headers.get("content-type") || "application/octet-stream",
  );
}
export const persona = (role: string) =>
  ["child", "peer", "examiner"][roles.indexOf(role)];
export async function createRemote(doc: string, role: string) {
  const s = await (
    await request(
      "/sessions",
      post({ doc_id: Number(doc), persona_type: persona(role) }),
    )
  ).json();
  if ((s.id ?? s.session_id) == null) throw Error("会话响应缺少编号。");
  return String(s.id ?? s.session_id);
}
export async function setPersona(id: string, role: string) {
  await request("/sessions/" + id + "/persona", {
    method: "PATCH",
    body: JSON.stringify({ persona_type: persona(role) }),
  });
}
type Citation = {
  doc_title: string;
  chunk_index: number;
  snippet: string;
  score: number;
};
type Update = {
  node_id: number;
  mastery_score: number;
  status: Concept["mastery"];
  tag: string;
};
type Meta = { citations?: Citation[]; updates?: Update[] };
type StreamPayloads = {
  token: { delta: string };
  citation: { citations: Citation[] };
  memory_update: { updates: Update[] };
  done: { message_id: number };
};
function assistant(
  side: number,
  text: string,
  meta: Meta,
  id: number,
): Message {
  return {
    id,
    who: side ? "百科" : "听众",
    text,
    node: meta.updates?.length ? String(meta.updates[0].node_id) : undefined,
    badge: meta.updates
      ?.map((u) => u.tag + " · " + u.mastery_score + "%")
      .join(" / "),
    citation: meta.citations
      ?.map((c) => c.doc_title + " · 片段 #" + c.chunk_index)
      .join(" / "),
    excerpt: meta.citations
      ?.map(
        (c) =>
          "片段 #" +
          c.chunk_index +
          " · 相关度 " +
          c.score.toFixed(2) +
          "\n" +
          c.snippet,
      )
      .join("\n\n"),
  };
}
export async function stream(
  id: string,
  side: number,
  content: string,
  signal: AbortSignal,
  onText: (text: string) => void,
) {
  const response = await request(
    "/sessions/" + id + "/" + (side ? "encyclopedia" : "feynman") + "/stream",
    {
      ...post({ content }),
      headers: { Accept: "text/event-stream" },
      signal,
    },
  );
  if (
    !response.body ||
    !response.headers.get("content-type")?.includes("text/event-stream")
  )
    throw Error("服务未返回 SSE 消息流。");
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "",
    text = "";
  const meta: Meta = {};
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += value || "";
      let split;
      while ((split = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, split.index);
        buffer = buffer.slice(split.index + split[0].length);
        const lines = frame.split(/\r?\n/);
        const kind =
          lines
            .find((l) => l.startsWith("event:"))
            ?.slice(6)
            .trim() || "token";
        const body = lines
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).replace(/^ /, ""))
          .join("\n");
        if (!body) continue;
        const data = JSON.parse(body);
        switch (kind) {
          case "token":
            text += (data as StreamPayloads["token"]).delta;
            onText(text);
            break;
          case "citation":
            meta.citations = (meta.citations || []).concat(
              (data as StreamPayloads["citation"]).citations,
            );
            break;
          case "memory_update":
            meta.updates = (meta.updates || []).concat(
              (data as StreamPayloads["memory_update"]).updates,
            );
            break;
          case "done": {
            const id = (data as StreamPayloads["done"]).message_id;
            if (!Number.isInteger(id)) throw Error("完成事件缺少消息编号。");
            return {
              reply: assistant(side, text, meta, id),
              scores: new Map(
                meta.updates?.map((u) => [
                  String(u.node_id),
                  { score: u.mastery_score, mastery: u.status },
                ]),
              ),
            };
          }
          case "error":
            throw Error(data.detail || "对话中断。");
        }
      }
      if (done) throw Error("回复中断，请重试。");
    }
  } finally {
    await reader.cancel();
  }
}
export async function remoteReport(
  id: string,
  doc: string,
  title: string,
  method: "GET" | "POST" = "POST",
  signal?: AbortSignal,
): Promise<Report> {
  const r = await (
    await request("/sessions/" + id + "/report", { method, signal })
  ).json();
  const list = (v: unknown) =>
    Array.isArray(v)
      ? v.map(String)
      : String(v || "")
          .split("\n")
          .filter(Boolean);
  return {
    id: String(r.id),
    sessionId: id,
    doc,
    title,
    date: r.created_at || new Date().toISOString(),
    scores: [
      r.coverage_score,
      r.accuracy_score,
      r.clarity_score,
      r.logic_score,
    ],
    comment: r.overall_comment,
    weaknesses: list(r.weakness_summary),
    plan: list(r.review_plan),
  };
}
export type ReviewTable =
  "documents" | "knowledge_nodes" | "weakness_logs" | "diagnostic_reports";
export type ReviewRow = {
  id: number | string;
  name: string;
  status?: string;
  updated_at?: string;
};
export async function reviewRows(
  table: ReviewTable,
  query: string,
  page: number,
  signal: AbortSignal,
): Promise<{ items: ReviewRow[]; total: number }> {
  const params = new URLSearchParams({
    q: query,
    page: String(page),
    page_size: "10",
  });
  const result = await (
    await request("/admin/tables/" + table + "?" + params, { signal })
  ).json();
  const items = result.items.map(
    (row: Record<string, string | number | boolean>) => {
      const id = String(row.id),
        date = String(row.updated_at || row.created_at || "");
      switch (table) {
        case "documents":
          return {
            id,
            name: String(row.title),
            status: String(row.parse_status),
            updated_at: date,
          };
        case "knowledge_nodes":
          return {
            id,
            name: String(row.concept_name),
            status: String(row.status),
            updated_at: date,
          };
        case "weakness_logs":
          return {
            id,
            name: String(row.detail),
            status: row.is_resolved ? "已解决" : "待复讲",
            updated_at: date,
          };
        case "diagnostic_reports":
          return {
            id,
            name: String(row.overall_comment || "会话 " + row.session_id),
            updated_at: date,
          };
      }
    },
  );
  return { items, total: result.total };
}
export async function removeRow(table: ReviewTable, id: string | number) {
  await request("/admin/tables/" + table + "/" + encodeURIComponent(id), {
    method: "DELETE",
  });
}
export async function syncNote(doc: string, node: string, user_note: string) {
  await request(
    "/documents/" +
      encodeURIComponent(doc) +
      "/nodes/" +
      encodeURIComponent(node) +
      "/note",
    {
      method: "PATCH",
      body: JSON.stringify({ user_note }),
    },
  );
}
export async function serverStats() {
  return (await request("/admin/stats")).json();
}
export async function serverExport(format: string) {
  return (
    await request("/admin/export/" + (format === "sqlite" ? "db" : "json"))
  ).blob();
}

type SessionRow = {
  id: number;
  doc_id: number;
  persona_type: string;
  status: string;
};
type HistoryRow = {
  id: number;
  window_type: string;
  sender_role: string;
  content: string;
  meta_json: Meta;
};
export async function sessionHistory(id: string, signal?: AbortSignal) {
  const messages = await json<HistoryRow[]>("/sessions/" + id + "/messages", {
    signal,
  });
  const history: Pick<Session, "left" | "right"> = { left: [], right: [] };
  for (const m of messages) {
    const right = m.window_type === "encyclopedia";
    history[right ? "right" : "left"].push(
      m.sender_role === "assistant"
        ? assistant(Number(right), m.content, m.meta_json || {}, m.id)
        : {
            id: m.id,
            who: m.sender_role === "user" ? "我" : "系统",
            text: m.content,
          },
    );
  }
  return history;
}
export async function restoreSpace(
  userId: string,
  signal: AbortSignal,
): Promise<Space> {
  const me = await json<{ username: string }>("/auth/me", { signal });
  if (userId !== "api:" + base + ":" + me.username) {
    window.dispatchEvent(new Event("fm:expired"));
    throw Error("请重新登录。");
  }
  const [docs, rows] = await Promise.all([
    json<DocumentRow[]>("/documents", { signal }),
    json<SessionRow[]>("/sessions", { signal }),
  ]);
  const bundles = await Promise.all(
    docs.map(async (doc) => {
      const memory =
        doc.parse_status === "completed"
          ? await json<Memory>("/documents/" + doc.id + "/memory", { signal })
          : { nodes: [], edges: [], weaknesses: [] };
      return {
        course: mapCourse(doc, memory),
        logs: mapWeaknesses(doc.id, memory),
      };
    }),
  );
  const courses = bundles.map((b) => b.course);
  const sessions = rows.map((s): Session => ({
    id: String(s.id),
    doc: String(s.doc_id),
    focus: courses.find((c) => c.id === String(s.doc_id))?.nodes[0]?.id || "",
    role: roles[["child", "peer", "examiner"].indexOf(s.persona_type)],
    closed: s.status === "completed",
    left: [],
    right: [],
    drafts: ["", ""],
  }));
  return {
    courses,
    sessions,
    logs: bundles.flatMap((b) => b.logs),
    reports: [],
  };
}

export async function refreshMemory(course: Course, signal: AbortSignal) {
  const doc: DocumentRow = {
    id: Number(course.id),
    title: course.title,
    subject: course.subject,
    file_type: course.format,
    parse_status: course.parseStatus || "completed",
  };
  const data = await json<Memory>("/documents/" + course.id + "/memory", {
    signal,
  });
  return { course: mapCourse(doc, data), logs: mapWeaknesses(doc.id, data) };
}

function mapWeaknesses(docId: number, memory: Memory) {
  return (memory.weaknesses || []).map((w): Weak => ({
    id: String(w.id),
    doc: String(docId),
    node: String(w.node_id),
    detail: w.detail,
    resolved: w.is_resolved,
    session: String(w.session_id),
  }));
}

export async function reportHistory(space: Space, signal: AbortSignal) {
  const titles = new Map(space.courses.map((c) => [c.id, c.title]));
  const reports = await Promise.all(
    space.sessions
      .filter((s) => s.closed)
      .map(async (s) => {
        try {
          return await remoteReport(
            s.id,
            s.doc,
            titles.get(s.doc) || "费曼报告",
            "GET",
            signal,
          );
        } catch (e) {
          if (e instanceof ApiError && e.status === 404) return null;
          throw e;
        }
      }),
  );
  return reports.filter((r): r is Report => r !== null);
}

export type ModelPurpose =
  "default" | "extract" | "feynman" | "encyclopedia" | "report";
export type ModelProfile = {
  id: string;
  name: string;
  mode: "api" | "chatgpt";
  model: string;
  base_url: string;
  has_key: boolean;
};
export type ProfileDraft = Omit<ModelProfile, "id" | "has_key"> & {
  api_key?: string;
};
export type ModelSettings = {
  profiles: ModelProfile[];
  bindings: Partial<Record<ModelPurpose, string>>;
  environment_live: boolean;
  chatgpt: { connected: boolean; pending: boolean; error: string };
};
const modelPath = "/model-settings";
export const modelSettingsApi = {
  read: () => json<ModelSettings>(modelPath),
  active: () =>
    json<Record<Exclude<ModelPurpose, "default">, string>>(
      modelPath + "/active",
    ),
  save: (p: ProfileDraft, id?: string) =>
    json<{ id: string }>(modelPath + "/profiles" + (id ? "/" + id : ""), {
      ...post(p),
      method: id ? "PUT" : "POST",
    }),
  remove: (id: string) =>
    request(modelPath + "/profiles/" + id, { method: "DELETE" }),
  bind: (purpose: ModelPurpose, id: string) =>
    request(modelPath + "/bindings/" + purpose, {
      ...post({ profile_id: id || null }),
      method: "PUT",
    }),
  catalog: (p: ProfileDraft, id?: string) =>
    json<string[]>(
      modelPath + "/catalog",
      post({ ...p, model: p.model || "catalog", id }),
    ),
  test: (id: string) =>
    json<{ latency_ms: number }>(modelPath + "/profiles/" + id + "/test", {
      method: "POST",
    }),
  login: () =>
    json<{ url: string }>(modelPath + "/chatgpt/login", { method: "POST" }),
  cancel: () => request(modelPath + "/chatgpt/login", { method: "DELETE" }),
  logout: () => request(modelPath + "/chatgpt/account", { method: "DELETE" }),
};
