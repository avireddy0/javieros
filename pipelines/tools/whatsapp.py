"""Tool definitions and HTTP caller for WhatsApp bridge (whatsapp-web.js sidecar)."""

import base64

import httpx

WHATSAPP_BRIDGE_URL = "http://localhost:3000"

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
        "description": "Get the WhatsApp QR code for authentication. Returns base64-encoded PNG image that can be displayed to the user for scanning.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


async def call_tool(name: str, arguments: dict) -> str:
    """Execute a WhatsApp bridge action via HTTP."""
    endpoint_map = {
        "whatsapp_send": "/send",
        "whatsapp_messages": "/messages",
        "whatsapp_status": "/status",
        "whatsapp_qr": "/qr",
    }
    endpoint = endpoint_map[name]

    async with httpx.AsyncClient(timeout=15.0) as client:
        if name == "whatsapp_status":
            resp = await client.get(f"{WHATSAPP_BRIDGE_URL}{endpoint}")
            return resp.text
        elif name == "whatsapp_qr":
            resp = await client.get(f"{WHATSAPP_BRIDGE_URL}{endpoint}")
            # Check if it's JSON (status message) or PNG (actual QR)
            content_type = resp.headers.get("content-type", "")
            if "image/png" in content_type:
                b64 = base64.b64encode(resp.content).decode()
                return f"![WhatsApp QR Code](data:image/png;base64,{b64})\n\nScan this QR code with WhatsApp on your phone to connect."
            else:
                return resp.text
        else:
            resp = await client.post(
                f"{WHATSAPP_BRIDGE_URL}{endpoint}",
                json=arguments,
            )
            resp.raise_for_status()
            return resp.text
