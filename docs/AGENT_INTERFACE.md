# 智溯费曼 · Agent 与混合记忆接口

模型应用通过 `model_profiles.resolve(purpose)` 获取“应用覆盖 → 默认方案 → 环境配置”的有效连接。管理员显式绑定模型时，资料解析复用分层提取流程，双窗和诊断由 `agents/model_teaching.py` 构建任务与证据，传输和结构校验由 `services/model_connection.py` 负责。下文的基线计分和本地追问适用于离线模式。在线模型调用位于业务写事务之外，同一会话的提交和报告生成串行处理。

## 接入边界

前端发送的会话 ID 由 API 层鉴权。API 加载资料和角色，构造 AgentInput，调用 agents 下的模块，将强类型事件序列化成 SSE 并保存消息。Agent 负责教学推理、引用选择与记忆更新。只需替换以下三个模块中的入口函数，保留签名与事件类型，即可继续运行同一套冒烟测试。

| 文件（相对 backend/app） | 保留入口 | 职责 |
| --- | --- | --- |
| agents/feynman_agent.py | stream(db, turn: AgentInput) → Iterator[AgentEvent] | 左窗讲解评估、追问、写掌握度/薄弱点、推送更新 |
| agents/encyclopedia_agent.py | stream(db, turn: AgentInput) → Iterator[AgentEvent] | 右窗检索、引用回答、记录 need_lookup |
| agents/report_agent.py | generate(db, session) → Report | 用 SQL 记忆汇总四维评估和复习建议 |

AgentInput 含 session_id、doc_id、persona_type、content。persona_type 是 child、peer、examiner 之一。db 为同一轮专用的同步 SQLAlchemy Session，不要传给并行图节点或其他线程。Agent 内使用 flush 可拿到新主键，最终 commit/rollback 由 API 负责。路由按会话校验归属，直接从脚本调用服务时由调用者保证访问权限。

## memory_service 的函数

从 app.services.memory_service 导入。除非另行说明，返回普通 Python dict/list；UTC 时间字段在 Python 端是 datetime，FastAPI 输出为 ISO 字符串。

| 函数 | 输入与行为 | 返回值 / 事务 |
| --- | --- | --- |
| ingest_document(db, doc_id) | 处理已有 pending/failed Document 的 file_path；申请 processing 状态，解析、450 字符分片、80 字符重叠，写向量和抽取的知识点、关系 | Document 字典。此函数自己提交；失败标记 failed，回滚部分 SQL 记录并清理向量，抛出 ValueError。不要在对话事务中调用 |
| search_rag(doc_id, query, top_k=4) | 仅查询当前文档；top_k 为 1–30；没有命中返回空列表 | [{doc_title, chunk_index, snippet, score}]。chunk_index 从 0 开始，score 为 0–1 的混合排序分数，结合向量余弦、中文词项 BM25 与连续术语命中；保持来源片段与分数，不生成虚构引用 |
| get_memory_context(db, doc_id, session_id=None, *, concept=None, query=None) | 查询资料全部节点、关系和未解决记录；传会话 ID 后仅收窄薄弱点，仍保留文档图谱 | {nodes, edges, weaknesses}。不匹配的会话与文档会抛出 ValueError |
| update_mastery(db, node_id, delta) | 使用 SQL 原子加减并钳位到 0–100；新分数 <40 为 weak，40–79 为 learning，至少 80 为 mastered。新建未评估节点为 unlearned | 更新后的 Node 字典；不会提交。事件 delta 应使用更新后减更新前的实际分差 |
| log_weakness(db, session_id, node_id, weakness_type, detail) | 校验会话和节点的资料归属；类型为 omission、misconception、jargon 或 need_lookup。need_lookup 自动将 lookup_count 加一 | 新日志字典；不会提交，无需再手工增加计数 |
| export_memory_markdown(db, doc_id) | 从同一份 SQL 记忆生成 Markdown | str，四个二级标题 |
| export_memory_json(db, doc_id) | 返回核心概念树、概念关联说明、已掌握清单、薄弱点警示；薄弱点警示含知识点和未解决记录 | dict，API 负责 JSON 序列化 |
| delete_document(db, doc) | 清理文档、外键记录、上传文件与该文档的向量 | 无返回值，自己提交。由资料与后台路由调用 |

核心概念树以节点数组提供，包含关系由 edges 中的 contains 边表达。source_node_id 与 target_node_id 都是知识点主键；prerequisite 的方向为前置概念指向依赖它的概念。可用 concept 传入节点 ID（整数）或概念全名，也可用 query 传入含概念名的讨论文本。同时传入时取匹配并集。额外返回 matched_node_ids、沿 prerequisite 反向追溯的全部 prerequisites、命中节点及其前置节点在同一份资料历次会话的 historical_weaknesses（含已解决记录）。原有 weaknesses 仍按当前会话及未解决状态过滤。节点的 summary、key_points、importance、mastery_score、status、lookup_count、user_note 可直接进入状态图上下文。不要把完整 ORM 对象送入模型。

