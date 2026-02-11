# WHATSAPP MCP SERVER

## OVERVIEW

MCP server exposing WhatsApp messaging tools with per-user session isolation.
Wraps the existing multi-user Baileys bridge.

## TOOLS

| Tool | Description |
|------|-------------|
| `get_whatsapp_status` | Check if WhatsApp is connected |
| `send_whatsapp_message` | Send a message to a phone/group |
| `get_whatsapp_messages` | Get recent messages from a chat |
| `start_whatsapp_session` | Start the WhatsApp connection |
| `disconnect_whatsapp` | Disconnect and unlink session |

## ENVIRONMENT

| Variable | Required | Default |
|----------|----------|---------|
| `WHATSAPP_BRIDGE_URL` | No | `http://localhost:3000` |
| `WHATSAPP_BRIDGE_TOKEN` | Yes | - |
| `PORT` | No | `8001` |

## OPEN WEBUI INTEGRATION

```json
{
  "type": "mcp",
  "url": "http://localhost:8001",
  "path": "/sse",
  "auth_type": "none",
  "info": {"id": "whatsapp-mcp", "name": "WhatsApp"}
}
```
