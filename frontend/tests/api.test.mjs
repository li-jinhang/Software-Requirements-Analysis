import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

// Vite's existing esbuild dependency bundles the actual adapter for Node tests.
const { outputFiles } = await build({
  entryPoints: [fileURLToPath(new URL("../src/api.ts", import.meta.url))],
  bundle: true, write: false, format: "esm", platform: "browser", packages: "external",
  define: { "import.meta.env": JSON.stringify({ VITE_BACKEND_URL: "http://backend.test" }) },
});
globalThis.sessionStorage = { values: new Map(), setItem(k, v) { this.values.set(k, v); }, getItem(k) { return this.values.get(k) || null; } };
globalThis.window = new EventTarget();
const api = await import("data:text/javascript;base64," + Buffer.from(outputFiles[0].text).toString("base64"));
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
function eventStream(frames, slice = 7) {
  const bytes = new TextEncoder().encode(frames);
  return new Response(new ReadableStream({ start(controller) {
    for (let offset = 0; offset < bytes.length; offset += slice) controller.enqueue(bytes.slice(offset, offset + slice));
    controller.close();
  } }), { headers: { "content-type": "text/event-stream; charset=utf-8" } });
}

test("SSE decodes split UTF-8, CRLF, citations, memory updates and final message ID", async () => {
  const received = [];
  await api.consumeStream(eventStream(
    'event: token\r\ndata: {"delta":"中文追问？"}\r\n\r\n' +
    'event: citation\ndata: {"citations":[{"doc_title":"课件","chunk_index":0,"snippet":"原文","score":0.8}]}\n\n' +
    'event: memory_update\ndata: {"updates":[{"node_id":1,"mastery_score":60,"status":"learning","tag":"继续深入 +8"}]}\n\n' +
    'event: done\ndata: {"message_id":23}\n\n', 1), (event) => received.push(event));
  assert.equal(received[0].delta, "中文追问？");
  assert.equal(received[1].citations[0].snippet, "原文");
  assert.equal(received[2].updates[0].mastery_score, 60);
  assert.deepEqual(received[3], { type: "done", message_id: 23 });
});

test("missing done and explicit error never become a successful turn", async () => {
  await assert.rejects(api.consumeStream(eventStream('event: token\ndata: {"delta":"半段回复"}\n\n'), () => {}), /回复中断/);
  await assert.rejects(api.consumeStream(eventStream('event: error\ndata: {"detail":"模型超时"}\n\n'), () => {}), /模型超时/);
  await assert.rejects(api.consumeStream(eventStream('event: done\ndata: {}\n\n'), () => {}), /格式有误/);
  await assert.rejects(api.consumeStream(eventStream('event: memory_update\ndata: {"updates":[{"node_id":1,"mastery_score":999}]}\n\n'), () => {}), /格式有误/);
});

test("message, persona and note requests match the backend contract", async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, ...options });
    return url.endsWith("/stream") ? eventStream('event: done\ndata: {"message_id":1}\n\n') : response({});
  };
  api.token("test-token");
  await api.stream("2", 0, "我的讲解", new AbortController().signal, () => {});
  await api.changePersona("2", "严苛考官");
  await api.syncNote("3", "4", "复习边界");
  assert.deepEqual(JSON.parse(requests[0].body), { content: "我的讲解" });
  assert.equal(requests[0].headers.get("Authorization"), "Bearer test-token");
  assert.deepEqual(JSON.parse(requests[1].body), { persona_type: "examiner" });
  assert.equal(requests[2].url, "http://backend.test/api/v1/documents/3/nodes/4/note");
  assert.deepEqual(JSON.parse(requests[2].body), { user_note: "复习边界" });
});

test("exports use authenticated server downloads", async () => {
  const paths = [];
  globalThis.fetch = async (url) => { paths.push(url); return response({}); };
  await api.serverExport("sqlite");
  await api.serverExport("json");
  await api.memoryExport("3", "md");
  assert.deepEqual(paths, [
    "http://backend.test/api/v1/admin/export/db", "http://backend.test/api/v1/admin/export/json",
    "http://backend.test/api/v1/documents/3/memory/export?format=md",
  ]);
});

test("401 clears the service token and notifies authentication recovery", async () => {
  let expired = false;
  window.addEventListener("fm:auth-expired", () => { expired = true; }, { once: true });
  api.token("expired-token");
  globalThis.fetch = async () => response({ detail: "请重新登录" }, 401);
  await assert.rejects(api.currentUser(), /请重新登录/);
  assert.equal(api.token(), "");
  assert.equal(expired, true);
});

test("mistyped login credentials do not invalidate an existing signed-in account", async () => {
  api.token("still-valid-token");
  globalThis.fetch = async () => response({ detail: "用户名或密码错误" }, 401);
  await assert.rejects(api.signIn("admin", "incorrect"), /用户名或密码错误/);
  assert.equal(api.token(), "still-valid-token");
});

test("workspace restores server records, unsent drafts and report session IDs", async () => {
  const node = { id: 1, concept_name: "学习率", summary: "原文", importance: 4, mastery_score: 35,
    user_note: "已同步笔记", status: "weak", key_points: ["边界"], lookup_count: 1 };
  globalThis.fetch = async (url) => {
    if (url.endsWith("/documents")) return response([{ id: 3, title: "课程", subject: "算法", file_type: "md", parse_status: "completed" }]);
    if (url.endsWith("/sessions")) return response([{ id: 2, doc_id: 3, persona_type: "peer", status: "completed", target_node_id: 1 }]);
    if (url.endsWith("/memory")) return response({ nodes: [node], edges: [], weaknesses: [] });
    if (url.endsWith("/report")) return response({ id: 99, session_id: 2, created_at: "2026-11-24T00:00:00", coverage_score: 80,
      accuracy_score: 70, clarity_score: 60, logic_score: 50, overall_comment: "继续复讲", weakness_summary: [], review_plan: "举例" });
    throw Error("Unexpected request: " + url);
  };
  const restored = await api.loadWorkspace({ courses: [], reports: [], logs: [], sessions: [{ id: "2", drafts: ["还没发出的讲解", ""] }] });
  assert.equal(restored.courses[0].nodes[0].note, "已同步笔记");
  assert.equal(restored.courses[0].nodes[0].keyPoints[0], "边界");
  assert.equal(restored.sessions[0].drafts[0], "还没发出的讲解");
  assert.equal(restored.reports[0].id, "2");
  assert.equal(restored.reports[0].date, "2026-11-24T00:00:00Z");
  const pendingNote = await api.loadWorkspace({ ...restored, courses: [{ ...restored.courses[0], nodes: [
    { ...restored.courses[0].nodes[0], note: "尚未同步的笔记", syncedNote: "旧笔记" },
  ] }] });
  assert.equal(pendingNote.courses[0].nodes[0].note, "尚未同步的笔记");
  assert.equal(pendingNote.courses[0].nodes[0].syncedNote, "已同步笔记");
});
