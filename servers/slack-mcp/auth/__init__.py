# auth/__init__.py

"""
Slack MCP Server Authentication Module

Provides OAuth 2.0 authentication for Slack API access.
"""

from auth.slack_auth import (
    SlackOAuthProvider,
    SlackAuthenticationError,
    get_slack_client,
    start_slack_auth_flow,
)

__all__ = [
    "SlackOAuthProvider",
    "SlackAuthenticationError",
    "get_slack_client",
    "start_slack_auth_flow",
]
