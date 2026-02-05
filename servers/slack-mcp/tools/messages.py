"""
Slack Message Tools.

Provides message sending, searching, and thread interactions.
"""

from typing import Optional, List, Dict, Any
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def send_message(
    client: WebClient,
    channel: str,
    text: str,
    thread_ts: Optional[str] = None,
    blocks: Optional[List[Dict]] = None,
    attachments: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """
    Send a message to a Slack channel or DM.

    Args:
        client: Authenticated Slack WebClient
        channel: Channel ID or name (e.g., #general, C1234567890)
        text: Message text to send
        thread_ts: Thread timestamp to reply to (optional)
        blocks: Block Kit blocks for rich formatting (optional)
        attachments: Legacy attachments (optional)

    Returns:
        dict: Message send result with timestamp
    """
    try:
        result = client.chat_postMessage(
            channel=channel,
            text=text,
            thread_ts=thread_ts,
            blocks=blocks,
            attachments=attachments,
        )

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


def search_messages(
    client: WebClient,
    query: str,
    count: int = 20,
    sort: str = "score",
    sort_dir: str = "desc",
) -> Dict[str, Any]:
    """
    Search messages across all channels.

    Args:
        client: Authenticated Slack WebClient
        query: Search query (supports Slack search syntax)
        count: Number of results to return (default: 20, max: 100)
        sort: Sort by 'score' or 'timestamp' (default: 'score')
        sort_dir: Sort direction 'asc' or 'desc' (default: 'desc')

    Returns:
        dict: Search results with messages
    """
    try:
        result = client.search_messages(
            query=query,
            count=min(count, 100),  # Slack API max is 100
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
                "type": msg.get("type"),
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


def reply_to_thread(
    client: WebClient,
    channel: str,
    thread_ts: str,
    text: str,
    broadcast: bool = False,
) -> Dict[str, Any]:
    """
    Reply to a thread in a channel.

    Args:
        client: Authenticated Slack WebClient
        channel: Channel ID where the thread exists
        thread_ts: Thread timestamp to reply to
        text: Reply message text
        broadcast: Also send to channel (default: False)

    Returns:
        dict: Reply result with timestamp
    """
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
