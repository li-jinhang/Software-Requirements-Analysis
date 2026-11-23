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

~~~powershell
python dev.py
~~~

打开 [学习工作台](http://127.0.0.1:5173) 和 [Swagger](http://127.0.0.1:8000/docs)。启动器为 Vite 设置 VITE_BACKEND_URL，Ctrl+C 同时结束两个进程。它固定使用 5173 和 8000；若先前的服务还在运行，请在原终端停止它再启动，以便载入本次更新。首次启动自动建立演示库；已有一期数据库会补上知识点要点字段。默认无需 API Key。

从侧栏的**账号登录**进入服务端空间，用学生 `student / 123456` 或管理员 `admin / admin123` 登录；侧栏的快捷演示入口使用浏览器本地数据。先试登录、上传 [课程样例](backend/data/sample_docs/) 和打开图谱，再按 [联调体检清单](docs/FRONTEND_SYNC_CHECKLIST.md) 核对双窗、笔记和导出。前端第一版有已知协议差异，完整后端流程可直接在 Swagger 中验证。

需要分开调试时，在两个终端中从仓库根目录执行：

~~~powershell
# 终端一
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
~~~

~~~powershell
# 终端二
cd frontend
$env:VITE_BACKEND_URL = "http://127.0.0.1:8000"
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
~~~

VITE_BACKEND_URL 填服务根地址，前端会拼接 /api/v1。仅运行 Vite 且不设置此变量可体验纯前端演示。前端构建命令为 `npm.cmd --prefix frontend run build`。

## 本地教学演示

浏览器演示空间内置三份示范资料，支持搜索、学科筛选和上传 20 MB 以内的 PDF、DOCX、MD、TXT。从课程卡片点击「开讲」，选择知识点和听众；可将左窗追问带入右窗查证。桌面支持拖动分隔条，手机通过页签切窗。完成两轮讲解后可查看四维雷达图与复习建议，并导出 Markdown 或通过浏览器保存 PDF。

本地模式按文本片段和规则生成教学反馈。演示管理员审阅本浏览器的演示空间；服务端管理员使用独立的 SQLite 数据库与管理接口。

## 模型、检索与测试

将 backend/.env.example 复制为 backend/.env 后填写 OPENAI_API_KEY、OPENAI_BASE_URL 与 OPENAI_MODEL。设 MOCK_LLM=false 后重启服务，新上传资料会走结构化抽取。长文分段归纳并合并，模型不可用时回退至本地规则。默认 EMBEDDING_PROVIDER=local_lite；如需验证远程向量，改为 api，并填写供应商实际支持的 EMBEDDING_MODEL 与对应的嵌入端点及凭据。

~~~powershell
# 显式调用真实模型：使用仓库样例与一次性索引，打印每一阶段耗时
.\backend\.venv\Scripts\python.exe backend/scripts/check_llm_live.py

# 完整离线回归
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
~~~

[前端接口契约](docs/API_CONTRACT.md) · [Agent 记忆服务接口](docs/AGENT_INTERFACE.md) · [第一版前端联调体检](docs/FRONTEND_SYNC_CHECKLIST.md)
