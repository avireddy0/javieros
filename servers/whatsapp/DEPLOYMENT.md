# WhatsApp API Deployment

## Overview

The WhatsApp API server is deployed as a sidecar container in the `open-webui` Cloud Run service in project `flow-os-1769675656`.

## Architecture

```
open-webui Cloud Run Service (port 8080)
├── open-webui (main container)
├── whatsapp-bridge (sidecar, port 3000) - Baileys session service
└── whatsapp-api (sidecar, port 8000) - OpenAPI wrapper
```

All containers share `localhost` networking, so:
- whatsapp-api connects to whatsapp-bridge at `http://localhost:3000`

## Deployment

### Build and Push Image

```bash
cd /Users/avireddy/GitHub/javieros/servers/whatsapp
gcloud builds submit \
  --tag us-central1-docker.pkg.dev/flow-os-1769675656/javieros/whatsapp-api:v2 \
  --project=flow-os-1769675656
```

### Deploy Service

```bash
cd /Users/avireddy/GitHub/javieros
gcloud run services replace service.yaml \
  --project=flow-os-1769675656 \
  --region=us-central1
```

## Current Status

**Image**: `us-central1-docker.pkg.dev/flow-os-1769675656/javieros/whatsapp-api:v2`
**Service**: `open-webui` in `flow-os-1769675656` (us-central1)
**Container**: `whatsapp-api` (sidecar)
**Internal Port**: 8000
**External Access**: NOT directly exposed (port 8080 is open-webui only)

## External Access

The whatsapp-api is NOT directly accessible from outside Cloud Run because:
1. Cloud Run only exposes ONE port (8080) per service
2. Port 8080 is mapped to the `open-webui` container

### Option 1: Deploy separate Cloud Run service

Deploy whatsapp-api + whatsapp-bridge as a separate service with its own URL. However, this would duplicate the WhatsApp session.

### Option 2: Open WebUI global tools (current architecture)

Open WebUI can import OpenAPI tools from internal URLs. The whatsapp-api provides OpenAPI at:
- `http://localhost:8000/openapi.json`
- `http://localhost:8000/docs` (Swagger UI)

Since Open WebUI runs in the same service, global tool connections can safely target `http://localhost:8000`.

## API Endpoints

**Base URL (internal)**: `http://localhost:8000`

- `GET /status` - Check WhatsApp connection status
- `POST /send_message` - Send a WhatsApp message
  ```json
  {
    "to": "+5215512345678",
    "message": "Hello from Cloud Run!"
  }
  ```
- `POST /get_messages` - Get recent messages from a chat
  ```json
  {
    "chat_id": "+5215512345678",
    "limit": 20
  }
  ```
- `POST /qr_session` - Create a short-lived QR modal session
- `GET /qr_modal?token=...` - Embedded QR modal page (polls `/qr` + `/status`)

## Environment Variables

| Variable | Value | Source |
|----------|-------|--------|
| `WHATSAPP_BRIDGE_URL` | `http://localhost:3000` | Direct |
| `WHATSAPP_BRIDGE_TOKEN` | (secret) | Secret `whatsapp-bridge-token` |
| `WHATSAPP_API_TOKEN` | (secret) | Secret `whatsapp-bridge-token` |
| `WHATSAPP_ALLOWED_ORIGINS` | Open WebUI HTTPS URL | Direct |

## Verification

```bash
# Check service is deployed
gcloud run services describe open-webui \
  --project=flow-os-1769675656 \
  --region=us-central1 \
  --format=json | jq '.spec.template.spec.containers[] | select(.name == "whatsapp-api")'

# Check logs
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=open-webui AND labels.instanceId:*" \
  --project=flow-os-1769675656 \
  --limit=20
```

## Recommended setup

Use Open WebUI global tool connections (`TOOL_SERVER_CONNECTIONS`) to expose WhatsApp endpoints without publishing port 8000 externally.
