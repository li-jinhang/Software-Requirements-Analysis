# 前端第一版联调体检

当前本地前端已完成本页所列的核心协议修复、历史恢复、角色切换、笔记、导出和管理表映射，并已在浏览器验证。以 [原版工作台本地验收](FRONTEND_LOCAL_ACCEPTANCE.md) 为当前说明；下面保留首版基线问题与当时的复现记录。

LangGraph 本地版本接受并校验可选 node_id；双窗消息体只接受 content。角色开场与后端教学图说明见 [LANGGRAPH_ACCEPTANCE.md](LANGGRAPH_ACCEPTANCE.md)。

审阅基线为提交 `2f60b70` 中的 frontend/src。行号均指该版本。用 Edge 加载 Vite（127.0.0.1:5173）编译的真实 api.ts，在一次性 FastAPI 数据库（127.0.0.1:8000）上调用适配器。下表区分浏览器实测与代码比对。所有业务路径都带 /api/v1 前缀，下文省略该前缀。

**第一版基线可以在线登录、上传和打开图谱。在线教学首先被创建会话的 422 阻断；修完创建请求后，还需要同时修复消息体和事件消费。** 可在 Swagger 建立有效会话后单测 SSE 和报告，避免前一个阻断掩盖后续问题。

## 可直接联调

| 前端位置（相对 frontend/src） | 后端接口与契约 | 核对结果 |
| --- | --- | --- |
| api.ts:2–13；main.tsx:59、138；../package.json:7 | VITE_BACKEND_URL 填根地址，适配器追加 /api/v1。JSON 使用 Content-Type，FormData 由浏览器设置边界，JWT 使用 Authorization: Bearer | 实测浏览器跨域成功。未设服务地址时为本地模式；即使设了地址，仍需通过账号表单登录才能进入在线空间 |
| api.ts:28–45 | POST /auth/register、/auth/login | 实测 201、200。username/password/nickname、access_token、user.nickname、user.role 均匹配 |
| Library.tsx:49；api.ts:64–71、77–101 | POST /documents/upload，GET /documents/{id}/graph | 实测 201、200。file 和 subject 匹配；节点 id、concept_name、summary、importance、mastery_score、user_note，以及边的两个端点和 relation_type 均能映射 |
| api.ts:130–171、184–187 | POST /sessions/{id}/{feynman,encyclopedia}/stream | 方法、路径、Bearer、Accept、Content-Type 检查、UTF-8 解码和空行分帧与后端一致。请求体及事件数据仍须按下一节修正 |
| api.ts:190–218；Workbench.tsx:334 | POST /sessions/{id}/report | 使用有效会话实测 200。四个分数字段、overall_comment、weakness_summary、review_plan 可映射。页面在两轮讲解后才显示生成结果，因此正常入口依赖双窗修复 |
| api.ts:254–256；Admin.tsx:203、230–247 | GET /admin/stats | 管理员实测 200。四项计数、avg_mastery、top_weaknesses[].concept_name/count 一致 |
| api.ts:228–246；Admin.tsx:86–110、180 | GET、DELETE /admin/tables/{table}[/记录ID] | 文档表实测分页 200、删除 204。四张表的名称、q/page/page_size、items/total 与契约一致；展示字段另见下表 |
| Admin.tsx:253 | GET /docs | 在线开放接口入口指向 Swagger，不带 /api/v1 前缀 |

## 修复清单

| 优先级与复现结果 | 前端位置 | 对应后端 | 给前端队友的改法 |
| --- | --- | --- | --- |
| P0 创建会话 422 | api.ts:109–117；main.tsx:420 | sessions.py，POST /sessions；SessionCreate 禁止多余字段 | 只传 `{doc_id:Number(doc),persona_type:persona(role)}`，可附 title，删除 node_id。保留选中的节点作为 UI 焦点，当前服务端按讲解内容匹配节点 |
| P0 左右窗均 422 | api.ts:139–145；Workbench.tsx:92 | sessions.py，POST /sessions/{id}/feynman/stream、/encyclopedia/stream；Turn 只接受 content | 两窗均只传 `{content}`；删除 node_id、persona_type。切换角色单独调用下述 PATCH |
| P0 消息内容、引用、掌握度丢失 | api.ts:119–129、172–182；Workbench.tsx:99–124、147–161 | agents/contracts.py 和 sessions.py，事件载荷见 API_CONTRACT | 实测原始帧为 token.delta、citation.citations[]、memory_update.updates[]、done.message_id。按事件名定义 TS 类型；累加 delta；逐条读 doc_title/chunk_index/snippet/score；按每项 node_id 更新 mastery_score，展示 tag。done 解析 message_id 后结束。中断先刷新历史再允许重发 |
| P1 切换听众仅改本地 | Workbench.tsx:440–456；api.ts:107–108 | PATCH /sessions/{id}/persona | 在线切换先提交 `{persona_type:persona(新角色)}`，成功后再更新 UI；失败保留原角色 |
| P1 笔记同步 405 | api.ts:248–252；Library.tsx:230–255 | documents.py，PATCH /documents/{id}/nodes/{node_id}/note | 两个 ID 放路径，JSON 只传 `{user_note}`。本地编辑内容与已同步状态分开显示 |
| P1 管理导出均 404 | api.ts:257–259；Admin.tsx:212–218、251–252 | admin.py，GET /admin/export/json、/admin/export/db | 将 json 映射到 /admin/export/json、sqlite 映射到 /admin/export/db；继续沿用带 Bearer 的 request 并下载 blob |
| P1 表有数据但名称/时间可能为空 | api.ts:220–241；Admin.tsx:147–149 | GET /admin/tables/{table}，items 返回对应表的实际记录 | documents 用 title/parse_status/created_at，knowledge_nodes 用 concept_name/status/updated_at，weakness_logs 用 detail/is_resolved/created_at，diagnostic_reports 用 overall_comment 或 session_id 与 created_at；适配后再返回 ReviewRow |
| P1 换浏览器后看不到示范课和历史 | main.tsx:123–138、175–185；storage.ts:10–19；Insights.tsx:186–206 | GET /documents、/documents/{id}/memory、/sessions、/sessions/{id}/messages、/sessions/{id}/report | 在线登录后拉取资料、会话；打开页面再加载图谱、消息和已完成会话报告。按 window_type 分窗，恢复 meta_json 中的引用与分数；只合并本地草稿。会话主键和报告主键独立，获取报告用 session_id |
| P1 资料导出用了本地缓存 | Library.tsx:210；model.ts:201–249 | GET /documents/{id}/memory/export?format=md 或 json | 在线分支带 Bearer 下载服务端响应；本地分支保留 archive。服务端 JSON 提供四节导出和历史薄弱点 |
| P2 上传轮询读错对象，失败原因被折叠 | api.ts:14–20、72–77；Library.tsx:49 | POST /documents/upload 同步返回 Document；GET /documents/{id}/memory 返回 nodes/edges/weaknesses；详情在 GET /documents/{id} | 成功上传后直接取 graph。需要状态时读详情的 parse_status/error_message。解析失败读 detail.doc_id、detail.message；允许 POST /documents/{id}/retry。422 的字段错误按 loc/msg 展示 |
| P2 身份过期或服务地址切换 | api.ts:2–19；main.tsx:123–138、175–185 | GET /auth/me、HTTP 401 | 在线恢复时验证 token 和当前 base，401 后清理该服务的缓存身份并打开登录；保留当前未发送草稿 |
| P2 节点状态和新增要点 | api.ts:47–54、78–86；model.ts:187–192；Library.tsx:216–227 | GET /documents/{id}/graph 或 /memory，Node 返回 status、key_points | 在线使用服务端 status 区分 unlearned 和 weak；在已有概念详情中显示 key_points。需要准确显示线下累计查证时从 lookup_count 或 weaknesses 读取 |

