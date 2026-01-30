"""Tool definitions and HTTP caller for workspace-mcp (Gmail, Calendar, Drive)."""

from tools.mcp_client import call_mcp_tool

WORKSPACE_MCP_URL = "http://localhost:8000"

TOOLS = [
    {
        "name": "search_gmail",
        "description": "Search Gmail messages. Returns matching emails with subject, sender, date, and snippet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query (same syntax as Gmail search bar)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "send_email",
        "description": "Send an email or reply to an existing thread.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Email subject line"},
                "body": {"type": "string", "description": "Email body (plain text or HTML)"},
                "thread_id": {
                    "type": "string",
                    "description": "Thread ID to reply to (optional)",
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "list_calendar_events",
        "description": "List upcoming calendar events. Returns event title, time, location, and attendees.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "Number of days ahead to look",
                    "default": 7,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of events",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "create_calendar_event",
        "description": "Create a new calendar event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Event title"},
                "start_time": {
                    "type": "string",
                    "description": "Start time in ISO 8601 format",
                },
                "end_time": {
                    "type": "string",
                    "description": "End time in ISO 8601 format",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of attendee email addresses",
                },
                "location": {"type": "string", "description": "Event location"},
                "description": {"type": "string", "description": "Event description"},
            },
            "required": ["title", "start_time", "end_time"],
        },
    },
    {
        "name": "search_drive",
        "description": "Search Google Drive for files and documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for Drive files",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


async def call_tool(name: str, arguments: dict) -> str:
    """Execute a workspace-mcp tool via Streamable HTTP."""
    return await call_mcp_tool(WORKSPACE_MCP_URL, name, arguments, timeout=30.0)
