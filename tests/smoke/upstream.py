"""Fake upstream for the smoke test: behind agentgateway target `oura`, a read tool, a write tool, and a log of writes."""

import json

import mcp_types as types
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

server = MCPServer("smoke-upstream")
added: list[str] = []


def reply(data) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data))])


async def sleep(day: str = "2026-09-27") -> types.CallToolResult:
    return reply({"day": day, "score": 81, "contributors": None})


async def add(text: str) -> types.CallToolResult:
    added.append(text)
    return reply({"added": text})


async def added_so_far() -> types.CallToolResult:
    return reply(added)


read_only = types.ToolAnnotations(read_only_hint=True)
server.add_tool(sleep, name="sleep", description="One night's sleep score.", annotations=read_only)
server.add_tool(added_so_far, name="added", description="The texts add has stored, in order.", annotations=read_only)
server.add_tool(add, name="add", description="Store a text.")  # no readOnlyHint: switchboard treats it as a write
app = server.streamable_http_app(
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False), host="0.0.0.0"
)
uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
