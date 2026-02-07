# WHATSAPP API SERVER

## OVERVIEW
OpenAPI tool server that proxies to the Baileys bridge and serves QR/session endpoints.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| API implementation | `servers/whatsapp/main.py` | All endpoints |
| Deployment notes | `servers/whatsapp/DEPLOYMENT.md` | Sidecar details |
| Dependencies | `servers/whatsapp/requirements.txt` | Python deps |

## CONVENTIONS
- Requires `WHATSAPP_BRIDGE_TOKEN` and `WHATSAPP_API_TOKEN` at startup.
- Runs as sidecar in the same Cloud Run service; uses localhost bridge URL.

## ANTI-PATTERNS
- Do not expose this service publicly; it is designed for internal use only.
