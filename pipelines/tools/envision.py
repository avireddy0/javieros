"""Tool definitions and HTTP caller for Envision-MCP (Slack, Procore, Sage, etc.)."""

import os

import httpx

ENVISION_MCP_URL = os.getenv(
    "ENVISION_MCP_URL",
    "https://envision-mcp-845049957105.us-central1.run.app",
)

TOOLS = [
    {
        "name": "slack_search_messages",
        "description": "Search Slack messages across all channels.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "count": {
                    "type": "integer",
                    "description": "Number of results",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "slack_send_message",
        "description": "Send a message to a Slack channel or DM.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel name (e.g. #general) or channel ID",
                },
                "text": {"type": "string", "description": "Message text"},
                "thread_ts": {
                    "type": "string",
                    "description": "Thread timestamp to reply to (optional)",
                },
            },
            "required": ["channel", "text"],
        },
    },
    {
        "name": "slack_get_conversation_history",
        "description": "Get recent messages from a Slack channel.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel name or ID",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of messages",
                    "default": 20,
                },
            },
            "required": ["channel"],
        },
    },
    {
        "name": "slack_get_thread_replies",
        "description": "Get replies in a Slack thread.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID"},
                "thread_ts": {
                    "type": "string",
                    "description": "Thread timestamp",
                },
            },
            "required": ["channel", "thread_ts"],
        },
    },
    {
        "name": "procore_get_projects",
        "description": "List Procore projects.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "procore_get_rfis",
        "description": "Get RFIs for a Procore project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Procore project ID"},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "procore_get_budget",
        "description": "Get budget line items for a Procore project.",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Procore project ID"},
            },
            "required": ["project_id"],
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


async def call_tool(name: str, arguments: dict) -> str:
    """Execute an Envision-MCP tool via HTTP."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{ENVISION_MCP_URL}/call-tool",
            json={"name": name, "arguments": arguments},
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", [])
        texts = [c["text"] for c in content if c.get("type") == "text"]
        return "\n".join(texts) if texts else str(data)
