"""Bounded MCP Streamable HTTP calls using the official SDK."""
import asyncio
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from jsonschema import Draft202012Validator, SchemaError

from app.services import extension_store as storage


def validate_url(value):
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.fragment or parts.query:
        raise ValueError("请输入不含凭据、查询参数的 HTTP(S) MCP 地址，令牌填写在独立字段")
    # Remote workbenches cannot pivot into the server's private network.
    return value.rstrip("/")


async def request(row, tool=None, arguments=None):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    if not row["enabled"]:
        raise HTTPException(409, "此 MCP 连接已停用")
    headers = {"Authorization": "Bearer " + row["token"]} if row["token"] else {}
    try:
        async with asyncio.timeout(45):
            async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=False, trust_env=False) as client:
                async with streamable_http_client(row["url"], http_client=client) as (read, write, _):
                    async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=35)) as session:
                        await session.initialize()
                        page = await session.list_tools()
                        tools = list(page.tools)
                        while page.nextCursor:
                            if len(tools) >= 200:
                                raise HTTPException(422, "服务工具超过 200 项，请拆分服务")
                            page = await session.list_tools(cursor=page.nextCursor)
                            tools.extend(page.tools)
                        catalog = [{"name": t.name, "description": (t.description or "")[:1200],
                                    "inputSchema": t.inputSchema, "annotations": t.annotations.model_dump() if t.annotations else {}}
                                   for t in tools]
                        storage.update_status(row["id"], catalog)
                        if tool is None:
                            return catalog
                        selected = next((t for t in catalog if t["name"] == tool), None)
                        if not selected:
                            raise HTTPException(404, "此服务没有提供选定工具，请刷新工具列表")
                        try:
                            Draft202012Validator.check_schema(selected["inputSchema"])
                            errors = list(Draft202012Validator(selected["inputSchema"]).iter_errors(arguments or {}))
                        except SchemaError:
                            raise HTTPException(422, "工具参数结构无效") from None
                        if errors:
                            raise HTTPException(422, "工具参数不符合要求：" + errors[0].message[:250])
                        result = await session.call_tool(tool, arguments or {})
                        if result.isError:
                            raise HTTPException(502, "MCP 工具返回失败，本轮未标记成功")
                        # Only text/structured results are embedded; no active HTML or remote images.
                        return {"text": "\n".join(c.text for c in result.content if c.type == "text")[:60000],
                                "structured": result.structuredContent}
    except HTTPException:
        raise
    except Exception as exc:
        # anyio task groups may wrap an application validation exception when
        # the HTTP transport closes. Preserve its original status and message.
        pending = [exc]
        while pending:
            error = pending.pop()
            if isinstance(error, HTTPException):
                raise error from None
            pending.extend(getattr(error, "exceptions", ()))
        storage.update_status(row["id"], status="连接失败，请检查地址和授权")
        raise HTTPException(502, "MCP 连接失败或超时；工具是否产生外部结果需在目标服务核对后再重试") from None
