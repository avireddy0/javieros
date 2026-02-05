"""
FastMCP Slack Tools.

Registers Slack operations as MCP tools using the @server.tool() decorator.
These tools get the authenticated Slack client from the session context.
"""

import logging
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from server import server
from auth.oauth21_session_store import get_oauth21_session_store, get_session_context

logger = logging.getLogger(__name__)


def _get_slack_client() -> Optional[WebClient]:
    """
    Get authenticated Slack WebClient from session context.

    Returns:
        WebClient if authenticated, None otherwise
    """
    # Try session context first
    context = get_session_context()
    if context and context.auth_context:
        access_token = context.auth_context.get("access_token")
        if access_token:
            return WebClient(token=access_token)

    # Try to find session from store
    store = get_oauth21_session_store()

    # Check if we have any session (for testing/debugging)
    if store._sessions:
        # Get first available session
        for session_key, session_info in store._sessions.items():
            access_token = session_info.get("access_token")
            if access_token:
                return WebClient(token=access_token)

    return None


def _require_auth() -> WebClient:
    """
    Get authenticated Slack client or raise error.

    Returns:
        Authenticated WebClient

    Raises:
        Exception if not authenticated
    """
    client = _get_slack_client()
    if not client:
        raise Exception(
            "Slack authentication required. Please complete OAuth authorization first."
        )
    return client


# =============================================================================
# Channel Tools
# =============================================================================


@server.tool()
async def slack_list_channels(
    types: str = "public_channel,private_channel",
    limit: int = 100,
    exclude_archived: bool = False,
) -> dict:
    """
    List Slack channels in the workspace.

    Args:
        types: Channel types to list (comma-separated: public_channel, private_channel, mpim, im)
        limit: Maximum channels to return (default: 100)
        exclude_archived: Exclude archived channels (default: False)

    Returns:
        List of channels with metadata
    """
    client = _require_auth()

    try:
        result = client.conversations_list(
            types=types,
            limit=limit,
            exclude_archived=exclude_archived,
        )

        channels = [
            {
                "id": ch["id"],
                "name": ch["name"],
                "is_private": ch.get("is_private", False),
                "is_archived": ch.get("is_archived", False),
                "is_member": ch.get("is_member", False),
                "num_members": ch.get("num_members", 0),
                "topic": ch.get("topic", {}).get("value", ""),
                "purpose": ch.get("purpose", {}).get("value", ""),
            }
            for ch in result["channels"]
        ]

        return {
            "ok": True,
            "count": len(channels),
            "channels": channels,
        }
    except SlackApiError as e:
        return {
            "ok": False,
            "error": f"Slack API error: {e.response['error']}",
        }


@server.tool()
async def slack_get_channel_info(channel_id: str) -> dict:
    """
    Get detailed information about a specific Slack channel.

    Args:
        channel_id: Channel ID (e.g., C1234567890)

    Returns:
        Channel details including name, topic, purpose, member count
    """
    client = _require_auth()

    try:
        result = client.conversations_info(channel=channel_id)
        ch = result["channel"]

        return {
            "ok": True,
            "channel": {
                "id": ch["id"],
                "name": ch["name"],
                "is_private": ch.get("is_private", False),
                "is_archived": ch.get("is_archived", False),
                "is_member": ch.get("is_member", False),
                "num_members": ch.get("num_members", 0),
                "topic": ch.get("topic", {}).get("value", ""),
                "purpose": ch.get("purpose", {}).get("value", ""),
                "created": ch.get("created", 0),
                "creator": ch.get("creator", ""),
            },
        }
    except SlackApiError as e:
        return {
            "ok": False,
            "error": f"Slack API error: {e.response['error']}",
        }


@server.tool()
async def slack_get_channel_history(
    channel_id: str,
    limit: int = 100,
    oldest: Optional[str] = None,
    latest: Optional[str] = None,
) -> dict:
    """
    Get message history from a Slack channel.

    Args:
        channel_id: Channel ID to get history from
        limit: Number of messages to fetch (default: 100)
        oldest: Oldest timestamp to fetch from (Unix timestamp)
        latest: Latest timestamp to fetch to (Unix timestamp)

    Returns:
        List of messages with text, user, and timestamps
    """
    client = _require_auth()

    try:
        kwargs = {"channel": channel_id, "limit": limit}
        if oldest:
            kwargs["oldest"] = oldest
        if latest:
            kwargs["latest"] = latest

        result = client.conversations_history(**kwargs)

        messages = [
            {
                "text": msg.get("text", ""),
                "user": msg.get("user"),
                "ts": msg["ts"],
                "type": msg.get("type"),
                "thread_ts": msg.get("thread_ts"),
                "reply_count": msg.get("reply_count", 0),
                "reply_users_count": msg.get("reply_users_count", 0),
            }
            for msg in result["messages"]
        ]

        return {
            "ok": True,
            "count": len(messages),
            "messages": messages,
            "has_more": result.get("has_more", False),
        }
    except SlackApiError as e:
        return {
            "ok": False,
            "error": f"Slack API error: {e.response['error']}",
        }


