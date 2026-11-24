# 智溯费曼 · 前端联调契约

## 本地启动与首次登录

从仓库根目录执行以下 PowerShell 命令。推荐 Python 3.11 或 3.12。虚拟环境、密钥、上传文件和数据库均已被 Git 忽略。

~~~powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env   # 首次配置时执行
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

Swagger UI 位于 http://127.0.0.1:8000/docs，完整字段定义可从 /openapi.json 读取。下文路径均相对于 **/api/v1**。默认允许 localhost 和 127.0.0.1 上的 5173、3000、4173 端口。更换前端端口时修改 CORS_ORIGINS 后重启。教学演示使用单个 Uvicorn worker。

| 角色 | 用户名 | 密码 | 首次登录可见内容 |
| --- | --- | --- | --- |
| 学生 | student | 123456 | 已完成的机器学习示范会话、图谱与报告；新建会话可体验双窗 |
| 管理员 | admin | admin123 | 全局统计、分表审阅和快照导出 |

以上是本机演示账号。共享部署前应选用新数据目录，关闭 SEED_DEMO 并自行配置管理员与 JWT_SECRET。默认数据位于 backend/data。SQLite 保存业务数据，uploads 保存原文件，chroma_db 保存向量索引。演示内容仅在用户表为空时建立。

## 通用约定

除注册与登录外，所有接口使用请求头 `Authorization: Bearer <access_token>`。普通业务请求使用 JSON。ID 为整数，时间为 UTC，SQL 中的 JSON 字符串在 HTTP 响应中已解码为对象或数组。学生只能读取自己的资料与会话。管理员使用专门的后台路由查看全局记录。

### 认证

| 方法和路径 | 请求 | 成功响应 |
| --- | --- | --- |
| POST /auth/register | {"username":"mei","password":"study123","nickname":"小梅"} | 201，User；注册固定为 student |
| POST /auth/login | {"username":"student","password":"123456"} | 200，{"access_token":"…","token_type":"bearer","user":{"id":2,"username":"student","nickname":"演示学生","role":"student"}} |
| GET /auth/me | 无 | 200，User，字段同上面的 user |

JWT 有效期 24 小时。注册密码至少 6 字符且 UTF-8 编码至多 72 字节。登录也可通过 Swagger 的登录端点取令牌，再在 Authorize 输入令牌。

### 学习资料和知识图谱

| 方法和路径 | 输入 | 成功响应 |
| --- | --- | --- |
| POST /documents/upload | multipart/form-data：file 必填，subject 可选 | 201，Document；请求等待解析、SQL 和向量索引完成 |
| GET /documents | 无 | Document[]，最新优先 |
| GET /documents/{id} | 无 | Document |
| POST /documents/{id}/retry | 无 | Document；重试 failed 文档 |
| DELETE /documents/{id} | 无 | 204，空响应；级联删除会话、知识点、日志、报告、上传文件与向量 |
| GET /documents/{id}/memory | 无 | {"nodes":[…],"edges":[…],"weaknesses":[…]}，weaknesses 仅含未解决记录；可选 query 匹配概念，附 matched_node_ids、prerequisites 与跨会话 historical_weaknesses |
| PATCH /documents/{id}/nodes/{node_id}/note | {"user_note":"复习步长边界"} | 更新后的 Node |
| GET /documents/{id}/memory/export?format=md | format 为 md（默认）或 json | 带 Content-Disposition 的 Markdown 或 JSON 文件 |
| GET /documents/{id}/graph | 无 | {"nodes":[…],"edges":[…]} |

支持文字版 PDF、DOCX、UTF-8 MD/TXT，单文件上限 20 MiB。浏览器上传时让 FormData 自动设置 multipart 边界。文档返回示例：

~~~json
{"id":1,"user_id":2,"title":"机器学习基础：梯度下降与过拟合","subject":"机器学习","file_type":"md","parse_status":"completed","error_message":"","chunk_count":2,"structured_summary":"从损失最小化到泛化误差与正则化。","created_at":"2026-11-23T03:10:00"}
~~~

节点字段为 id、doc_id、concept_name、summary、key_points（核心要点数组，旧节点默认为空）、importance、mastery_score、status、lookup_count、user_note、updated_at。图谱用 id 连接边的 source_node_id 与 target_node_id；边还返回 id、doc_id、relation_type 和 description。未学习用 unlearned，中低高掌握显示 weak（红）、learning（黄）、mastered（绿）。weaknesses 项含 id、session_id、node_id、weakness_type、detail、is_resolved、created_at。导出包含“核心概念树”“概念关联说明”“已掌握清单”“薄弱点警示”四个部分。

### 学习会话

| 方法和路径 | 请求 | 成功响应 |
| --- | --- | --- |
| POST /sessions | {"doc_id":1,"title":"梯度下降复述","persona_type":"peer","node_id":3} | 201，Session；标题、角色和目标知识点可省略；node_id 须属于当前资料 |
| POST /sessions/{id}/opening/stream | 无 | text/event-stream；U0003 角色开场，重复调用重放已保存的开场 |
| GET /sessions | 无 | Session[]，最新优先 |
| PATCH /sessions/{id}/persona | {"persona_type":"examiner"} | 更新后的 Session；实际切换时追加 system 分隔消息，保留上下文 |
| GET /sessions/{id}/messages | 无 | 按 id 升序的 Message[]，按 window_type 分窗 |
| POST /sessions/{id}/feynman/stream | {"content":"梯度决定下降方向。"} | text/event-stream |
| POST /sessions/{id}/encyclopedia/stream | {"content":"学习率过大会怎样？"} | text/event-stream |
| POST /sessions/{id}/report | 无 | Report；结束会话，重复调用返回已生成的报告 |
| GET /sessions/{id}/report | 无 | Report；尚未生成返回 404 |

