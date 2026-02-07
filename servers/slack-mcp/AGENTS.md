# SLACK MCP SERVER

## OVERVIEW
Slack OAuth MCP server used by Open WebUI for tool access.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Entrypoints | `servers/slack-mcp/main.py`, `servers/slack-mcp/server.py` | Server wiring |
| OAuth | `servers/slack-mcp/auth/` | OAuth handlers |
| Tool handlers | `servers/slack-mcp/tools/` | MCP tool implementations |
| Dependencies | `servers/slack-mcp/requirements.txt` | Python deps |

## CONVENTIONS
- OAuth flow lives under `auth/` and is required for tool access.

## ANTI-PATTERNS
- Avoid hardcoding Slack workspace IDs or tokens.
