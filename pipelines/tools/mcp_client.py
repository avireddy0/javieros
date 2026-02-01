"""Shared MCP Streamable HTTP client for calling tools on MCP servers."""

import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

# Cache session IDs per base_url to avoid re-initializing every call
_sessions: dict[str, str] = {}


def _get_id_token(audience: str) -> str | None:
    """Fetch a Google Cloud ID token for service-to-service auth."""
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token

        request = google.auth.transport.requests.Request()
        return google.oauth2.id_token.fetch_id_token(request, audience)
    except Exception:
        return None


def _build_headers(base_url: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if base_url.startswith("https://") and ".run.app" in base_url:
        token = _get_id_token(base_url)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


async def _initialize_session(client: httpx.AsyncClient, base_url: str, headers: dict) -> str | None:
    """Send MCP initialize handshake and return session ID."""
    resp = await client.post(
        f"{base_url}/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "javieros-pipeline", "version": "1.0.0"},
            },
        },
        headers=headers,
    )
    resp.raise_for_status()
    session_id = resp.headers.get("mcp-session-id")
    if session_id:
        _sessions[base_url] = session_id
        logger.info("MCP session initialized for %s: %s", base_url, session_id)

        # Send initialized notification (required by spec)
        notify_headers = {**headers}
        if session_id:
            notify_headers["mcp-session-id"] = session_id
        await client.post(
            f"{base_url}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            headers=notify_headers,
        )
    return session_id


async def call_mcp_tool(
    base_url: str, name: str, arguments: dict, timeout: float = 60.0
) -> str:
    """Call a tool on an MCP server using Streamable HTTP transport.

    Performs MCP initialize handshake if no session exists for this server.
    Retries once on 400/404 in case the session expired.
    """
    headers = _build_headers(base_url)

    async with httpx.AsyncClient(timeout=timeout) as client:
        # Initialize session if we don't have one
        session_id = _sessions.get(base_url)
        if not session_id:
            session_id = await _initialize_session(client, base_url, headers)

        for attempt in range(2):
            call_headers = {**headers}
            if session_id:
                call_headers["mcp-session-id"] = session_id

            resp = await client.post(
                f"{base_url}/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                headers=call_headers,
            )

            # Session expired — re-initialize and retry once
            if resp.status_code in (400, 404) and attempt == 0:
                logger.warning("MCP session may be stale for %s, re-initializing", base_url)
                _sessions.pop(base_url, None)
                session_id = await _initialize_session(client, base_url, headers)
                continue

            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                return _parse_sse_response(resp.text)

            data = resp.json()
            return _extract_text(data)

    return "Error: MCP tool call failed after retries"


def _parse_sse_response(text: str) -> str:
    """Extract MCP tool result from SSE event stream."""
    for line in text.splitlines():
        if line.startswith("data: "):
            try:
                data = json.loads(line[6:])
                return _extract_text(data)
            except json.JSONDecodeError:
                continue
    return text


def _extract_text(data: dict) -> str:
    """Extract text content from MCP JSON-RPC response."""
    result = data.get("result", data)
    content = result.get("content", [])
    texts = [c["text"] for c in content if isinstance(c, dict) and c.get("type") == "text"]
    return "\n".join(texts) if texts else json.dumps(result)
