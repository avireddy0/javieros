"""
WhatsApp MCP Server for Open WebUI.
"""

from __future__ import annotations
import logging, os, base64
from typing import Annotated, Any
import httpx
from mcp.server.fastmcp import FastMCP, Context
from pydantic import Field

BRIDGE_URL = os.getenv('WHATSAPP_BRIDGE_URL', 'http://localhost:3000').rstrip('/')
BRIDGE_TOKEN = os.getenv('WHATSAPP_BRIDGE_TOKEN', '')
DEFAULT_USER_ID = os.getenv('WHATSAPP_DEFAULT_USER_ID', 'default')
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('whatsapp-mcp')
if not BRIDGE_TOKEN: raise RuntimeError('WHATSAPP_BRIDGE_TOKEN required')
_client: httpx.AsyncClient | None = None

async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(5.0, 60.0, 30.0, 10.0))
    return _client

def _get_user_id(ctx: Context) -> str:
    if hasattr(ctx, 'request_context') and ctx.request_context:
        if uid := ctx.request_context.get('user_id'): return str(uid)
    if hasattr(ctx, 'headers') and ctx.headers:
        if uid := ctx.headers.get('x-user-id') or ctx.headers.get('X-User-ID'): return str(uid)
    return DEFAULT_USER_ID

async def _bridge(method, path, uid, body=None, timeout=30.0):
    hdrs = {'X-WhatsApp-Bridge-Token': BRIDGE_TOKEN, 'X-User-ID': uid, 'Content-Type': 'application/json'}
    try:
        r = await (await get_client()).request(method, f'{BRIDGE_URL}{path}', headers=hdrs, json=body, timeout=timeout)
        r.raise_for_status()
        return r
    except httpx.TimeoutException: return {'error': 'timeout'}
    except httpx.HTTPStatusError as e: return {'error': f'{e.response.status_code}: {e.response.text}'}
    except Exception as e: return {'error': str(e)}

async def _bridge_json(method, path, uid, body=None, timeout=30.0):
    r = await _bridge(method, path, uid, body, timeout)
    if isinstance(r, dict): return r
    return r.json() if 'json' in r.headers.get('content-type', '') else r.text

mcp = FastMCP('whatsapp')

@mcp.tool()
async def get_whatsapp_status(ctx: Context) -> str:
    """Check WhatsApp connection status."""
    r = await _bridge_json('GET', '/status', _get_user_id(ctx), timeout=10.0)
    if isinstance(r, dict):
        if r.get('error'): return f"Error: {r['error']}"
        if r.get('connected'): return 'WhatsApp is connected and ready to use\!'
        if r.get('qr_available'): return 'WhatsApp is not connected. Use get_whatsapp_qr to scan the QR code.'
        return 'WhatsApp session not started. Use start_whatsapp_session first.'
    return str(r)

@mcp.tool()
async def start_whatsapp_session(ctx: Context) -> str:
    """Start WhatsApp session. Call this first, then use get_whatsapp_qr to get the QR code."""
    r = await _bridge_json('POST', '/start', _get_user_id(ctx), timeout=15.0)
    if isinstance(r, dict):
        if r.get('error'): return f"Error: {r['error']}"
        if r.get('connected'): return 'WhatsApp is already connected\!'
        if r.get('started'): return 'Session started\! Now use get_whatsapp_qr to display the QR code for scanning.'
    return str(r)

