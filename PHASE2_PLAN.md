# Phase 2 — Platform Expansion: Unified Messaging Service

## Overview

Add Telegram, Discord, and Microsoft Teams messaging bridges to JavierOS via a single **unified messaging service** — a FastAPI sidecar container handling webhooks from all three platforms.

## Architecture Decision

### Problem
Cloud Run gen2 supports ~8 containers max. Currently 5 containers used. Adding 3 platforms × 2 containers each = 6 → exceeds limit.

### Solution
A **single FastAPI container** (`servers/messaging/main.py`) on port `8005` handling all three platforms on different route prefixes:

```
/telegram/webhook    — Telegram Bot API webhook (POST from Telegram servers)
/discord/webhook     — Discord Interactions Endpoint (POST from Discord servers)
/teams/webhook       — Microsoft Teams Bot Framework (POST from Azure)
/health              — Health check (no auth)
/docs                — OpenAPI spec (Swagger UI)
```

### Why NOT the WhatsApp Bridge Pattern?
WhatsApp requires a **persistent WebSocket** (Baileys) → needs a dedicated bridge container. Telegram, Discord, and Teams are all **webhook-native** — the platforms POST to us. No persistent connection, no bridge container needed.

## Container Specification

```yaml
- image: us-central1-docker.pkg.dev/flow-os-1769675656/javieros/messaging-service:latest
  name: messaging-service
  ports:
    - containerPort: 8005
  resources:
    limits:
      cpu: "0.5"
      memory: 512Mi
  startupProbe:
    httpGet:
      path: /health
      port: 8005
    initialDelaySeconds: 3
    periodSeconds: 5
    failureThreshold: 5
```

**Updated resource totals**: 5.0 CPU, 5.0Gi (within Cloud Run gen2 limits)

## Port Allocation

| Port | Service | Status |
|------|---------|--------|
| 8080 | open-webui (ingress) | Existing |
| 9099 | pipelines | Existing |
| 3000 | whatsapp-bridge | Existing |
| 8000 | whatsapp-api | Existing |
| 8003 | memory-service | Existing |
| **8005** | **messaging-service** | **NEW** |

## Platform Technical Details

### Telegram
- **Library**: `python-telegram-bot` v21+ (async)
- **Mode**: Webhook — Telegram POSTs `Update` JSON to `/telegram/webhook`
- **Auth**: Bot token from @BotFather, validated via `X-Telegram-Bot-Api-Secret-Token` header
- **Message flow**: Receive Update → extract text → forward to Open WebUI API → send reply via Bot API
- **Constraints**: Simplest platform. Natively HTTP, no persistent connections.

### Discord
- **Library**: Manual FastAPI + `PyNaCl` for Ed25519 signature verification
- **Mode**: Interactions Endpoint — Discord POSTs to `/discord/webhook`
- **Auth**: Ed25519 signature verification using Discord public key
- **Message flow**: Verify signature → handle PING/PONG → process slash commands → respond with content
- **Constraints**: Must respond to PING with PONG (type 1). Must verify Ed25519 signatures on every request. No gateway WebSocket — HTTP-only interactions.

### Microsoft Teams
- **Library**: `botbuilder-core` + `botbuilder-integration-aiohttp` (Microsoft Bot Framework SDK)
- **Mode**: Bot Framework webhook — Azure Bot Service POSTs to `/teams/webhook`
- **Auth**: Azure AD App ID + Password, JWT token validation via Bot Framework connector
- **Message flow**: Validate JWT → parse Activity → forward to Open WebUI API → reply via connector
- **Constraints**: Requires Azure AD app registration. JWT validation is mandatory.

## Secrets Required

| Secret Name | Source | Purpose |
|-------------|--------|---------|
| `telegram-bot-token` | @BotFather | Telegram Bot API authentication |
| `discord-app-id` | Discord Developer Portal | Discord application identifier |
| `discord-public-key` | Discord Developer Portal | Ed25519 signature verification |
| `discord-bot-token` | Discord Developer Portal | Discord API calls |
| `teams-app-id` | Azure AD | Teams bot app registration |
| `teams-app-password` | Azure AD | Teams bot client secret |
| `messaging-api-token` | Self-generated | Internal service authentication |

## Message Routing Architecture

```
Telegram servers ──POST──▶ /telegram/webhook ──┐
                                                │
Discord servers  ──POST──▶ /discord/webhook  ──┤──▶ normalize_message()
                                                │         │
Teams (Azure)    ──POST──▶ /teams/webhook    ──┘         ▼
                                                  Open WebUI API
                                                  (POST /api/chat)
                                                         │
                                                         ▼
                                                  AI Response
                                                         │
                                               ┌─────────┼─────────┐
                                               ▼         ▼         ▼
                                          Telegram   Discord    Teams
                                          Bot API    REST API   Connector
                                          sendMsg    respond    reply
```

All three platforms converge into a **normalized message format**:

```python
@dataclass
class IncomingMessage:
    platform: str           # "telegram" | "discord" | "teams"
    user_id: str            # Platform-specific user ID
    user_name: str          # Display name
    channel_id: str         # Chat/channel/conversation ID
    text: str               # Message text
    timestamp: datetime     # When received
    raw_payload: dict       # Original platform payload for debugging
```

## File Structure

```
servers/messaging/
├── main.py              # FastAPI app with /telegram, /discord, /teams routers
├── requirements.txt     # python-telegram-bot, PyNaCl, botbuilder-core, etc.
├── Dockerfile           # python:3.12-slim, uvicorn on port 8005
└── AGENTS.md            # Service documentation
```

## Implementation Order

1. ✅ Create PHASE2_PLAN.md (this document)
2. Create `servers/messaging/` with main.py, requirements.txt, Dockerfile, AGENTS.md
3. Implement Telegram handler first (simplest — webhook-native)
4. Implement Discord handler (Interactions Endpoint + Ed25519)
5. Implement Teams handler (Bot Framework + JWT)
6. Update `service.yaml` — add messaging-service container
7. Update `docker-compose.yml` — add messaging service
8. Update root `AGENTS.md` — document messaging bridges
9. Create GCP secrets
10. Build, push, deploy
11. Register in TOOL_SERVER_CONNECTIONS

## Open WebUI Integration

The messaging service will be registered as an **OpenAPI tool server** in Open WebUI's `TOOL_SERVER_CONNECTIONS`, just like the WhatsApp API server. This allows the AI to:
- Send messages to Telegram/Discord/Teams users
- Query message history
- Check connection status

## Security Considerations

- All webhook endpoints validate platform-specific signatures/tokens before processing
- Internal service-to-service calls use `MESSAGING_API_TOKEN` header auth
- No user credentials stored — only platform bot tokens
- Rate limiting: 100 req/10s per platform endpoint
- DLP pipeline filter (Phase 1.4) applies to all outgoing messages routed through Open WebUI
