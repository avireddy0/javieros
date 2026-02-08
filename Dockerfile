FROM ghcr.io/open-webui/open-webui:dev

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
COPY webui/patch_main.py /tmp/patch_main.py

RUN python3 /tmp/patch_main.py && rm /tmp/patch_main.py

RUN grep -q 'whatsapp_qr.router' /app/backend/open_webui/main.py && \
    grep -q 'ide_hook.router' /app/backend/open_webui/main.py && \
    grep -q 'cron_proxy.router' /app/backend/open_webui/main.py || \
    (echo "FATAL: Patch verification failed" && exit 1)

CMD ["/app/start.sh"]
