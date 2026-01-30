#!/bin/bash
# Wait for Cloud SQL proxy to accept connections before starting Open WebUI
SOCKET_DIR="/cloudsql/flow-os-1769675656:us-central1:open-webui-db"
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
  echo "Attempt $i/60: proxy not ready, waiting 1s..."
  sleep 1
done

# Run the original Open WebUI start script
exec bash /app/backend/start.sh
