"""
Slack User Tools.

Provides user listing and profile information.
"""

from typing import Optional, Dict, Any
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def list_users(
    client: WebClient,
    limit: int = 100,
    include_bots: bool = True,
    include_deleted: bool = False,
) -> Dict[str, Any]:
    """
    List workspace members.

    Args:
        client: Authenticated Slack WebClient
        limit: Maximum users to return (default: 100)
        include_bots: Include bot users (default: True)
        include_deleted: Include deleted users (default: False)

    Returns:
        dict: User list with profiles
    """
    try:
        result = client.users_list(limit=limit)

        users = []
        for u in result["members"]:
            # Filter based on parameters
            if not include_bots and u.get("is_bot", False):
                continue
            if not include_deleted and u.get("deleted", False):
                continue

            users.append({
                "id": u["id"],
                "name": u["name"],
                "real_name": u.get("real_name", ""),
                "display_name": u.get("profile", {}).get("display_name", ""),
                "email": u.get("profile", {}).get("email", ""),
                "is_bot": u.get("is_bot", False),
                "is_admin": u.get("is_admin", False),
                "is_owner": u.get("is_owner", False),
                "is_primary_owner": u.get("is_primary_owner", False),
                "deleted": u.get("deleted", False),
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


def get_user_info(client: WebClient, user_id: str) -> Dict[str, Any]:
    """
    Get detailed user profile information.

    Args:
        client: Authenticated Slack WebClient
        user_id: User ID (e.g., U1234567890)

    Returns:
        dict: User profile details
    """
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
                "is_bot": u.get("is_bot", False),
                "is_admin": u.get("is_admin", False),
                "is_owner": u.get("is_owner", False),
                "is_primary_owner": u.get("is_primary_owner", False),
                "deleted": u.get("deleted", False),
                "tz": u.get("tz", ""),
                "tz_label": u.get("tz_label", ""),
                "tz_offset": u.get("tz_offset", 0),
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
