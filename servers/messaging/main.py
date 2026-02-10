"""Unified Messaging Service — Telegram, Discord, Microsoft Teams bridges.

Single FastAPI container handling webhooks from all three platforms.
Normalizes incoming messages and routes them to Open WebUI for AI processing.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MESSAGING_API_TOKEN = os.environ.get("MESSAGING_API_TOKEN", "")
OPENWEBUI_BASE_URL = os.environ.get("OPENWEBUI_BASE_URL", "http://localhost:8080")
OPENWEBUI_API_KEY = os.environ.get("OPENWEBUI_API_KEY", "")
OPENWEBUI_MODEL = os.environ.get(
    "OPENWEBUI_MODEL", "anthropic/claude-sonnet-4-20250514"
)

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_SECRET_TOKEN = os.environ.get("TELEGRAM_SECRET_TOKEN", "")

# Discord
DISCORD_APP_ID = os.environ.get("DISCORD_APP_ID", "")
DISCORD_PUBLIC_KEY = os.environ.get("DISCORD_PUBLIC_KEY", "")
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

# Teams
TEAMS_APP_ID = os.environ.get("TEAMS_APP_ID", "")
TEAMS_APP_PASSWORD = os.environ.get("TEAMS_APP_PASSWORD", "")

# Rate limiting
RATE_LIMIT_REQUESTS = 100
RATE_LIMIT_WINDOW_SECONDS = 10

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("messaging-service")

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class IncomingMessage:
    """Normalized message from any platform."""

    platform: str
    user_id: str
    user_name: str
    channel_id: str
    text: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw_payload: dict = field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    platforms: dict[str, bool]


class SendMessageRequest(BaseModel):
    platform: str
    channel_id: str
    text: str


class SendMessageResponse(BaseModel):
    ok: bool
    platform: str
    detail: str = ""


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

_rate_buckets: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(key: str) -> None:
    now = time.monotonic()
    bucket = _rate_buckets[key]
    # Evict expired entries
    _rate_buckets[key] = [t for t in bucket if now - t < RATE_LIMIT_WINDOW_SECONDS]
    if len(_rate_buckets[key]) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    _rate_buckets[key].append(now)


# ---------------------------------------------------------------------------
# Auth Helpers
# ---------------------------------------------------------------------------


def _verify_api_token(token: str | None) -> None:
    if not MESSAGING_API_TOKEN:
        raise HTTPException(
            status_code=503, detail="Messaging authentication not configured"
        )
    if not token or not hmac.compare_digest(token, MESSAGING_API_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid API token")


def _verify_telegram_secret(secret_token: str | None) -> None:
    if not TELEGRAM_SECRET_TOKEN:
        return
    if not secret_token or not hmac.compare_digest(secret_token, TELEGRAM_SECRET_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid Telegram secret token")


def _verify_discord_signature(
    body: bytes, signature: str | None, timestamp: str | None
) -> None:
    if not DISCORD_PUBLIC_KEY:
        return
    if not signature or not timestamp:
        raise HTTPException(status_code=401, detail="Missing Discord signature headers")
    try:
        from nacl.signing import VerifyKey

        vk = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))
        vk.verify(timestamp.encode() + body, bytes.fromhex(signature))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Discord signature")


# ---------------------------------------------------------------------------
# Open WebUI Client
# ---------------------------------------------------------------------------


async def _chat_with_openwebui(message: IncomingMessage) -> str:
    """Send a normalized message to Open WebUI and get the AI response."""
    headers = {
        "Authorization": f"Bearer {OPENWEBUI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": OPENWEBUI_MODEL,
        "messages": [{"role": "user", "content": message.text}],
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{OPENWEBUI_BASE_URL}/api/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return (
                    choices[0]
                    .get("message", {})
                    .get("content", "No response generated.")
                )
            return "No response generated."
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Open WebUI API error: %s %s",
            exc.response.status_code,
            exc.response.text[:200],
        )
        return f"Error: Open WebUI returned {exc.response.status_code}"
    except Exception as exc:
        logger.error("Open WebUI request failed: %s", exc)
        return "Error: Could not reach the AI backend."


# ---------------------------------------------------------------------------
# Telegram Helpers
# ---------------------------------------------------------------------------


async def _telegram_send_message(chat_id: str | int, text: str) -> bool:
    """Send a message via Telegram Bot API."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram bot token not configured")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Discord Helpers
# ---------------------------------------------------------------------------


