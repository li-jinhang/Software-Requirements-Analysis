# 智溯费曼 · Agent 与混合记忆接口

## 接入边界

前端发送的会话 ID 由 API 层鉴权。API 加载资料和角色，构造 AgentInput，调用 agents 下的模块，将强类型事件序列化成 SSE 并保存消息。Agent 负责教学推理、引用选择与记忆更新。只需替换以下三个模块中的入口函数，保留签名与事件类型，即可继续运行同一套冒烟测试。

| 文件（相对 backend/app） | 保留入口 | 职责 |
| --- | --- | --- |
| agents/feynman_agent.py | stream(db, turn: AgentInput) → Iterator[AgentEvent] | 左窗讲解评估、追问、写掌握度/薄弱点、推送更新 |
| agents/encyclopedia_agent.py | stream(db, turn: AgentInput) → Iterator[AgentEvent] | 右窗检索、引用回答、记录 need_lookup |
| agents/report_agent.py | generate(db, session) → Report | 用 SQL 记忆汇总四维评估和复习建议 |

AgentInput 含 session_id、doc_id、persona_type、content，可选 target_node_id，以及默认为 explain 的 action（opening 用于角色开场）。persona_type 是 child、peer、examiner 之一。db 为同一轮专用的同步 SQLAlchemy Session，不要传给并行图节点或其他线程。Agent 内使用 flush 可拿到新主键，最终 commit/rollback 由 API 负责。路由按会话校验归属，直接从脚本调用服务时由调用者保证访问权限。

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

U0003/U0004 已在 `agents/teaching/graph.py` 接入真实 StateGraph，`feynman_agent.stream` 保留入口签名。当前教学图的状态、事务细节与本地验收见 [LANGGRAPH_ACCEPTANCE.md](LANGGRAPH_ACCEPTANCE.md)。API 调用该图前不得预先添加待提交消息：图读取授权上下文后会结束只读事务，以避免远程推理期间占用 SQLite 快照。图使用运行时 context 注入本轮 db，不把连接存入图状态，也不并发共享同一 SQLAlchemy Session。

`TurnMetadata` 是新增的内部结果，API 将其合并到助手消息的 `meta_json`，不作为 SSE 发送。它记录评估证据、实际节点路径和连续达标次数。教学图在流式追问结束后尝试以保存点写入记忆；API 最终同时提交消息和记忆，提交成功才发送 `memory_update` 与 `done`。记忆写入失败可仅保存追问和失败标记；模型或最终提交失败不发 `done`。现有百科与报告保留基线实现。

1. 左窗构建和缓存图，通过运行时 context 注入本轮 turn 和 db，读取 SQL 与历史后释放只读事务。图执行输入分流、原文检索、评估、一次结构纠错、记忆方案、流式追问及最终记忆写入。右窗仍为检索 → citation → 原文回答 → 记录查证的基线。
2. 图产生文本增量时 yield Token(delta=...)；百科窗使用 yield Citation(citations=hits)。没有依据时明确说明检索结果为空。记忆写入成功后用 NodeUpdate 组装 MemoryUpdate。只传当前资料中的节点 ID。
3. API 在工作线程中消费事件，通过队列立即发送 token/citation。memory_update 暂存到事务提交成功后发送。正常结束时，在同一事务保存用户输入、助手完整文本、引用、评估和记忆变化，再发 Done(message_id=...)。Agent 不自行创建 Message、提交事务或发送 done。模型或最终提交失败时回滚本轮写入，不发 done；流已开始时以 error 事件说明原因。连接断开后已接受的轮次继续完成。
4. SQLite 为单写者。教学图在远程推理结束后才开启短写事务；可选记忆写入使用保存点，失败仍保留追问。单 worker 内按会话串行执行教学、开场、角色切换和报告，其他会话可并行推理。并发容量仍需实测，不要在持锁期间加入无界重试。
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

默认 MOCK_LLM=true、EMBEDDING_PROVIDER=local_lite，无需 Key 或模型下载。离线抽取优先使用章节名、定义前缀和段落；很短的资料会用标明“离线阅读提示”的条目补齐 6 项。真实抽取将 MOCK_LLM 设为 false，填写 OPENAI_BASE_URL、OPENAI_API_KEY 和供应商对应的 OPENAI_MODEL，重启服务后上传新资料。短文直接按 JSON schema 抽取，长文分段压缩并分层合并后综合抽取。每个模型步骤最多额外重试两次，持续失败时记录异常类型并降级离线抽取。文件损坏仍标记为 failed。

本地基础集合 feynman_doc_chunks 使用显式 384 维中英文词元哈希向量；所有 upsert/query 均传入显式向量，不调用 Chroma 默认下载器。若要使用远程嵌入，将 EMBEDDING_PROVIDER 设为 api，设置 EMBEDDING_MODEL，必要时单独提供 EMBEDDING_API_KEY、EMBEDDING_BASE_URL。系统始终保留本地集合，并按供应商与模型标识为远程向量建立独立集合，避免不同维度混写；远程嵌入失败回退本地集合。切换模型后，已有资料仍可使用本地检索；重新导入可建立新模型的向量。

## 验证

从仓库根目录运行 `backend\.venv\Scripts\python -m pytest backend/tests/test_smoke.py -v`。测试创建临时数据目录，启动真实 Uvicorn 进程，覆盖 Swagger、鉴权、两种窗口的事件与落盘、报告、图谱、四节导出和 SQLite 备份完整性。接入新图后，应继续满足同一协议。
