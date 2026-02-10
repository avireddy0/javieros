"""WhatsApp OpenAPI tool server for Open WebUI."""

from __future__ import annotations

import hmac
import hashlib
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://whatsapp-bridge:3000").rstrip("/")
BRIDGE_TOKEN = os.getenv("WHATSAPP_BRIDGE_TOKEN", "")
API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", "")
WHATSAPP_QR_COOKIE = "whatsapp_qr_session"
QR_SESSION_TTL_SECONDS = int(os.getenv("WHATSAPP_QR_SESSION_TTL_SECONDS", "120"))
_qr_sessions: dict[str, float] = {}

_log = logging.getLogger("whatsapp-api")

if not BRIDGE_TOKEN:
    raise RuntimeError("WHATSAPP_BRIDGE_TOKEN must be set and non-empty")

if not API_TOKEN:
    raise RuntimeError("WHATSAPP_API_TOKEN must be set and non-empty")

_origins = [
    o.strip() for o in os.getenv("WHATSAPP_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
if not _origins:
    raise RuntimeError(
        "WHATSAPP_ALLOWED_ORIGINS must be set (comma-separated list of allowed origins)"
    )
ALLOWED_ORIGINS = _origins
ALLOW_CREDENTIALS = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=10.0)
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=100)
    app.state.bridge_client = httpx.AsyncClient(timeout=timeout, limits=limits)
    try:
        yield
    finally:
        await app.state.bridge_client.aclose()


app = FastAPI(
    title="WhatsApp Tools",
    description="Send and read WhatsApp messages via WhatsApp bridge. Use these tools to interact with WhatsApp messaging.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Pydantic Models for Tool Parameters ---

class SendMessageRequest(BaseModel):
    """Request body for sending a WhatsApp message."""
    to: str = Field(
        min_length=3,
        max_length=128,
        description="Phone number with country code (e.g., +14155551234) or group ID",
        examples=["+14155551234", "120363123456789012@g.us"],
    )
    message: str = Field(
        min_length=1,
        max_length=4096,
        description="The message text to send",
        examples=["Hello! How are you?"],
    )


class GetMessagesRequest(BaseModel):
    """Request body for retrieving WhatsApp messages."""
    chat_id: str = Field(
        min_length=3,
        max_length=128,
        description="Phone number with country code (e.g., +14155551234) or group ID to get messages from",
        examples=["+14155551234", "120363123456789012@g.us"],
    )
    limit: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Number of recent messages to retrieve (1-200)",
    )


# --- Response Models ---

class MessageSentResponse(BaseModel):
    """Response after successfully sending a message."""
    success: bool = Field(description="Whether the message was sent successfully")
    message_id: str = Field(default="", description="ID of the sent message")


class WhatsAppMessage(BaseModel):
    """A single WhatsApp message."""
    id: str = Field(description="Message ID")
    from_me: bool = Field(description="Whether the message was sent by you")
    sender: str = Field(default="", description="Sender phone number or name")
    body: str = Field(description="Message text content")
    timestamp: int = Field(description="Unix timestamp of the message")


class GetMessagesResponse(BaseModel):
    """Response containing retrieved messages."""
    messages: list[dict] = Field(description="List of messages from the chat")


class ConnectionStatus(BaseModel):
    """WhatsApp connection status."""
    connected: bool = Field(description="Whether WhatsApp is connected")
    phone_number: str = Field(default="", description="Connected phone number if available")


# --- Auth Helpers ---

def _extract_token(req: Request) -> str:
    header_token = req.headers.get("X-WhatsApp-API-Token", "")
    if header_token:
        return header_token
    auth = req.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return req.cookies.get(WHATSAPP_QR_COOKIE, "")


def _extract_user_id(req: Request, required: bool = False) -> str:
    """Extract user ID from Open WebUI context.

    For tool server calls (external tools), the X-User-ID header may not be present.
    In that case, use a default session ID based on the API token hash to maintain
    session isolation while still allowing tool server access.
    """
    user_id = req.headers.get("X-User-ID", "")
    if user_id:
        return user_id
    if required:
        raise HTTPException(
            status_code=401,
            detail="Missing X-User-ID header. User context required."
        )
    # For tool server calls without user context, derive a session ID from the token
    # This ensures all tool server calls share the same WhatsApp session
    token = _extract_token(req)
    if token:
        # Use first 16 chars of token hash as default user ID
        return f"toolserver-{hashlib.sha256(token.encode()).hexdigest()[:16]}"
    return "toolserver-default"