async def _discord_send_followup(interaction_token: str, content: str) -> bool:
    """Send a followup message to a Discord interaction."""
    if not DISCORD_APP_ID:
        return False
    url = f"https://discord.com/api/v10/webhooks/{DISCORD_APP_ID}/{interaction_token}"
    payload = {"content": content[:2000]}  # Discord 2000 char limit
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url, json=payload, headers={"Content-Type": "application/json"}
            )
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("Discord followup failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Teams Helpers
# ---------------------------------------------------------------------------


async def _teams_send_reply(
    service_url: str, conversation_id: str, activity_id: str, text: str
) -> bool:
    """Send a reply to a Teams conversation."""
    if not TEAMS_APP_ID or not TEAMS_APP_PASSWORD:
        return False

    # Get Bot Framework access token
    token_url = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
    token_data = {
        "grant_type": "client_credentials",
        "client_id": TEAMS_APP_ID,
        "client_secret": TEAMS_APP_PASSWORD,
        "scope": "https://api.botframework.com/.default",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(token_url, data=token_data)
            token_resp.raise_for_status()
            access_token = token_resp.json()["access_token"]

            reply_url = f"{service_url}v3/conversations/{conversation_id}/activities/{activity_id}"
            reply_payload = {"type": "message", "text": text}
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            }
            resp = await client.post(reply_url, json=reply_payload, headers=headers)
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("Teams reply failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="JavierOS Unified Messaging Service",
    description="Telegram, Discord, and Microsoft Teams bridges for JavierOS.",
    version="1.0.0",
)

_messaging_origins = [
    o.strip()
    for o in os.environ.get("MESSAGING_ALLOWED_ORIGINS", OPENWEBUI_BASE_URL).split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_messaging_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        platforms={
            "telegram": bool(TELEGRAM_BOT_TOKEN),
            "discord": bool(DISCORD_PUBLIC_KEY),
            "teams": bool(TEAMS_APP_ID and TEAMS_APP_PASSWORD),
        },
    )


# ---------------------------------------------------------------------------
# Telegram Webhook
# ---------------------------------------------------------------------------


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None),
) -> dict[str, Any]:
    _check_rate_limit("telegram")
    _verify_telegram_secret(x_telegram_bot_api_secret_token)

    body = await request.json()
    logger.info(
        "Telegram update received: type=%s", "message" if "message" in body else "other"
    )

    message_data = body.get("message") or body.get("edited_message")
    if not message_data or "text" not in message_data:
        return {"ok": True, "detail": "Non-text update ignored"}

    sender = message_data.get("from", {})
    msg = IncomingMessage(
        platform="telegram",
        user_id=str(sender.get("id", "")),
        user_name=sender.get("first_name", "") + " " + sender.get("last_name", ""),
        channel_id=str(message_data["chat"]["id"]),
        text=message_data["text"],
        raw_payload=body,
    )

    ai_response = await _chat_with_openwebui(msg)
    await _telegram_send_message(msg.channel_id, ai_response)

    return {"ok": True}


# ---------------------------------------------------------------------------
# Discord Interactions Endpoint
# ---------------------------------------------------------------------------


@app.post("/discord/webhook", response_model=None)
async def discord_webhook(
    request: Request,
    x_signature_ed25519: str | None = Header(None),
    x_signature_timestamp: str | None = Header(None),
) -> dict[str, Any] | Response:
    _check_rate_limit("discord")

    raw_body = await request.body()
    _verify_discord_signature(raw_body, x_signature_ed25519, x_signature_timestamp)

    body = await request.json()
    interaction_type = body.get("type")

    # Type 1: PING — must respond with PONG
    if interaction_type == 1:
        logger.info("Discord PING received — responding with PONG")
        return {"type": 1}

    # Type 2: Application command (slash command)
    if interaction_type == 2:
        command_data = body.get("data", {})
        command_name = command_data.get("name", "")
        options = command_data.get("options", [])
        user_text = ""

        # Extract text from the "message" option if present
        for opt in options:
            if opt.get("name") == "message":
                user_text = opt.get("value", "")

        if not user_text:
            user_text = f"/{command_name}"

        user = body.get("member", {}).get("user", {}) or body.get("user", {})
        msg = IncomingMessage(
            platform="discord",
            user_id=user.get("id", ""),
            user_name=user.get("username", ""),
            channel_id=body.get("channel_id", ""),
            text=user_text,
            raw_payload=body,
        )

        # Respond with DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE, then send followup
        interaction_token = body.get("token", "")

        # Fire-and-forget: process in background
        import asyncio

        asyncio.create_task(_discord_process_and_reply(msg, interaction_token))

        return {"type": 5}  # DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE

    logger.info("Discord interaction type %s ignored", interaction_type)
    return {"ok": True}


async def _discord_process_and_reply(
    msg: IncomingMessage, interaction_token: str
) -> None:
    """Process Discord message and send followup."""
    try:
        ai_response = await _chat_with_openwebui(msg)
        # Split long responses (Discord 2000 char limit)
        chunks = [ai_response[i : i + 2000] for i in range(0, len(ai_response), 2000)]
        for chunk in chunks:
            await _discord_send_followup(interaction_token, chunk)
    except Exception as exc:
        logger.error("Discord processing failed: %s", exc)
        await _discord_send_followup(
            interaction_token, "Sorry, an error occurred while processing your message."
        )