# =============================================================================
# Message Tools
# =============================================================================


@server.tool()
async def slack_send_message(
    channel: str,
    text: str,
    thread_ts: Optional[str] = None,
) -> dict:
    """
    Send a message to a Slack channel or DM.

    Args:
        channel: Channel ID or name (e.g., #general, C1234567890)
        text: Message text to send
        thread_ts: Thread timestamp to reply to (optional, for threaded replies)

    Returns:
        Message send result with timestamp
    """
    client = _require_auth()

    try:
        kwargs = {"channel": channel, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        result = client.chat_postMessage(**kwargs)

        return {
            "ok": True,
            "channel": result["channel"],
            "ts": result["ts"],
            "message": {
                "text": result["message"]["text"],
                "user": result["message"].get("user"),
                "bot_id": result["message"].get("bot_id"),
            },
        }
    except SlackApiError as e:
        return {
            "ok": False,
            "error": f"Slack API error: {e.response['error']}",
        }


@server.tool()
async def slack_search_messages(
    query: str,
    count: int = 20,
    sort: str = "score",
    sort_dir: str = "desc",
) -> dict:
    """
    Search messages across all Slack channels.

    Args:
        query: Search query (supports Slack search syntax like "from:@user in:#channel")
        count: Number of results to return (default: 20, max: 100)
        sort: Sort by 'score' or 'timestamp' (default: 'score')
        sort_dir: Sort direction 'asc' or 'desc' (default: 'desc')

    Returns:
        Search results with matching messages
    """
    client = _require_auth()

    try:
        result = client.search_messages(
            query=query,
            count=min(count, 100),
            sort=sort,
            sort_dir=sort_dir,
        )

        messages = [
            {
                "text": msg["text"],
                "user": msg.get("user", msg.get("username")),
                "ts": msg["ts"],
                "channel": {
                    "id": msg.get("channel", {}).get("id"),
                    "name": msg.get("channel", {}).get("name"),
                },
                "permalink": msg.get("permalink"),
            }
            for msg in result["messages"]["matches"]
        ]

        return {
            "ok": True,
            "query": query,
            "total": result["messages"]["total"],
            "count": len(messages),
            "messages": messages,
        }
    except SlackApiError as e:
        return {
            "ok": False,
            "error": f"Slack API error: {e.response['error']}",
        }


# =============================================================================
# User Tools
# =============================================================================


@server.tool()
async def slack_list_users(
    limit: int = 100,
    include_bots: bool = False,
) -> dict:
    """
    List members in the Slack workspace.

    Args:
        limit: Maximum users to return (default: 100)
        include_bots: Include bot users (default: False)

    Returns:
        List of users with profile information
    """
    client = _require_auth()

    try:
        result = client.users_list(limit=limit)

        users = []
        for u in result["members"]:
            if not include_bots and u.get("is_bot", False):
                continue
            if u.get("deleted", False):
                continue

            users.append({
                "id": u["id"],
                "name": u["name"],
                "real_name": u.get("real_name", ""),
                "display_name": u.get("profile", {}).get("display_name", ""),
                "email": u.get("profile", {}).get("email", ""),
                "is_admin": u.get("is_admin", False),
                "status_text": u.get("profile", {}).get("status_text", ""),
                "status_emoji": u.get("profile", {}).get("status_emoji", ""),
            })

        return {
            "ok": True,
            "count": len(users),
            "users": users,
        }
    except SlackApiError as e:
        return {
            "ok": False,
            "error": f"Slack API error: {e.response['error']}",
        }


@server.tool()
async def slack_get_user_info(user_id: str) -> dict:
    """
    Get detailed profile information for a Slack user.

    Args:
        user_id: User ID (e.g., U1234567890)

    Returns:
        User profile with name, email, title, timezone, and status
    """
    client = _require_auth()

    try:
        result = client.users_info(user=user_id)
        u = result["user"]

        return {
            "ok": True,
            "user": {
                "id": u["id"],
                "name": u["name"],
                "real_name": u.get("real_name", ""),
                "display_name": u.get("profile", {}).get("display_name", ""),
                "email": u.get("profile", {}).get("email", ""),
                "phone": u.get("profile", {}).get("phone", ""),
                "title": u.get("profile", {}).get("title", ""),
                "is_admin": u.get("is_admin", False),
                "is_owner": u.get("is_owner", False),
                "tz": u.get("tz", ""),
                "tz_label": u.get("tz_label", ""),
                "status_text": u.get("profile", {}).get("status_text", ""),
                "status_emoji": u.get("profile", {}).get("status_emoji", ""),
                "avatar_url": u.get("profile", {}).get("image_192", ""),
            },
        }
    except SlackApiError as e:
        return {
            "ok": False,
            "error": f"Slack API error: {e.response['error']}",
        }


logger.info("Slack MCP tools registered successfully")
