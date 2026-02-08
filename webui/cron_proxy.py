"""
Cron Proxy Router — forwards /api/cron/* → memory-service sidecar at localhost:8003.

Cloud Scheduler hits the main Cloud Run URL (port 8080). This proxy
forwards cron requests to the memory-service sidecar on port 8003.
Authorization header is passed through for CRON_TOKEN validation.
"""

import os
import logging

import aiohttp
from fastapi import APIRouter, Request, Response

logger = logging.getLogger(__name__)

router = APIRouter()

MEMORY_SERVICE_BASE_URL = os.environ.get(
    "MEMORY_SERVICE_BASE_URL", "http://localhost:8003"
)
CRON_TOKEN = "".join(os.environ.get("CRON_TOKEN", "").split())

PROXY_TIMEOUT = aiohttp.ClientTimeout(
    total=180
)  # cron jobs may take time for LLM calls


async def _proxy_to_memory_service(
    method: str,
    path: str,
    request: Request,
    timeout: aiohttp.ClientTimeout = PROXY_TIMEOUT,
) -> Response:
    """Forward request to memory-service sidecar."""
    url = f"{MEMORY_SERVICE_BASE_URL}{path}"
    headers = {}
    if CRON_TOKEN:
        headers["Authorization"] = f"Bearer {CRON_TOKEN}"
    elif "authorization" in request.headers:
        headers["Authorization"] = request.headers["authorization"]
    if "content-type" in request.headers:
        headers["Content-Type"] = request.headers["content-type"]

    body = await request.body()

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(
                method,
                url,
                headers=headers,
                data=body if body else None,
            ) as resp:
                content = await resp.read()
                return Response(
                    content=content,
                    status_code=resp.status,
                    media_type=resp.content_type,
                )
    except aiohttp.ClientError as exc:
        logger.error("Cron proxy error: %s %s → %s", method, url, exc)
        return Response(
            content=f'{{"error": "Memory service unavailable: {exc}"}}',
            status_code=502,
            media_type="application/json",
        )


@router.post("/morning-briefing")
async def proxy_morning_briefing(request: Request) -> Response:
    return await _proxy_to_memory_service("POST", "/cron/morning-briefing", request)


@router.post("/inbox-summary")
async def proxy_inbox_summary(request: Request) -> Response:
    return await _proxy_to_memory_service("POST", "/cron/inbox-summary", request)


@router.post("/weekly-report")
async def proxy_weekly_report(request: Request) -> Response:
    return await _proxy_to_memory_service("POST", "/cron/weekly-report", request)


@router.get("/health")
async def cron_proxy_health() -> dict:
    """Health check for the cron proxy layer."""
    return {"status": "ok", "proxy": "cron", "target": MEMORY_SERVICE_BASE_URL}
