"""
OAuth 2.1 Session Store for Slack MCP

This module provides a global store for OAuth 2.1 authenticated sessions
with GCS-backed token persistence. It manages dynamic client registration,
authorization code flow, and token storage.
"""

import contextvars
import logging
import hmac
import json
import hashlib
import secrets
from typing import Dict, Optional, Any, Tuple
from threading import RLock
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

from fastmcp.server.auth import AccessToken
from auth.oauth_config import get_oauth_config

logger = logging.getLogger(__name__)


# =============================================================================
# Session Context Management
# =============================================================================


@dataclass
class SessionContext:
    """Container for session-related information."""

    session_id: Optional[str] = None
    user_id: Optional[str] = None  # Slack user ID
    team_id: Optional[str] = None  # Slack workspace ID
    auth_context: Optional[Any] = None
    request: Optional[Any] = None
    metadata: Dict[str, Any] = None
    issuer: Optional[str] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


# Context variable to store the current session information
_current_session_context: contextvars.ContextVar[Optional[SessionContext]] = (
    contextvars.ContextVar("current_session_context", default=None)
)


def set_session_context(context: Optional[SessionContext]):
    """Set the current session context."""
    _current_session_context.set(context)
    if context:
        logger.debug(
            f"Set session context: session_id={context.session_id}, "
            f"user_id={context.user_id}, team_id={context.team_id}"
        )
    else:
        logger.debug("Cleared session context")


def get_session_context() -> Optional[SessionContext]:
    """Get the current session context."""
    return _current_session_context.get()


def clear_session_context():
    """Clear the current session context."""
    set_session_context(None)


