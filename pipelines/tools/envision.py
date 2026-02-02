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


INTERNAL_DOMAINS = {"envsn.com", "loxsle.com", "prometheus.ventures"}


def _is_internal(email: str | None) -> bool:
    if not email or "@" not in email:
        return False
    return email.lower().split("@")[-1] in INTERNAL_DOMAINS


async def call_tool(name: str, arguments: dict, user_email: str | None = None) -> str:
    """Execute an Envision-MCP tool via Streamable HTTP.

    For external users, injects authorized_project_ids from Procore directory cache.
    Internal users (envsn.com, loxsle.com, prometheus.ventures) get full access.
    """
    if user_email and not _is_internal(user_email):
        # External user — resolve project-level ACL from BigQuery
        project_ids = await _get_authorized_project_ids(user_email)
        if project_ids is not None:
            arguments["authorized_project_ids"] = project_ids
    return await call_mcp_tool(ENVISION_MCP_URL, name, arguments, timeout=60.0)


async def _get_authorized_project_ids(email: str) -> list[str] | None:
    """Query procore_directory_cache for projects this external user can access."""
    try:
        from google.cloud import bigquery as bq

        client = bq.Client(project="claude-mcp-457317")
        query = """
        SELECT DISTINCT project_id
        FROM `claude-mcp-457317.owner_communications.procore_directory_cache`
        WHERE LOWER(email) = LOWER(@email)
        ORDER BY project_id
        """
        job_config = bq.QueryJobConfig(
            query_parameters=[bq.ScalarQueryParameter("email", "STRING", email)]
        )
        rows = list(client.query(query, job_config=job_config).result())
        return [r.project_id for r in rows if r.project_id]
    except Exception:
        return []  # Fail secure
