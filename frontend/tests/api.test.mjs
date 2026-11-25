import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const { outputFiles } = await build({
  entryPoints: [fileURLToPath(new URL("../src/api.ts", import.meta.url))],
  bundle: true, write: false, format: "esm", platform: "browser", packages: "external",
  define: { "import.meta.env": JSON.stringify({ VITE_BACKEND_URL: "http://backend.test" }) },
});
globalThis.sessionStorage = { values: new Map(), setItem(k, v) { this.values.set(k, v); }, getItem(k) { return this.values.get(k) || null; } };
globalThis.window = new EventTarget();
const api = await import("data:text/javascript;base64," + Buffer.from(outputFiles[0].text).toString("base64"));
const response = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
const frame = (kind, data) => `event: ${kind}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
function eventStream(frames) {
  const bytes = new TextEncoder().encode(frames);
  return new Response(new ReadableStream({ start(controller) {
    // One-byte chunks deliberately split multi-byte Chinese characters.
    for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
    controller.close();
  } }), { headers: { "content-type": "text/event-stream" } });
}
const send = (callback = () => {}) => api.stream("2", 0, "我的讲解", new AbortController().signal, callback);

test("deletion sends the exact confirmation for the signed-in account only", async () => {
  const calls = [];
  api.token("fixture-account-token");
  globalThis.fetch = async (url, options) => {
    calls.push({url, options});
    return new Response(null, {status: 204});
  };
  for (const text of ["", "确认", "确认删除 "]) {
    await assert.rejects(api.localAccountsApi.removeCurrent(text), /确认删除/);
  }
  assert.equal(calls.length, 0);
  await api.localAccountsApi.removeCurrent("确认删除");
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /local-accounts\/current$/);
  assert.equal(calls[0].options.method, "DELETE");
  assert.equal(calls[0].options.headers.get("Authorization"), "Bearer fixture-account-token");
  assert.deepEqual(JSON.parse(calls[0].options.body), {confirmation: "确认删除"});
  api.token("");
});

test("split UTF-8 and CRLF preserve citations, committed scores and final ID", async () => {
  globalThis.fetch = async () => eventStream(
    frame("citation", { citations: [{ doc_title: "课件", chunk_index: 0, snippet: "原文", score: 0.8 }] }) +
    frame("token", { delta: "中文追问？" }) +
    frame("memory_update", { updates: [{ node_id: 1, concept_name: "梯度", delta: 8, mastery_score: 60, status: "learning", tag: "继续深入 +8" }] }) +
    frame("done", { message_id: 23 }));
  const parts = [];
  const result = await send(text => parts.push(text));
  assert.deepEqual(parts, ["中文追问？"]);
  assert.equal(result.reply.id, 23);
  assert.equal(result.reply.citations[0].snippet, "原文");
  assert.equal(result.scores.get("1").score, 60);
});

test("left stream dispatches a durable right card and rubric update before done", async () => {
  const card = {message_id: 42, proactive: true, issue_type: "wrong", label: "理解错误", item_id: 1,
    concept_name: "梯度", item_statement: "负梯度", user_claim: "正方向", accurate_explanation: "沿负梯度更新。",
    citations: [{kind: "cognitive_item", item_id: 1, section_title: "梯度"}]};
  const update = {items: [{id: 1, status: "wrong"}], current_item_id: 1, mastery_score: 15, mastered_count: 0};
  globalThis.fetch = async () => eventStream(frame("encyclopedia_push", card) + frame("cognitive_update", update)
    + frame("token", {delta: "请再讲一次。"}) + frame("done", {message_id: 43}));
  const order = [];
  const result = await api.stream("2", 0, "正方向", new AbortController().signal,
    () => order.push("left"), false, data => {assert.deepEqual(data, update); order.push("mark");},
    message => {assert.equal(message.id, 42); assert.equal(message.correction.item_id, 1); order.push("right");});
  assert.deepEqual(order, ["right", "mark", "left"]);
  assert.equal(result.reply.id, 43);
});

test("provider previews remain provisional until a committed done event", async () => {
  globalThis.fetch = async () => eventStream(frame("preview", { text: "正在生成" }) + frame("token", { delta: "最终回复" }) + frame("done", { message_id: 3 }));
  const parts = [];
  assert.equal((await send(text => parts.push(text))).reply.text, "最终回复");
  assert.deepEqual(parts, ["正在生成"]);
});

test("guided lesson uses the recommended target and restores the saved teacher message", async () => {
  const lesson = "## 梯度\n\n想象自己站在山坡上。";
  const citations = [{ doc_title: "课件", chunk_index: 0, snippet: "梯度的定义" }];
  globalThis.fetch = async (url, options) => {
    if (url.endsWith("/sessions/2/lesson/stream")) {
      assert.deepEqual(JSON.parse(options.body), {});
      return eventStream(frame("preview", { text: "## 梯度" }) + frame("token", { delta: lesson }) +
        frame("citation", { citations }) + frame("done", { message_id: 6 }));
    }
    if (url.endsWith("/messages")) return response([{ id: 6, sender_role: "assistant", window_type: "encyclopedia",
      content: lesson, meta_json: { input_kind: "lesson", citations } }]);
    if (url.endsWith("/sessions")) {
      if (options.method !== "POST") return response([]);
      assert.deepEqual(JSON.parse(options.body), { doc_id: 3, persona_type: "peer", mode: "guided" });
      return response({ id: 2, target_node_id: 5 });
    }
    throw Error(url);
  };
  assert.deepEqual(await api.createGuidedSession("3", "互助同窗"), { id: "2", target: "5" });
  const previews = [];
  const { reply, scores } = await api.stream("2", 1, "", new AbortController().signal,
    text => previews.push(text), "lesson");
  assert.deepEqual(previews, ["## 梯度"]);
  assert.equal(reply.lesson, true);
  assert.equal(scores.size, 0);
  const restored = await api.sessionHistory("2");
  assert.deepEqual(restored.right, [reply]);
  assert.deepEqual(restored.left, []);
});

test("early EOF, provider errors and invalid memory never become successful turns", async () => {
  for (const [events, message] of [
    [frame("token", { delta: "半段" }), /回复中断/],
    [frame("error", { detail: "模型超时" }), /模型超时/],
    [frame("done", {}), /缺少消息编号/],
    [frame("memory_update", { updates: [{ node_id: 1, mastery_score: 999 }] }), /格式有误/],
  ]) {
    globalThis.fetch = async () => eventStream(events);
    await assert.rejects(send(), message);
  }
});

test("opening, focus, persona and notes match backend contracts", async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, ...options });
    return url.endsWith("/stream") ? eventStream(frame("done", { message_id: 1 })) : response({ id: 2 });
  };
  api.token("test-token");
  await api.createRemote("3", "严苛考官", "4");
  await api.stream("2", 0, "", new AbortController().signal, () => {}, true);
  await api.setPersona("2", "互助同窗");
  await api.syncNote("3", "4", "复习边界");
  assert.deepEqual(JSON.parse(requests[0].body), { doc_id: 3, persona_type: "examiner", node_id: 4 });
  assert.match(requests[1].url, /2\/opening\/stream$/);
  assert.equal(requests[1].headers.get("Authorization"), "Bearer test-token");
  assert.deepEqual(JSON.parse(requests[2].body), { persona_type: "peer" });
  assert.match(requests[3].url, /documents\/3\/nodes\/4\/note$/);
  assert.deepEqual(JSON.parse(requests[3].body), { user_note: "复习边界" });
});

test("401 expires a session while wrong login credentials retain the old token", async () => {
  let expired = false;
  window.addEventListener("fm:expired", () => { expired = true; }, { once: true });
  api.token("old-token");
  globalThis.fetch = async () => response({ detail: "请重新登录" }, 401);
  await assert.rejects(api.signIn("admin", "incorrect"), /请重新登录/);
  assert.equal(api.token(), "old-token");
  await assert.rejects(api.serverStats(), /请重新登录/);
  assert.equal(api.token(), "");
  assert.equal(expired, true);
});

test("server workspace restores focus and system messages with authoritative role", async () => {
  const rows = [{ id: 2, doc_id: 3, persona_type: "examiner", status: "active", target_node_id: 5 }];
  globalThis.fetch = async url => {
    if (url.endsWith("/auth/me")) return response({ username: "student" });
    if (url.endsWith("/documents")) return response([{ id: 3, title: "课程", file_type: "md", parse_status: "completed" }]);
    if (url.endsWith("/memory")) return response({ nodes: [{ id: 5, concept_name: "梯度", mastery_score: 30, user_note: "原笔记", status: "learning" }], edges: [] });
    if (url.endsWith("/messages")) return response([{ id: 1, window_type: "feynman", sender_role: "system", content: "听众已切换为严苛考官" }]);
    if (url.endsWith("/sessions")) return response(rows);
    throw Error(url);
  };
  const space = await api.restoreSpace("api:http://backend.test:student", new AbortController().signal);
  assert.equal(space.sessions[0].focus, "5");
  assert.equal(space.courses[0].nodes[0].syncedNote, "原笔记");
  const history = await api.sessionHistory("2");
  assert.equal(history.left[0].who, "系统");
  assert.equal(history.role, "严苛考官");
});

test("memory refresh preserves unsynced notes and exports use real server paths", async () => {
  const remote = { nodes: [{ id: "5", note: "服务器", syncedNote: "服务器" }] };
  assert.equal(api.preserveNotes(remote, { nodes: [{ id: "5", note: "未同步", syncedNote: "旧笔记" }] }).nodes[0].note, "未同步");
  const paths = [];
  globalThis.fetch = async url => { paths.push(url); return response({}); };
  await api.serverExport("sqlite");
  await api.serverExport("json");
  assert.deepEqual(paths, ["http://backend.test/api/v1/admin/export/db", "http://backend.test/api/v1/admin/export/json"]);
});

test("report ID and session ID remain distinct when restoring reports", async () => {
  globalThis.fetch = async () => response({ id: 18, created_at: "2026-11-24T00:00:00", coverage_score: 80, accuracy_score: 81, clarity_score: 82, logic_score: 83, weakness_summary: ["边界"], review_plan: "1. 复讲" });
  const report = await api.remoteReport("2", "3", "课程", "GET");
  assert.equal(report.id, "18");
  assert.equal(report.sessionId, "2");
  assert.deepEqual(report.scores, [80, 81, 82, 83]);
});

test("capability stream decodes text and accepts only committed completion", async () => {
  const requests = [];
  globalThis.fetch = async (url, options) => { requests.push({url,...options}); return eventStream(frame("status",{text:"执行中"})+frame("token",{delta:"技能结果"})+frame("done",{message_id:42})); };
  const seen=[];
  await api.capabilitiesApi.stream({session_id:2,window:"feynman",kind:"skill",capability_id:"s1",content:"复习",request_id:"unique-123"}, text=>seen.push(text));
  assert.deepEqual(seen,["技能结果"]);
  assert.match(requests[0].url,/capabilities\/execute\/stream$/);
  assert.equal(JSON.parse(requests[0].body).capability_id,"s1");
});

test("capability failures and interrupted streams do not confirm completion", async () => {
  const body={session_id:2,window:"feynman",kind:"progress",content:"",request_id:"unique-456"};
  for (const [stream,pattern] of [[frame("error",{detail:"工具参数错误"}),/参数错误/],[frame("token",{delta:"未完成"}),/回复中断/],[frame("done",{}),/未确认保存/]]) {
    globalThis.fetch=async()=>eventStream(stream);
    await assert.rejects(api.capabilitiesApi.stream(body,()=>{}),pattern);
  }
});

test("deleteDocument, deleteSession, and deleteReport issue DELETE requests and surface errors", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, method: options?.method });
    return new Response(null, { status: 204 });
  };
  await api.deleteDocument("11");
  await api.deleteSession("22");
  await api.deleteReport("22");
  assert.deepEqual(calls, [
    { url: "http://backend.test/api/v1/documents/11", method: "DELETE" },
    { url: "http://backend.test/api/v1/sessions/22", method: "DELETE" },
    { url: "http://backend.test/api/v1/sessions/22/report", method: "DELETE" },
  ]);
  globalThis.fetch = async () => response({ detail: "无权删除该记录" }, 404);
  await assert.rejects(api.deleteSession("99"), /无权删除/);
});

test("review aiChallenge and aiVerify call expected endpoints", async () => {
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, method: options?.method, body: options?.body });
    if (url.endsWith("/ai-challenge")) {
      return response({
        available: true,
        scenario_question: "请结合实例解释梯度方向。",
        challenge: "请结合实例解释梯度方向。",
        hint: "思考负号含义",
        notice: "",
      });
    }
    return response({
      available: true,
      verdict: "pass",
      score_hint: "remembered",
      feedback: "方向与步长机制表述准确。",
      socratic_followup: "",
      missing_points: [],
      matched_criteria: ["能解释方向"],
      previous_evidence: "原表达",
      updated_evidence: "沿负梯度下降（补充：步长过大震荡）",
      evidence_evolved: true,
      notice: "",
    });
  };
  const ch = await api.reviews.aiChallenge("3", 5);
  const vr = await api.reviews.aiVerify("3", 5, "沿负梯度下降", "步长过大震荡");
  assert.equal(ch.scenario_question, "请结合实例解释梯度方向。");
  assert.equal(vr.evidence_evolved, true);
  assert.deepEqual(calls, [
    { url: "http://backend.test/api/v1/documents/3/reviews/5/ai-challenge", method: "POST", body: "{}" },
    {
      url: "http://backend.test/api/v1/documents/3/reviews/5/ai-verify",
      method: "POST",
      body: JSON.stringify({ answer: "沿负梯度下降", followup_answer: "步长过大震荡" }),
    },
  ]);
});
