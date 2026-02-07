# WHATSAPP BRIDGE

## OVERVIEW
Baileys-based Node bridge that maintains WhatsApp session state and message history.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Bridge logic | `whatsapp-bridge/index.js` | Connection + endpoints |
| Dependencies | `whatsapp-bridge/package.json` | Node deps |
| Docker image | `whatsapp-bridge/Dockerfile` | Runtime image |

## CONVENTIONS
- Session data stored under `/data/whatsapp-session`.
- Auth required unless `WHATSAPP_ALLOW_INSECURE=true`.

## ANTI-PATTERNS
- Do not run multiple bridge instances against the same session store.
