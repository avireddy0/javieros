# OPEN WEBUI CUSTOMIZATIONS

## OVERVIEW
Custom Open WebUI hooks for WhatsApp QR modal and tool toggle behavior.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| UI hook | `webui/custom.js` | Tool toggle → modal |
| QR proxy/router | `webui/whatsapp_qr.py` | `/api/v1/whatsapp/*` |
| Image patching | `Dockerfile` | Injects JS + routers |

## CONVENTIONS
- Custom JS is injected into `/app/build/index.html` at build time.
- Router patching is string-based; keep in sync with upstream changes.

## ANTI-PATTERNS
- Avoid relying on DOM text if a stable tool ID is available.
