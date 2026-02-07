# SERVERS

## OVERVIEW
Sidecar services for MCP and WhatsApp; each subdirectory is a standalone server with its own deps.

## STRUCTURE
```
servers/
├── slack-mcp/      # OAuth MCP server
├── whatsapp/       # WhatsApp OpenAPI tool server
└── utilities/      # Utility server
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Slack MCP | `servers/slack-mcp/` | OAuth + tools |
| WhatsApp API | `servers/whatsapp/main.py` | OpenAPI proxy |
| Utility server | `servers/utilities/main.py` | Misc helpers |

## CONVENTIONS
- Each server has its own `requirements.txt`.
- Entrypoints: `main.py` (and `server.py` for slack-mcp).

## ANTI-PATTERNS
- Don’t share secrets between unrelated servers.
