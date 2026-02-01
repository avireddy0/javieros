"""WhatsApp OpenAPI Tool Server for Open WebUI.

Wraps the whatsapp-web.js bridge (whatsapp-bridge sidecar) as an
OpenAPI-compatible tool server following the open-webui/openapi-servers pattern.
"""

import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(
    title="WhatsApp Tools",
    description="Send and read WhatsApp messages via whatsapp-web.js bridge.",
    version="0.1.0",
)

BRIDGE_URL = os.getenv("WHATSAPP_BRIDGE_URL", "http://whatsapp-bridge:3000")


class SendMessageRequest(BaseModel):
    to: str = Field(description="Phone number with country code, e.g. +5215512345678")
    message: str = Field(description="Message text to send")


class GetMessagesRequest(BaseModel):
    chat_id: str = Field(description="Phone number with country code")
    limit: int = Field(default=20, description="Number of recent messages to fetch")


@app.get("/status")
async def status():
    """Check WhatsApp connection status and QR code availability."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{BRIDGE_URL}/status")
        resp.raise_for_status()
        return resp.json()


@app.post("/send_message")
async def send_message(req: SendMessageRequest):
    """Send a WhatsApp message to a phone number."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BRIDGE_URL}/send",
            json={"to": req.to, "message": req.message},
        )
        if resp.status_code == 503:
            raise HTTPException(503, "WhatsApp not connected. Scan QR code first.")
        resp.raise_for_status()
        return resp.json()


@app.post("/get_messages")
async def get_messages(req: GetMessagesRequest):
    """Get recent messages from a WhatsApp chat."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BRIDGE_URL}/messages",
            json={"chat_id": req.chat_id, "limit": req.limit},
        )
        if resp.status_code == 503:
            raise HTTPException(503, "WhatsApp not connected. Scan QR code first.")
        resp.raise_for_status()
        return resp.json()