def _purge_qr_sessions() -> None:
    now = time.time()
    expired = [token for token, expires_at in _qr_sessions.items() if expires_at <= now]
    for token in expired:
        _qr_sessions.pop(token, None)


def _is_valid_qr_session(token: str) -> bool:
    _purge_qr_sessions()
    return bool(token and _qr_sessions.get(token, 0) > time.time())


def _require_api_auth(req: Request, *, allow_qr_session: bool = False) -> None:
    # Allow localhost requests without auth (sidecar trust within Cloud Run)
    client_host = req.client.host if req.client else None
    if client_host in ("127.0.0.1", "localhost", "::1"):
        return

    provided = _extract_token(req)
    if hmac.compare_digest(provided, API_TOKEN):
        return
    if allow_qr_session and _is_valid_qr_session(provided):
        return
    raise HTTPException(status_code=401, detail="Unauthorized")


def _bridge_headers(user_id: str | None = None) -> dict[str, str]:
    """Build headers for bridge requests, including user context."""
    headers = {"X-WhatsApp-Bridge-Token": BRIDGE_TOKEN}
    if user_id:
        headers["X-User-ID"] = user_id
    return headers


def _raise_bridge_error(exc: Exception) -> None:
    if isinstance(exc, httpx.TimeoutException):
        raise HTTPException(
            status_code=504, detail="WhatsApp bridge timed out."
        ) from exc
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text
        if status == 503:
            raise HTTPException(
                status_code=503,
                detail="WhatsApp not connected. Scan QR code first.",
            ) from exc
        if status == 401:
            raise HTTPException(
                status_code=502, detail="Bridge token rejected."
            ) from exc
        raise HTTPException(
            status_code=502, detail=f"Bridge error ({status}): {body}"
        ) from exc
    if isinstance(exc, httpx.HTTPError):
        raise HTTPException(
            status_code=502, detail=f"Bridge request failed: {exc}"
        ) from exc
    raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}") from exc


async def _bridge_request(
    req: Request,
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
    user_id: str | None = None,
) -> httpx.Response:
    client: httpx.AsyncClient = req.app.state.bridge_client
    url = f"{BRIDGE_URL}{path}"
    kwargs: dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": _bridge_headers(user_id),
        "json": json_body,
    }
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds

    last_error: Exception | None = None
    for _ in range(2):
        try:
            response = await client.request(**kwargs)
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.HTTPError) as exc:
            last_error = exc
            if (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code < 500
            ):
                break

    assert last_error is not None
    _raise_bridge_error(last_error)
    raise HTTPException(status_code=500, detail="Unreachable")


# --- Endpoints ---

@app.get("/health", include_in_schema=False)
async def health() -> dict[str, Any]:
    """Health check endpoint (not exposed as a tool)."""
    return {"status": "healthy", "service": "whatsapp-api", "version": "0.2.0"}


@app.get(
    "/status",
    operation_id="get_whatsapp_status",
    summary="Check WhatsApp connection status",
    description="Check if WhatsApp is connected and ready to send/receive messages. Use this before sending messages to verify the connection is active.",
    response_model=ConnectionStatus,
    tags=["WhatsApp"],
)
async def status(req: Request) -> dict[str, Any]:
    """Check WhatsApp connection status and QR code availability."""
    _require_api_auth(req, allow_qr_session=True)
    user_id = _extract_user_id(req)
    response = await _bridge_request(
        req, "GET", "/status", timeout_seconds=10.0, user_id=user_id
    )
    return response.json()