## 替换为 LangGraph

1. 保留同步生成器入口，在本模块构建和缓存图。把 turn 及经授权的记忆字典传入图。左窗建议读取 context → 对齐原文 → 校验评分结构 → 更新 SQL → 发出追问。右窗建议检索 → 发 citation → 根据原文作答 → 记录查证。
2. 图产生文本增量时 yield Token(delta=...)；百科窗使用 yield Citation(citations=hits)。没有依据时明确说明检索结果为空。记忆写入成功后用 NodeUpdate 组装 MemoryUpdate。只传当前资料中的节点 ID。
3. API 在工作线程中消费事件并通过队列立即发帧。正常结束时，它在同一事务保存用户输入、助手完整文本、引用和记忆变化，提交后发 Done(message_id=...)。Agent 无需自行创建 Message、提交事务或发送 done。中途异常会回滚本轮写入，客户端不会收到 done。连接断开后已接受的轮次继续完成。
4. SQLite 为单写者；当前基线把一轮更新放在同一写事务中。接入高延迟远程推理后，连续流式内容可直接联调；并发容量评估需重点测量写锁等待（busy_timeout=10s），不要在持锁期间加入无界重试。
5. 报告函数返回 agents.contracts.Report。四项分数均按 0–100 输出；weakness_summary 为字符串数组，review_plan 为文本。API 负责唯一性、结束时间与幂等返回。基线的覆盖度按提及节点比例、准确度按文档平均掌握度、通俗度按较长输入比例、逻辑性按连接词比例计算，属于联调规则。替换时建议用课程量规与证据评分。

~~~python
from app.agents.contracts import Citation, Token
from app.services import memory_service as memory

def stream(db, turn):
    hits = memory.search_rag(turn.doc_id, turn.content, top_k=3)
    yield Citation(citations=hits)
    if hits:
        yield Token(delta=hits[0]["snippet"])
    else:
        yield Token(delta="资料中没有可引用的片段。")
~~~

上例只演示事件类型；实际右窗须保留基线中的 need_lookup 记录。左窗的 MemoryUpdate 事件结构和 fetch 解码示例见同目录 API_CONTRACT.md。

## 模型与索引

上传解析使用「教学设置」分配的模型。也可设置 OPENAI_BASE_URL、OPENAI_API_KEY 和 OPENAI_MODEL 作为环境默认模型。短文直接按 JSON schema 抽取，长文分段压缩并分层合并后综合抽取。模型请求或解析失败会进入失败状态，可修正配置后重试。默认向量索引使用 EMBEDDING_PROVIDER=local_lite。

本地基础集合 feynman_doc_chunks 使用显式 384 维中英文词元哈希向量；所有 upsert/query 均传入显式向量，不调用 Chroma 默认下载器。若要使用远程嵌入，将 EMBEDDING_PROVIDER 设为 api，设置 EMBEDDING_MODEL，必要时单独提供 EMBEDDING_API_KEY、EMBEDDING_BASE_URL。系统始终保留本地集合，并按供应商与模型标识为远程向量建立独立集合，避免不同维度混写；远程嵌入失败回退本地集合。切换模型后，已有资料仍可使用本地检索；重新导入可建立新模型的向量。

## 验证

从仓库根目录运行 `backend\.venv\Scripts\python -m pytest backend/tests/test_smoke.py -v`。测试创建临时数据目录，启动真实 Uvicorn 进程，覆盖 Swagger、鉴权、两种窗口的事件与落盘、报告、图谱、四节导出和 SQLite 备份完整性。接入新图后，应继续满足同一协议。

## 增量上下文说明

`get_memory_context(db, doc_id, session_id=None, *, concept=None, query=None)` 保留原有参数和字段，新增以下上下文（旧客户端可忽略）。

- `recommended_learning_path` 为文档级有序数组。每项含 `node_id`、`concept_name`、`mastery_score`、`status`、`prerequisites`（直接前置概念名）和 `reason`。仅 prerequisite 边约束顺序；就绪节点先排未掌握项，再按未解决薄弱记录数、重要度、掌握度和节点 ID 排序。遇环仅在本次计算中放宽环内依赖，理由会提示复讲起点。SQL 图谱保持原样。也可直接调用 `compute_learning_path(db, doc_id)` 或授权读取 `GET /api/v1/documents/{doc_id}/learning-path`。
- 传入有效 `session_id` 时还返回 `recent_encyclopedia_lookups`，按新到旧最多五项。每项含 `question`、`related_concepts`（概念名列表）、`summary`（至多 180 字）。相同轮次的右窗提问与 need_lookup 日志合并，允许独立日志；没有回复时摘要可为空。未指定会话时省略此字段，不跨会话读取消息。
- Markdown / JSON 记忆档案在原四节之后追加「推荐学习与复讲路径」。
