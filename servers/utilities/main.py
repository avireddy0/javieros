"""
Utilities MCP Server - timezone and date/time helpers for Open WebUI tools.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastmcp import FastMCP

_http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def app_lifespan(_: Any):
    global _http_client
    timeout = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0)
    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
    _http_client = httpx.AsyncClient(timeout=timeout, limits=limits)
    try:
        yield
    finally:
        if _http_client is not None:
            await _http_client.aclose()
            _http_client = None


# FastMCP instance with streamable HTTP transport
mcp = FastMCP(
    name="utilities",
    instructions="Utility tools for timezone detection and user context.",
    lifespan=app_lifespan,
)


async def _lookup_timezone(ip_address: str | None) -> dict[str, Any]:
    """Use ipapi.co over HTTPS for timezone geolocation lookup."""
    lookup_ip = (ip_address or "").strip()
    endpoint = f"https://ipapi.co/{lookup_ip}/json/" if lookup_ip else "https://ipapi.co/json/"
    if _http_client is None:
        async with httpx.AsyncClient(timeout=10.0) as fallback_client:
            response = await fallback_client.get(endpoint)
    else:
        response = await _http_client.get(endpoint)
    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        return {"error": data.get("reason", "Failed to detect timezone")}

    timezone_name = data.get("timezone")
    if not timezone_name:
        return {"error": "Timezone was not returned by geolocation provider"}

    return data


@mcp.tool()
async def get_user_timezone(ip_address: str | None = None) -> dict:
    """
    Detect user's timezone based on IP address.

    Args:
        ip_address: Optional IP address to look up (defaults to caller IP)

    Returns:
        Timezone information including name, offset, and current local time
    """
    try:
        data = await _lookup_timezone(ip_address)
        if data.get("error"):
            return data

        tz_name = data.get("timezone", "UTC")
        now = datetime.now(ZoneInfo(tz_name))
        return {
            "timezone": tz_name,
            "utc_offset": now.strftime("%z"),
            "current_local_time": now.isoformat(),
            "city": data.get("city"),
            "region": data.get("region"),
            "country": data.get("country_name") or data.get("country"),
            "country_code": data.get("country_code"),
            "coordinates": {
                "lat": data.get("latitude"),
                "lon": data.get("longitude"),
            },
        }
    except Exception as exc:
        return {
            "error": str(exc),
            "timezone": "UTC",
            "current_local_time": datetime.utcnow().isoformat(),
        }


@mcp.tool()
async def get_current_datetime(timezone: str = "UTC") -> dict:
    """
    Get current date and time in a specific timezone.

    Args:
        timezone: IANA timezone name (e.g., "America/New_York")
    """
    try:
        now = datetime.now(ZoneInfo(timezone))
        return {
            "timezone": timezone,
            "iso": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "day_of_week": now.strftime("%A"),
            "utc_offset": now.strftime("%z"),
            "timestamp": int(now.timestamp()),
        }
    except Exception as exc:
        return {"error": f"Invalid timezone: {exc}"}


@mcp.tool()
async def convert_timezone(
    datetime_str: str,
    from_timezone: str,
    to_timezone: str,
) -> dict:
    """
    Convert a datetime from one timezone to another.
    """
    try:
        from_tz = ZoneInfo(from_timezone)
        to_tz = ZoneInfo(to_timezone)
        dt = datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=from_tz)
        converted = dt.astimezone(to_tz)
        return {
            "original": {"datetime": datetime_str, "timezone": from_timezone},
            "converted": {
                "iso": converted.isoformat(),
                "date": converted.strftime("%Y-%m-%d"),
                "time": converted.strftime("%H:%M:%S"),
                "timezone": to_timezone,
                "day_of_week": converted.strftime("%A"),
            },
        }
    except Exception as exc:
        return {"error": str(exc)}


@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    from starlette.responses import JSONResponse

    return JSONResponse(
        {
            "status": "healthy",
            "service": "utilities-mcp",
            "version": "1.1.0",
        }
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
