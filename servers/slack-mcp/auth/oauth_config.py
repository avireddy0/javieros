"""
OAuth Configuration Management for Slack MCP

This module centralizes OAuth-related configuration to eliminate hardcoded values
scattered throughout the codebase. It provides environment variable support and
sensible defaults for all OAuth-related settings.

Supports OAuth 2.1 with PKCE for secure Slack authentication.
"""

import os
from urllib.parse import urlparse
from typing import List, Optional, Dict, Any


class OAuthConfig:
    """
    Centralized OAuth configuration management for Slack MCP.

    This class provides a single source of truth for all
    OAuth-related configuration values.
    """

    def __init__(self):
        # Base server configuration
        self.base_uri = os.getenv("SLACK_MCP_BASE_URI", "http://localhost")
        self.port = int(os.getenv("PORT", os.getenv("SLACK_MCP_PORT", "8000")))
        self.base_url = f"{self.base_uri}:{self.port}"

        # External URL for reverse proxy scenarios
        self.external_url = os.getenv("SLACK_EXTERNAL_URL")

        # Slack OAuth client configuration
        self.client_id = os.getenv("SLACK_OAUTH_CLIENT_ID")
        self.client_secret = os.getenv("SLACK_OAUTH_CLIENT_SECRET")

        # OAuth 2.1 configuration (always enabled for Slack)
        self.oauth21_enabled = True
        self.pkce_required = True  # PKCE is mandatory in OAuth 2.1
        self.supported_code_challenge_methods = ["S256"]

        # GCS configuration for token storage
        self.gcs_bucket = os.getenv("GCS_BUCKET", "slack-mcp-creds-flow-os")
        self.gcs_project = os.getenv("GCP_PROJECT", "procore-479405")

        # Transport mode (will be set at runtime)
        self._transport_mode = "stdio"  # Default

        # Redirect URI configuration
        self.redirect_uri = self._get_redirect_uri()
        self.redirect_path = self._get_redirect_path(self.redirect_uri)

    def _get_redirect_uri(self) -> str:
        """
        Get the OAuth redirect URI, supporting reverse proxy configurations.

        Returns:
            The configured redirect URI
        """
        explicit_uri = os.getenv("SLACK_REDIRECT_URI")
        if explicit_uri:
            return explicit_uri

        # Use external URL if configured (for reverse proxy)
        base = self.external_url if self.external_url else self.base_url
        return f"{base}/oauth/callback"

    @staticmethod
    def _get_redirect_path(uri: str) -> str:
        """Extract the redirect path from a full redirect URI."""
        parsed = urlparse(uri)
        if parsed.scheme or parsed.netloc:
            path = parsed.path or "/oauth/callback"
        else:
            # If the value was already a path, ensure it starts with '/'
            path = uri if uri.startswith("/") else f"/{uri}"
        return path or "/oauth/callback"

    def get_redirect_uris(self) -> List[str]:
        """
        Get all valid OAuth redirect URIs.

        Returns:
            List of all supported redirect URIs
        """
        uris = []

        # Primary redirect URI
        uris.append(self.redirect_uri)

        # Custom redirect URIs from environment
        custom_uris = os.getenv("OAUTH_CUSTOM_REDIRECT_URIS")
        if custom_uris:
            uris.extend([uri.strip() for uri in custom_uris.split(",")])

        # Remove duplicates while preserving order
        return list(dict.fromkeys(uris))

    def get_allowed_origins(self) -> List[str]:
        """
        Get allowed CORS origins for OAuth endpoints.

        Returns:
            List of allowed origins for CORS
        """
        origins = []

        # Server's own origin
        origins.append(self.base_url)

        # VS Code and development origins
        origins.extend(
            [
                "vscode-webview://",
                "https://vscode.dev",
                "https://github.dev",
            ]
        )

        # Custom origins from environment
        custom_origins = os.getenv("OAUTH_ALLOWED_ORIGINS")
        if custom_origins:
            origins.extend([origin.strip() for origin in custom_origins.split(",")])

        return list(dict.fromkeys(origins))

    def is_configured(self) -> bool:
        """
        Check if OAuth is properly configured.

        Returns:
            True if OAuth client credentials are available
        """
        return bool(self.client_id and self.client_secret)

    def get_oauth_base_url(self) -> str:
        """
        Get OAuth base URL for constructing OAuth endpoints.

        Uses SLACK_EXTERNAL_URL if set (for reverse proxy scenarios),
        otherwise falls back to constructed base_url with port.

        Returns:
            Base URL for OAuth endpoints
        """
        if self.external_url:
            return self.external_url
        return self.base_url

    def validate_redirect_uri(self, uri: str) -> bool:
        """
        Validate if a redirect URI is allowed.

        Args:
            uri: The redirect URI to validate

        Returns:
            True if the URI is allowed, False otherwise
        """
        allowed_uris = self.get_redirect_uris()
        return uri in allowed_uris

    def get_environment_summary(self) -> dict:
        """
        Get a summary of the current OAuth configuration.

        Returns:
            Dictionary with configuration summary (excluding secrets)
        """
        return {
            "base_url": self.base_url,
            "external_url": self.external_url,
            "effective_oauth_url": self.get_oauth_base_url(),
            "redirect_uri": self.redirect_uri,
            "redirect_path": self.redirect_path,
            "client_configured": bool(self.client_id),
            "oauth21_enabled": self.oauth21_enabled,
            "pkce_required": self.pkce_required,
            "transport_mode": self._transport_mode,
            "gcs_bucket": self.gcs_bucket,
            "gcs_project": self.gcs_project,
            "total_redirect_uris": len(self.get_redirect_uris()),
            "total_allowed_origins": len(self.get_allowed_origins()),
        }

    def set_transport_mode(self, mode: str) -> None:
        """
        Set the current transport mode for OAuth callback handling.

        Args:
            mode: Transport mode ("stdio", "streamable-http", etc.)
        """
        self._transport_mode = mode

    def get_transport_mode(self) -> str:
        """
        Get the current transport mode.

        Returns:
            Current transport mode
        """
        return self._transport_mode

    def is_oauth21_enabled(self) -> bool:
        """
        Check if OAuth 2.1 mode is enabled.

        Returns:
            True (always enabled for Slack MCP)
        """
        return self.oauth21_enabled

    def detect_oauth_version(self, request_params: Dict[str, Any]) -> str:
        """
        Detect OAuth version based on request parameters.

        For Slack MCP, we always use OAuth 2.1.

        Args:
            request_params: Request parameters from authorization or token request

        Returns:
            "oauth21" (always for Slack MCP)
        """
        return "oauth21"

    def get_authorization_server_metadata(
        self, scopes: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Get OAuth authorization server metadata per RFC 8414.

        Args:
            scopes: Optional list of supported scopes to include in metadata

        Returns:
            Authorization server metadata dictionary
        """
        oauth_base = self.get_oauth_base_url()
        metadata = {
            "issuer": "https://slack.com",
            "authorization_endpoint": "https://slack.com/oauth/v2/authorize",
            "token_endpoint": "https://slack.com/api/oauth.v2.access",
            "revocation_endpoint": "https://slack.com/api/auth.revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_post",
                "client_secret_basic",
            ],
            "code_challenge_methods_supported": self.supported_code_challenge_methods,
            "pkce_required": True,
            "require_exact_redirect_uri": True,
        }

        # Include scopes if provided
        if scopes is not None:
            metadata["scopes_supported"] = scopes

        return metadata


# Global configuration instance
_oauth_config = None


def get_oauth_config() -> OAuthConfig:
    """
    Get the global OAuth configuration instance.

    Returns:
        The singleton OAuth configuration instance
    """
    global _oauth_config
    if _oauth_config is None:
        _oauth_config = OAuthConfig()
    return _oauth_config


def reload_oauth_config() -> OAuthConfig:
    """
    Reload the OAuth configuration from environment variables.

    This is useful for testing or when environment variables change.

    Returns:
        The reloaded OAuth configuration instance
    """
    global _oauth_config
    _oauth_config = OAuthConfig()
    return _oauth_config


# Convenience functions for backward compatibility
def get_oauth_base_url() -> str:
    """Get OAuth base URL."""
    return get_oauth_config().get_oauth_base_url()


def get_redirect_uris() -> List[str]:
    """Get all valid OAuth redirect URIs."""
    return get_oauth_config().get_redirect_uris()


def get_allowed_origins() -> List[str]:
    """Get allowed CORS origins."""
    return get_oauth_config().get_allowed_origins()


def is_oauth_configured() -> bool:
    """Check if OAuth is properly configured."""
    return get_oauth_config().is_configured()


def set_transport_mode(mode: str) -> None:
    """Set the current transport mode."""
    get_oauth_config().set_transport_mode(mode)


def get_transport_mode() -> str:
    """Get the current transport mode."""
    return get_oauth_config().get_transport_mode()


def is_oauth21_enabled() -> bool:
    """Check if OAuth 2.1 is enabled (always True for Slack MCP)."""
    return get_oauth_config().is_oauth21_enabled()


def get_oauth_redirect_uri() -> str:
    """Get the primary OAuth redirect URI."""
    return get_oauth_config().redirect_uri
