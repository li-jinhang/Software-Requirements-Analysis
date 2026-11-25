import { useEffect, useId, useRef, useState } from "react";
import { ArrowUp, BookMarked, BookOpen, CornerDownLeft, MessagesSquare, X } from "lucide-react";
import { capabilitiesApi, type CapabilityCatalog, type CapabilityRun } from "./api";
import type { SlashItem } from "./slash";
import { commands, filterSlash, parseCommand, slashQuery } from "./slash";
import SlashMenu from "./SlashMenu";
import { Dialog } from "./ui";

type Props = {
  value: string; change: (value: string) => void; send: (text: string) => void;
  command: (name: string, argument: string) => void;
  execute: (body: Pick<CapabilityRun, "kind" | "capability_id" | "tool" | "arguments" | "content" | "confirmed">) => Promise<boolean>;
  onSwitchPane: () => void;
  manager: () => void; online: boolean; locked: boolean; readOnly: boolean; side: number;
  field: (el: HTMLTextAreaElement | null) => void; revision: number;
  context: { title: string; content: string; date: string };
};

export default function CommandComposer(p: Props) {
  const id = useId(), root = useRef<HTMLDivElement>(null), input = useRef<HTMLTextAreaElement | null>(null);
  const [catalog, setCatalog] = useState<CapabilityCatalog>({ skills: [], servers: [] });
  const [selected, setSelected] = useState(0), [dismissed, setDismissed] = useState(false), [focused, setFocused] = useState(false);
  const [loading, setLoading] = useState(false), [error, setError] = useState("");
  const [skill, setSkill] = useState<SlashItem | null>(null), [tool, setTool] = useState<SlashItem | null>(null);
  const [argumentsText, setArgumentsText] = useState("{}"), [invoking, setInvoking] = useState(false);
  const query = slashQuery(p.value);
  useEffect(() => {
    let alive = true;
    if (p.online) {
      setLoading(true);
      capabilitiesApi.list().then(data => { if (alive) { setCatalog(data); setError(""); } }).catch(e => { if (alive) setError(e.message); }).finally(() => { if (alive) setLoading(false); });
    }
    return () => { alive = false; };
  }, [p.online, p.revision, focused]);
  useEffect(() => { setSelected(0); setDismissed(false); }, [query]);
  useEffect(() => {
    const outside = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setDismissed(true); };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, []);
  const items: SlashItem[] = [...commands,
    ...catalog.skills.map(s => ({ id: "skill:" + s.id, name: s.name, command: "/" + s.name, description: s.description, type: "Skills" as const, available: s.available, reason: !s.enabled ? "已停用" : s.reason, skillId: s.id })),
    ...catalog.servers.flatMap(s => s.tools.map(t => ({ id: "mcp:" + s.id + ":" + t.name, name: t.name, command: "/" + t.name, description: s.name + " · " + t.description, type: "MCP" as const, available: s.enabled && s.status === "已连接", reason: !s.enabled ? "连接已停用" : s.status !== "已连接" ? s.status : "", serverId: s.id, toolName: t.name }))),
  ];
  const matches = query === null ? [] : filterSlash(items, query);
  const open = query !== null && focused && !dismissed && !p.readOnly;
  const active = Math.min(selected, Math.max(0, matches.length - 1));
  const toolSchema = catalog.servers.find(s => s.id === tool?.serverId)?.tools.find(t => t.name === tool?.toolName)?.inputSchema;
  const properties = (toolSchema?.properties || {}) as Record<string, { type?: string; description?: string; title?: string; enum?: string[] }>;
  const required = (toolSchema?.required || []) as string[];
  let values: Record<string, unknown> = {};
  try { values = JSON.parse(argumentsText); } catch { /* Keep the raw editor available. */ }
  function parameter(name: string, value: unknown) { setArgumentsText(JSON.stringify({ ...values, [name]: value }, null, 2)); setError(""); }
  function choose(item: SlashItem) {
    if (!item.available || p.locked || invoking) return;
    setDismissed(true); setError("");
    if (item.skillId) { setSkill(item); p.change(""); input.current?.focus(); return; }
    if (item.serverId) {
      setTool(item);
      const schema = catalog.servers.find(s => s.id === item.serverId)?.tools.find(t => t.name === item.toolName)?.inputSchema;
      const fields = (schema?.properties || {}) as Record<string, unknown>;
      const defaults = Object.fromEntries(Object.entries(p.context).filter(([key]) => key in fields));
      setArgumentsText(JSON.stringify(defaults, null, 2)); return;
    }
    if (["role", "lookup"].includes(item.id)) { p.change(item.command + " "); input.current?.focus(); return; }
    p.command(item.id, ""); p.change("");
  }
  async function submit() {
    if (p.locked || invoking) return;
    const text = p.value.trim();
    if (!text) return;
    setError("");
    if (text.startsWith("//")) { p.send(text.slice(1)); return; }
    const parsed = parseCommand(text);
    if (parsed) {
      const builtin = commands.find(c => c.id === parsed.command || c.name.toLowerCase() === parsed.command);
      if (builtin) { p.command(builtin.id, parsed.argument); return; }
      const installed = items.filter(c => c.type === "Skills" || c.type === "MCP").filter(c => c.command.slice(1) === parsed.command);
      if (installed.length === 1) { choose(installed[0]); if (parsed.argument && installed[0].skillId) p.change(parsed.argument); return; }
      setError(installed.length > 1 ? "名称重复，请在 / 菜单中选择具体服务。" : "未知命令。输入 /help 查看用法；普通文本以 / 开头时可输入 //。"); return;
    }
    if (text.startsWith("/")) { setError("请从 / 菜单选择命令或技能。"); return; }
    if (skill) {
      setInvoking(true);
      try { if (await p.execute({ kind: "skill", capability_id: skill.skillId, content: text })) setSkill(null); }
      finally { setInvoking(false); }
    } else p.send(text);
  }
  return <div className="composer reply-box command-composer" ref={root}>
    {open && <SlashMenu items={matches} selected={active} select={choose} id={id} loading={loading} />}
    {skill && <div className="selected-skill"><BookMarked size={14} aria-hidden="true" /><span>{skill.name} · 仅本次任务</span><button aria-label="移除所选技能" disabled={invoking} onClick={() => setSkill(null)}><X size={14} /></button></div>}
    <textarea aria-label={p.side ? "百科问题" : "讲解内容"} ref={el => { input.current = el; p.field(el); }} readOnly={p.readOnly || invoking}
      value={p.value} onFocus={() => setFocused(true)} onBlur={() => setFocused(false)}
      aria-controls={open ? id : undefined} aria-expanded={open} aria-autocomplete="list"
      aria-activedescendant={open && matches.length ? id + "-" + active : undefined}
      onChange={e => p.change(e.target.value)} placeholder={skill ? "输入交给技能处理的任务…" : p.side ? "向百科助教提问，输入 / 使用工具…" : "用自己的话讲一讲，输入 / 使用工具…"}
      onKeyDown={e => {
        if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) return;
        if (open && ["ArrowDown", "ArrowUp"].includes(e.key)) { e.preventDefault(); setSelected((active + (e.key === "ArrowDown" ? 1 : -1) + matches.length) % Math.max(1, matches.length)); return; }
        if (open && e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setDismissed(true); return; }
        if (e.key !== "Enter" || e.shiftKey || e.altKey) return;
        e.preventDefault(); if (e.repeat) return;
        if (open && matches.length) choose(matches[active]); else void submit();
      }} />
    <div className="composer-tools">
      <button type="button" className="quiet switch-pane" onClick={p.onSwitchPane}>
        {p.side ? <MessagesSquare aria-hidden="true" /> : <BookOpen aria-hidden="true" />}
        {p.side ? "回到讲解" : "前往查证"}
      </button>
      <small className="compose-hint"><CornerDownLeft aria-hidden="true" /> 发送 · Shift + Enter 换行</small>
      <button className="send" aria-label={p.side ? "提交查证" : "发送讲解"} disabled={p.locked || invoking || p.readOnly || !p.value.trim()} onClick={() => void submit()}><ArrowUp /></button>
    </div>
    {error && <div className="composer-notice" role="alert">{error} <button className="quiet" onClick={p.manager}>能力中心</button></div>}
    {tool && <Dialog className="tool-dialog" title={"调用工具 · " + tool.name} close={() => { if (!invoking) setTool(null); }}>
      {error && <p role="alert">{error}</p>}
      <p>{tool.description}</p><details><summary>参数格式</summary><pre>{JSON.stringify(catalog.servers.find(s => s.id === tool.serverId)?.tools.find(t => t.name === tool.toolName)?.inputSchema, null, 2)}</pre></details>
      {Object.entries(properties).filter(([, field]) => ["string", "integer", "number", "boolean"].includes(field.type || "")).map(([name, field]) => {
        const labels: Record<string,string> = { title: "标题", content: "内容", date: "复习日期", details: "补充说明", note_id: "笔记编号" };
        return <label key={name}>{labels[name] || field.title || name}{required.includes(name) ? " *" : ""}
          {field.enum ? <select value={String(values[name] || "")} onChange={e => parameter(name,e.target.value)}><option value="">请选择</option>{field.enum.map(value => <option key={value}>{value}</option>)}</select>
          : field.type === "boolean" ? <input type="checkbox" checked={Boolean(values[name])} onChange={e => parameter(name,e.target.checked)} />
          : name === "content" || name === "details" ? <textarea value={String(values[name] || "")} onChange={e => parameter(name,e.target.value)} />
          : <input type={field.type === "number" || field.type === "integer" ? "number" : name === "date" ? "date" : "text"} value={String(values[name] ?? "")} onChange={e => parameter(name,field.type === "number" || field.type === "integer" ? Number(e.target.value) : e.target.value)} />}
          {field.description && <small className="meta">{field.description}</small>}
        </label>;
      })}
      <details><summary>高级参数（JSON）</summary><label>参数（JSON 对象）<textarea className="tool-arguments" value={argumentsText} onChange={e => setArgumentsText(e.target.value)} spellCheck={false} /></label></details>
      <p className="meta">确认后将向此服务发送以上参数。归档或日历工具可能在目标服务创建记录。</p>
      <div className="tools"><button disabled={invoking} onClick={() => setTool(null)}>取消</button><button className="primary" disabled={invoking || p.locked} onClick={async () => {
        let args: Record<string, unknown>;
        try { args = JSON.parse(argumentsText); if (!args || Array.isArray(args) || typeof args !== "object") throw Error(); } catch { setError("参数必须是有效的 JSON 对象"); return; }
        setInvoking(true);
        try { if (await p.execute({ kind: "mcp", capability_id: tool.serverId, tool: tool.toolName, arguments: args, confirmed: true, content: "/" + tool.toolName })) { setTool(null); p.change(""); } else setError("执行未确认完成。关闭此窗口后查看具体错误；请核对目标服务中的结果后再重试。"); }
        finally { setInvoking(false); }
      }}>{invoking ? "执行中…" : "确认执行"}</button></div>
    </Dialog>}
  </div>;
}