### 已提供、首版未调用的入口

资料列表、详情、失败重试、归属用户的资料删除（DELETE /documents/{id}）、身份恢复、角色切换、历史消息和历史报告都已在 [API_CONTRACT.md](API_CONTRACT.md) 定义。首版调用集中在 api.ts:28–259。未提供资料删除按钮可保留为待办；管理员删表与学生删除自己的资料是不同入口。新阶段支持 `GET /documents/{id}/memory?query=梯度下降`，可选地返回相关前置节点与该文档的历史薄弱点。

### 可直接发给前端 Codex

> 参照 docs/API_CONTRACT.md 与 docs/FRONTEND_SYNC_CHECKLIST.md，先修 P0 的创建会话、双窗请求体和 SSE 消费，再修角色切换、笔记、导出、四表映射和历史恢复。把请求和字段转换集中在 frontend/src/api.ts，页面只改消费端，保留本地演示分支与现有样式。用服务端账号完成“上传 → 开讲 → 切角色 → 左讲右查 → 刷新恢复 → 生成报告”，核对服务端历史中 assistant 文本、引用和掌握度；另用管理员检查两种导出及分页。执行 npm run build。

## 验收记录

- `npm run build` 的 TypeScript 检查与 Vite 生产构建通过。
- 运行根目录 `python dev.py`，用临时数据目录验证真实前端适配器。注册、登录、上传、读取记忆和图谱、统计、分表查询、删除测试记录成功。通过契约创建有效会话后，前端报告适配器成功映射四维分数。
- 同一测试中首版适配器的会话创建和两窗 POST 均返回 422，笔记 PATCH 返回 405，两个管理导出均返回 404。用契约请求体重测两窗，均返回 200 和完整的 done 帧。
- CORS 自动化测试检查 localhost、127.0.0.1 上 5173（Vite 开发）、3000（备选开发端口）、4173（Vite 预览）的带 Authorization 和 Content-Type 预检。自定义 backend/.env 的 CORS_ORIGINS 会覆盖默认列表；变更后重启服务。

## 第二阶段修复复验

2026-11-23 使用 `python dev.py`、临时数据目录、默认基线智能体和 Edge 完成真实 FastAPI 联调。

| 路径 | 已核对的结果 |
| --- | --- |
| 学生教学 | 上传课件、同步节点笔记、下载服务端 MD / JSON、创建会话、切换考官、两次讲解、右窗查证和报告生成均通过。 |
| 刷新与恢复 | 逐条对照服务端消息中的 assistant 文本、引用片段、评估标签与节点分数；刷新保留未发草稿。新浏览器重新登录后可打开报告及其双窗历史。 |
| SSE 中断 | 在后端提交第二轮回复后截断浏览器收到的 done 帧。前端查询服务端历史、恢复该轮回复、清除已提交草稿；服务端只记录两轮讲解。 |
| 管理员 | 核对文档、节点、日志、报告名称与节点第二页。JSON 包含八张表并隐藏密码哈希；SQLite 快照通过完整性检查，会话消息数与历史接口一致。 |
| 本地演示与构建 | 本地账号在没有业务 API 请求的情况下完成开讲、换角色、查证和刷新；TypeScript 与 Vite 生产构建通过。 |

回归脚本在 [frontend/tests/online_sync.py](../frontend/tests/online_sync.py)。在一次性种子库上启动 `python dev.py`，通过环境变量 `FM_STUDENT_PASSWORD` 和 `FM_ADMIN_PASSWORD` 提供该实例的种子账号密码，再用装有 Python Playwright 的解释器运行脚本。默认浏览器为 Edge。脚本会新增一份资料和一轮会话。
