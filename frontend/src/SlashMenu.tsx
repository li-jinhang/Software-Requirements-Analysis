import { useEffect, useRef } from "react";
import { Wrench, BookMarked, Terminal, Plug } from "lucide-react";
import type { SlashItem } from "./slash";
import "./capabilities.css";

export default function SlashMenu({ items, selected, select, id, loading }: { items: SlashItem[]; selected: number; select: (item: SlashItem) => void; id: string; loading: boolean }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => { root.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" }); }, [selected]);
  return <div className="slash-menu" id={id} role="listbox" aria-label="命令、工具与技能" ref={root}>
    <div className="slash-heading">命令 · 工具 · Skills · MCP <span>↑ ↓ 选择 · Enter 确认 · Esc 关闭</span></div>
    {loading && <p role="status">正在读取已安装能力…</p>}
    {!loading && !items.length && <p>没有匹配项。输入 /skills 管理技能，或 /mcp 管理连接。</p>}
    {items.map((item, i) => {
      const Icon = item.type === "Skills" ? BookMarked : item.type === "MCP" ? Plug : item.type === "项目工具" ? Wrench : Terminal;
      return <div key={item.id} id={id + "-" + i} role="option" aria-selected={i === selected} aria-disabled={!item.available}
        className="slash-option" onMouseDown={e => e.preventDefault()} onClick={() => item.available && select(item)}>
        <Icon aria-hidden="true" size={18} /><div><strong>{item.name}</strong><small>{item.reason || item.description}</small></div>
        <span>{item.available ? item.type === "Skills" || item.type === "MCP" ? item.type : item.command : "不可用"}</span>
      </div>;
    })}
  </div>;
}
