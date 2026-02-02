"""Tool definitions and HTTP caller for workspace-mcp (Gmail, Calendar, Drive)."""

import os

from tools.mcp_client import call_mcp_tool

WORKSPACE_MCP_URL = os.getenv("WORKSPACE_MCP_URL", "http://localhost:8000")
GOOGLE_USER_EMAIL = os.getenv("GOOGLE_DELEGATED_USER", "jabarrios@flow.life")

TOOLS = [
    {
        "name": "search_gmail_messages",
        "description": "Search Gmail messages. Returns matching emails with subject, sender, date, and snippet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query (same syntax as Gmail search bar, e.g. 'newer_than:3d' or 'from:john@example.com')",
                },
                "page_size": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "send_gmail_message",
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
                "cc": {"type": "string", "description": "CC recipients (optional)"},
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "get_events",
        "description": "List upcoming calendar events. Returns event title, time, location, and attendees.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min": {
                    "type": "string",
                    "description": "Start of time range in ISO 8601 format (e.g. 2026-02-01T00:00:00Z). Defaults to now.",
                },
                "time_max": {
                    "type": "string",
                    "description": "End of time range in ISO 8601 format.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of events",
                    "default": 25,
                },
                "query": {
                    "type": "string",
                    "description": "Free-text search query to filter events",
                },
            },
        },
    },
    {
        "name": "create_event",
        "description": "Create a new calendar event.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Event title"},
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
                "timezone": {"type": "string", "description": "Timezone (e.g. America/New_York)"},
            },
            "required": ["summary", "start_time", "end_time"],
        },
    },
    {
        "name": "search_drive_files",
        "description": "Search Google Drive for files and documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for Drive files",
                },
                "page_size": {
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
    # Workspace-MCP requires user_google_email for domain-wide delegation
    if "user_google_email" not in arguments:
        arguments["user_google_email"] = GOOGLE_USER_EMAIL
    return await call_mcp_tool(WORKSPACE_MCP_URL, name, arguments, timeout=30.0)