Session 示例：`{"id":2,"user_id":2,"doc_id":1,"title":"梯度下降复述","persona_type":"peer","status":"active","created_at":"2026-11-23T03:15:00","ended_at":null}`。角色为 child、peer、examiner。已结束的 completed 会话可以浏览历史或报告。

LangGraph 版本的 Session 另含可空的 target_node_id，映射创建请求中的 node_id。教学消息的 meta_json 新增 graph_nodes、assessment（含原文证据与连续达标次数）、memory_synced 等字段，供本地验收与历史恢复；它们不单独作为 SSE 事件。左右窗消息请求仍只接受 content。memory_update 与 done 在最终提交成功后才发送；失败的流可返回 error 事件，客户端不得将缺少 done 的轮次视为已保存。

Message 示例：`{"id":5,"session_id":2,"window_type":"feynman","sender_role":"user","content":"梯度沿哪个方向？","related_node_ids":[],"meta_json":{},"created_at":"2026-11-23T03:16:00"}`。助手消息的 meta_json 保留 citations 或 updates，供刷新页面后恢复引用卡片与分数标签。

Report 示例：`{"id":1,"session_id":1,"coverage_score":88,"accuracy_score":82,"clarity_score":91,"logic_score":79,"overall_comment":"下山类比清晰","weakness_summary":["遗漏步长边界条件"],"review_plan":"比较三种步长的损失曲线","created_at":"2026-11-23T03:20:00"}`。动态基线报告为练习指标，种子报告使用预置的四维示例分值。

### 双窗 SSE

用 fetch 发送 POST，按空行拆分帧。网络分片可能在 UTF-8 字符中间断开，也可能一次包含多帧。以下是事件示例，不代表四种事件会在每轮中全部出现。

~~~text
event: token
data: {"delta":"学习率"}

event: citation
data: {"citations":[{"doc_title":"机器学习基础","chunk_index":0,"snippet":"步长过大可能震荡。","score":0.85}]}

event: memory_update
data: {"updates":[{"node_id":2,"concept_name":"梯度","mastery_score":75,"delta":8,"status":"learning","tag":"提及核心概念 +8"}]}

event: done
data: {"message_id":123}

~~~

chunk_index 从 0 开始。score 为文档内混合排序分数（0 到 1），可用于比较本次命中。左窗按 token 拼接文本并消费 memory_update；右窗展示 citation 和文本，可将左窗的问题直接作为右窗 content 提交。done 表示本轮消息和关联记忆已提交；若连接提前结束，先刷新历史再决定是否重发，避免重复计分。

~~~javascript
async function streamTurn(url, accessToken, content, onEvent) {
  const res = await fetch(url, {
    method: "POST",
    headers: { Authorization: "Bearer " + accessToken, "Content-Type": "application/json" },
    body: JSON.stringify({ content })
  });
  if (!res.ok) throw new Error(await res.text());
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "", finished = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const [event, data] = buffer.slice(0, boundary).split("\n");
        buffer = buffer.slice(boundary + 2);
        const type = event.slice(7);
        onEvent(type, JSON.parse(data.slice(6)));
        if (type === "done") finished = true;
      }
    }
    if (!finished) throw new Error("对话流提前结束，请刷新历史");
  } finally { reader.releaseLock(); }
}
~~~

### 管理员后台

| 方法和路径 | 参数 | 成功响应 |
| --- | --- | --- |
| GET /admin/stats | 无 | {"total_users":2,"total_docs":1,"total_nodes":8,"total_sessions":1,"avg_mastery":64.0,"top_weaknesses":[{"node_id":4,"concept_name":"学习率","count":1}]}；排行最多 10 项 |
| GET /admin/tables/{table_name} | page 从 1 开始，page_size 为 1–100，q 为可选关键词 | {"page":1,"page_size":20,"total":1,"items":[…]}；items 为所查询表的记录 |
| DELETE /admin/tables/{table_name}/{id} | 无 | 204；清理记录，数据库外键级联生效 |
| GET /admin/export/json | 无 | 单个 JSON 下载，八个表名为顶层键，记录为数组；账号与昵称按 ID 替换，移除密码哈希与服务器文件路径；学习内容与笔记保留原文 |
| GET /admin/export/db | 无 | feynman.db 下载，使用 SQLite 在线备份 API 合并 WAL 内的已提交内容 |

允许查看与清理的表为 documents、knowledge_nodes、weakness_logs、diagnostic_reports。二进制数据库下载包含真实账户哈希；仅限管理员。SQLite 快照覆盖业务表，恢复完整检索环境还需同时保留 uploads 与 chroma_db。

### 错误处理

错误使用 FastAPI 的 detail 字段。401 为令牌过期、未登录或凭据错误；403 为权限不足；404 为记录不存在、非本人资源或报告尚未生成；409 为重名、状态冲突或会话已结束；413 为文件过大；415 为不支持的后缀；422 为字段校验或文件解析错误。解析失败时示例：`{"detail":{"doc_id":4,"message":"无可解析文字；扫描件请上传文字版"}}`。字段校验的 detail 为错误数组。流开始后的故障表现为流中断，客户端应检查 done。

