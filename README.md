# 智溯费曼 · FeynmanMind

React 学习工作台提供费曼反向讲解和百科溯源双窗，FastAPI 负责认证、SQL 知识图谱、ChromaDB 检索、教学会话和报告。需求规格见 [SRS.md](SRS.md)。

## 首次安装

在仓库根目录运行以下 PowerShell 命令，需要 Python 3.12 和 Node.js 20 或以上版本。若已完成第一阶段安装，可复用 backend/.venv。首次需要网络下载依赖。

~~~powershell
python -m venv backend/.venv
.\backend\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
npm.cmd --prefix frontend ci
~~~

## 一键联调

Windows 完成首次安装后，双击仓库根目录的 [run.bat](run.bat) 即可同时启动前端和后端。也可以在终端运行：

~~~powershell
python dev.py
~~~

打开 [学习工作台](http://127.0.0.1:5173) 和 [Swagger](http://127.0.0.1:18080/docs)。启动器为 Vite 设置 VITE_BACKEND_URL，Ctrl+C 同时结束两个进程。默认前端 5173、后端 18080，可用 `--frontend-port` 和 `--backend-port` 调整。首次启动自动建立演示库；已有数据库会补上知识点要点及会话目标节点字段。开始教学前请先配置模型。

从侧栏的**账号登录**进入服务端空间，用学生 `student / 123456` 或管理员 `admin / admin123` 登录；侧栏的快捷演示入口使用浏览器本地数据。可上传 [课程样例](backend/data/sample_docs/)，选择听众开始讲解，在右窗查证后回到左窗复讲。在线会话、引用、掌握度和报告从服务端恢复，未发送的草稿保存在当前浏览器。[前端联调清单与验收记录](docs/FRONTEND_SYNC_CHECKLIST.md)

需要分开调试时，在两个终端中从仓库根目录执行：

~~~powershell
# 终端一
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18080
~~~

~~~powershell
# 终端二
cd frontend
$env:VITE_BACKEND_URL = "http://127.0.0.1:18080"
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
~~~

VITE_BACKEND_URL 填服务根地址，前端会拼接 /api/v1。仅运行 Vite 且不设置此变量可体验纯前端演示。前端构建命令为 `npm.cmd --prefix frontend run build`。

## 浏览示范资料

浏览器演示空间内置三份示范资料，可搜索、筛选和浏览记忆档案。上传解析、开讲、双窗回复和诊断报告使用已配置的模型和学习账号。缺少模型时，弹窗可直接打开模型配置。

## 模型、检索与测试

在运行服务的电脑使用 [本机工作台](http://127.0.0.1:5173)，打开右上角 **设置 → 模型配置**，可直接新建和编辑配置。配置供本机教学服务使用，远程登录的学习账号可查看各环节使用的模型。

1. **配置 API**。点击「新建配置」，填写名称、OpenAI 兼容 Base URL、密钥与模型 ID。可先获取模型目录，也可手动填写。支持 HTTP(S) 兼容服务，包括局域网推理端点。保存后点「测试连接」会发起一次真实的文本请求。
2. **使用 ChatGPT 账号**。在「新建配置」中切换至「ChatGPT 登录」。在运行后端的电脑上点击「登录 ChatGPT」并打开授权页。浏览器通过本机 1455 端口回调。显示已登录后，选择该账号支持的模型并保存配置。服务端管理令牌与续期。退出 ChatGPT 会影响所有使用该账号的配置。
3. **分配模型**。在 **设置 → 教学设置** 分配。首个配置自动成为默认模型，可为资料解析、费曼听众、百科助教和诊断报告分别覆盖。修改自下一次请求生效。已生成的课程、消息和报告保留原结果。删除配置前须先解除绑定。
4. **迁移配置**。点击「导出配置」下载模型和应用绑定的 JSON 文件。在另一台电脑上点击「导入配置」，文件中的方案和绑定会补充到本机。已有方案及已分配的绑定优先。换机器后补填 API Key 或重新授权 ChatGPT。
5. **体验教学**。用服务端学生账号上传资料、开始教学。解析模型生成知识点和关系；听众结合原文与最近对话评估讲解并追问；百科模型依据检索片段回答；报告模型综合学习记录给出四维评估和复习计划。服务端检查知识点归属、引用范围和得分，校验通过后提交本轮数据。结构化回复经过校验后通过原有双窗 SSE 协议发送。若资料图谱超出模型单次输出上限，后端会让同一模型分步生成概念大纲、定义和关系，再校验完整图谱。

方案和凭据保存在后端数据目录的私有 `model-settings/profiles.sqlite3`，由运行后端的系统账号保管。教学 JSON 和数据库导出仅包含业务数据。显式绑定的方案失败时界面会显示错误。模型读取超时可用 `MODEL_TIMEOUT_SECONDS` 调整，默认 180 秒。

也可在 `backend/.env` 设置 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `OPENAI_MODEL`，重启后作为四个环节的环境默认值。旧配置中的 `MOCK_LLM=true` 会停用环境模型。嵌入模型独立配置，默认 `EMBEDDING_PROVIDER=local_lite`，远程嵌入使用 `api` 和供应商对应的嵌入模型、端点及凭据。

~~~powershell
# 显式调用真实模型：使用仓库样例与一次性索引，打印每一阶段耗时
.\backend\.venv\Scripts\python.exe backend/scripts/check_llm_live.py

# 完整离线回归
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
~~~

[前端接口契约](docs/API_CONTRACT.md) · [Agent 记忆服务接口](docs/AGENT_INTERFACE.md) · [第一版前端联调体检](docs/FRONTEND_SYNC_CHECKLIST.md)

U0003/U0004 已接入真实 LangGraph 教学图，复用统一模型配置，提供三种角色开场、SQL＋RAG 评估、证据校验、定向追问和连续两轮掌握判定。见 [LangGraph 验收说明](docs/LANGGRAPH_ACCEPTANCE.md)、[原版工作台验收](docs/FRONTEND_LOCAL_ACCEPTANCE.md) 和 [2026-11-24 更新报告](docs/UPDATE_REPORT_2026-11-24.md)。
