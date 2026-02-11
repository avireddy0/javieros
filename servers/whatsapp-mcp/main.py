"""
WhatsApp MCP Server for Open WebUI.
"""

from __future__ import annotations
import logging, os
from typing import Annotated, Any
import httpx
from mcp.server.fastmcp import FastMCP, Context
from pydantic import Field

BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://localhost:3000").rstrip("/")
BRIDGE_TOKEN = os.getenv("WHATSAPP_BRIDGE_TOKEN", "")
DEFAULT_USER_ID = os.getenv("WHATSAPP_DEFAULT_USER_ID", "default")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("whatsapp-mcp")
if not BRIDGE_TOKEN: raise RuntimeError("WHATSAPP_BRIDGE_TOKEN required")
_client: httpx.AsyncClient | None = None

async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, 60.0, 30.0, 10.0))
    return _client

def _get_user_id(ctx: Context) -> str:
    if hasattr(ctx, "request_context") and ctx.request_context:
        if uid := ctx.request_context.get("user_id"): return str(uid)
    if hasattr(ctx, "headers") and ctx.headers:
        if uid := ctx.headers.get("x-user-id") or ctx.headers.get("X-User-ID"): return str(uid)
    return DEFAULT_USER_ID

async def _bridge(req, method, path, uid, body=None, timeout=30.0):
    hdrs = {"X-WhatsApp-Bridge-Token": BRIDGE_TOKEN, "X-User-ID": uid, "Content-Type": "application/json"}
    try:
        r = await (await get_client()).request(method, f"{BRIDGE_URL}{path}", headers=hdrs, json=body, timeout=timeout)
        r.raise_for_status()
        return r.json() if "json" in r.headers.get("content-type", "") else r.text
    except httpx.TimeoutException: return {"error": "timeout"}
    except httpx.HTTPStatusError as e: return {"error": f"{e.response.status_code}: {e.response.text}"}
    except Exception as e: return {"error": str(e)}

mcp = FastMCP("whatsapp", description="WhatsApp messaging with per-user sessions")

@mcp.tool()
async def get_whatsapp_status(ctx: Context) -> str:
    """Check WhatsApp connection status."""
    r = await _bridge(ctx, "GET", "/status", _get_user_id(ctx), timeout=10.0)
    if isinstance(r, dict):
        if r.get("error"): return f"Error: {r['error']}"
        if r.get("connected"): return "WhatsApp connected and ready."
        if r.get("qr_available"): return "Not connected. QR code available."
        return "Not connected. Open QR modal."
    return str(r)

@mcp.tool()
async def send_whatsapp_message(ctx: Context, to: Annotated[str, Field(description="Phone or group ID")], message: Annotated[str, Field(description="Message text")]) -> str:
    """Send a WhatsApp message."""
    if not to or not message: return "Error: to and message required"
    r = await _bridge(ctx, "POST", "/send", _get_user_id(ctx), {"to": to, "message": message})
    if isinstance(r, dict):
        if r.get("error"): return f"Error: {r['error']}"
        if r.get("success"): return f"Sent to {to}"
    return str(r)

@mcp.tool()
async def get_whatsapp_messages(ctx: Context, chat_id: Annotated[str, Field(description="Phone or group ID")], limit: Annotated[int, Field(description="Count", ge=1, le=200)] = 20) -> str:
    """Get recent messages from a chat."""
    r = await _bridge(ctx, "POST", "/messages", _get_user_id(ctx), {"chat_id": chat_id, "limit": limit})
    if isinstance(r, dict):
        if r.get("error"): return f"Error: {r['error']}"
        msgs = r.get("messages", [])
        if not msgs: return "No messages found."
        lines = [f"[{'You' if m.get('from_me') else m.get('sender', '?')}]: {m.get('text', m.get('body', ''))}" for m in msgs]
        return "Messages (" + str(len(msgs)) + "):" + chr(10) + chr(10).join(lines)
    return str(r)

@mcp.tool()
async def start_whatsapp_session(ctx: Context) -> str:
    """Start WhatsApp session."""
    r = await _bridge(ctx, "POST", "/start", _get_user_id(ctx), timeout=15.0)
    if isinstance(r, dict):
        if r.get("error"): return f"Error: {r['error']}"
        if r.get("connected"): return "Already connected."
        if r.get("started"): return "Started. Scan QR code."
    return str(r)

@mcp.tool()
async def disconnect_whatsapp(ctx: Context) -> str:
    """Disconnect WhatsApp session."""
    r = await _bridge(ctx, "DELETE", "/session", _get_user_id(ctx), timeout=15.0)
    if isinstance(r, dict):
        if r.get("error"): return f"Error: {r['error']}"
        if r.get("success"): return "Disconnected."
    return str(r)

if __name__ == "__main__":
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    
    sse = SseServerTransport("/messages")
    
    async def handle_sse(req):
        async with sse.connect_sse(req.scope, req.receive, req._send) as s:
            await mcp._mcp_server.run(s[0], s[1], mcp._mcp_server.create_initialization_options())
    
    async def handle_msg(req): 
        await sse.handle_post_message(req.scope, req.receive, req._send)
    
    async def handle_health(req):
        return JSONResponse({"status": "ok"})
    
    app = Starlette(routes=[
        Route("/health", endpoint=handle_health),
        Route("/sse", endpoint=handle_sse),
        Route("/messages", endpoint=handle_msg, methods=["POST"]),
    ])
    
    port = int(os.getenv("PORT", "8001"))
    logger.info(f"WhatsApp MCP on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
