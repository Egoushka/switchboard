"""Fake upstream for the smoke test: one read tool behind agentgateway target `oura`."""

import json

import mcp_types as types
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

server = MCPServer("smoke-upstream")


async def sleep(day: str = "2026-09-27") -> types.CallToolResult:
    data = {"day": day, "score": 81, "contributors": None}
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(data))])


server.add_tool(sleep, name="sleep", description="One night's sleep score.",
                annotations=types.ToolAnnotations(read_only_hint=True))
app = server.streamable_http_app(
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False), host="0.0.0.0"
)
uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