class SessionContextManager:
    """
    Context manager for temporarily setting session context.

    Usage:
        with SessionContextManager(session_context):
            # Code that needs access to session context
            pass
    """

    def __init__(self, context: Optional[SessionContext]):
        self.context = context
        self.token = None

    def __enter__(self):
        """Set the session context."""
        self.token = _current_session_context.set(self.context)
        return self.context

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Reset the session context."""
        if self.token:
            _current_session_context.reset(self.token)


def extract_session_from_headers(headers: Dict[str, str]) -> Optional[str]:
    """
    Extract session ID from request headers.

    Args:
        headers: Request headers

    Returns:
        Session ID if found
    """
    # Try different header names
    session_id = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
    if session_id:
        return session_id

    session_id = headers.get("x-session-id") or headers.get("X-Session-ID")
    if session_id:
        return session_id

    # Try Authorization header for Bearer token
    auth_header = headers.get("authorization") or headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[7:]  # Remove "Bearer " prefix
        if token:
            # Look for a session that has this access token
            store = get_oauth21_session_store()
            for session_key, session_info in store._sessions.items():
                if hmac.compare_digest(session_info.get("access_token", ""), token):
                    return session_info.get("session_id") or f"bearer_{session_key}"

        # If no session found, create a temporary session ID from token hash
        token_hash = hashlib.sha256(token.encode()).hexdigest()[:8]
        return f"bearer_token_{token_hash}"

    return None


# =============================================================================
# OAuth21SessionStore - Main Session Management
# =============================================================================


class OAuth21SessionStore:
    """
    Global store for OAuth 2.1 authenticated sessions with GCS token persistence.

    This store maintains:
    - Dynamic client registrations
    - Authorization code mappings
    - Token storage (access + refresh tokens)
    - PKCE challenge/verifier validation
    - Session-to-user bindings

    Security: Sessions are bound to specific users and can only access
    their own credentials.
    """

    def __init__(self):
        # Session storage: session_key -> session_info
        # session_key format: "slack_{team_id}_{user_id}"
        self._sessions: Dict[str, Dict[str, Any]] = {}

        # MCP session mapping: mcp_session_id -> session_key
        self._mcp_session_mapping: Dict[str, str] = {}

        # Session auth binding: session_id -> session_key (immutable)
        self._session_auth_binding: Dict[str, str] = {}

        # OAuth state storage: state -> state_info
        self._oauth_states: Dict[str, Dict[str, Any]] = {}

        # Authorization code storage: code -> code_info
        self._auth_codes: Dict[str, Dict[str, Any]] = {}

        # Dynamic client registrations: client_id -> client_info
        self._dynamic_clients: Dict[str, Dict[str, Any]] = {}

        self._lock = RLock()

    # =========================================================================
    # OAuth State Management
    # =========================================================================

    def _cleanup_expired_oauth_states_locked(self):
        """Remove expired OAuth state entries. Caller must hold lock."""
        now = datetime.now(timezone.utc)
        expired_states = [
            state
            for state, data in self._oauth_states.items()
            if data.get("expires_at") and data["expires_at"] <= now
        ]
        for state in expired_states:
            del self._oauth_states[state]
            logger.debug(
                "Removed expired OAuth state: %s",
                state[:8] if len(state) > 8 else state,
            )

    def store_oauth_state(
        self,
        state: str,
        session_id: Optional[str] = None,
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        scopes: Optional[str] = None,
        expires_in_seconds: int = 600,
    ) -> None:
        """
        Persist an OAuth state value for later validation.

        Args:
            state: OAuth state parameter
            session_id: MCP session ID that initiated the flow
            code_challenge: PKCE code challenge
            code_challenge_method: PKCE challenge method (S256)
            redirect_uri: Redirect URI for this flow
            scopes: Requested scopes
            expires_in_seconds: State expiration time (default 10 minutes)
        """
        if not state:
            raise ValueError("OAuth state must be provided")
        if expires_in_seconds < 0:
            raise ValueError("expires_in_seconds must be non-negative")

        with self._lock:
            self._cleanup_expired_oauth_states_locked()
            now = datetime.now(timezone.utc)
            expiry = now + timedelta(seconds=expires_in_seconds)
            self._oauth_states[state] = {
                "session_id": session_id,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
                "redirect_uri": redirect_uri,
                "scopes": scopes,
                "expires_at": expiry,
                "created_at": now,
            }
            logger.debug(
                "Stored OAuth state %s (expires at %s)",
                state[:8] if len(state) > 8 else state,
                expiry.isoformat(),
            )

    def validate_and_consume_oauth_state(
        self,
        state: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate that a state value exists and consume it.

        Args:
            state: The OAuth state returned by Slack
            session_id: Optional session identifier that initiated the flow

        Returns:
            Metadata associated with the state (includes PKCE challenge)

        Raises:
            ValueError: If the state is missing, expired, or session mismatch
        """
        if not state:
            raise ValueError("Missing OAuth state parameter")

        with self._lock:
            self._cleanup_expired_oauth_states_locked()
            state_info = self._oauth_states.get(state)

            if not state_info:
                logger.error(
                    "SECURITY: OAuth callback received unknown or expired state"
                )
                raise ValueError("Invalid or expired OAuth state parameter")

            bound_session = state_info.get("session_id")
            if bound_session and session_id and bound_session != session_id:
                # Consume the state to prevent replay attempts
                del self._oauth_states[state]
                logger.error(
                    "SECURITY: OAuth state session mismatch (expected %s, got %s)",
                    bound_session,
                    session_id,
                )
                raise ValueError("OAuth state does not match the initiating session")

            # State is valid – consume it to prevent reuse
            del self._oauth_states[state]
            logger.debug(
                "Validated OAuth state %s",
                state[:8] if len(state) > 8 else state,
            )
            return state_info

    # =========================================================================
    # Authorization Code Management
    # =========================================================================

    def store_authorization_code(
        self,
        code: str,
        user_id: str,
        team_id: str,
        scopes: list,
        code_challenge: Optional[str] = None,
        slack_access_token: Optional[str] = None,
        expires_in_seconds: int = 600,
    ) -> None:
        """
        Store an authorization code for token exchange.

        Args:
            code: Authorization code
            user_id: Slack user ID
            team_id: Slack workspace ID
            scopes: Granted scopes
            code_challenge: PKCE code challenge for validation
            slack_access_token: The real Slack access token from OAuth
            expires_in_seconds: Code expiration (default 10 minutes)
        """
        with self._lock:
            now = datetime.now(timezone.utc)
            expiry = now + timedelta(seconds=expires_in_seconds)
            self._auth_codes[code] = {
                "user_id": user_id,
                "team_id": team_id,
                "scopes": scopes,
                "code_challenge": code_challenge,
                "slack_access_token": slack_access_token,
                "expires_at": expiry,
                "created_at": now,
                "used": False,
            }
            logger.debug(f"Stored authorization code for {user_id}@{team_id}")

    def validate_and_consume_authorization_code(
        self,
        code: str,
        code_verifier: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate and consume an authorization code.

        Args:
            code: Authorization code
            code_verifier: PKCE code verifier for validation

        Returns:
            Code metadata (user_id, team_id, scopes)

        Raises:
            ValueError: If code is invalid, expired, or PKCE validation fails
        """
        with self._lock:
            code_info = self._auth_codes.get(code)

            if not code_info:
                logger.error("SECURITY: Invalid authorization code")
                raise ValueError("Invalid authorization code")

            if code_info.get("used"):
                logger.error("SECURITY: Authorization code already used")
                raise ValueError("Authorization code already used")

            now = datetime.now(timezone.utc)
            if code_info.get("expires_at") and code_info["expires_at"] <= now:
                del self._auth_codes[code]
                logger.error("SECURITY: Expired authorization code")
                raise ValueError("Authorization code expired")

            # Validate PKCE if challenge was provided
            code_challenge = code_info.get("code_challenge")
            if code_challenge:
                if not code_verifier:
                    del self._auth_codes[code]
                    logger.error("SECURITY: Missing PKCE code_verifier")
                    raise ValueError("PKCE code_verifier required")

                # Validate code_verifier against code_challenge
                if not self._validate_pkce(code_verifier, code_challenge):
                    del self._auth_codes[code]
                    logger.error("SECURITY: PKCE validation failed")
                    raise ValueError("Invalid PKCE code_verifier")

            # Mark as used and return
            code_info["used"] = True
            logger.debug(
                f"Validated authorization code for {code_info['user_id']}@{code_info['team_id']}"
            )
            return code_info

    def _validate_pkce(self, code_verifier: str, code_challenge: str) -> bool:
        """
        Validate PKCE code_verifier against code_challenge.

        Args:
            code_verifier: Code verifier from token request
            code_challenge: Code challenge from authorization request

        Returns:
            True if validation passes
        """
        import base64

        # S256: code_challenge = BASE64URL(SHA256(code_verifier))
        verifier_hash = hashlib.sha256(code_verifier.encode()).digest()
        computed_challenge = (
            base64.urlsafe_b64encode(verifier_hash).decode().rstrip("=")
        )
        return secrets.compare_digest(computed_challenge, code_challenge)

    # =========================================================================
    # Session Management
    # =========================================================================

    def store_session(
        self,
        user_id: str,
        team_id: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        token_type: str = "Bearer",
        scopes: Optional[list] = None,
        expiry: Optional[Any] = None,
        session_id: Optional[str] = None,
        mcp_session_id: Optional[str] = None,
        bot_user_id: Optional[str] = None,
        enterprise_id: Optional[str] = None,
        slack_access_token: Optional[str] = None,
    ):
        """
        Store OAuth 2.1 session information.

        Args:
            user_id: Slack user ID
            team_id: Slack workspace ID
            access_token: OAuth 2.1 access token (our bearer token)
            refresh_token: OAuth 2.1 refresh token
            token_type: Token type (usually "Bearer")
            scopes: List of granted scopes
            expiry: Token expiry time
            session_id: OAuth 2.1 session ID
            mcp_session_id: FastMCP session ID to map to this user
            bot_user_id: Bot user ID if bot token
            enterprise_id: Slack enterprise ID
            slack_access_token: The real Slack access token for API calls
        """
        with self._lock:
            session_key = f"slack_{team_id}_{user_id}"
            normalized_expiry = self._normalize_expiry(expiry)

            session_info = {
                "user_id": user_id,
                "team_id": team_id,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": token_type,
                "scopes": scopes or [],
                "expiry": normalized_expiry,
                "session_id": session_id,
                "mcp_session_id": mcp_session_id,
                "bot_user_id": bot_user_id,
                "enterprise_id": enterprise_id,
                "slack_access_token": slack_access_token,
                "issuer": "https://slack.com",
            }

            self._sessions[session_key] = session_info

            # Store MCP session mapping if provided
            if mcp_session_id:
                # Create immutable session binding
                if mcp_session_id not in self._session_auth_binding:
                    self._session_auth_binding[mcp_session_id] = session_key
                    logger.info(
                        f"Created immutable session binding: {mcp_session_id} -> {session_key}"
                    )
                elif self._session_auth_binding[mcp_session_id] != session_key:
                    logger.error(
                        f"SECURITY: Attempt to rebind session {mcp_session_id} from "
                        f"{self._session_auth_binding[mcp_session_id]} to {session_key}"
                    )
                    raise ValueError(
                        f"Session {mcp_session_id} is already bound to a different user"
                    )

                self._mcp_session_mapping[mcp_session_id] = session_key
                logger.info(
                    f"Stored OAuth 2.1 session for {user_id}@{team_id} "
                    f"(session_id: {session_id}, mcp_session_id: {mcp_session_id})"
                )
            else:
                logger.info(
                    f"Stored OAuth 2.1 session for {user_id}@{team_id} (session_id: {session_id})"
                )

            # Also create binding for the OAuth session ID
            if session_id and session_id not in self._session_auth_binding:
                self._session_auth_binding[session_id] = session_key

    def get_session(self, user_id: str, team_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session information for a user.

        Args:
            user_id: Slack user ID
            team_id: Slack workspace ID

        Returns:
            Session information or None
        """
        with self._lock:
            session_key = f"slack_{team_id}_{user_id}"
            return self._sessions.get(session_key)

    def get_session_by_mcp_session(
        self, mcp_session_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get session using FastMCP session ID.

        Args:
            mcp_session_id: FastMCP session ID

        Returns:
            Session information or None
        """
        with self._lock:
            session_key = self._mcp_session_mapping.get(mcp_session_id)
            if not session_key:
                logger.debug(f"No user mapping found for MCP session {mcp_session_id}")
                return None

            logger.debug(
                f"Found session {session_key} for MCP session {mcp_session_id}"
            )
            return self._sessions.get(session_key)

    def remove_session(self, user_id: str, team_id: str):
        """Remove session for a user."""
        with self._lock:
            session_key = f"slack_{team_id}_{user_id}"
            if session_key in self._sessions:
                session_info = self._sessions.get(session_key, {})
                mcp_session_id = session_info.get("mcp_session_id")
                session_id = session_info.get("session_id")

                # Remove from sessions
                del self._sessions[session_key]

                # Remove from MCP mapping if exists
                if mcp_session_id and mcp_session_id in self._mcp_session_mapping:
                    del self._mcp_session_mapping[mcp_session_id]
                    if mcp_session_id in self._session_auth_binding:
                        del self._session_auth_binding[mcp_session_id]

                # Remove OAuth session binding if exists
                if session_id and session_id in self._session_auth_binding:
                    del self._session_auth_binding[session_id]

                logger.info(f"Removed OAuth 2.1 session for {user_id}@{team_id}")

    def has_session(self, user_id: str, team_id: str) -> bool:
        """Check if a user has an active session."""
        with self._lock:
            session_key = f"slack_{team_id}_{user_id}"
            return session_key in self._sessions

    def _normalize_expiry(self, expiry: Optional[Any]) -> Optional[datetime]:
        """Convert expiry values to timezone-naive UTC datetimes."""
        if expiry is None:
            return None

        if isinstance(expiry, datetime):
            if expiry.tzinfo is not None:
                try:
                    return expiry.astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    logger.debug("Failed to normalize aware expiry")
                    return expiry.replace(tzinfo=None)
            return expiry  # Already naive

        if isinstance(expiry, str):
            try:
                parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            except ValueError:
                logger.debug("Failed to parse expiry string '%s'", expiry)
                return None
            return self._normalize_expiry(parsed)

        if isinstance(expiry, (int, float)):
            try:
                return datetime.fromtimestamp(expiry, tz=timezone.utc).replace(
                    tzinfo=None
                )
            except Exception:
                logger.debug("Failed to parse expiry timestamp %s", expiry)
                return None

        logger.debug("Unsupported expiry type '%s' (%s)", expiry, type(expiry))
        return None

    def get_stats(self) -> Dict[str, Any]:
        """Get store statistics."""
        with self._lock:
            return {
                "total_sessions": len(self._sessions),
                "session_keys": list(self._sessions.keys()),
                "mcp_session_mappings": len(self._mcp_session_mapping),
                "mcp_sessions": list(self._mcp_session_mapping.keys()),
                "oauth_states": len(self._oauth_states),
                "auth_codes": len(self._auth_codes),
                "dynamic_clients": len(self._dynamic_clients),
            }


# Global instance
_global_store = OAuth21SessionStore()


def get_oauth21_session_store() -> OAuth21SessionStore:
    """Get the global OAuth 2.1 session store."""
    return _global_store