# ---------------------------------------------------------------------------
# Teams Bot Framework Webhook
# ---------------------------------------------------------------------------


@app.post("/teams/webhook")
async def teams_webhook(
    request: Request,
    authorization: str | None = Header(None),
) -> dict[str, Any]:
    _check_rate_limit("teams")

    # Validate Teams Bot Framework JWT claims
    if TEAMS_APP_ID:
        if not authorization:
            raise HTTPException(status_code=401, detail="Missing Authorization header")
        token = authorization.replace("Bearer ", "").strip()
        try:
            # Decode JWT payload (header.payload.signature)
            parts = token.split(".")
            if len(parts) != 3:
                raise ValueError("Malformed JWT")
            payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            # Validate audience matches our app ID
            aud = payload.get("aud", "")
            if aud != TEAMS_APP_ID:
                raise ValueError(f"Invalid audience: {aud}")
            # Validate issuer is Bot Framework or Azure AD
            iss = payload.get("iss", "")
            valid_issuers = (
                "https://api.botframework.com",
                "https://sts.windows.net/",
                "https://login.microsoftonline.com/",
            )
            if not any(iss.startswith(vi) for vi in valid_issuers):
                raise ValueError(f"Invalid issuer: {iss}")
            # Validate token is not expired
            exp = payload.get("exp", 0)
            if time.time() > exp:
                raise ValueError("Token expired")
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            logger.warning("Teams JWT validation failed: %s", exc)
            raise HTTPException(status_code=401, detail="Invalid authorization token")

    body = await request.json()
    activity_type = body.get("type", "")
    logger.info("Teams activity received: type=%s", activity_type)

    if activity_type != "message":
        return {"ok": True, "detail": f"Activity type '{activity_type}' ignored"}

    text = body.get("text", "").strip()
    # Teams sometimes prepends the bot mention — strip it
    if text.startswith("<at>"):
        # Remove <at>BotName</at> prefix
        import re

        text = re.sub(r"<at>[^<]*</at>\s*", "", text).strip()

    if not text:
        return {"ok": True, "detail": "Empty message ignored"}

    sender = body.get("from", {})
    msg = IncomingMessage(
        platform="teams",
        user_id=sender.get("id", ""),
        user_name=sender.get("name", ""),
        channel_id=body.get("conversation", {}).get("id", ""),
        text=text,
        raw_payload=body,
    )

    ai_response = await _chat_with_openwebui(msg)

    service_url = body.get("serviceUrl", "")
    conversation_id = body.get("conversation", {}).get("id", "")
    activity_id = body.get("id", "")

    if service_url and conversation_id:
        await _teams_send_reply(service_url, conversation_id, activity_id, ai_response)

    return {"ok": True}


# ---------------------------------------------------------------------------
# OpenAPI Tool Endpoints (for Open WebUI tool server registration)
# ---------------------------------------------------------------------------


@app.post("/send", response_model=SendMessageResponse)
async def send_message(
    req: SendMessageRequest,
    x_api_token: str | None = Header(None, alias="X-Messaging-Token"),
) -> SendMessageResponse:
    """Send a message to a specific platform and channel. Used by Open WebUI as a tool."""
    _verify_api_token(x_api_token)

    if req.platform == "telegram":
        ok = await _telegram_send_message(req.channel_id, req.text)
    elif req.platform == "discord":
        # For direct sends, use Discord bot token to post to channel
        ok = await _discord_channel_send(req.channel_id, req.text)
    elif req.platform == "teams":
        # Teams direct send requires service URL context — not available here
        return SendMessageResponse(
            ok=False,
            platform=req.platform,
            detail="Teams requires conversation context; use webhook flow.",
        )
    else:
        return SendMessageResponse(
            ok=False, platform=req.platform, detail=f"Unknown platform: {req.platform}"
        )

    return SendMessageResponse(
        ok=ok, platform=req.platform, detail="" if ok else "Send failed"
    )


async def _discord_channel_send(channel_id: str, content: str) -> bool:
    """Send a message directly to a Discord channel via bot token."""
    if not DISCORD_BOT_TOKEN:
        return False
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    payload = {"content": content[:2000]}
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return True
    except Exception as exc:
        logger.error("Discord channel send failed: %s", exc)
        return False


@app.get("/status")
async def status(
    x_api_token: str | None = Header(None, alias="X-Messaging-Token"),
) -> dict[str, Any]:
    """Get connection status for all platforms. Used by Open WebUI as a tool."""
    _verify_api_token(x_api_token)
    return {
        "telegram": {
            "configured": bool(TELEGRAM_BOT_TOKEN),
            "webhook_path": "/telegram/webhook",
        },
        "discord": {
            "configured": bool(DISCORD_PUBLIC_KEY),
            "webhook_path": "/discord/webhook",
        },
        "teams": {
            "configured": bool(TEAMS_APP_ID and TEAMS_APP_PASSWORD),
            "webhook_path": "/teams/webhook",
        },
    }
