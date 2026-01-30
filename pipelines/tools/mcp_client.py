"""Shared MCP Streamable HTTP client for calling tools on MCP servers."""

import json

import httpx


async def call_mcp_tool(
    base_url: str, name: str, arguments: dict, timeout: float = 60.0
) -> str:
    """Call a tool on an MCP server using Streamable HTTP transport.

    Handles both JSON and SSE responses.
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base_url}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")

        # SSE response — parse the event stream for the result
        if "text/event-stream" in content_type:
            return _parse_sse_response(resp.text)

        # Plain JSON response
        data = resp.json()
        return _extract_text(data)


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
