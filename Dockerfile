FROM ghcr.io/open-webui/open-webui:main
# TODO: Pin to SHA digest for supply chain security (V003)

ENV WEBUI_AUTH=true \
    ENABLE_OPENAI_API=true \
    ENABLE_OLLAMA_API=false \
    ENABLE_FORWARD_USER_INFO_HEADERS=false \
    ENABLE_DIRECT_CONNECTIONS=true \
    ENABLE_BASE_MODELS_CACHE=true \
    MODELS_CACHE_TTL=300 \
    THREAD_POOL_SIZE=128 \
    OPENAI_API_BASE_URL=https://api.openai.com/v1 \
    OPENAI_API_KEY="" \
    DEFAULT_MODELS=gpt-5.2-chat-latest \
    TASK_MODEL=gpt-5.2-chat-latest \
    TASK_MODEL_EXTERNAL=gpt-5.2-chat-latest \
    ENABLE_WEB_SEARCH=true \
    WEB_SEARCH_ENGINE=duckduckgo \
    WEB_SEARCH_RESULT_COUNT=5 \
    PORT=8080

COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

COPY webui/custom.js /app/build/static/custom.js
COPY webui/whatsapp_qr.py /app/backend/open_webui/routers/whatsapp_qr.py
COPY webui/ide_hook.py /app/backend/open_webui/routers/ide_hook.py
COPY webui/cron_proxy.py /app/backend/open_webui/routers/cron_proxy.py

RUN python3 - <<'PY'
from pathlib import Path

index_path = Path("/app/build/index.html")
index_text = index_path.read_text()
if "/static/custom.js" not in index_text:
    index_text = index_text.replace(
        "</body>",
        "  <script defer src=\"/static/custom.js\"></script>\n</body>",
    )
    index_path.write_text(index_text)

main_path = Path("/app/backend/open_webui/main.py")
main_text = main_path.read_text()
if "whatsapp_qr" not in main_text:
    main_text = main_text.replace(
        "    scim,\n)",
        "    scim,\n    whatsapp_qr,\n)",
    )

if "ide_hook" not in main_text:
    if "whatsapp_qr" in main_text:
        main_text = main_text.replace(
            "    whatsapp_qr,\n)",
            "    whatsapp_qr,\n    ide_hook,\n)",
        )
    else:
        main_text = main_text.replace(
            "    scim,\n)",
            "    scim,\n    ide_hook,\n)",
        )

if "cron_proxy" not in main_text:
    main_text = main_text.replace(
        "    ide_hook,\n)",
        "    ide_hook,\n    cron_proxy,\n)",
    )

marker = "app.include_router(tools.router, prefix=\"/api/v1/tools\", tags=[\"tools\"])"
if marker in main_text and "whatsapp_qr.router" not in main_text:
    main_text = main_text.replace(
        marker,
        marker
        + "\napp.include_router(whatsapp_qr.router, prefix=\"/api/v1/whatsapp\", tags=[\"whatsapp\"])",
    )

if "ide_hook.router" not in main_text:
    main_text = main_text.replace(
        marker,
        marker + "\napp.include_router(ide_hook.router, tags=[\"ide\"])",
    )

if "cron_proxy.router" not in main_text:
    main_text = main_text.replace(
        marker,
        marker + "\napp.include_router(cron_proxy.router, prefix=\"/api/cron\", tags=[\"cron\"])",
    )

main_path.write_text(main_text)
PY

RUN grep -q 'whatsapp_qr.router' /app/backend/open_webui/main.py && \
    grep -q 'ide_hook.router' /app/backend/open_webui/main.py && \
    grep -q 'cron_proxy.router' /app/backend/open_webui/main.py || \
    (echo "FATAL: Patch verification failed" && exit 1)

CMD ["/app/start.sh"]
