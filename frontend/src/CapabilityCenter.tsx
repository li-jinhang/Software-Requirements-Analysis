import { useEffect, useState } from "react";
import { capabilitiesApi as api, type CapabilityCatalog, type UserSkill, type McpServer } from "./api";
import { Dialog } from "./ui";
import "./capabilities.css";

export default function CapabilityCenter({ close, admin = false, changed, initialTab = "skills" }: { close: () => void; admin?: boolean; changed?: () => void; initialTab?: "skills" | "mcp" }) {
  const [tab, setTab] = useState<"skills" | "mcp">(initialTab);
  const [data, setData] = useState<CapabilityCatalog>({ skills: [], servers: [] });
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [replace, setReplace] = useState(false), [detail, setDetail] = useState<(UserSkill & { instructions: string }) | null>(null);
  const [editing, setEditing] = useState<McpServer | null | undefined>();
  const [draft, setDraft] = useState({ name: "", url: "", token: "", enabled: true });
  const [confirm, setConfirm] = useState<{ label: string; work: () => Promise<unknown> } | null>(null);
  const refresh = async () => { setData(await api.list()); changed?.(); };
  useEffect(() => { void refresh().catch(e => setError(e.message)); }, []);
  async function run(work: () => Promise<unknown>, message = "已保存") {
    if (busy) return;
    setBusy(true); setError(""); setNotice("");
    try { await work(); await refresh(); setNotice(message); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function edit(server?: McpServer) {
    setDraft({ name: server?.name || "", url: server?.url || "", token: "", enabled: server?.enabled ?? true });
    setEditing(server || null);
  }
  return <Dialog title="能力中心" close={close} className="capability-center">
    <div className="tools capability-tabs"><button aria-pressed={tab === "skills"} onClick={() => setTab("skills")}>Skills</button><button aria-pressed={tab === "mcp"} onClick={() => setTab("mcp")}>MCP 服务</button></div>
    <p className="meta">能力与当前登录账号关联。对话输入 / 搜索并调用；安装内容不会自动发送给模型。</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {tab === "skills" ? <>
      <div className="tools"><label className="capability-import">导入技能 ZIP<input type="file" accept=".zip,application/zip" disabled={busy} onChange={e => { const file = e.target.files?.[0]; e.target.value = ""; if (file) void run(() => api.importSkill(file, replace), "技能已导入，可在 / 菜单中选择"); }} /></label>
      <button disabled={busy} onClick={() => run(api.template, "示例技能包已下载，可直接导入验收")}>下载示例技能</button>
      <label className="inline-check"><input type="checkbox" checked={replace} onChange={e => setReplace(e.target.checked)} />更新同名技能</label></div>
      {!data.skills.length && <div className="capability-empty">尚未安装技能。导入含 SKILL.md 的 ZIP，或先下载示例体验。</div>}
      {data.skills.map(skill => <article className="capability-item" key={skill.id}><div><strong>{skill.name}</strong><small>v{skill.version} · {skill.shared ? "公共技能" : "个人技能"} · {skill.enabled ? skill.reason || "可调用" : "已停用"}</small><p>{skill.description}</p></div><div className="tools">
        <button disabled={busy} onClick={() => run(async () => setDetail(await api.skill(skill.id)), "")}>详情</button>
        {skill.editable && <><button disabled={busy} onClick={() => run(() => api.changeSkill(skill.id, { enabled: !skill.enabled }))}>{skill.enabled ? "停用" : "启用"}</button>
        {admin && <button disabled={busy} onClick={() => run(() => api.changeSkill(skill.id, { shared: !skill.shared }))}>{skill.shared ? "取消公共" : "设为公共"}</button>}
        <button className="quiet" disabled={busy} onClick={() => setConfirm({ label: "卸载技能「" + skill.name + "」？已保存的对话保留。", work: () => api.removeSkill(skill.id) })}>卸载</button></>}
      </div></article>)}
      <p className="meta">当前支持说明、参考资料和模板类技能。含脚本、工具或环境依赖的技能会标为“需要适配”，不会假装执行。</p>
    </> : <>
      <div className="tools"><button disabled={busy} onClick={() => edit()}>添加 MCP 服务</button><button disabled={busy} onClick={() => run(refresh, "已刷新")}>刷新列表</button></div>
      <p className="meta">支持 Streamable HTTP。连接地址和令牌由本机工作台管理，令牌不会显示在工具列表中。连接后工具会出现在 / 菜单。</p>
      {!data.servers.length && <div className="capability-empty">尚未连接 MCP 服务。可按验收说明启动附带的学习档案 MCP 服务。</div>}
      {data.servers.map(server => <article className="capability-item" key={server.id}><div><strong>{server.name}</strong><small>{server.url} · {server.enabled ? server.status : "已停用"} · {server.tools.length} 个工具</small>
        <details><summary>查看工具与参数</summary>{server.tools.map(tool => <div key={tool.name}><strong>{tool.name}</strong><p>{tool.description}</p><pre>{JSON.stringify(tool.inputSchema, null, 2)}</pre></div>)}</details>
      </div><div className="tools"><button disabled={busy || !server.enabled} onClick={() => run(() => api.connect(server.id), "已连接并刷新工具目录")}>连接 / 刷新工具</button>
      <button disabled={busy} onClick={() => edit(server)}>编辑</button><button className="quiet" disabled={busy} onClick={() => setConfirm({ label: "移除 MCP 连接「" + server.name + "」？", work: () => api.removeServer(server.id) })}>移除</button></div></article>)}
    </>}
    {detail && <Dialog title={detail.name} close={() => setDetail(null)}><p>{detail.description}</p><pre className="skill-body">{detail.instructions}</pre><h3>配套文件</h3><ul>{detail.files.map(f => <li key={f}>{f}</li>)}</ul></Dialog>}
    {editing !== undefined && <Dialog title={editing ? "编辑 MCP 服务" : "添加 MCP 服务"} close={() => { if (!busy) setEditing(undefined); }}>
      <form onSubmit={e => { e.preventDefault(); void run(async () => { await api.saveServer({ ...draft, token: editing && !draft.token ? undefined : draft.token }, editing?.id); setEditing(undefined); }); }}>
        <label>名称<input required value={draft.name} maxLength={100} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
        <label>MCP 地址<input type="url" required placeholder="http://127.0.0.1:18090/mcp" value={draft.url} onChange={e => setDraft({ ...draft, url: e.target.value })} /></label>
        <label>Bearer 令牌<input type="password" autoComplete="new-password" placeholder={editing?.has_token ? "已保存，留空沿用" : "免密服务可留空"} value={draft.token} onChange={e => setDraft({ ...draft, token: e.target.value })} /></label>
        <label className="inline-check"><input type="checkbox" checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} />启用连接</label>
        <button className="primary" disabled={busy}>{busy ? "保存中…" : "保存连接"}</button>
      </form>
    </Dialog>}
    {confirm && <Dialog title="确认操作" close={() => setConfirm(null)}><p>{confirm.label}</p><div className="tools"><button onClick={() => setConfirm(null)}>取消</button><button disabled={busy} onClick={() => { const work = confirm.work; setConfirm(null); void run(work); }}>确认</button></div></Dialog>}
  </Dialog>;
}
