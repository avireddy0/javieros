"""WhatsApp OpenAPI Tool Server for Open WebUI.

Wraps the whatsapp-web.js bridge (whatsapp-bridge sidecar) as an
OpenAPI-compatible tool server following the open-webui/openapi-servers pattern.
"""

import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(
    title="WhatsApp Tools",
    description="Send and read WhatsApp messages via whatsapp-web.js bridge.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://whatsapp-bridge:3000")
BRIDGE_TOKEN = os.getenv("WHATSAPP_BRIDGE_TOKEN", "")
API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", "")


def _require_api_auth(req: Request) -> None:
    if not API_TOKEN:
        return
    if req.headers.get("X-WhatsApp-API-Token") != API_TOKEN:
        raise HTTPException(401, "Unauthorized")


class SendMessageRequest(BaseModel):
    to: str = Field(description="Phone number with country code, e.g. +5215512345678")
    message: str = Field(description="Message text to send")


class GetMessagesRequest(BaseModel):
    chat_id: str = Field(description="Phone number with country code")
    limit: int = Field(default=20, description="Number of recent messages to fetch")


@app.get("/status")
async def status(req: Request):
    """Check WhatsApp connection status and QR code availability."""
    _require_api_auth(req)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{BRIDGE_URL}/status",
            headers={"X-WhatsApp-Bridge-Token": BRIDGE_TOKEN} if BRIDGE_TOKEN else None,
        )
        resp.raise_for_status()
        return resp.json()


@app.get("/qr")
async def get_qr(req: Request):
    """Get QR code for WhatsApp authentication. Returns PNG image or status JSON."""
    _require_api_auth(req)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(
            f"{BRIDGE_URL}/qr",
            headers={"X-WhatsApp-Bridge-Token": BRIDGE_TOKEN} if BRIDGE_TOKEN else None,
        )
        resp.raise_for_status()
        # Check if response is JSON (status) or image (QR code)
        content_type = resp.headers.get("content-type", "")
        if "image" in content_type:
            from fastapi.responses import Response
            return Response(content=resp.content, media_type=content_type)
        return resp.json()


@app.post("/start")
async def start_client(req: Request):
    """Manually start the WhatsApp client initialization."""
    _require_api_auth(req)
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{BRIDGE_URL}/start",
            headers={"X-WhatsApp-Bridge-Token": BRIDGE_TOKEN} if BRIDGE_TOKEN else None,
        )
        resp.raise_for_status()
        return resp.json()


@app.post("/send_message")
async def send_message(req: Request, body: SendMessageRequest):
    """Send a WhatsApp message to a phone number."""
    _require_api_auth(req)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BRIDGE_URL}/send",
            json={"to": body.to, "message": body.message},
            headers={"X-WhatsApp-Bridge-Token": BRIDGE_TOKEN} if BRIDGE_TOKEN else None,
        )
        if resp.status_code == 503:
            raise HTTPException(503, "WhatsApp not connected. Scan QR code first.")
        resp.raise_for_status()
        return resp.json()


@app.post("/get_messages")
async def get_messages(req: Request, body: GetMessagesRequest):
    """Get recent messages from a WhatsApp chat."""
    _require_api_auth(req)
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BRIDGE_URL}/messages",
            json={"chat_id": body.chat_id, "limit": body.limit},
            headers={"X-WhatsApp-Bridge-Token": BRIDGE_TOKEN} if BRIDGE_TOKEN else None,
        )
        if resp.status_code == 503:
            raise HTTPException(503, "WhatsApp not connected. Scan QR code first.")
        resp.raise_for_status()
        return resp.json()
