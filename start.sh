#!/bin/bash
set -euo pipefail

# Wait for Cloud SQL socket only when DATABASE_URL targets Cloud SQL unix socket.
SOCKET_DIR="/cloudsql/flow-os-1769675656:us-central1:open-webui-db"
DB_URL="${DATABASE_URL:-}"

if [[ -n "${TOOL_SERVER_CONNECTIONS:-}" && -n "${WHATSAPP_API_TOKEN:-}" ]]; then
  TOOL_SERVER_CONNECTIONS="$(python3 - <<'PY'
import json
import os

raw = os.environ.get("TOOL_SERVER_CONNECTIONS", "[]")
token = os.environ.get("WHATSAPP_API_TOKEN", "")

data = json.loads(raw)
for conn in data:
    info = conn.get("info", {})
    if info.get("id") == "whatsapp" or info.get("name") == "WhatsApp Tools":
        headers = conn.get("headers") or {}
        headers["X-WhatsApp-API-Token"] = token
        conn["headers"] = headers

print(json.dumps(data))
PY
  )"
  export TOOL_SERVER_CONNECTIONS
fi

if [[ "$DB_URL" == *"/cloudsql/"* ]] || [[ -d "/cloudsql" ]]; then
  echo "Waiting for Cloud SQL proxy to be ready..."
  for i in $(seq 1 60); do
    if python3 -c "
import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
try:
    s.connect('${SOCKET_DIR}/.s.PGSQL.5432')
    s.close()
    sys.exit(0)
except Exception:
    sys.exit(1)
" 2>/dev/null; then
      echo "Cloud SQL proxy accepting connections (attempt $i)."
      break
    fi
    if [[ "$i" -eq 60 ]]; then
      echo "Cloud SQL proxy not ready after 60s; continuing startup."
      break
    fi
    echo "Attempt $i/60: proxy not ready, waiting 1s..."
    sleep 1
  done
else
  echo "Cloud SQL socket wait skipped (non-CloudSQL DATABASE_URL)."
fi

exec bash /app/backend/start.sh
