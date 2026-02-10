import json
import os
import time
from typing import Optional

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from open_webui.env import AIOHTTP_CLIENT_TIMEOUT
from open_webui.utils.auth import get_verified_user


WHATSAPP_API_BASE_URL = os.getenv(
    "WHATSAPP_API_BASE_URL", "http://localhost:8000"
).rstrip("/")
WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN", "")
WHATSAPP_QR_COOKIE = "openwebui_whatsapp_qr"
WHATSAPP_QR_COOKIE_SECURE = (
    os.getenv("WHATSAPP_QR_COOKIE_SECURE", "true").lower() == "true"
)


router = APIRouter()


async def _proxy_request(
    method: str,
    path: str,
    token: str,
    user_id: Optional[str] = None,
    json_payload: Optional[dict] = None,
    timeout_seconds: float = 15.0,
):
    url = f"{WHATSAPP_API_BASE_URL}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    if user_id:
        headers["X-User-ID"] = user_id
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            method, url, headers=headers, json=json_payload
        ) as resp:
            content = await resp.read()
            # Normalize header keys to lowercase for consistent access
            normalized_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, normalized_headers, content


@router.post("/qr_session")
async def create_qr_session(user=Depends(get_verified_user)):
    if not WHATSAPP_API_TOKEN:
        raise HTTPException(status_code=500, detail="WhatsApp API token missing")
    response = await _proxy_request(
        "POST",
        "/qr_session",
        WHATSAPP_API_TOKEN,
        user_id=user.id,
        timeout_seconds=AIOHTTP_CLIENT_TIMEOUT,
    )
    status_code, _, content = response
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=content.decode("utf-8"))
    payload = json.loads(content.decode("utf-8"))
    token = payload.get("token")
    expires_at = payload.get("expires_at")
    if not token:
        raise HTTPException(status_code=500, detail="Invalid QR session response")
    response = JSONResponse(
        {
            "expires_at": expires_at,
            "modal_url": "/api/v1/whatsapp/qr_modal",
        }
    )
    max_age = None
    if expires_at:
        max_age = max(0, int(expires_at - time.time()))
    response.set_cookie(
        WHATSAPP_QR_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=WHATSAPP_QR_COOKIE_SECURE,
        samesite="strict",
        path="/api/v1/whatsapp",
    )
    return response


