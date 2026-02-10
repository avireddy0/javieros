"""
Cron Proxy Router — forwards /api/cron/* → memory-service sidecar at localhost:8003.

Cloud Scheduler hits the main Cloud Run URL (port 8080). This proxy
forwards cron requests to the memory-service sidecar on port 8003.
Authorization is validated via CRON_TOKEN and optionally via OIDC.
"""

import os
import logging

import aiohttp
from fastapi import APIRouter, Request, Response, HTTPException, Depends

logger = logging.getLogger(__name__)

router = APIRouter()

MEMORY_SERVICE_BASE_URL = os.environ.get(
    "MEMORY_SERVICE_BASE_URL", "http://localhost:8003"
)
CRON_TOKEN = "".join(os.environ.get("CRON_TOKEN", "").split())
CRON_OIDC_AUDIENCE = os.environ.get("CRON_OIDC_AUDIENCE", "")

PROXY_TIMEOUT = aiohttp.ClientTimeout(
    total=180
)  # cron jobs may take time for LLM calls


def _validate_oidc_token(request: Request) -> None:
    """Validate Google Cloud Scheduler OIDC token if configured."""
    if not CRON_OIDC_AUDIENCE:
        return  # OIDC not configured, skip

    oidc_token = request.headers.get("x-cloudscheduler-jobname")
    if not oidc_token:
        logger.warning("OIDC configured but no X-CloudScheduler-JobName header")

    oidc_header = request.headers.get("authorization", "")
    if not oidc_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing OIDC token")

    token = oidc_header.split(" ", 1)[1].strip()
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        claims = google_id_token.verify_token(
            token, google_requests.Request(), audience=CRON_OIDC_AUDIENCE
        )
        issuer = claims.get("iss", "")
        if issuer not in (
            "https://accounts.google.com",
            "accounts.google.com",
        ):
            raise HTTPException(
                status_code=403, detail=f"Invalid OIDC issuer: {issuer}"
            )
        logger.info("OIDC validation passed: sub=%s", claims.get("sub"))
    except ImportError:
        logger.warning("google-auth not installed, skipping OIDC validation")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("OIDC token validation failed: %s", e)
        raise HTTPException(status_code=403, detail="OIDC validation failed")


def _validate_cron_token(request: Request):
    """Validate cron requests via CRON_TOKEN and optionally OIDC."""
    if not CRON_TOKEN:
        raise HTTPException(
            status_code=503, detail="Cron authentication not configured"
        )

    # If OIDC is configured, validate the OIDC token instead of CRON_TOKEN
    if CRON_OIDC_AUDIENCE:
        _validate_oidc_token(request)
        return

    # Fall back to CRON_TOKEN validation
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization")
    incoming = "".join(auth.split(" ", 1)[1].split())
    if incoming != CRON_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid cron token")


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
async def proxy_morning_briefing(
    request: Request, _validated=Depends(_validate_cron_token)
) -> Response:
    return await _proxy_to_memory_service("POST", "/cron/morning-briefing", request)


@router.post("/inbox-summary")
async def proxy_inbox_summary(
    request: Request, _validated=Depends(_validate_cron_token)
) -> Response:
    return await _proxy_to_memory_service("POST", "/cron/inbox-summary", request)


@router.post("/weekly-report")
async def proxy_weekly_report(
    request: Request, _validated=Depends(_validate_cron_token)
) -> Response:
    return await _proxy_to_memory_service("POST", "/cron/weekly-report", request)


@router.post("/heartbeat-check")
async def proxy_heartbeat_check(
    request: Request, _validated=Depends(_validate_cron_token)
) -> Response:
    return await _proxy_to_memory_service("POST", "/cron/heartbeat-check", request)


@router.get("/health")
async def cron_proxy_health() -> dict:
    """Health check for the cron proxy layer."""
    return {"status": "ok", "proxy": "cron", "target": MEMORY_SERVICE_BASE_URL}
