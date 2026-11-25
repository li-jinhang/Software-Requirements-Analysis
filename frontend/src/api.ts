import {
  CognitiveDoc, CognitiveUpdate, Correction, ReviewCard, ReviewLists,
  CourseCitation,
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
    readonly code?: string,
  ) {
    super(message);
  }
}
async function request(
  path: string,
  opts: RequestInit = {},
  useSession = true,
) {
  const headers = new Headers(opts.headers);
  if (useSession && token()) headers.set("Authorization", "Bearer " + token());
  if (opts.body && !(opts.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const res = await fetch(base + "/api/v1" + path, { ...opts, headers });
  if (!res.ok) {
    const msg = await res.json().catch(() => ({}));
    const detail = msg.detail;
    if (
      res.status === 401 &&
      useSession &&
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
      detail?.code,
    );
  }
  return res;
}
const post = (body: unknown) => ({
  method: "POST",
  body: JSON.stringify(body),
});

export type UserSkill = { id: string; name: string; description: string; version: string; enabled: boolean; shared: boolean; editable: boolean; available: boolean; reason: string; files: string[] };
export type McpTool = { name: string; description: string; inputSchema: Record<string, unknown>; annotations: Record<string, unknown> };
export type McpServer = { id: string; name: string; url: string; enabled: boolean; has_token: boolean; status: string; tools: McpTool[] };
export type CapabilityCatalog = { skills: UserSkill[]; servers: McpServer[] };
export type CapabilityRun = { session_id: number; window: string; kind: "skill" | "mcp" | "progress" | "history" | "lookup"; capability_id?: string; content: string; tool?: string; arguments?: Record<string, unknown>; confirmed?: boolean; request_id: string };
export const capabilitiesApi = {
  runs: async (sessionId: string): Promise<{ id: string; kind: string; capability_id: string; status: string; created_at: string }[]> => (await request("/capabilities/runs?session_id=" + encodeURIComponent(sessionId))).json(),
  list: async (): Promise<CapabilityCatalog> => (await request("/capabilities")).json(),
  importSkill: async (file: File, replace: boolean) => { const body = new FormData(); body.append("file", file); body.append("replace", String(replace)); return (await request("/capabilities/skills/import", { method: "POST", body })).json(); },
  skill: async (id: string): Promise<UserSkill & { instructions: string }> => (await request("/capabilities/skills/" + id)).json(),
  changeSkill: (id: string, change: { enabled?: boolean; shared?: boolean }) => request("/capabilities/skills/" + id, { method: "PATCH", body: JSON.stringify(change) }),
  removeSkill: (id: string) => request("/capabilities/skills/" + id, { method: "DELETE" }),
  template: async () => { const r = await request("/capabilities/skills/template"); download("study-review.zip", await r.blob(), "application/zip"); },
  saveServer: async (data: { name: string; url: string; token?: string; enabled: boolean }, id?: string): Promise<McpServer> => (await request("/capabilities/servers" + (id ? "/" + id : ""), { method: id ? "PUT" : "POST", body: JSON.stringify(data) })).json(),
  connect: async (id: string) => (await request("/capabilities/servers/" + id + "/connect", { method: "POST" })).json(),
  removeServer: (id: string) => request("/capabilities/servers/" + id, { method: "DELETE" }),
  run: async (body: CapabilityRun): Promise<{ message: { id: number; content: string } }> => (await request("/capabilities/execute", post(body))).json(),
  stream: async (body: CapabilityRun, onText: (text: string) => void) => {
    const response = await request("/capabilities/execute/stream", post(body));
    if (!response.body) throw Error("能力服务未返回消息流");
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "", text = "";
    try {
      while (true) {
        const { value, done } = await reader.read(); buffer += value || "";
        let split: RegExpExecArray | null;
        while ((split = /\r?\n\r?\n/.exec(buffer))) {
          const frame = buffer.slice(0, split.index); buffer = buffer.slice(split.index + split[0].length);
          const kind = frame.split(/\r?\n/).find(l => l.startsWith("event:"))?.slice(6).trim();
          const data = frame.split(/\r?\n/).filter(l => l.startsWith("data:")).map(l => l.slice(5).trimStart()).join("\n");
          if (!data) continue;
          const payload = JSON.parse(data);
          if (kind === "error") throw Error(payload.detail || "能力执行失败");
          if (kind === "token") { if (typeof payload.delta !== "string") throw Error("能力消息格式无效"); text += payload.delta; onText(text); }
          if (kind === "done") { if (!Number.isInteger(payload.message_id)) throw Error("能力结果未确认保存"); return; }
        }
        if (done) throw Error("能力回复中断，请刷新历史核对结果");
      }
    } finally { await reader.cancel().catch(() => {}); }
  },
};
type AccountUser = {
  id: number;
  username: string;
  nickname: string;
  role: "admin" | "student";
};
type AccountSession = { access_token: string; user: AccountUser };
export type LocalAccount = AccountUser & { protected: boolean };
export const accountId = (username: string) => "api:" + base + ":" + username;
function accountSession(a: AccountSession, username: string) {
  if (!a.access_token) throw Error("登录响应缺少访问令牌。");
  const u = a.user || a;
  token(a.access_token);
  return {
    id: accountId(username),
    name: u.nickname || username,
    admin: u.role === "admin",
  };
}
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
  return accountSession(a, username);
}

const accountPath = "/local-accounts";
export const accountDeletePhrase = "确认删除";
async function openLocalAccount(path: string, options: RequestInit) {
  const result: AccountSession = await (
    await request(accountPath + path, options, false)
  ).json();
  return accountSession(result, result.user.username);
}
export const localAccountsApi = {
  list: async (): Promise<LocalAccount[]> =>
    (await request(accountPath, {}, false)).json(),
  create: (name: string) => openLocalAccount("", post({ name })),
  activate: (id: number, password: string) =>
    openLocalAccount("/" + id + "/activate", post({ password })),
  importBackup: (file: File) => {
    const body = new FormData();
    body.set("file", file);
    return openLocalAccount("/import", { method: "POST", body });
  },
  exportBackup: async (name: string) => {
    const res = await request(accountPath + "/export");
    download(name + ".zip", await res.blob(), "application/zip");
  },
  removeCurrent: async (confirmation: string) => {
    if (confirmation !== accountDeletePhrase) throw Error(`请输入完整的「${accountDeletePhrase}」。`);
    await request(accountPath + "/current", { method: "DELETE", body: JSON.stringify({ confirmation }) });
  },
};
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
      syncedNote: n.user_note || "",
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
export function cognitiveDoc(doc: string, session: string, signal: AbortSignal) {
  return json<CognitiveDoc>(`/documents/${doc}/cognitive-doc?session_id=${session}`, { signal });
}
export async function generateCourse(topic: string, subject: string) {
  return uploaded(await json<DocumentRow>("/documents/generate-from-topic", post({topic, subject})));
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
export const reviews = {
  get: (doc: string, signal?: AbortSignal) => json<ReviewLists>(`/documents/${doc}/due-reviews`, {signal}),
  record: (doc: string, card: ReviewCard, outcome: "remembered" | "again", signal: AbortSignal) =>
    json<ReviewLists>(`/documents/${doc}/reviews/${card.id}/result`, {
      ...post({ outcome, review_count: card.review_count }), signal,
    }),
  skip: (doc: string, item: number, signal: AbortSignal) =>
    json<ReviewLists>(`/documents/${doc}/reviews/${item}/skip`, { ...post({}), signal }),
};
async function createSession(doc: string, role: string, node?: string, options?: {
  mode: "assessment" | "guided";
  target_item_ids?: number[];
}) {
  const s = await (
    await request(
      "/sessions",
      post({ doc_id: Number(doc), persona_type: persona(role), ...(node ? { node_id: Number(node) } : {}),
        ...options }),
    )
  ).json();
  if ((s.id ?? s.session_id) == null) throw Error("会话响应缺少编号。");
  return { id: String(s.id ?? s.session_id), target: s.target_node_id };
}
export async function createRemote(doc: string, role: string, node?: string, assessmentIds?: number[]) {
  return (await createSession(doc, role, node, assessmentIds ? { mode: "assessment", target_item_ids: assessmentIds } : undefined)).id;
}
export async function createGuidedSession(doc: string, role: string, node?: string) {
  const { id, target } = await createSession(doc, role, node, { mode: "guided" });
  if (!Number.isInteger(target) || target <= 0) throw Error("讲解会话缺少目标知识点。");
  return { id, target: String(target) };
}
export async function setPersona(id: string, role: string) {
  await request("/sessions/" + id + "/persona", {
    method: "PATCH",
    body: JSON.stringify({ persona_type: persona(role) }),
  });
}
export async function deleteDocument(id: string) {
  await request("/documents/" + id, { method: "DELETE" });
}
export async function deleteSession(id: string) {
  await request("/sessions/" + id, { method: "DELETE" });
}
export async function deleteReport(sessionId: string) {
  await request("/sessions/" + sessionId + "/report", { method: "DELETE" });
}
type Citation = CourseCitation & { score: number };
type Update = {
  node_id: number;
  concept_name: string;
  delta: number;
  mastery_score: number;
  status: Concept["mastery"];
  tag: string;
};
type Meta = Partial<Correction> & { updates?: Update[]; input_kind?: string; no_assessment?: boolean; capability_kind?: string; skill_version?: string };
type StreamPayloads = {
  token: { delta: string };
  preview: { text: string };
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
    operation: meta.no_assessment,
    text,
    node: meta.updates?.length ? String(meta.updates[0].node_id) : undefined,
    badge: meta.updates
      ?.map((u) => {
        const change = u.delta >= 0 ? "+" + u.delta : String(u.delta);
        const tag = u.tag.endsWith(" " + change) ? u.tag : u.tag + " " + change;
        return [u.concept_name, tag, u.mastery_score + "%"]
          .filter(Boolean)
          .join(" · ");
      })
      .join(" / ") || (meta.no_assessment ? (meta.capability_kind === "skill" ? "Skill · v" + meta.skill_version : "工具操作") : undefined),
    citations: meta.citations?.filter((hit): hit is CourseCitation => "chunk_index" in hit),
    ...(meta.input_kind === "lesson" ? { lesson: true } : {}),
    ...(meta.proactive ? {correction: meta as Correction} : {}),
  };
}
export async function stream(
  id: string,
  side: number,
  content: string,
  signal: AbortSignal,
  onText: (text: string) => void,
  initial: boolean | "lesson" = false,
  onMarks?: (event: CognitiveUpdate) => void,
  onCorrection?: (message: Message) => void,
) {
  const endpoint = initial === "lesson" ? "lesson" : initial ? "opening" : side ? "encyclopedia" : "feynman";
  const response = await request(
    "/sessions/" + id + "/" + endpoint + "/stream",
    {
      ...post(initial ? {} : { content }),
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
    text = "",
    previewing = false;
  const meta: Meta = endpoint === "lesson" ? { input_kind: "lesson" } : {};
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
          case "preview": {
            const preview = (data as StreamPayloads["preview"]).text;
            if (typeof preview !== "string") throw Error("流式事件格式有误。");
            previewing = !!preview;
            onText(preview);
            break;
          }
          case "token":
            if (typeof data.delta !== "string") throw Error("流式事件格式有误。");
            text += (data as StreamPayloads["token"]).delta;
            if (!previewing) onText(text);
            break;
          case "citation":
            if (!Array.isArray(data.citations) || data.citations.some((c: Citation) => typeof c.snippet !== "string" || typeof c.doc_title !== "string" || !Number.isInteger(c.chunk_index))) throw Error("流式引用格式有误。");
            meta.citations = (meta.citations || []).concat(
              (data as StreamPayloads["citation"]).citations,
            );
            break;
          case "cognitive_update":
            if (!Array.isArray(data.items) || !Number.isInteger(data.mastery_score))
              throw Error("认知文档更新格式有误。");
            onMarks?.(data);
            break;
          case "encyclopedia_push":
            if (!data.proactive || !Number.isInteger(data.message_id) || !data.accurate_explanation)
              throw Error("纠偏消息格式有误。");
            onCorrection?.(assistant(1, data.accurate_explanation, data, data.message_id));
            break;
          case "memory_update":
            if (!Array.isArray(data.updates) || data.updates.some((u: Update) => !Number.isInteger(u.node_id) || !Number.isInteger(u.mastery_score) || u.mastery_score < 0 || u.mastery_score > 100 || !["weak", "learning", "mastered", "unlearned"].includes(u.status || ""))) throw Error("流式记忆事件格式有误。");
            meta.updates = (meta.updates || []).concat(
              (data as StreamPayloads["memory_update"]).updates,
            );
            break;
          case "done": {
            const id = (data as StreamPayloads["done"]).message_id;
            if (!Number.isInteger(id) || id <= 0) throw Error("完成事件缺少消息编号。");
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
            throw new ApiError(data.detail || "对话中断。", undefined, undefined, data.code);
        }
      }
      if (done) throw Error("回复中断，请重试。");
    }
  } finally {
    await reader.cancel().catch(() => {});
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
    plan: list(r.review_plan)
      .flatMap((line) => line.split("；"))
      .map((line) => line.trim().replace(/^(?:[-*•]|\d+[.)、])\s*/, ""))
      .filter(Boolean),
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
  mode?: string;
  review_completed?: boolean;
  id: number;
  doc_id: number;
  persona_type: string;
  status: string;
  target_node_id?: number | null;
};
type HistoryRow = {
  id: number;
  window_type: string;
  sender_role: string;
  content: string;
  meta_json: Meta;
};
function assessmentState(session?: SessionRow) {
  return session && (session.mode === "assessment" || session.mode === "review")
    ? { targeted: true, assessmentCompleted: session.review_completed } : {};
}
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
            operation: m.meta_json?.no_assessment,
          },
    );
  }
  // The history endpoint waits for an accepted turn; reload session state after it.
  const rows = await json<SessionRow[]>("/sessions", { signal });
  const session = rows.find((s) => String(s.id) === id);
  return {
    ...history,
    ...assessmentState(session),
    ...(session ? { role: roles[["child", "peer", "examiner"].indexOf(session.persona_type)], closed: session.status === "completed" } : {}),
  };
}
export async function restoreSpace(
  userId: string,
  signal: AbortSignal,
): Promise<Space> {
  const me = await json<{ username: string }>("/auth/me", { signal });
  signal.throwIfAborted();
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
    focus: s.target_node_id ? String(s.target_node_id) : courses.find((c) => c.id === String(s.doc_id))?.nodes[0]?.id || "",
    role: roles[["child", "peer", "examiner"].indexOf(s.persona_type)],
    closed: s.status === "completed",
    ...assessmentState(s),
    ...(s.mode === "guided" ? { guided: true } : {}),
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
  return { course: preserveNotes(mapCourse(doc, data), course), logs: mapWeaknesses(doc.id, data) };
}

