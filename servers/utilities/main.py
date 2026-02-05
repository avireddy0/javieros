"""
Utilities MCP Server - Timezone and Location Detection
Provides timezone detection from IP for Open WebUI External Tools.
"""

import os
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from fastmcp import FastMCP

# FastMCP instance with streamable HTTP transport
mcp = FastMCP(
    name="utilities",
    instructions="Utility tools for timezone detection and user context.",
)


@mcp.tool()
async def get_user_timezone(ip_address: str | None = None) -> dict:
    """
    Detect user's timezone based on IP address.

    Uses IP geolocation to determine the user's timezone.
    If no IP provided, uses the requesting client's IP.

    Args:
        ip_address: Optional IP address to look up (defaults to client IP)

    Returns:
        Timezone information including name, offset, and current local time
    """
    try:
        # Use ip-api.com (free, no key required, 45 req/min limit)
        lookup_ip = ip_address or ""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://ip-api.com/json/{lookup_ip}",
                params={"fields": "status,message,timezone,city,region,country,lat,lon"},
                timeout=10.0
            )
            data = response.json()

        if data.get("status") != "success":
            return {"error": data.get("message", "Failed to detect timezone")}

        tz_name = data.get("timezone", "UTC")
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)

        return {
            "timezone": tz_name,
            "utc_offset": now.strftime("%z"),
            "current_local_time": now.isoformat(),
            "city": data.get("city"),
            "region": data.get("region"),
            "country": data.get("country"),
            "coordinates": {
                "lat": data.get("lat"),
                "lon": data.get("lon")
            }
        }
    except Exception as e:
        return {"error": str(e), "timezone": "UTC", "current_local_time": datetime.utcnow().isoformat()}


@mcp.tool()
async def get_current_datetime(timezone: str = "UTC") -> dict:
    """
    Get current date and time in a specific timezone.

    Args:
        timezone: IANA timezone name (e.g., "America/New_York", "Europe/London")

    Returns:
        Current datetime information for the specified timezone
    """
    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)

        return {
            "timezone": timezone,
            "iso": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "day_of_week": now.strftime("%A"),
            "utc_offset": now.strftime("%z"),
            "timestamp": int(now.timestamp())
        }
    except Exception as e:
        return {"error": f"Invalid timezone: {e}"}


@mcp.tool()
async def convert_timezone(
    datetime_str: str,
    from_timezone: str,
    to_timezone: str
) -> dict:
    """
    Convert a datetime from one timezone to another.

    Args:
        datetime_str: ISO format datetime string (e.g., "2024-01-15T14:30:00")
        from_timezone: Source IANA timezone (e.g., "America/Los_Angeles")
        to_timezone: Target IANA timezone (e.g., "America/New_York")

    Returns:
        Converted datetime in the target timezone
    """
    try:
        from_tz = ZoneInfo(from_timezone)
        to_tz = ZoneInfo(to_timezone)

        # Parse the datetime
        dt = datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=from_tz)

        # Convert to target timezone
        converted = dt.astimezone(to_tz)

        return {
            "original": {
                "datetime": datetime_str,
                "timezone": from_timezone
            },
            "converted": {
                "iso": converted.isoformat(),
                "date": converted.strftime("%Y-%m-%d"),
                "time": converted.strftime("%H:%M:%S"),
                "timezone": to_timezone,
                "day_of_week": converted.strftime("%A")
            }
        }
    except Exception as e:
        return {"error": str(e)}


# Custom route for health check
@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    from starlette.responses import JSONResponse
    return JSONResponse({
        "status": "healthy",
        "service": "utilities-mcp",
        "version": "1.0.0"
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
