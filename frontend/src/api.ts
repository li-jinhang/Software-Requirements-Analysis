import { Concept, Course, Link, Report, roles } from "./model";
const base = String(import.meta.env.VITE_BACKEND_URL || "").replace(/\/$/, "");
export const connected = Boolean(base);
export function token(value?: string) {
  if (value !== undefined) sessionStorage.setItem("fm.jwt:" + base, value);
  return sessionStorage.getItem("fm.jwt:" + base) || "";
}
async function request(path: string, opts: RequestInit = {}) {
  const headers = new Headers(opts.headers);
  if (token()) headers.set("Authorization", "Bearer " + token());
  if (opts.body && !(opts.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const res = await fetch(base + "/api/v1" + path, { ...opts, headers });
  if (!res.ok) {
    const msg = await res.json().catch(() => ({}));
    throw Error(
      typeof msg.detail === "string"
        ? msg.detail
        : "请求失败（" + res.status + "），请重试。",
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
};
type RawEdge = {
  source_node_id: string | number;
  target_node_id: string | number;
  relation_type: string;
};
export async function uploadRemote(
  file: File,
  subject: string,
): Promise<Course> {
  const data = new FormData();
  data.append("file", file);
  data.append("subject", subject);
  const doc = await (
      await request("/documents/upload", { method: "POST", body: data })
    ).json(),
    id = doc.doc_id ?? doc.id;
  if (id == null) throw Error("上传响应缺少文档编号。");
  for (let attempt = 0; attempt < 30; attempt++) {
    const memory = await (await request("/documents/" + id + "/memory")).json();
    if (memory.parse_status === "failed")
      throw Error(memory.detail || "解析失败，请重试。");
    if (!["pending", "processing"].includes(memory.parse_status)) {
      const g = await (await request("/documents/" + id + "/graph")).json();
      const nodes: Concept[] = (g.nodes || []).map((n: RawNode) => ({
        id: String(n.id),
        name: n.concept_name,
        summary: n.summary,
        importance: n.importance,
        score: n.mastery_score,
        note: n.user_note || "",
        streak: 0,
      }));
      if (!nodes.length) throw Error("未解析到知识节点。");
      const links: Link[] = (g.edges || []).map((e: RawEdge) => ({
        from: String(e.source_node_id),
        to: String(e.target_node_id),
        label: e.relation_type,
      }));
      return {
        id: String(id),
        title: doc.title || file.name,
        subject: subject || "未分类",
        format: file.name.split(".").pop()!.toUpperCase(),
        cover: 3,
        nodes,
        links,
      };
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw Error("等待解析超时，请检查后端任务状态。");
}
export const persona = (role: string) =>
  ["child", "peer", "examiner"][roles.indexOf(role)];
export async function createRemote(doc: string, node: string, role: string) {
  const s = await (
    await request(
      "/sessions",
      post({ doc_id: doc, node_id: node, persona_type: persona(role) }),
    )
  ).json();
  if ((s.id ?? s.session_id) == null) throw Error("会话响应缺少编号。");
  return String(s.id ?? s.session_id);
}
export type StreamEvent = {
  excerpt?: string;
  text?: string;
  token?: string;
  doc_title?: string;
  chunk_index?: number;
  node_id?: number | string;
  mastery_score?: number;
  assessment?: string;
  weakness?: string;
};
export async function stream(
  id: string,
  side: number,
  content: string,
  node: string,
  role: string,
  signal: AbortSignal,
  consume: (kind: string, e: StreamEvent) => void,
) {
  const response = await request(
    "/sessions/" + id + "/" + (side ? "encyclopedia" : "feynman") + "/stream",
    {
      ...post({ content, node_id: node, persona_type: persona(role) }),
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
  let buffer = "";
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
        if (kind === "done" || body === "[DONE]") return;
        if (!body) continue;
        let event;
        try {
          event = JSON.parse(body);
        } catch {
          event = body;
        }
        if (kind === "error")
          throw Error(event.detail || event.text || "回复中断，请重试。");
        consume(kind, typeof event === "string" ? { text: event } : event);
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
): Promise<Report> {
  const r = await (
    await request("/sessions/" + id + "/report", { method: "POST" })
  ).json();
  const list = (v: unknown) =>
    Array.isArray(v)
      ? v.map(String)
      : String(v || "")
          .split("\n")
          .filter(Boolean);
  return {
    id,
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
  return (
    await request("/admin/tables/" + table + "?" + params, { signal })
  ).json();
}
export async function removeRow(table: ReviewTable, id: string | number) {
  await request("/admin/tables/" + table + "/" + encodeURIComponent(id), {
    method: "DELETE",
  });
}
export async function syncNote(doc: string, node: string, user_note: string) {
  await request("/documents/" + encodeURIComponent(doc) + "/memory", {
    method: "PATCH",
    body: JSON.stringify({ node_id: node, user_note }),
  });
}
export async function serverStats() {
  return (await request("/admin/stats")).json();
}
export async function serverExport(format: string) {
  return (await request("/admin/export?format=" + format)).blob();
}
