FROM ghcr.io/open-webui/open-webui:main

ENV WEBUI_AUTH=true \
    ENABLE_OPENAI_API=true \
    ENABLE_OLLAMA_API=false \
    OPENAI_API_BASE_URL=http://localhost:9099 \
    OPENAI_API_KEY=0p3n-w3bu! \
    PORT=8080

COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
