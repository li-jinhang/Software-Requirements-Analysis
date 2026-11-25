export type SlashItem = { id: string; name: string; command: string; description: string; type: "命令" | "项目工具" | "Skills" | "MCP"; available: boolean; reason?: string; skillId?: string; serverId?: string; toolName?: string };
export const commands: SlashItem[] = [
  ["help", "帮助", "查看命令及使用方式"],
  ["role", "切换听众", "选择小学生、同窗或考官"],
  ["lookup", "课件查证", "检索当前课件，在右窗展示原文"],
  ["progress", "学习进度", "查看掌握度和薄弱点"],
  ["review", "开始复习", "选择认知条目进行复讲"],
  ["report", "诊断报告", "结束本轮并生成报告"],
  ["history", "会话历史", "查看最近的双窗记录"],
  ["graph", "知识图谱", "打开当前课程图谱"],
  ["model", "切换模型", "打开模型配置，点击模型行切换"],
  ["skills", "Skills", "导入、选择和管理自己的技能"],
  ["tools", "项目工具", "查看课程检索、进度、报告等工具"],
  ["mcp", "MCP", "管理 MCP 连接和已发现的工具"],
].map(([id, name, description]) => ({ id, name, command: "/" + id, description, type: ["lookup", "progress", "graph", "report"].includes(id) ? "项目工具" : "命令", available: true }));

export function slashQuery(text: string): string | null {
  return /^\/[^\s/]*$/.test(text) ? text.slice(1).toLowerCase() : null;
}
export function filterSlash(items: SlashItem[], query: string) {
  return items.filter(item => (item.command + " " + item.name + " " + item.description + " " + item.type).toLowerCase().includes(query.toLowerCase()));
}
export function parseCommand(text: string) {
  const match = /^\/([^\s/]+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  return match ? { command: match[1].toLowerCase(), argument: (match[2] || "").trim() } : null;
}
