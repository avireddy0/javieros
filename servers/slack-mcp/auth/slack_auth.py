# auth/slack_auth.py

import asyncio
import logging
import os
from typing import Optional, Dict, Any
from urllib.parse import urlencode

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SlackOAuthProvider:
    """Handles Slack OAuth 2.0 authentication flow."""

    # Slack OAuth endpoints
    AUTHORIZATION_URL = "https://slack.com/oauth/v2/authorize"
    TOKEN_URL = "https://slack.com/api/oauth.v2.access"
    AUTH_TEST_URL = "https://slack.com/api/auth.test"

    # Required scopes for MCP functionality
    DEFAULT_SCOPES = [
        "channels:read",
        "channels:history",
        "chat:write",
        "users:read",
        "search:read",
        "groups:read",
        "groups:history",
        "im:read",
        "im:history",
        "mpim:read",
        "mpim:history",
    ]

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ):
        """
        Initialize Slack OAuth provider.

        Args:
            client_id: Slack app client ID (defaults to SLACK_CLIENT_ID env var)
            client_secret: Slack app client secret (defaults to SLACK_CLIENT_SECRET env var)
            redirect_uri: OAuth redirect URI (defaults to SLACK_REDIRECT_URI env var)
        """
        self.client_id = client_id or os.getenv("SLACK_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("SLACK_CLIENT_SECRET")
        self.redirect_uri = redirect_uri or os.getenv(
            "SLACK_REDIRECT_URI", "http://localhost:8080/oauth/callback"
        )

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Slack client ID and secret must be provided via constructor or environment variables "
                "(SLACK_CLIENT_ID, SLACK_CLIENT_SECRET)"
            )

        logger.info("SlackOAuthProvider initialized")

    def get_authorization_url(
        self, state: str, scopes: Optional[list[str]] = None
    ) -> str:
        """
        Generate authorization URL for user to grant permissions.

        Args:
            state: CSRF protection state parameter
            scopes: List of OAuth scopes (defaults to DEFAULT_SCOPES)

        Returns:
            Authorization URL for user to visit
        """
        scopes = scopes or self.DEFAULT_SCOPES
        params = {
            "client_id": self.client_id,
            "scope": ",".join(scopes),
            "redirect_uri": self.redirect_uri,
            "state": state,
        }
        auth_url = f"{self.AUTHORIZATION_URL}?{urlencode(params)}"
        logger.info(f"Generated authorization URL with state: {state[:8]}...")
        return auth_url

    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """
        Exchange authorization code for access token.

        Args:
            code: Authorization code from OAuth callback

        Returns:
            Token response containing access_token, team_id, user_id, etc.

        Raises:
            SlackApiError: If token exchange fails
        """
        logger.info("Exchanging authorization code for access token")

        # Use a temporary client for the token exchange
        client = WebClient()
        try:
            response = await asyncio.to_thread(
                client.oauth_v2_access,
                client_id=self.client_id,
                client_secret=self.client_secret,
                code=code,
                redirect_uri=self.redirect_uri,
            )

            if not response.get("ok"):
                error = response.get("error", "unknown_error")
                logger.error(f"Token exchange failed: {error}")
                raise SlackApiError(f"Token exchange failed: {error}", response)

            logger.info(
                f"Successfully exchanged code for token. Team: {response.get('team', {}).get('id')}"
            )
            return response.data

        except SlackApiError as e:
            logger.error(f"Slack API error during token exchange: {e}")
            raise

    async def validate_token(self, access_token: str) -> Dict[str, Any]:
        """
        Validate access token and get authentication info.

        Args:
            access_token: Slack access token to validate

        Returns:
            Auth test response containing user_id, team_id, etc.

        Raises:
            SlackApiError: If validation fails
        """
        logger.info("Validating access token")

        client = WebClient(token=access_token)
        try:
            response = await asyncio.to_thread(client.auth_test)

            if not response.get("ok"):
                error = response.get("error", "invalid_auth")
                logger.error(f"Token validation failed: {error}")
                raise SlackApiError(f"Token validation failed: {error}", response)

            logger.info(
                f"Token validated for user: {response.get('user_id')} in team: {response.get('team_id')}"
            )
            return response.data

        except SlackApiError as e:
            logger.error(f"Slack API error during token validation: {e}")
            raise

    async def get_user_info(self, access_token: str, user_id: str) -> Dict[str, Any]:
        """
        Get user information from Slack.

        Args:
            access_token: Slack access token
            user_id: Slack user ID

        Returns:
            User information including name, email, etc.

        Raises:
            SlackApiError: If user info retrieval fails
        """
        logger.info(f"Fetching user info for user_id: {user_id}")

        client = WebClient(token=access_token)
        try:
            response = await asyncio.to_thread(client.users_info, user=user_id)

            if not response.get("ok"):
                error = response.get("error", "user_not_found")
                logger.error(f"Failed to fetch user info: {error}")
                raise SlackApiError(f"Failed to fetch user info: {error}", response)

            user_data = response.get("user", {})
            logger.info(f"Retrieved user info for: {user_data.get('name')}")
            return user_data

        except SlackApiError as e:
            logger.error(f"Slack API error fetching user info: {e}")
            raise