export function preserveNotes(remote: Course, local?: Course): Course {
  return { ...remote, nodes: remote.nodes.map((node) => {
    const edit = local?.nodes.find((n) => n.id === node.id);
    return edit && edit.syncedNote !== undefined && edit.note !== edit.syncedNote
      ? { ...node, note: edit.note } : node;
  }) };
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
export type TeachingPurpose = Exclude<ModelPurpose, "default">;
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
const localHosts = ["localhost", "127.0.0.1", "[::1]"];
function isLocalModelService() {
  try {
    return (
      connected &&
      localHosts.includes(location.hostname) &&
      localHosts.includes(new URL(base, location.href).hostname)
    );
  } catch {
    return false;
  }
}
export const localModelSettings = isLocalModelService();
const modelRequest = (path: string, options?: RequestInit) =>
  request(modelPath + path, options, false);
async function modelJson<T>(path: string, options?: RequestInit): Promise<T> {
  return (await modelRequest(path, options)).json();
}
export const modelSettingsApi = {
  availability: () =>
    modelJson<Record<TeachingPurpose, boolean>>("/availability"),
  read: () => modelJson<ModelSettings>(""),
  exportArchive: () => modelJson<unknown>("/export"),
  importArchive: (archive: unknown) =>
    modelJson<{ added: number; skipped: number; bindings_added: number }>(
      "/import",
      post(archive),
    ),
  active: () => json<Record<TeachingPurpose, string>>(modelPath + "/active"),
  save: (p: ProfileDraft, id?: string) =>
    modelJson<{ id: string }>("/profiles" + (id ? "/" + id : ""), {
      ...post(p),
      method: id ? "PUT" : "POST",
    }),
  remove: (id: string) => modelRequest("/profiles/" + id, { method: "DELETE" }),
  bind: (purpose: ModelPurpose, id: string) =>
    modelRequest("/bindings/" + purpose, {
      ...post({ profile_id: id || null }),
      method: "PUT",
    }),
  catalog: (p: ProfileDraft, id?: string) =>
    modelJson<string[]>(
      "/catalog",
      post({ ...p, model: p.model || "catalog", id }),
    ),
  test: (id: string) =>
    modelJson<{ latency_ms: number }>("/profiles/" + id + "/test", {
      method: "POST",
    }),
  login: () => modelJson<{ url: string }>("/chatgpt/login", { method: "POST" }),
  cancel: () => modelRequest("/chatgpt/login", { method: "DELETE" }),
  logout: () => modelRequest("/chatgpt/account", { method: "DELETE" }),
};
