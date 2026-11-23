# Software Requirements Analysis

## 智溯费曼 · FeynmanMind

按 [软件需求规格说明](SRS.md) 实现的 React 18 学习工作台。前端源码在 [frontend](frontend/)。界面沿用 LJHAgent 的中性白灰侧栏、石墨色按钮和本机中文字体，双窗分别使用靛蓝与青绿标记。

### 本地运行

需要 Node.js 20 或更高版本。在仓库根目录执行以下命令，然后打开 Vite 输出的本地地址。

```sh
cd frontend
npm ci
npm run dev
```

执行 `npm run build` 会检查 TypeScript 并生成可静态托管的 `dist/`。执行 `npm run preview` 可预览构建结果。

### 体验教学流程

1. 首页内置三份示范资料。可搜索、按学科筛选，或上传 20 MB 以内的 PDF、DOCX、MD、TXT。记忆档案支持概念笔记和 Markdown、JSON 导出。
2. 在课程卡片点击「开讲」，选择知识点和听众。左侧讲解、右侧查证可独立输入，追问可直接带入右窗，引用可展开。桌面支持拖动分隔条或使用左右方向键调宽，手机通过页签切窗。
3. 从图谱点选节点可发起专项考核。完成至少两轮讲解后，可生成诊断报告、查看四维雷达图、导出 Markdown，或通过浏览器打印保存 PDF。
4. 侧栏底部可注册个人空间、切换学生演示或管理员演示。管理页提供薄弱点排行、分页检索、记录清理及 JSON / SQLite 导出。

### 数据与后端联调

默认使用浏览器本地存储。演示模式按文本片段建立档案，使用课程原文和规则模拟追问、掌握度与四维评分。演示管理员可审阅本浏览器的本地演示空间。SQLite 演示快照按空间保存 JSON 数据，在线服务使用后端返回的数据库文件。

如需连接 FastAPI，在 `frontend/.env.local` 设置 `VITE_BACKEND_URL=http://127.0.0.1:8000`，重启 Vite 或重新构建，再通过侧栏账号入口登录服务端账号。后端须允许前端来源访问。内置学生与管理员演示入口始终使用本地数据。

服务适配器位于 [api.ts](frontend/src/api.ts)，按 SRS 第 5.2 节使用 `/api/v1` 下的认证、上传、记忆、图谱、会话、双窗 SSE、报告与管理接口。在线空间缓存本浏览器已打开的课程、会话、报告及草稿。真实鉴权、知识抽取、检索和评分由后端提供。

| 联调项 | 前端采用的约定 |
| --- | --- |
| 登录与上传 | 登录返回 `access_token` 和含 `role`、`nickname` 的 `user`。上传返回 `doc_id` 或 `id`，随后查询记忆的 `parse_status` 及图谱的 `nodes`、`edges`。 |
| 双窗消息 | POST 以 Bearer JWT 发送 `content`、`node_id`、`persona_type`。事件 `token` 追加文本，`citation` 提供 `doc_title`、`chunk_index` 和可选 `excerpt`，`memory_update` 更新节点分数与评估，`done` 结束回复。 |
| 笔记同步 | PATCH `/documents/{id}/memory` 发送 `node_id` 和 `user_note`。 |
| 后台审阅 | GET `/admin/tables/{table}?q=&page=1&page_size=10` 返回 `{items,total}`，每行含 `id,name,status,updated_at`。DELETE 同路径下的 `/{id}` 清理记录。服务端校验管理员权限并维护关联记录。 |

笔记写入和后台分页维护的具体路径是前端为 SRS 用例补充的接口约定，可在联调时集中调整。仓库当前提供前端，完整 FastAPI、LangGraph、SQLite 和 ChromaDB 服务需由后端实现。
