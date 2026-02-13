"""
FastMCP Slack Tools.

Registers Slack operations as MCP tools using the @server.tool() decorator.
These tools get the authenticated Slack client from the session context or Bearer token.
"""

import hmac
import logging
import os
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from server import server
from auth.oauth21_session_store import get_oauth21_session_store, get_session_context

logger = logging.getLogger(__name__)
ALLOW_SESSION_FALLBACK = (
    os.getenv("SLACK_ALLOW_SESSION_FALLBACK", "false").lower() == "true"
)


def _get_slack_client_from_bearer_token() -> Optional[WebClient]:
    """
    Get authenticated Slack WebClient from Bearer token in current request.

    Uses FastMCP's dependency injection to access HTTP headers.

    Returns:
        WebClient if Bearer token found and valid, None otherwise
    """
    try:
        # Try to import FastMCP's request dependencies
        from fastmcp.server.dependencies import get_http_request

        # Get the current HTTP request
        request = get_http_request()
        if not request:
            return None

        # Extract Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None

        token = auth_header[7:]  # Remove "Bearer " prefix

        # Look up session by access token
        session_store = get_oauth21_session_store()
        for session_key, session_info in session_store._sessions.items():
            stored_token = session_info.get("access_token", "")
            if stored_token and hmac.compare_digest(stored_token, token):
                slack_token = session_info.get("slack_access_token")
                if slack_token:
                    logger.debug(f"Found Slack token via Bearer auth for session {session_key}")
                    return WebClient(token=slack_token)

        logger.debug("Bearer token provided but no matching session found")
        return None

    except ImportError:
        logger.debug("FastMCP dependencies not available, skipping Bearer token auth")
        return None
    except Exception as e:
        logger.debug(f"Error getting Slack client from Bearer token: {e}")
        return None


def _get_slack_client() -> Optional[WebClient]:
    """
    Get authenticated Slack WebClient from session context or Bearer token.

    Tries multiple authentication methods in order:
    1. Session context (set during OAuth callback)
    2. Bearer token from current HTTP request
    3. Any available session in the store (fallback for testing)

    Returns:
        WebClient if authenticated, None otherwise
    """
    # Try session context first (set during OAuth flow)
    context = get_session_context()
    logger.info(f"[AUTH] Session context check: context={context is not None}")
    if context and context.auth_context:
        slack_token = context.auth_context.get("slack_access_token")
        if slack_token:
            logger.info("[AUTH] Using Slack token from session context")
            return WebClient(token=slack_token)

    # Try Bearer token from current HTTP request
    logger.info("[AUTH] Trying Bearer token auth...")
    client = _get_slack_client_from_bearer_token()
    if client:
        logger.info("[AUTH] Got client from Bearer token")
        return client

    if ALLOW_SESSION_FALLBACK:
        store = get_oauth21_session_store()
        for session_info in store._sessions.values():
            slack_token = session_info.get("slack_access_token")
            if slack_token:
                logger.warning("Using Slack session fallback (debug mode)")
                return WebClient(token=slack_token)

    logger.debug("No Slack authentication found")
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


# =============================================================================
# Thread Tools
# =============================================================================