class SlackAuthenticationError(Exception):
    """Exception raised when Slack authentication is required or fails."""

    def __init__(self, message: str, auth_url: Optional[str] = None):
        super().__init__(message)
        self.auth_url = auth_url


async def get_slack_client(
    access_token: Optional[str] = None,
    validate: bool = True,
) -> tuple[WebClient, Dict[str, Any]]:
    """
    Get authenticated Slack WebClient.

    Args:
        access_token: Slack access token (defaults to SLACK_BOT_TOKEN env var)
        validate: Whether to validate token before returning client

    Returns:
        tuple[WebClient, auth_info] - Authenticated client and auth info

    Raises:
        SlackAuthenticationError: If authentication fails or token is invalid
    """
    token = access_token or os.getenv("SLACK_BOT_TOKEN")

    if not token:
        error_msg = (
            "Slack authentication required. No access token provided. "
            "Please set SLACK_BOT_TOKEN environment variable or provide access_token parameter."
        )
        logger.error(error_msg)
        raise SlackAuthenticationError(error_msg)

    client = WebClient(token=token)

    # Validate token if requested
    auth_info = {}
    if validate:
        try:
            response = await asyncio.to_thread(client.auth_test)

            if not response.get("ok"):
                error = response.get("error", "invalid_auth")
                error_msg = f"Slack token validation failed: {error}"
                logger.error(error_msg)
                raise SlackAuthenticationError(error_msg)

            auth_info = response.data
            logger.info(
                f"Slack client authenticated for user: {auth_info.get('user_id')} "
                f"in team: {auth_info.get('team_id')}"
            )

        except SlackApiError as e:
            error_msg = f"Failed to validate Slack token: {str(e)}"
            logger.error(error_msg)
            raise SlackAuthenticationError(error_msg)

    return client, auth_info


async def start_slack_auth_flow(
    oauth_provider: SlackOAuthProvider,
    state: str,
    scopes: Optional[list[str]] = None,
) -> str:
    """
    Initiate Slack OAuth flow and return authorization message.

    Args:
        oauth_provider: Configured SlackOAuthProvider instance
        state: CSRF protection state parameter
        scopes: Optional list of OAuth scopes

    Returns:
        Formatted message with authorization URL for user
    """
    auth_url = oauth_provider.get_authorization_url(state, scopes)

    message_lines = [
        "**ACTION REQUIRED: Slack Authentication Needed**\n",
        "To proceed, you must authorize this application to access your Slack workspace.",
        "**Please click the link below to authorize:**",
        f"Authorization URL: {auth_url}",
        f"Markdown for hyperlink: [Click here to authorize Slack access]({auth_url})\n",
        "**Instructions:**",
        "1. Click the link and complete the authorization in your browser.",
        "2. After successful authorization, you will be redirected back.",
        "3. Retry your original command after authorization completes.",
    ]

    return "\n".join(message_lines)
