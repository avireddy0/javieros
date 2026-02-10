# JavierOS Open WebUI Stack

This deployment now uses **native Open WebUI model infrastructure** for GPT.

## Current behavior

- Open WebUI calls OpenAI directly (`https://api.openai.com/v1`)
- Default model is `gpt-5.2-chat-latest`
- External tool servers are enabled by default (`ENABLE_DIRECT_CONNECTIONS=true`)
- `TOOL_SERVER_CONNECTIONS` preloads four standard integrations:
  - `envision-mcp` (MCP streamable HTTP)
  - `slack-oauth` (MCP + OAuth 2.1)
  - `google-workspace` (MCP + OAuth 2.1)
- `whatsapp` (OpenAPI sidecar)
- WhatsApp tool toggle opens an embedded QR modal via `/api/v1/whatsapp/qr_session`
- Native web search is enabled with `duckduckgo` for reliable built-in tool behavior
- Persistent config is enabled so admin UI tool changes survive restarts

## Cloud Run secret requirements

`/Users/avireddy/GitHub/javieros/service.yaml` expects:

- `openai-api-key`
- `webui-admin-password`
- `db-url`
- `webui-secret`
- `oauth-client-info-key`
- `oauth-session-token-key`
- `whatsapp-bridge-token` (used for bridge + API auth and tool header injection)

```bash
echo -n "sk-..." | gcloud secrets versions add openai-api-key \
  --project=flow-os-1769675656 \
  --data-file=-

echo -n "strong-admin-password" | gcloud secrets versions add webui-admin-password \
  --project=flow-os-1769675656 \
  --data-file=-

gcloud secrets add-iam-policy-binding openai-api-key \
  --project=flow-os-1769675656 \
  --member="serviceAccount:open-webui-sa@flow-os-1769675656.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding webui-admin-password \
  --project=flow-os-1769675656 \
  --member="serviceAccount:open-webui-sa@flow-os-1769675656.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## Open WebUI config behavior

This stack follows Open WebUI docs for persistent admin-managed configuration:

- `ENABLE_PERSISTENT_CONFIG=true`
- `ENABLE_OAUTH_PERSISTENT_CONFIG=true`
- `ENABLE_DIRECT_CONNECTIONS=true`
- `WEBUI_URL` must match the production domain before using OAuth

Operational implication:

- Tool server and OAuth settings persist across restarts
- OAuth client/session encryption keys must remain stable (`WEBUI_SECRET_KEY`, `OAUTH_CLIENT_INFO_ENCRYPTION_KEY`, `OAUTH_SESSION_TOKEN_ENCRYPTION_KEY`)
- If WhatsApp tools were configured before, update the tool headers to include `X-WhatsApp-API-Token`.

## Deploy

```bash
gcloud run services replace /Users/avireddy/GitHub/javieros/service.yaml \
  --project=flow-os-1769675656 \
  --region=us-central1
```

## Local docker-compose

Set `OPENAI_API_KEY`, `WHATSAPP_BRIDGE_TOKEN`, `WHATSAPP_API_TOKEN`, and `WHATSAPP_API_BASE_URL` in your environment before running:

```bash
docker compose up --build
```

## Testing

Run the Web UI smoke test:

```bash
make test
```

The smoke test boots `db` + `open-webui`, waits for `http://localhost:8080`,
and validates the page title is `Open WebUI`.
`OPENAI_API_KEY` must be set to a real key value.

For external tools in production, also validate:

- `Admin Settings -> External Tools` shows the four configured servers
- Google Workspace and Slack OAuth clients are registered and saved
- Tools are enabled per chat via the `+` menu in the message input

## Tooling notes

- Primary executive assistant experience is native Open WebUI + OpenAI (GPT).
- Pipelines remain in-repo for optional advanced workflows, but WhatsApp pipeline tools are disabled by default.
- WhatsApp QR modal support is proxied by Open WebUI via `/api/v1/whatsapp/qr_session`.
# Trigger redeploy Tue, Feb 10, 2026  2:10:08 PM
