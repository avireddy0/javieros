#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_URL="${WEBUI_TEST_URL:-http://localhost:8080}"
MAX_ATTEMPTS="${WEBUI_TEST_MAX_ATTEMPTS:-120}"
SLEEP_SECONDS="${WEBUI_TEST_SLEEP_SECONDS:-2}"
TMP_HTML="$(mktemp)"

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "[webui-smoke] failed: OPENAI_API_KEY is not set."
  echo "[webui-smoke] export a real OpenAI key and re-run."
  exit 1
fi

if [[ "${OPENAI_API_KEY}" == "dummy" || "${OPENAI_API_KEY}" == "sk-..." ]]; then
  echo "[webui-smoke] failed: OPENAI_API_KEY is a placeholder value."
  echo "[webui-smoke] set a real OpenAI key and re-run."
  exit 1
fi

export OPENAI_API_KEY

cleanup() {
  rm -f "$TMP_HTML"
  (
    cd "$ROOT_DIR"
    docker compose down --remove-orphans >/dev/null 2>&1 || true
  )
}
trap cleanup EXIT INT TERM

echo "[webui-smoke] starting db + open-webui..."
(
  cd "$ROOT_DIR"
  docker compose up -d db open-webui
)

echo "[webui-smoke] waiting for ${APP_URL}..."
ready=0
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  status_code="$(curl -s -o "$TMP_HTML" -w "%{http_code}" "$APP_URL" || true)"
  if [[ "$status_code" == "200" ]]; then
    ready=1
    break
  fi
  sleep "$SLEEP_SECONDS"
done

if [[ "$ready" -ne 1 ]]; then
  echo "[webui-smoke] failed: endpoint did not return 200 in time."
  (
    cd "$ROOT_DIR"
    docker compose logs --no-color --tail=200 db open-webui || true
  )
  exit 1
fi

if ! rg -q "<title>Open WebUI</title>" "$TMP_HTML"; then
  echo "[webui-smoke] failed: page title mismatch."
  echo "[webui-smoke] expected: <title>Open WebUI</title>"
  echo "[webui-smoke] received (first 40 lines):"
  sed -n '1,40p' "$TMP_HTML"
  exit 1
fi

echo "[webui-smoke] passed: HTTP 200 + Open WebUI title."
