"""
Slack Channel Tools.

Provides channel listing, information, and history retrieval.
"""

from typing import Optional, List, Dict, Any
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def list_channels(
    client: WebClient,
    types: str = "public_channel,private_channel",
    limit: int = 100,
    exclude_archived: bool = False,
) -> Dict[str, Any]:
    """
    List workspace channels.

    Args:
        client: Authenticated Slack WebClient
        types: Channel types to list (comma-separated)
        limit: Maximum channels to return (default: 100)
        exclude_archived: Exclude archived channels (default: False)

    Returns:
        dict: Channel list with metadata
    """
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


def get_channel_info(client: WebClient, channel_id: str) -> Dict[str, Any]:
    """
    Get detailed information about a specific channel.

    Args:
        client: Authenticated Slack WebClient
        channel_id: Channel ID (e.g., C1234567890)

    Returns:
        dict: Channel details
    """
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


def get_channel_history(
    client: WebClient,
    channel_id: str,
    limit: int = 100,
    oldest: Optional[str] = None,
    latest: Optional[str] = None,
    inclusive: bool = False,
) -> Dict[str, Any]:
    """
    Get message history from a channel.

    Args:
        client: Authenticated Slack WebClient
        channel_id: Channel ID
        limit: Number of messages to fetch (default: 100)
        oldest: Oldest timestamp to fetch from
        latest: Latest timestamp to fetch to
        inclusive: Include messages with oldest/latest timestamps

    Returns:
        dict: Message history
    """
    try:
        result = client.conversations_history(
            channel=channel_id,
            limit=limit,
            oldest=oldest,
            latest=latest,
            inclusive=inclusive,
        )

        messages = [
            {
                "text": msg.get("text", ""),
                "user": msg.get("user"),
                "ts": msg["ts"],
                "type": msg.get("type"),
                "thread_ts": msg.get("thread_ts"),
                "reply_count": msg.get("reply_count", 0),
                "reply_users_count": msg.get("reply_users_count", 0),
                "latest_reply": msg.get("latest_reply"),
            }
            for msg in result["messages"]
        ]

        return {
            "ok": True,
            "count": len(messages),
            "messages": messages,
            "has_more": result.get("has_more", False),
            "response_metadata": result.get("response_metadata", {}),
        }
    except SlackApiError as e:
        return {
            "ok": False,
            "error": f"Slack API error: {e.response['error']}",
        }