@router.get("/qr_modal", response_class=HTMLResponse)
async def qr_modal(user=Depends(get_verified_user)):
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
      .btn { display: inline-block; margin-top: 16px; padding: 10px 20px; background: #ff4757; color: #fff; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500; text-decoration: none; }
      .btn:hover { background: #ff3838; }
      .btn:disabled { background: #555; cursor: not-allowed; }
      #disconnect-btn { display: none; }
    </style>
  </head>
  <body>
    <div class="card">
      <h1>Scan WhatsApp QR</h1>
      <p>Open WhatsApp on your phone and scan this code to connect.</p>
      <img id="qr" alt="WhatsApp QR code" />
      <div id="status" class="status">Loading QR...</div>
      <button id="disconnect-btn" class="btn" onclick="disconnect()">Disconnect WhatsApp</button>
    </div>
    <script>
      const statusEl = document.getElementById('status');
      const qrEl = document.getElementById('qr');
      const disconnectBtn = document.getElementById('disconnect-btn');

      async function startSession() {
        console.log('[WA] startSession called');
        const token = localStorage.getItem('token');
        if (!token) {
          console.error('[WA] No token in localStorage');
          statusEl.textContent = 'Please log in first.';
          statusEl.classList.add('error');
          return false;
        }

        try {
          console.log('[WA] Calling /start...');
          const response = await fetch('/api/v1/whatsapp/start', {
            method: 'POST',
            headers: { 'Authorization': 'Bearer ' + token }
          });
          console.log('[WA] /start response status:', response.status);

          if (response.status === 401) {
            statusEl.textContent = 'Session expired. Please log in again.';
            statusEl.classList.add('error');
            return false;
          }

          if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            console.error('[WA] Failed to start session:', data);
          }

          return true;
        } catch (err) {
          console.error('[WA] startSession error:', err);
          return true;
        }
      }

      async function fetchQr() {
        console.log('[WA] fetchQr called');
        const token = localStorage.getItem('token');
        if (!token) {
          console.error('[WA] No token for fetchQr');
          statusEl.textContent = 'Please log in first.';
          statusEl.classList.add('error');
          return;
        }

        console.log('[WA] Calling /qr...');
        const response = await fetch('/api/v1/whatsapp/qr', {
          headers: { 'Authorization': 'Bearer ' + token }
        });
        console.log('[WA] /qr response status:', response.status);

        if (response.status === 401) {
          statusEl.textContent = 'Session expired. Please log in again.';
          statusEl.classList.add('error');
          return;
        }

        const contentType = response.headers.get('content-type') || '';
        console.log('[WA] /qr content-type:', contentType);

        if (contentType.includes('image/')) {
          const blob = await response.blob();
          console.log('[WA] Got image blob, size:', blob.size);
          qrEl.src = URL.createObjectURL(blob);
          statusEl.textContent = 'Waiting for scan...';
          statusEl.classList.remove('error');
          return;
        }
        const data = await response.json();
        console.log('[WA] /qr JSON response:', data);
        statusEl.textContent = data.message || 'Waiting for QR...';
        statusEl.classList.remove('error');
      }

      async function pollStatus() {
        console.log('[WA] pollStatus called');
        const token = localStorage.getItem('token');
        if (!token) {
          console.error('[WA] No token for pollStatus');
          statusEl.textContent = 'Please log in first.';
          statusEl.classList.add('error');
          return false;
        }

        console.log('[WA] Calling /status...');
        const response = await fetch('/api/v1/whatsapp/status', {
          headers: { 'Authorization': 'Bearer ' + token }
        });
        console.log('[WA] /status response status:', response.status);

        if (response.status === 401) {
          statusEl.textContent = 'Session expired. Please log in again.';
          statusEl.classList.add('error');
          return false;
        }

        const data = await response.json();
        console.log('[WA] /status JSON response:', data);
        if (data.connected) {
          statusEl.textContent = 'Connected! You can close this window or disconnect below.';
          disconnectBtn.style.display = 'inline-block';
          return true;
        }
        return false;
      }

      async function disconnect() {
        if (!confirm('Are you sure you want to disconnect WhatsApp?')) return;

        const token = localStorage.getItem('token');
        if (!token) {
          statusEl.textContent = 'Please log in first.';
          statusEl.classList.add('error');
          return;
        }

        disconnectBtn.disabled = true;
        try {
          const response = await fetch('/api/v1/whatsapp/disconnect', {
            method: 'DELETE',
            headers: { 'Authorization': 'Bearer ' + token }
          });

          if (response.status === 401) {
            statusEl.textContent = 'Session expired. Please log in again.';
            statusEl.classList.add('error');
            disconnectBtn.disabled = false;
            return;
          }

          if (response.ok) {
            statusEl.textContent = 'Disconnected. You can close this window.';
            disconnectBtn.style.display = 'none';
          } else {
            statusEl.textContent = 'Failed to disconnect. Please try again.';
            statusEl.classList.add('error');
            disconnectBtn.disabled = false;
          }
        } catch (err) {
          statusEl.textContent = 'Network error. Please try again.';
          statusEl.classList.add('error');
          disconnectBtn.disabled = false;
        }
      }

      async function loop() {
        console.log('[WA] loop() starting');
        try {
          const started = await startSession();
          if (!started) {
            console.log('[WA] startSession returned false, stopping');
            return;
          }

          await fetchQr();
          const connected = await pollStatus();
          if (connected) {
            console.log('[WA] Connected! Stopping loop.');
            return;
          }
          console.log('[WA] Not connected yet, scheduling next poll in 4s');
          setTimeout(loop, 4000);
        } catch (err) {
          console.error('[WA] loop() caught error:', err);
          statusEl.textContent = 'Unable to load QR. Please refresh.';
          statusEl.classList.add('error');
        }
      }

      console.log('[WA] Script loaded, starting loop');
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


@router.post("/start")
async def start_whatsapp(req: Request, user=Depends(get_verified_user)):
    token = WHATSAPP_API_TOKEN
    if not token:
        raise HTTPException(status_code=500, detail="WhatsApp API token not configured")
    response = await _proxy_request(
        "POST",
        "/start",
        token,
        user_id=user.id,
        timeout_seconds=AIOHTTP_CLIENT_TIMEOUT,
    )
    status_code, headers, content = response
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=content.decode("utf-8"))
    return Response(content=content, media_type=headers.get("content-type"))


@router.get("/qr")
async def get_qr(req: Request, user=Depends(get_verified_user)):
    token = WHATSAPP_API_TOKEN
    if not token:
        raise HTTPException(status_code=500, detail="WhatsApp API token not configured")
    response = await _proxy_request(
        "GET",
        "/qr",
        token,
        user_id=user.id,
        timeout_seconds=AIOHTTP_CLIENT_TIMEOUT,
    )
    status_code, headers, content = response
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=content.decode("utf-8"))
    content_type = headers.get("content-type", "")
    # Check for image content-type OR PNG magic bytes (fallback if header is wrong)
    is_png = content[:8] == b'\x89PNG\r\n\x1a\n' if len(content) >= 8 else False
    if "image/" in content_type or is_png:
        return Response(
            content=content,
            media_type="image/png" if is_png else content_type,
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return Response(content=content, media_type=content_type or "application/json")


@router.get("/status")
async def get_status(req: Request, user=Depends(get_verified_user)):
    token = WHATSAPP_API_TOKEN
    if not token:
        raise HTTPException(status_code=500, detail="WhatsApp API token not configured")
    response = await _proxy_request(
        "GET",
        "/status",
        token,
        user_id=user.id,
        timeout_seconds=AIOHTTP_CLIENT_TIMEOUT,
    )
    status_code, headers, content = response
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=content.decode("utf-8"))
    return Response(content=content, media_type=headers.get("content-type"))


@router.delete("/disconnect")
async def disconnect_whatsapp(req: Request, user=Depends(get_verified_user)):
    token = WHATSAPP_API_TOKEN
    if not token:
        raise HTTPException(status_code=500, detail="WhatsApp API token not configured")
    response = await _proxy_request(
        "DELETE",
        "/disconnect",
        token,
        user_id=user.id,
        timeout_seconds=AIOHTTP_CLIENT_TIMEOUT,
    )
    status_code, headers, content = response
    if status_code >= 400:
        raise HTTPException(status_code=status_code, detail=content.decode("utf-8"))
    return Response(content=content, media_type=headers.get("content-type"))
