"""Local, real MCP service: learning notes and portable calendar reminders.

Run: python backend/scripts/learning_mcp.py --port 18090
Connect the workbench to http://127.0.0.1:18090/mcp .
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from mcp.server.fastmcp import FastMCP


def create_server(folder: Path, port=18090):
    folder.mkdir(parents=True, exist_ok=True)
    server = FastMCP("学习档案", host="127.0.0.1", port=port, stateless_http=True, json_response=True)

    @server.tool()
    def archive_learning_note(title: str, content: str) -> dict:
        """把用户确认的学习笔记保存为 Markdown，返回可查找的记录编号与路径。"""
        if not title.strip() or not content.strip() or len(content) > 100000:
            raise ValueError("标题和内容不能为空，内容最多 100000 字")
        id = uuid4().hex
        path = folder / (id + ".md")
        path.write_text("# " + title.replace("\n", " ")[:200] + "\n\n" + content, encoding="utf-8")
        return {"id": id, "path": str(path), "title": title}

    @server.tool()
    def list_learning_notes() -> list[dict]:
        """查看最近归档的学习笔记。"""
        return [{"id": p.stem, "title": p.read_text(encoding="utf-8").splitlines()[0].lstrip("# ")}
                for p in sorted(folder.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:30]]

    @server.tool()
    def read_learning_note(note_id: str) -> str:
        """根据归档记录编号读取笔记。"""
        if not re.fullmatch(r"[0-9a-f]{32}", note_id):
            raise ValueError("笔记编号不正确")
        return (folder / (note_id + ".md")).read_text(encoding="utf-8")

    @server.tool()
    def create_review_reminder(title: str, date: str, details: str = "") -> dict:
        """创建复习提醒 ICS 文件；date 为 YYYY-MM-DD，导入个人日历后生效。"""
        day = datetime.strptime(date, "%Y-%m-%d").strftime("%Y%m%d")
        def escape(value):
            return value.replace("\\", "\\\\").replace("\r", "").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")
        id = uuid4().hex
        path = folder / (id + ".ics")
        content = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//FeynmanMind//Review//ZH", "BEGIN:VEVENT",
                   "UID:" + id + "@feynmanmind", "DTSTAMP:" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                   "DTSTART;VALUE=DATE:" + day, "SUMMARY:" + escape(title[:200]), "DESCRIPTION:" + escape(details[:10000]),
                   "END:VEVENT", "END:VCALENDAR", ""]
        path.write_bytes("\r\n".join(content).encode("utf-8"))
        return {"id": id, "path": str(path), "status": "已创建 ICS 文件，导入日历后生效"}

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18090)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "mcp-notes")
    args = parser.parse_args()
    create_server(args.directory, args.port).run(transport="streamable-http")
