# Memory Service

## Overview

The Memory Service is a FastAPI sidecar that provides persistent, per-user markdown file storage backed by Google Cloud Storage (GCS). It enables JavierOS to maintain long-term memory, user profiles, daily logs, and heartbeat state for each user.

## Architecture

- **Runtime**: Python 3.12 + FastAPI + Uvicorn
- **Storage**: GCS bucket `javieros-memory`
- **Port**: 8003 (internal), 8004 (docker-compose host)
- **Auth**: Bearer token via `MEMORY_API_TOKEN` environment variable (constant-time comparison)

## Storage Layout

```
gs://javieros-memory/
├── {user_id}/
│   ├── SOUL.md          # Persona and behavioral directives
│   ├── USER.md          # User profile, preferences, context
│   ├── MEMORY.md        # Long-term memory and learned facts
│   ├── HEARTBEAT.md     # Active reminders and scheduled check-ins
│   └── DAILY_LOG_YYYY-MM-DD.md  # Daily interaction logs
```

## Allowed Filenames

Only the following filenames are accepted (validated via regex whitelist):

- `SOUL.md`
- `USER.md`
- `MEMORY.md`
- `HEARTBEAT.md`
- `DAILY_LOG_YYYY-MM-DD.md` (date pattern)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (returns `{"status": "ok"}`) |
| `GET` | `/files/{user_id}` | List all memory files for a user |
| `GET` | `/files/{user_id}/{filename}` | Read a specific memory file |
| `PUT` | `/files/{user_id}/{filename}` | Create or overwrite a memory file |
| `POST` | `/files/{user_id}/{filename}/append` | Append content to a memory file |
| `DELETE` | `/files/{user_id}/{filename}` | Delete a memory file |

## Authentication

All endpoints (except `/health`) require a Bearer token in the `Authorization` header:

```
Authorization: Bearer <MEMORY_API_TOKEN>
```

The token is validated using `hmac.compare_digest` for constant-time comparison to prevent timing attacks. The service hard-fails on startup if `MEMORY_API_TOKEN` is not set.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MEMORY_API_TOKEN` | Yes | Bearer token for API authentication (hard-fail if missing) |
| `GCS_BUCKET_NAME` | No | GCS bucket name (default: `javieros-memory`) |
| `MEMORY_ALLOWED_ORIGINS` | No | Comma-separated CORS origins (default: `*`) |

## Security

- Input validation on all user IDs (alphanumeric, 1-128 chars) and filenames (regex whitelist)
- Constant-time token comparison via `hmac.compare_digest`
- Hard-fail on missing secrets (no silent fallback)
- CORS middleware with configurable allowed origins
- No direct user input in GCS blob paths beyond validated IDs and filenames

## Development

```bash
# Local run
cd servers/memory
pip install -r requirements.txt
MEMORY_API_TOKEN=dev-token uvicorn main:app --host 0.0.0.0 --port 8003 --reload

# Docker
docker build -t memory-service .
docker run -p 8003:8003 -e MEMORY_API_TOKEN=dev-token memory-service
```

## Constraints

- Do not store binary files — markdown only
- Do not bypass filename validation
- Do not add new endpoints without updating this document
- Changes must be reflected in both `service.yaml` and `docker-compose.yml`