@mcp.tool()
async def get_whatsapp_qr(ctx: Context) -> str:
    """Get the WhatsApp QR code for authentication. Display this QR code image to the user so they can scan it with their phone."""
    uid = _get_user_id(ctx)
    r = await _bridge('GET', '/qr', uid, timeout=15.0)
    if isinstance(r, dict):
        if r.get('error'): return f"Error: {r['error']}"
        return 'Error: unexpected response'
    
    content_type = r.headers.get('content-type', '')
    # Check for PNG magic bytes
    is_png = len(r.content) > 4 and r.content[0] == 0x89 and r.content[1:4] == b'PNG'
    if 'image/' in content_type or is_png:
        b64 = base64.b64encode(r.content).decode()
        img = chr(33) + '[WhatsApp QR Code](data:image/png;base64,' + b64 + ')'
        nl = chr(10)
        return img + nl + nl + 'Scan this QR code with WhatsApp on your phone:' + nl + '1. Open WhatsApp' + nl + '2. Go to Settings > Linked Devices' + nl + '3. Tap Link a Device' + nl + '4. Scan this code' + nl + nl + 'The code expires in 60 seconds. If it expires, ask me to get a new one.'
    
    try:
        data = r.json()
        if data.get('connected'): return 'WhatsApp is already connected\! No QR code needed.'
        if data.get('message'): return data['message']
    except: pass
    return 'QR code not available. Try start_whatsapp_session first.'

@mcp.tool()
async def send_whatsapp_message(ctx: Context, to: Annotated[str, Field(description='Phone number with country code (e.g., +14155551234) or group ID')], message: Annotated[str, Field(description='Message text to send')]) -> str:
    """Send a WhatsApp message to a phone number or group."""
    if not to or not message: return 'Error: both to and message are required'
    r = await _bridge_json('POST', '/send', _get_user_id(ctx), {'to': to, 'message': message})
    if isinstance(r, dict):
        if r.get('error'): return f"Error: {r['error']}"
        if r.get('success'): return f'Message sent to {to}\!'
    return str(r)

@mcp.tool()
async def get_whatsapp_messages(ctx: Context, chat_id: Annotated[str, Field(description='Phone number with country code or group ID')], limit: Annotated[int, Field(description='Number of messages to retrieve (1-200)', ge=1, le=200)] = 20) -> str:
    """Get recent messages from a WhatsApp chat."""
    r = await _bridge_json('POST', '/messages', _get_user_id(ctx), {'chat_id': chat_id, 'limit': limit})
    if isinstance(r, dict):
        if r.get('error'): return f"Error: {r['error']}"
        msgs = r.get('messages', [])
        if not msgs: return f'No messages found in chat with {chat_id}.'
        lines = []
        for m in msgs:
            sender = 'You' if m.get('from_me') else m.get('sender', 'Unknown')
            text = m.get('text', m.get('body', '[no text]'))
            lines.append(f'[{sender}]: {text}')
        return f'Recent messages from {chat_id} ({len(msgs)} messages):' + chr(10) + chr(10).join(lines)
    return str(r)

@mcp.tool()
async def disconnect_whatsapp(ctx: Context) -> str:
    """Disconnect and unlink the WhatsApp session. Use this to switch to a different phone number."""
    r = await _bridge_json('DELETE', '/session', _get_user_id(ctx), timeout=15.0)
    if isinstance(r, dict):
        if r.get('error'): return f"Error: {r['error']}"
        if r.get('success'): return 'WhatsApp disconnected. Use start_whatsapp_session to connect a new number.'
    return str(r)

if __name__ == '__main__':
    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    
    sse = SseServerTransport('/messages')
    
    async def handle_sse(req):
        async with sse.connect_sse(req.scope, req.receive, req._send) as s:
            await mcp._mcp_server.run(s[0], s[1], mcp._mcp_server.create_initialization_options())
    
    async def handle_msg(req): 
        await sse.handle_post_message(req.scope, req.receive, req._send)
    
    async def handle_health(req):
        return JSONResponse({'status': 'ok'})
    
    app = Starlette(routes=[
        Route('/health', endpoint=handle_health),
        Route('/sse', endpoint=handle_sse),
        Route('/messages', endpoint=handle_msg, methods=['POST']),
    ])
    
    port = int(os.getenv('PORT', '8001'))
    logger.info(f'WhatsApp MCP on port {port}')
    uvicorn.run(app, host='0.0.0.0', port=port)