@server.tool()
async def slack_get_thread_replies(
    channel_id: str,
    thread_ts: str,
    limit: int = 100,
) -> dict:
    """
    Get all replies in a message thread.

    Args:
        channel_id: Channel ID where the thread exists
        thread_ts: Timestamp of the parent message (thread root)
        limit: Maximum replies to return (default: 100)

    Returns:
        Full thread with parent message and all replies
    """
    client = _require_auth()

    try:
        result = client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            limit=limit,
        )

        messages = [
            {
                "text": msg.get("text", ""),
                "user": msg.get("user"),
                "ts": msg["ts"],
                "thread_ts": msg.get("thread_ts"),
                "is_parent": msg["ts"] == thread_ts,
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
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


@server.tool()
async def slack_reply_to_thread(
    channel: str,
    thread_ts: str,
    text: str,
    broadcast: bool = False,
) -> dict:
    """
    Reply to a thread in a Slack channel.

    Args:
        channel: Channel ID where the thread exists
        thread_ts: Timestamp of the parent message to reply to
        text: Reply message text
        broadcast: Also send to channel (default: False)

    Returns:
        Reply result with timestamp
    """
    client = _require_auth()

    try:
        result = client.chat_postMessage(
            channel=channel,
            text=text,
            thread_ts=thread_ts,
            reply_broadcast=broadcast,
        )

        return {
            "ok": True,
            "channel": result["channel"],
            "ts": result["ts"],
            "thread_ts": result["message"]["thread_ts"],
        }
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


# =============================================================================
# DM Tools
# =============================================================================


@server.tool()
async def slack_list_dms(limit: int = 100) -> dict:
    """
    List direct message conversations.

    Args:
        limit: Maximum DMs to return (default: 100)

    Returns:
        List of DM conversations with user info
    """
    client = _require_auth()

    try:
        result = client.conversations_list(types="im", limit=limit)

        dms = [
            {
                "id": ch["id"],
                "user": ch.get("user"),
                "is_open": ch.get("is_open", False),
                "latest_ts": ch.get("latest", {}).get("ts") if isinstance(ch.get("latest"), dict) else None,
            }
            for ch in result["channels"]
        ]

        return {"ok": True, "count": len(dms), "dms": dms}
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


@server.tool()
async def slack_list_group_dms(limit: int = 100) -> dict:
    """
    List multi-person direct message conversations (group DMs).

    Args:
        limit: Maximum group DMs to return (default: 100)

    Returns:
        List of group DM conversations
    """
    client = _require_auth()

    try:
        result = client.conversations_list(types="mpim", limit=limit)

        group_dms = [
            {
                "id": ch["id"],
                "name": ch.get("name", ""),
                "is_open": ch.get("is_open", False),
                "num_members": ch.get("num_members", 0),
                "purpose": ch.get("purpose", {}).get("value", ""),
            }
            for ch in result["channels"]
        ]

        return {"ok": True, "count": len(group_dms), "group_dms": group_dms}
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


@server.tool()
async def slack_open_dm(user_id: str) -> dict:
    """
    Open a direct message conversation with a user.

    Args:
        user_id: User ID to open DM with

    Returns:
        DM channel info for sending messages
    """
    client = _require_auth()

    try:
        result = client.conversations_open(users=[user_id])
        ch = result["channel"]

        return {
            "ok": True,
            "channel": {
                "id": ch["id"],
                "is_im": ch.get("is_im", True),
            },
        }
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


# =============================================================================
# Reaction Tools
# =============================================================================


@server.tool()
async def slack_add_reaction(
    channel: str,
    timestamp: str,
    name: str,
) -> dict:
    """
    Add an emoji reaction to a message.

    Args:
        channel: Channel ID where the message is
        timestamp: Message timestamp to react to
        name: Emoji name without colons (e.g., 'thumbsup', 'heart', 'eyes')

    Returns:
        Result confirming reaction was added
    """
    client = _require_auth()

    try:
        client.reactions_add(channel=channel, timestamp=timestamp, name=name)
        return {"ok": True, "reaction": name}
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


@server.tool()
async def slack_get_reactions(channel: str, timestamp: str) -> dict:
    """
    Get all reactions on a message.

    Args:
        channel: Channel ID where the message is
        timestamp: Message timestamp to get reactions for

    Returns:
        Message with all reactions and who reacted
    """
    client = _require_auth()

    try:
        result = client.reactions_get(channel=channel, timestamp=timestamp)
        msg = result["message"]

        reactions = [
            {
                "name": r["name"],
                "count": r["count"],
                "users": r["users"],
            }
            for r in msg.get("reactions", [])
        ]

        return {"ok": True, "reactions": reactions}
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


# =============================================================================
# Presence & Channel Management
# =============================================================================


@server.tool()
async def slack_get_user_presence(user_id: str) -> dict:
    """
    Check if a user is currently online, away, or offline.

    Args:
        user_id: User ID to check presence for

    Returns:
        User presence status (active/away)
    """
    client = _require_auth()

    try:
        result = client.users_getPresence(user=user_id)
        return {
            "ok": True,
            "presence": result["presence"],
            "online": result.get("online", False),
            "auto_away": result.get("auto_away", False),
        }
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


@server.tool()
async def slack_join_channel(channel_id: str) -> dict:
    """
    Join a public Slack channel.

    Args:
        channel_id: Channel ID to join

    Returns:
        Channel info after joining
    """
    client = _require_auth()

    try:
        result = client.conversations_join(channel=channel_id)
        ch = result["channel"]
        return {
            "ok": True,
            "channel": {"id": ch["id"], "name": ch["name"]},
        }
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


@server.tool()
async def slack_get_my_info() -> dict:
    """
    Get the authenticated user's own Slack profile and workspace info.

    Returns:
        Current user's profile, workspace name, and permissions
    """
    client = _require_auth()

    try:
        result = client.auth_test()
        return {
            "ok": True,
            "user_id": result["user_id"],
            "user": result["user"],
            "team_id": result["team_id"],
            "team": result["team"],
            "url": result.get("url", ""),
        }
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


# =============================================================================
# File, Pin, Bookmark & Star Tools (OpenClaw parity)
# =============================================================================


@server.tool()
async def slack_list_files(
    channel: Optional[str] = None,
    user: Optional[str] = None,
    types: str = "all",
    count: int = 20,
) -> dict:
    """
    List files shared in a channel or by a user.

    Args:
        channel: Channel ID to filter by (optional)
        user: User ID to filter by (optional)
        types: File types to include (all, spaces, snippets, images, gdocs, zips, pdfs)
        count: Number of files to return (default: 20, max: 100)

    Returns:
        List of shared files with metadata
    """
    client = _require_auth()

    try:
        kwargs = {"types": types, "count": min(count, 100)}
        if channel:
            kwargs["channel"] = channel
        if user:
            kwargs["user"] = user

        result = client.files_list(**kwargs)

        files = [
            {
                "id": f["id"],
                "name": f.get("name", ""),
                "title": f.get("title", ""),
                "filetype": f.get("filetype", ""),
                "size": f.get("size", 0),
                "user": f.get("user", ""),
                "url_private": f.get("url_private", ""),
                "permalink": f.get("permalink", ""),
                "created": f.get("created", 0),
                "channels": f.get("channels", []),
                "shares": list(f.get("shares", {}).get("public", {}).keys())
                + list(f.get("shares", {}).get("private", {}).keys())
                if f.get("shares")
                else [],
            }
            for f in result.get("files", [])
        ]

        return {
            "ok": True,
            "count": len(files),
            "total": result.get("paging", {}).get("total", len(files)),
            "files": files,
        }
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


@server.tool()
async def slack_get_pins(channel_id: str) -> dict:
    """
    Get pinned messages in a channel.

    Args:
        channel_id: Channel ID to get pins from

    Returns:
        List of pinned items with message content and who pinned them
    """
    client = _require_auth()

    try:
        result = client.pins_list(channel=channel_id)

        pins = [
            {
                "type": item.get("type", ""),
                "created": item.get("created", 0),
                "created_by": item.get("created_by", ""),
                "message": {
                    "text": item.get("message", {}).get("text", ""),
                    "user": item.get("message", {}).get("user", ""),
                    "ts": item.get("message", {}).get("ts", ""),
                    "permalink": item.get("message", {}).get("permalink", ""),
                }
                if item.get("message")
                else None,
            }
            for item in result.get("items", [])
        ]

        return {"ok": True, "count": len(pins), "pins": pins}
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


@server.tool()
async def slack_get_bookmarks(channel_id: str) -> dict:
    """
    Get bookmarks saved in a channel.

    Args:
        channel_id: Channel ID to get bookmarks from

    Returns:
        List of bookmarks with titles, links, and who added them
    """
    client = _require_auth()

    try:
        result = client.bookmarks_list(channel_id=channel_id)

        bookmarks = [
            {
                "id": b.get("id", ""),
                "title": b.get("title", ""),
                "type": b.get("type", ""),
                "link": b.get("link", ""),
                "emoji": b.get("emoji", ""),
                "icon_url": b.get("icon_url", ""),
                "created": b.get("date_created", 0),
                "updated": b.get("date_updated", 0),
            }
            for b in result.get("bookmarks", [])
        ]

        return {"ok": True, "count": len(bookmarks), "bookmarks": bookmarks}
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


@server.tool()
async def slack_get_stars(count: int = 20) -> dict:
    """
    Get the authenticated user's starred items.

    Args:
        count: Number of starred items to return (default: 20, max: 100)

    Returns:
        List of starred messages, files, and channels
    """
    client = _require_auth()

    try:
        result = client.stars_list(count=min(count, 100))

        stars = []
        for item in result.get("items", []):
            star = {"type": item.get("type", "")}

            if item.get("type") == "message":
                msg = item.get("message", {})
                star["message"] = {
                    "text": msg.get("text", ""),
                    "user": msg.get("user", ""),
                    "ts": msg.get("ts", ""),
                    "permalink": msg.get("permalink", ""),
                }
                star["channel"] = item.get("channel", "")
            elif item.get("type") == "file":
                f = item.get("file", {})
                star["file"] = {
                    "id": f.get("id", ""),
                    "name": f.get("name", ""),
                    "title": f.get("title", ""),
                    "permalink": f.get("permalink", ""),
                }
            elif item.get("type") == "channel":
                star["channel"] = item.get("channel", "")

            stars.append(star)

        return {
            "ok": True,
            "count": len(stars),
            "total": result.get("paging", {}).get("total", len(stars)),
            "stars": stars,
        }
    except SlackApiError as e:
        return {"ok": False, "error": f"Slack API error: {e.response['error']}"}


logger.info("Slack MCP tools registered successfully")
