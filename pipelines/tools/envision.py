"""Tool definitions and HTTP caller for Envision-MCP (Slack, Procore, Sage, etc.)."""

import os

from tools.mcp_client import call_mcp_tool

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
                "channel_id": {
                    "type": "string",
                    "description": "Slack channel ID",
                },
            },
            "required": ["channel_id"],
        },
    },
    {
        "name": "slack_get_thread_replies",
        "description": "Get replies in a Slack thread.",
        "input_schema": {
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Slack channel ID"},
                "thread_ts": {
                    "type": "string",
                    "description": "Thread timestamp",
                },
            },
            "required": ["channel_id", "thread_ts"],
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
                "project_id": {"type": "string", "description": "Procore project ID"},
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
                "project_id": {"type": "string", "description": "Procore project ID"},
            },
            "required": ["project_id"],
        },
    },
]

TOOL_NAMES = {t["name"] for t in TOOLS}


async def call_tool(name: str, arguments: dict) -> str:
    """Execute an Envision-MCP tool via Streamable HTTP."""
    return await call_mcp_tool(ENVISION_MCP_URL, name, arguments, timeout=60.0)