@app.get("/qr", include_in_schema=False)
async def get_qr(req: Request):
    """Get QR code for WhatsApp authentication (not exposed as a tool)."""
    _require_api_auth(req, allow_qr_session=True)
    user_id = _extract_user_id(req)
    response = await _bridge_request(
        req, "GET", "/qr", timeout_seconds=60.0, user_id=user_id
    )
    content_type = response.headers.get("content-type", "")
    if "image/" in content_type:
        return Response(
            content=response.content,
            media_type=content_type,
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return response.json()


@app.post("/qr_session", include_in_schema=False)
async def create_qr_session(req: Request):
    """Create a QR session for authentication (not exposed as a tool)."""
    _require_api_auth(req)
    token = secrets.token_urlsafe(24)
    expires_at = time.time() + QR_SESSION_TTL_SECONDS
    _qr_sessions[token] = expires_at
    response = JSONResponse(
        {
            "expires_at": int(expires_at),
            "modal_url": "/qr_modal",
        }
    )
    max_age = max(0, int(expires_at - time.time()))
    response.set_cookie(
        WHATSAPP_QR_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return response


@app.get("/qr_modal", response_class=HTMLResponse, include_in_schema=False)
async def qr_modal(req: Request):
    """QR code modal page (not exposed as a tool)."""
    _require_api_auth(req, allow_qr_session=True)
    html = """
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>WhatsApp QR</title>
    <style>
      body { font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; padding: 24px; background: #0b0f12; color: #f5f6f7; }
      .card { background: #141a1f; border-radius: 16px; padding: 20px; max-width: 420px; margin: 0 auto; box-shadow: 0 12px 40px rgba(0,0,0,0.35); }
      h1 { font-size: 20px; margin: 0 0 12px; }
      p { margin: 0 0 16px; color: #b8c0c8; }
      img { width: 100%; border-radius: 12px; background: #0f1418; }
      .status { margin-top: 12px; font-size: 14px; color: #8fd19e; }
      .error { color: #ffb3b3; }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>Scan WhatsApp QR</h1>
      <p>Open WhatsApp on your phone and scan this code to connect.</p>
      <img id="qr" alt="WhatsApp QR code" />
      <div id="status" class="status">Loading QR…</div>
    </div>
    <script>
      const statusEl = document.getElementById('status');
      const qrEl = document.getElementById('qr');

      async function fetchQr() {
        const response = await fetch('/qr', {
          credentials: 'include'
        });
        const contentType = response.headers.get('content-type') || '';
        if (contentType.includes('image/')) {
          const blob = await response.blob();
          qrEl.src = URL.createObjectURL(blob);
          statusEl.textContent = 'Waiting for scan…';
          statusEl.classList.remove('error');
          return;
        }
        const data = await response.json();
        statusEl.textContent = data.message || 'Waiting for QR…';
        statusEl.classList.remove('error');
      }

      async function pollStatus() {
        const response = await fetch('/status', {
          credentials: 'include'
        });
        const data = await response.json();
        if (data.connected) {
          statusEl.textContent = 'Connected! You can close this window.';
          return true;
        }
        return false;
      }

      async function loop() {
        try {
          await fetchQr();
          const connected = await pollStatus();
          if (connected) return;
          setTimeout(loop, 4000);
        } catch (err) {
          statusEl.textContent = 'Unable to load QR. Please refresh.';
          statusEl.classList.add('error');
        }
      }

      loop();
    </script>
  </body>
</html>
"""
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.post("/start", include_in_schema=False)
async def start_client(req: Request):
    """Manually start the WhatsApp client initialization (not exposed as a tool)."""
    _require_api_auth(req)
    user_id = _extract_user_id(req)
    response = await _bridge_request(
        req, "POST", "/start", timeout_seconds=10.0, user_id=user_id
    )
    return response.json()


@app.post(
    "/send_message",
    operation_id="send_whatsapp_message",
    summary="Send a WhatsApp message",
    description="Send a text message to a WhatsApp phone number or group. The recipient must be specified with country code (e.g., +14155551234). Returns confirmation when the message is sent successfully.",
    response_model=MessageSentResponse,
    tags=["WhatsApp"],
)
async def send_message(req: Request, body: SendMessageRequest) -> dict[str, Any]:
    """Send a WhatsApp message to a phone number or group."""
    _require_api_auth(req)
    user_id = _extract_user_id(req)
    response = await _bridge_request(
        req,
        "POST",
        "/send",
        json_body={"to": body.to, "message": body.message},
        timeout_seconds=30.0,
        user_id=user_id,
    )
    return response.json()


@app.post(
    "/get_messages",
    operation_id="get_whatsapp_messages",
    summary="Get recent WhatsApp messages",
    description="Retrieve recent messages from a WhatsApp chat. Specify the phone number with country code (e.g., +14155551234) or group ID. Returns up to 200 most recent messages.",
    response_model=GetMessagesResponse,
    tags=["WhatsApp"],
)
async def get_messages(req: Request, body: GetMessagesRequest) -> dict[str, Any]:
    """Get recent messages from a WhatsApp chat."""
    _require_api_auth(req)
    user_id = _extract_user_id(req)
    response = await _bridge_request(
        req,
        "POST",
        "/messages",
        json_body={"chat_id": body.chat_id, "limit": body.limit},
        timeout_seconds=30.0,
        user_id=user_id,
    )
    return response.json()
