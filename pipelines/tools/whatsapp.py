"""Tool definitions and HTTP caller for WhatsApp bridge."""

import base64
import os
from typing import Any

import httpx

WHATSAPP_BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://localhost:3000")
WHATSAPP_BRIDGE_TOKEN = os.getenv("WHATSAPP_BRIDGE_TOKEN", "")
WHATSAPP_PIPELINE_ENABLED = (
    os.getenv("WHATSAPP_PIPELINE_ENABLED", "false").lower() == "true"
)
WHATSAPP_ALLOWED_EMAILS = {
    e.strip().lower()
    for e in os.getenv("WHATSAPP_ALLOWED_EMAILS", "").split(",")
    if e.strip()
}

TOOLS = [
    {
        "name": "whatsapp_send",
        "description": "Send a WhatsApp message to a phone number or group.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "Phone number with country code (e.g. +13105551234) or group ID",
                },
                "message": {"type": "string", "description": "Message text"},
            },
            "required": ["to", "message"],
        },
    },
    {
        "name": "whatsapp_messages",
        "description": "Get recent WhatsApp messages from a chat.",
        "input_schema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "Phone number or group ID",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of messages to retrieve",
                    "default": 20,
                },
            },
            "required": ["chat_id"],
        },
    },
    {
        "name": "whatsapp_status",
        "description": "Check WhatsApp connection status and whether QR scan is needed.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "whatsapp_qr",
        "description": (
            "Get the WhatsApp QR code for authentication. "
            "Returns base64-encoded PNG image suitable for display."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


def _bridge_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if WHATSAPP_BRIDGE_TOKEN:
        headers["X-WhatsApp-Bridge-Token"] = WHATSAPP_BRIDGE_TOKEN
    return headers


def _format_upstream_error(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "Error: WhatsApp bridge timed out. Please retry in a few seconds."
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text
        if status == 401:
            return "Error: WhatsApp bridge authorization failed."
        if status == 503:
            return "Error: WhatsApp is not connected. Request the QR code and scan it first."
        return f"Error: WhatsApp bridge returned {status}: {body}"
    if isinstance(exc, httpx.HTTPError):
        return f"Error: Unable to reach WhatsApp bridge: {exc}"
    return f"Error: {exc}"


async def _request(
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 20.0,
) -> httpx.Response:
    headers = _bridge_headers() or None
    url = f"{WHATSAPP_BRIDGE_URL}{endpoint}"
    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=timeout) as client:
        for _ in range(2):
            try:
                response = await client.request(
                    method, url, json=payload, headers=headers
                )
                response.raise_for_status()
                return response
            except (
                httpx.TimeoutException,
                httpx.HTTPStatusError,
                httpx.HTTPError,
            ) as exc:
                last_error = exc
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code < 500
                ):
                    break

    assert last_error is not None
    raise last_error


async def call_tool(name: str, arguments: dict, user_email: str | None = None) -> str:
    """Execute a WhatsApp bridge action via HTTP."""
    if not WHATSAPP_PIPELINE_ENABLED:
        return (
            "Error: WhatsApp pipeline tools are disabled. "
            "Use the Open WebUI tool server for WhatsApp instead."
        )
    if WHATSAPP_ALLOWED_EMAILS:
        if not user_email or user_email.lower() not in WHATSAPP_ALLOWED_EMAILS:
            return "Error: WhatsApp tools are restricted for this user."

    endpoint_map = {
        "whatsapp_send": "/send",
        "whatsapp_messages": "/messages",
        "whatsapp_status": "/status",
        "whatsapp_qr": "/qr",
    }

    endpoint = endpoint_map[name]

    try:
        if name == "whatsapp_status":
            resp = await _request("GET", endpoint, timeout=10.0)
            return resp.text

        if name == "whatsapp_qr":
            resp = await _request("GET", endpoint, timeout=60.0)
            content_type = resp.headers.get("content-type", "")
            if "image/png" in content_type:
                b64 = base64.b64encode(resp.content).decode()
                return (
                    f"![WhatsApp QR Code](data:image/png;base64,{b64})\n\n"
                    "Scan this QR code in WhatsApp to reconnect."
                )
            return resp.text

        resp = await _request("POST", endpoint, payload=arguments, timeout=30.0)
        return resp.text
    except Exception as exc:
        return _format_upstream_error(exc)
