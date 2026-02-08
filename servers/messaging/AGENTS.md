# Messaging Service — Unified Telegram/Discord/Teams Bridge

## Overview
Single FastAPI sidecar container handling webhooks from Telegram, Discord, and Microsoft Teams. Normalizes incoming messages and routes them through Open WebUI for AI processing.

## Architecture
- **Port**: 8005
- **Framework**: FastAPI + uvicorn
- **Auth**: Platform-specific signature/token verification per endpoint; internal API token for tool calls
- **Rate Limiting**: 100 req / 10s per platform endpoint

## Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | None | Health check with platform config status |
| POST | `/telegram/webhook` | `X-Telegram-Bot-Api-Secret-Token` | Telegram Bot API webhook |
| POST | `/discord/webhook` | Ed25519 signature (`X-Signature-Ed25519` + `X-Signature-Timestamp`) | Discord Interactions Endpoint |
| POST | `/teams/webhook` | `Authorization: Bearer <JWT>` | Microsoft Teams Bot Framework |
| POST | `/send` | `X-Messaging-Token` | Send message to any platform (tool endpoint) |
| GET | `/status` | `X-Messaging-Token` | Platform connection status (tool endpoint) |
| GET | `/docs` | None | OpenAPI/Swagger UI |

## Environment Variables

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `MESSAGING_API_TOKEN` | No | `""` | Internal auth token for tool endpoints |
| `OPENWEBUI_BASE_URL` | No | `http://localhost:8080` | Open WebUI API base URL |
| `OPENWEBUI_API_KEY` | Yes | `""` | Open WebUI API key for chat completions |
| `OPENWEBUI_MODEL` | No | `anthropic/claude-sonnet-4-20250514` | Model to use for responses |
| `TELEGRAM_BOT_TOKEN` | No | `""` | Telegram bot token from @BotFather |
| `TELEGRAM_SECRET_TOKEN` | No | `""` | Webhook secret for Telegram verification |
| `DISCORD_APP_ID` | No | `""` | Discord application ID |
| `DISCORD_PUBLIC_KEY` | No | `""` | Discord Ed25519 public key |
| `DISCORD_BOT_TOKEN` | No | `""` | Discord bot token for API calls |
| `TEAMS_APP_ID` | No | `""` | Azure AD app ID for Teams bot |
| `TEAMS_APP_PASSWORD` | No | `""` | Azure AD client secret for Teams bot |

## Conventions
- Do not add new endpoints without updating this document.
- Changes must be reflected in both `service.yaml` and `docker-compose.yml`.
- All webhook endpoints must validate platform-specific signatures/tokens before processing.
- Outgoing messages are routed through Open WebUI, so DLP pipeline filters apply automatically.

## Anti-Patterns
- Do not store user credentials — only platform bot tokens.
- Do not bypass signature verification in production.
- Do not call platform APIs without rate limiting.
