# PROJECT KNOWLEDGE BASE

## OVERVIEW
Open WebUI deployment with custom WhatsApp (Baileys) integration, MCP tool servers, and Cloud Run sidecar architecture.

## STRUCTURE
```
./
├── Dockerfile              # Patches Open WebUI image (custom JS + routers)
├── service.yaml            # Cloud Run service (sidecars + secrets)
├── docker-compose.yml      # Local dev stack
├── start.sh                # Startup helpers + tool header injection
├── pipelines/              # Optional AI pipelines (Anthropic/Gemini)
├── servers/                # MCP + OpenAPI sidecars
├── whatsapp-bridge/        # Node Baileys bridge
├── webui/                  # Custom Open WebUI hooks/routes
├── tests/                  # Smoke tests
└── reverse/                # Vendor/installer artifacts (not core code)
```

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Open WebUI image patching | `Dockerfile` | Injects `webui/custom.js` + routers |
| Cloud Run config | `service.yaml` | Sidecars + secrets + URLs |
| Local dev stack | `docker-compose.yml` | Mirrors sidecars |
| WhatsApp bridge | `whatsapp-bridge/index.js` | Baileys session + history |
| WhatsApp API | `servers/whatsapp/main.py` | OpenAPI tool server |
| WhatsApp UI hook | `webui/custom.js` | Tool toggle → QR modal |
| QR proxy/router | `webui/whatsapp_qr.py` | `/api/v1/whatsapp/*` |
| MCP servers | `servers/slack-mcp/` | OAuth MCP |
| CI deploy | `.github/workflows/deploy.yml` | gcloud replace |
| Tests | `tests/webui-smoke.sh` | Requires OPENAI_API_KEY |

## CONVENTIONS
- Dependency files live per-service (`requirements.txt` under `servers/*` and `pipelines/`).
- Deployment uses `gcloud run services replace service.yaml` (no manual deploy scripts).
- Tool server connections are preloaded but persistent config is enabled; admin UI overrides can persist.

## ANTI-PATTERNS (THIS PROJECT)
- Do not edit `reverse/` artifacts for runtime behavior.
- Avoid adding new tool servers without matching updates in `service.yaml` and `docker-compose.yml`.

## UNIQUE STYLES
- Cloud Run sidecars: Open WebUI + WhatsApp bridge + WhatsApp API in one service.
- Open WebUI base image is patched at build time to add custom JS and routers.

## COMMANDS
```bash
docker compose up --build
make test
gcloud run services replace service.yaml
```

## NOTES
- Direct connections enabled; tool calls still server-side.
- WhatsApp QR modal flow depends on `webui/custom.js` + `webui/whatsapp_qr.py`.
