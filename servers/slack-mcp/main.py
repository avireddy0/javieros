"""
Main entry point for Slack MCP server with OAuth 2.1 support for Open WebUI External Tools.

This server implements:
- OAuth 2.1 Authorization Server endpoints (RFC 8414)
- Dynamic client registration (RFC 7591)
- PKCE support (RFC 7636)
- Token storage in GCS
- FastMCP server with streamable-http transport

The server acts as an OAuth 2.1 proxy:
1. Open WebUI registers as a dynamic client
2. Open WebUI requests authorization with PKCE
3. Server redirects user to Slack OAuth
4. Slack redirects back with authorization code
5. Server exchanges code with Slack, stores token in GCS
6. Server issues its own OAuth 2.1 token to Open WebUI
7. Open WebUI uses token to call MCP tools
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from importlib import metadata
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlparse, parse_qs
import hmac

from dotenv import load_dotenv
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from google.cloud import storage
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=dotenv_path)

from server import server
from auth.oauth_config import get_oauth_config
from auth.oauth21_session_store import get_oauth21_session_store

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def safe_print(text: str) -> None:
    """Print to stderr safely, avoiding JSON parsing errors in MCP mode."""
    if not sys.stderr.isatty():
        logger.debug(f"[MCP Server] {text}")
        return
    try:
        print(text, file=sys.stderr)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode(), file=sys.stderr)


# Registration security: only allow redirect URIs to known hosts
ALLOWED_REDIRECT_HOSTS = [
    h.strip()
    for h in os.environ.get("ALLOWED_REDIRECT_HOSTS", "").split(",")
    if h.strip()
]

# OAuth 2.1 scopes for Slack
SLACK_SCOPES = [
    "channels:history",
    "channels:read",
    "chat:write",
    "users:read",
    "users:read.email",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "mpim:history",
    "mpim:read",
    "search:read",
    "reactions:read",
    "reactions:write",
    "files:read",
    "pins:read",
    "bookmarks:read",
    "stars:read",
]


# =============================================================================
# Dynamic Client Registration Store
# =============================================================================


class DynamicClientStore:
    """Store for dynamically registered OAuth clients with GCS FUSE persistence."""

    PERSIST_PATH = os.getenv(
        "DYNAMIC_CLIENTS_PATH",
        "/app/store_creds/dynamic_clients.json",
    )

    def __init__(self):
        self._clients: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self):
        """Load client registrations from persistent storage."""
        try:
            if os.path.exists(self.PERSIST_PATH):
                with open(self.PERSIST_PATH, "r") as f:
                    self._clients = json.load(f)
                logger.info(f"Loaded {len(self._clients)} dynamic client(s) from {self.PERSIST_PATH}")
        except Exception as e:
            logger.warning(f"Failed to load dynamic clients: {e}")

    def _save(self):
        """Save client registrations to persistent storage."""
        try:
            os.makedirs(os.path.dirname(self.PERSIST_PATH), exist_ok=True)
            with open(self.PERSIST_PATH, "w") as f:
                json.dump(self._clients, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to persist dynamic clients: {e}")

    def register_client(
        self,
        redirect_uris: list[str],
        client_name: Optional[str] = None,
        grant_types: Optional[list[str]] = None,
        response_types: Optional[list[str]] = None,
        scope: Optional[str] = None,
        token_endpoint_auth_method: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Register a new OAuth client and return credentials."""
        client_id = f"slack_mcp_{secrets.token_urlsafe(16)}"
        client_secret = secrets.token_urlsafe(32)

        client_info = {
            "client_id": client_id,
            "client_secret": client_secret,
            "client_name": client_name or "Slack MCP Client",
            "redirect_uris": redirect_uris,
            "grant_types": grant_types or ["authorization_code", "refresh_token"],
            "response_types": response_types or ["code"],
            "token_endpoint_auth_method": token_endpoint_auth_method
            or "client_secret_post",
            "scope": scope or " ".join(SLACK_SCOPES),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        self._clients[client_id] = client_info
        self._save()
        logger.info(f"Registered dynamic client: {client_id} ({client_name})")
        return client_info

    def get_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        """Get client information."""
        return self._clients.get(client_id)

    def validate_client(
        self,
        client_id: str,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ) -> bool:
        """Validate client credentials and redirect URI."""
        client = self.get_client(client_id)
        if not client:
            return False

        if client_secret is not None:
            if not hmac.compare_digest(client["client_secret"], client_secret):
                return False

        if redirect_uri is not None:
            if redirect_uri not in client["redirect_uris"]:
                logger.error(
                    f"Invalid redirect_uri for client {client_id}: {redirect_uri} "
                    f"not in {client['redirect_uris']}"
                )
                return False

        return True


# Global dynamic client store
_dynamic_client_store = DynamicClientStore()


def get_dynamic_client_store() -> DynamicClientStore:
    """Get the global dynamic client store."""
    return _dynamic_client_store


# =============================================================================
# GCS Token Storage
# =============================================================================


class GCSTokenStore:
    """Store OAuth tokens in Google Cloud Storage."""

    def __init__(self, bucket_name: str):
        self.bucket_name = bucket_name
        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)

    def _get_blob_name(self, user_id: str, team_id: str) -> str:
        """Generate GCS blob name for user tokens."""
        return f"tokens/{team_id}/{user_id}.json"

    async def store_token(
        self,
        user_id: str,
        team_id: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        expires_in: Optional[int] = None,
        scopes: Optional[list] = None,
    ) -> None:
        """Store token in GCS."""
        blob_name = self._get_blob_name(user_id, team_id)
        blob = self.bucket.blob(blob_name)

        token_data = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": expires_in,
            "scopes": scopes or [],
            "team_id": team_id,
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        await asyncio.to_thread(
            blob.upload_from_string,
            json.dumps(token_data),
            content_type="application/json",
        )
        logger.info(f"Stored token for {user_id}@{team_id} in GCS")

    async def get_token(self, user_id: str, team_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve token from GCS."""
        blob_name = self._get_blob_name(user_id, team_id)
        blob = self.bucket.blob(blob_name)

        try:
            content = await asyncio.to_thread(blob.download_as_string)
            return json.loads(content)
        except Exception as e:
            logger.debug(f"Token not found in GCS for {user_id}@{team_id}: {e}")
            return None

    async def delete_token(self, user_id: str, team_id: str) -> None:
        """Delete token from GCS."""
        blob_name = self._get_blob_name(user_id, team_id)
        blob = self.bucket.blob(blob_name)

        try:
            await asyncio.to_thread(blob.delete)
            logger.info(f"Deleted token for {user_id}@{team_id} from GCS")
        except Exception as e:
            logger.debug(f"Failed to delete token from GCS: {e}")


# Global GCS token store
_gcs_token_store: Optional[GCSTokenStore] = None


def get_gcs_token_store() -> GCSTokenStore:
    """Get or create the global GCS token store."""
    global _gcs_token_store
    if _gcs_token_store is None:
        bucket_name = os.getenv("SLACK_CREDS_BUCKET", "slack-mcp-creds-flow-os")
        _gcs_token_store = GCSTokenStore(bucket_name)
    return _gcs_token_store


# =============================================================================
# PKCE Helper Functions
# =============================================================================


def validate_pkce(code_verifier: str, code_challenge: str) -> bool:
    """
    Validate PKCE code_verifier against code_challenge.

    Args:
        code_verifier: Code verifier from token request
        code_challenge: Code challenge from authorization request (S256)

    Returns:
        True if validation passes
    """
    # S256: code_challenge = BASE64URL(SHA256(code_verifier))
    verifier_hash = hashlib.sha256(code_verifier.encode()).digest()
    computed_challenge = base64.urlsafe_b64encode(verifier_hash).decode().rstrip("=")
    return secrets.compare_digest(computed_challenge, code_challenge)


# =============================================================================
# OAuth 2.1 Endpoints
# =============================================================================


@server.custom_route("/.well-known/oauth-authorization-server", methods=["GET"])
async def oauth_metadata(request: Request):
    """
    OAuth 2.1 authorization server metadata endpoint (RFC 8414).

    This advertises the server's OAuth 2.1 capabilities to clients like Open WebUI.
    """
    config = get_oauth_config()
    base_url = config.get_oauth_base_url()

    metadata = {
        "issuer": base_url,
        "authorization_endpoint": f"{base_url}/oauth2/authorize",
        "token_endpoint": f"{base_url}/oauth2/token",
        "registration_endpoint": f"{base_url}/register",
        "scopes_supported": SLACK_SCOPES,
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": [
            "client_secret_post",
            "client_secret_basic",
        ],
    }

    return JSONResponse(metadata)


@server.custom_route("/register", methods=["POST"])
async def register_client(request: Request):
    """
    Dynamic client registration endpoint (RFC 7591).

    Allows Open WebUI to register as an OAuth client and receive credentials.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    redirect_uris = body.get("redirect_uris")
    if not redirect_uris or not isinstance(redirect_uris, list):
        raise HTTPException(status_code=400, detail="redirect_uris required as array")

    # Validate redirect URIs against allowed hosts
    if not ALLOWED_REDIRECT_HOSTS:
        raise HTTPException(
            status_code=503,
            detail="Client registration not configured (ALLOWED_REDIRECT_HOSTS unset)",
        )
    for uri in redirect_uris:
        parsed = urlparse(uri)
        if parsed.hostname not in ALLOWED_REDIRECT_HOSTS:
            raise HTTPException(
                status_code=403,
                detail=f"Redirect URI host not allowed: {parsed.hostname}",
            )

    client_name = body.get("client_name")
    grant_types = body.get("grant_types")
    response_types = body.get("response_types")
    scope = body.get("scope")
    token_endpoint_auth_method = body.get("token_endpoint_auth_method")

    store = get_dynamic_client_store()
    client_info = store.register_client(
        redirect_uris=redirect_uris,
        client_name=client_name,
        grant_types=grant_types,
        response_types=response_types,
        scope=scope,
        token_endpoint_auth_method=token_endpoint_auth_method,
    )

    # Return client credentials
    config = get_oauth_config()
    base_url = config.get_oauth_base_url()

    response = {
        "client_id": client_info["client_id"],
        "client_secret": client_info["client_secret"],
        "client_name": client_info["client_name"],
        "redirect_uris": client_info["redirect_uris"],
        "grant_types": client_info["grant_types"],
        "response_types": client_info["response_types"],
        "token_endpoint_auth_method": client_info["token_endpoint_auth_method"],
        "registration_access_token": secrets.token_urlsafe(32),  # Not implemented yet
        "registration_client_uri": f"{base_url}/register/{client_info['client_id']}",
    }

    return JSONResponse(response, status_code=201)


@server.custom_route("/oauth2/authorize", methods=["GET"])
async def oauth2_authorize(request: Request):
    """
    OAuth 2.1 authorization endpoint.

    Validates the authorization request and redirects to Slack OAuth.
    """
    params = dict(request.query_params)

    # Validate required parameters
    client_id = params.get("client_id")
    redirect_uri = params.get("redirect_uri")
    response_type = params.get("response_type")
    state = params.get("state")
    code_challenge = params.get("code_challenge")
    code_challenge_method = params.get("code_challenge_method")
    scope = params.get("scope")

    if not client_id:
        raise HTTPException(status_code=400, detail="client_id required")

    if not redirect_uri:
        raise HTTPException(status_code=400, detail="redirect_uri required")

    if response_type != "code":
        raise HTTPException(status_code=400, detail="Only response_type=code supported")

    if not state:
        raise HTTPException(status_code=400, detail="state required")

    # OAuth 2.1 requires PKCE
    if not code_challenge or not code_challenge_method:
        raise HTTPException(
            status_code=400,
            detail="PKCE required (code_challenge and code_challenge_method)",
        )

    if code_challenge_method != "S256":
        raise HTTPException(
            status_code=400, detail="Only code_challenge_method=S256 supported"
        )

    # Validate client and redirect_uri
    client_store = get_dynamic_client_store()
    if not client_store.validate_client(client_id, redirect_uri=redirect_uri):
        raise HTTPException(status_code=400, detail="Invalid client_id or redirect_uri")

    # Generate internal state for Slack OAuth flow
    internal_state = secrets.token_urlsafe(32)

    # Store OAuth state with PKCE challenge
    session_store = get_oauth21_session_store()
    session_store.store_oauth_state(
        state=internal_state,
        session_id=state,  # Map to external state
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        redirect_uri=redirect_uri,
        scopes=scope or " ".join(SLACK_SCOPES),
        expires_in_seconds=600,
    )

    # Store mapping from internal state to external state and client info
    session_store._oauth_states[internal_state]["external_state"] = state
    session_store._oauth_states[internal_state]["client_id"] = client_id
    session_store._oauth_states[internal_state]["external_redirect_uri"] = redirect_uri

    # Get Slack OAuth credentials
    slack_client_id = os.getenv("SLACK_OAUTH_CLIENT_ID")
    slack_client_secret = os.getenv("SLACK_OAUTH_CLIENT_SECRET")

    if not slack_client_id or not slack_client_secret:
        raise HTTPException(status_code=500, detail="Slack OAuth not configured")

    # Build Slack OAuth URL
    config = get_oauth_config()
    base_url = config.get_oauth_base_url()
    slack_redirect_uri = f"{base_url}/oauth2/callback"

    slack_params = {
        "client_id": slack_client_id,
        "user_scope": ",".join(SLACK_SCOPES),
        "redirect_uri": slack_redirect_uri,
        "state": internal_state,
    }

    slack_auth_url = f"https://slack.com/oauth/v2/authorize?{urlencode(slack_params)}"

    logger.info(f"Redirecting to Slack OAuth: {slack_auth_url}")
    return RedirectResponse(url=slack_auth_url, status_code=302)


@server.custom_route("/oauth2callback", methods=["GET"])
async def oauth2_callback_compat(request: Request):
    """Backward-compatible callback route (matches Slack app redirect URI)."""
    return await _handle_slack_callback(request)


@server.custom_route("/oauth2/callback", methods=["GET"])
async def oauth2_callback(request: Request):
    """OAuth callback from Slack (canonical route)."""
    return await _handle_slack_callback(request)


async def _handle_slack_callback(request: Request):
    """
    OAuth callback from Slack.

    Exchanges the Slack authorization code for tokens and redirects back to Open WebUI.
    """
    params = dict(request.query_params)

    code = params.get("code")
    state = params.get("state")
    error = params.get("error")

    if error:
        logger.error(f"Slack OAuth error: {error}")
        raise HTTPException(status_code=400, detail=f"Slack OAuth error: {error}")

    if not code or not state:
        raise HTTPException(status_code=400, detail="code and state required")

    # Validate and consume state
    session_store = get_oauth21_session_store()
    try:
        state_info = session_store.validate_and_consume_oauth_state(state)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Get external state and redirect URI
    external_state = state_info.get("external_state")
    external_redirect_uri = state_info.get("external_redirect_uri")
    client_id = state_info.get("client_id")
    code_challenge = state_info.get("code_challenge")

    if not external_state or not external_redirect_uri:
        raise HTTPException(status_code=500, detail="Internal state error")

    # Exchange code with Slack
    slack_client_id = os.getenv("SLACK_OAUTH_CLIENT_ID")
    slack_client_secret = os.getenv("SLACK_OAUTH_CLIENT_SECRET")
    config = get_oauth_config()
    base_url = config.get_oauth_base_url()
    slack_redirect_uri = f"{base_url}/oauth2/callback"

    slack_client = WebClient()

    try:
        response = await asyncio.to_thread(
            slack_client.oauth_v2_access,
            client_id=slack_client_id,
            client_secret=slack_client_secret,
            code=code,
            redirect_uri=slack_redirect_uri,
        )

        if not response.get("ok"):
            error_msg = response.get("error", "unknown_error")
            logger.error(f"Slack token exchange failed: {error_msg}")
            raise HTTPException(
                status_code=500, detail=f"Slack token exchange failed: {error_msg}"
            )

        # Extract token information — use user token (xoxp-*), not bot token
        team_id = response.get("team", {}).get("id")
        authed_user = response.get("authed_user", {})
        user_id = authed_user.get("id")
        # Personal OAuth: user token is in authed_user, not top-level
        access_token = authed_user.get("access_token") or response.get("access_token")
        refresh_token = authed_user.get("refresh_token") or response.get("refresh_token")
        expires_in = authed_user.get("expires_in")
        scopes = authed_user.get("scope", "").split(",")

        if not access_token or not user_id or not team_id:
            logger.error(
                f"Invalid Slack token response: access_token={bool(access_token)}, "
                f"user_id={user_id}, team_id={team_id}, "
                f"keys={list(response.data.keys()) if hasattr(response, 'data') else list(response.keys())}"
            )
            raise HTTPException(status_code=500, detail="Invalid Slack token response")

        # Store Slack token in GCS
        gcs_store = get_gcs_token_store()
        await gcs_store.store_token(
            user_id=user_id,
            team_id=team_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
            scopes=scopes,
        )

        # Generate our own authorization code for Open WebUI
        our_auth_code = secrets.token_urlsafe(32)

        # Store authorization code with the Slack access token
        session_store.store_authorization_code(
            code=our_auth_code,
            user_id=user_id,
            team_id=team_id,
            scopes=scopes,
            code_challenge=code_challenge,
            slack_access_token=access_token,
            expires_in_seconds=600,
        )

        # Redirect back to Open WebUI with our authorization code
        callback_params = {
            "code": our_auth_code,
            "state": external_state,
        }

        callback_url = f"{external_redirect_uri}?{urlencode(callback_params)}"
        logger.info(f"Redirecting to client: {callback_url}")
        return RedirectResponse(url=callback_url, status_code=302)

    except SlackApiError as e:
        logger.error(f"Slack API error: {e}")
        raise HTTPException(status_code=500, detail=f"Slack API error: {str(e)}")


@server.custom_route("/oauth2/token", methods=["POST"])
async def oauth2_token(request: Request):
    """
    OAuth 2.1 token endpoint.

    Exchanges authorization code for access token or refreshes an existing token.
    """
    try:
        # Try form data first (standard OAuth)
        form = await request.form()
        params = dict(form)
    except Exception:
        # Fall back to JSON
        try:
            params = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request body")

    grant_type = params.get("grant_type")

    if grant_type == "authorization_code":
        return await handle_authorization_code_grant(params)
    elif grant_type == "refresh_token":
        return await handle_refresh_token_grant(params)
    else:
        raise HTTPException(status_code=400, detail="Unsupported grant_type")


async def handle_authorization_code_grant(params: Dict[str, Any]) -> JSONResponse:
    """Handle authorization_code grant type."""
    code = params.get("code")
    redirect_uri = params.get("redirect_uri")
    client_id = params.get("client_id")
    client_secret = params.get("client_secret")
    code_verifier = params.get("code_verifier")

    if not code:
        raise HTTPException(status_code=400, detail="code required")

    if not client_id:
        raise HTTPException(status_code=400, detail="client_id required")

    if not code_verifier:
        raise HTTPException(status_code=400, detail="code_verifier required for PKCE")

    # Validate client credentials
    client_store = get_dynamic_client_store()
    if not client_store.validate_client(client_id, client_secret):
        raise HTTPException(status_code=401, detail="Invalid client credentials")

    # Validate and consume authorization code
    session_store = get_oauth21_session_store()
    try:
        code_info = session_store.validate_and_consume_authorization_code(
            code=code,
            code_verifier=code_verifier,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    user_id = code_info["user_id"]
    team_id = code_info["team_id"]
    scopes = code_info["scopes"]
    slack_access_token = code_info.get("slack_access_token")

    # Generate access token and refresh token
    access_token = secrets.token_urlsafe(32)
    refresh_token = secrets.token_urlsafe(32)

    # Store session
    session_id = f"oauth_{secrets.token_urlsafe(16)}"
    expires_in = 3600  # 1 hour
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    session_store.store_session(
        user_id=user_id,
        team_id=team_id,
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="Bearer",
        scopes=scopes,
        expiry=expiry,
        session_id=session_id,
        slack_access_token=slack_access_token,
    )

    # Return token response
    response = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": refresh_token,
        "scope": " ".join(scopes),
    }

    logger.info(f"Issued access token for {user_id}@{team_id}")
    return JSONResponse(response)


async def handle_refresh_token_grant(params: Dict[str, Any]) -> JSONResponse:
    """Handle refresh_token grant type."""
    refresh_token = params.get("refresh_token")
    client_id = params.get("client_id")
    client_secret = params.get("client_secret")

    if not refresh_token:
        raise HTTPException(status_code=400, detail="refresh_token required")

    if not client_id:
        raise HTTPException(status_code=400, detail="client_id required")

    # Validate client credentials
    client_store = get_dynamic_client_store()
    if not client_store.validate_client(client_id, client_secret):
        raise HTTPException(status_code=401, detail="Invalid client credentials")

    # Find session by refresh token
    session_store = get_oauth21_session_store()
    session_info = None

    for session_key, info in session_store._sessions.items():
        if hmac.compare_digest(info.get("refresh_token", ""), refresh_token):
            session_info = info
            break

    if not session_info:
        raise HTTPException(status_code=400, detail="Invalid refresh_token")

    user_id = session_info["user_id"]
    team_id = session_info["team_id"]
    scopes = session_info["scopes"]

    # Generate new access token
    new_access_token = secrets.token_urlsafe(32)
    new_refresh_token = secrets.token_urlsafe(32)

    expires_in = 3600  # 1 hour
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Update session with new tokens
    session_store.store_session(
        user_id=user_id,
        team_id=team_id,
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        token_type="Bearer",
        scopes=scopes,
        expiry=expiry,
        session_id=session_info.get("session_id"),
    )

    response = {
        "access_token": new_access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "refresh_token": new_refresh_token,
        "scope": " ".join(scopes),
    }

    logger.info(f"Refreshed access token for {user_id}@{team_id}")
    return JSONResponse(response)


# =============================================================================
# Health and Info Endpoints
# =============================================================================

# =============================================================================
# REST API Endpoints for Open WebUI External Tools
# =============================================================================


def _get_slack_client_from_token(request: Request) -> Optional[WebClient]:
    """Extract Slack client from Bearer token."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]  # Remove "Bearer " prefix
    session_store = get_oauth21_session_store()

    # Look up session by access token
    for session_key, session_info in session_store._sessions.items():
        if hmac.compare_digest(session_info.get("access_token", ""), token):
            slack_token = session_info.get("slack_access_token")
            if slack_token:
                return WebClient(token=slack_token)
    return None


@server.custom_route("/api/channels", methods=["GET"])
async def api_list_channels(request: Request):
    """REST API: List Slack channels."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(
            status_code=401, detail="Unauthorized - valid OAuth token required"
        )

    params = request.query_params
    types = params.get("types", "public_channel,private_channel")
    limit = int(params.get("limit", "100"))

    try:
        result = client.conversations_list(
            types=types, limit=limit, exclude_archived=False
        )
        channels = [
            {
                "id": ch["id"],
                "name": ch["name"],
                "is_private": ch.get("is_private", False),
            }
            for ch in result["channels"]
        ]
        return JSONResponse({"ok": True, "channels": channels})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/channels/{channel_id}", methods=["GET"])
async def api_get_channel(request: Request):
    """REST API: Get channel info."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    channel_id = request.path_params.get("channel_id")
    try:
        result = client.conversations_info(channel=channel_id)
        return JSONResponse({"ok": True, "channel": result["channel"]})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/channels/{channel_id}/history", methods=["GET"])
async def api_get_channel_history(request: Request):
    """REST API: Get channel message history."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    channel_id = request.path_params.get("channel_id")
    limit = int(request.query_params.get("limit", "100"))

    try:
        result = client.conversations_history(channel=channel_id, limit=limit)
        messages = [
            {"text": m.get("text", ""), "user": m.get("user"), "ts": m["ts"]}
            for m in result["messages"]
        ]
        return JSONResponse({"ok": True, "messages": messages})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/messages", methods=["POST"])
async def api_send_message(request: Request):
    """REST API: Send a message."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    channel = body.get("channel")
    text = body.get("text")
    thread_ts = body.get("thread_ts")

    if not channel or not text:
        return JSONResponse(
            {"ok": False, "error": "channel and text required"}, status_code=400
        )

    try:
        kwargs = {"channel": channel, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        result = client.chat_postMessage(**kwargs)
        return JSONResponse(
            {"ok": True, "ts": result["ts"], "channel": result["channel"]}
        )
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/search", methods=["GET"])
async def api_search_messages(request: Request):
    """REST API: Search messages."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    query = request.query_params.get("query")
    count = int(request.query_params.get("count", "20"))

    if not query:
        return JSONResponse(
            {"ok": False, "error": "query parameter required"}, status_code=400
        )

    try:
        result = client.search_messages(query=query, count=min(count, 100))
        messages = [
            {
                "text": m["text"],
                "user": m.get("user"),
                "ts": m["ts"],
                "channel": m.get("channel", {}).get("name"),
            }
            for m in result["messages"]["matches"]
        ]
        return JSONResponse(
            {"ok": True, "messages": messages, "total": result["messages"]["total"]}
        )
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/users", methods=["GET"])
async def api_list_users(request: Request):
    """REST API: List users."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    limit = int(request.query_params.get("limit", "100"))

    try:
        result = client.users_list(limit=limit)
        users = [
            {"id": u["id"], "name": u["name"], "real_name": u.get("real_name", "")}
            for u in result["members"]
            if not u.get("deleted") and not u.get("is_bot")
        ]
        return JSONResponse({"ok": True, "users": users})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/users/{user_id}", methods=["GET"])
async def api_get_user(request: Request):
    """REST API: Get user info."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    user_id = request.path_params.get("user_id")

    try:
        result = client.users_info(user=user_id)
        return JSONResponse({"ok": True, "user": result["user"]})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/channels/{channel_id}/threads/{thread_ts}", methods=["GET"])
async def api_get_thread_replies(request: Request):
    """REST API: Get thread replies."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    channel_id = request.path_params.get("channel_id")
    thread_ts = request.path_params.get("thread_ts")
    limit = int(request.query_params.get("limit", "100"))

    try:
        result = client.conversations_replies(
            channel=channel_id, ts=thread_ts, limit=limit
        )
        messages = [
            {"text": m.get("text", ""), "user": m.get("user"), "ts": m["ts"]}
            for m in result["messages"]
        ]
        return JSONResponse({"ok": True, "messages": messages})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/dms", methods=["GET"])
async def api_list_dms(request: Request):
    """REST API: List direct messages."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    limit = int(request.query_params.get("limit", "100"))

    try:
        result = client.conversations_list(types="im", limit=limit)
        dms = [
            {"id": ch["id"], "user": ch.get("user")}
            for ch in result["channels"]
        ]
        return JSONResponse({"ok": True, "dms": dms})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/reactions", methods=["POST"])
async def api_add_reaction(request: Request):
    """REST API: Add reaction to a message."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    channel = body.get("channel")
    timestamp = body.get("timestamp")
    name = body.get("name")

    if not all([channel, timestamp, name]):
        return JSONResponse(
            {"ok": False, "error": "channel, timestamp, and name required"},
            status_code=400,
        )

    try:
        client.reactions_add(channel=channel, timestamp=timestamp, name=name)
        return JSONResponse({"ok": True, "reaction": name})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/files", methods=["GET"])
async def api_list_files(request: Request):
    """REST API: List shared files."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    params = request.query_params
    kwargs = {
        "types": params.get("types", "all"),
        "count": min(int(params.get("count", "20")), 100),
    }
    if params.get("channel"):
        kwargs["channel"] = params["channel"]
    if params.get("user"):
        kwargs["user"] = params["user"]

    try:
        result = client.files_list(**kwargs)
        files = [
            {
                "id": f["id"],
                "name": f.get("name", ""),
                "title": f.get("title", ""),
                "filetype": f.get("filetype", ""),
                "size": f.get("size", 0),
                "user": f.get("user", ""),
                "permalink": f.get("permalink", ""),
            }
            for f in result.get("files", [])
        ]
        return JSONResponse({"ok": True, "files": files})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/channels/{channel_id}/pins", methods=["GET"])
async def api_get_pins(request: Request):
    """REST API: Get pinned messages in a channel."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    channel_id = request.path_params.get("channel_id")
    try:
        result = client.pins_list(channel=channel_id)
        pins = [
            {
                "type": item.get("type", ""),
                "created": item.get("created", 0),
                "message": {
                    "text": item.get("message", {}).get("text", ""),
                    "user": item.get("message", {}).get("user", ""),
                    "ts": item.get("message", {}).get("ts", ""),
                }
                if item.get("message")
                else None,
            }
            for item in result.get("items", [])
        ]
        return JSONResponse({"ok": True, "pins": pins})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/channels/{channel_id}/bookmarks", methods=["GET"])
async def api_get_bookmarks(request: Request):
    """REST API: Get bookmarks in a channel."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    channel_id = request.path_params.get("channel_id")
    try:
        result = client.bookmarks_list(channel_id=channel_id)
        bookmarks = [
            {
                "id": b.get("id", ""),
                "title": b.get("title", ""),
                "type": b.get("type", ""),
                "link": b.get("link", ""),
            }
            for b in result.get("bookmarks", [])
        ]
        return JSONResponse({"ok": True, "bookmarks": bookmarks})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/stars", methods=["GET"])
async def api_get_stars(request: Request):
    """REST API: Get starred items."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    count = min(int(request.query_params.get("count", "20")), 100)
    try:
        result = client.stars_list(count=count)
        stars = []
        for item in result.get("items", []):
            star = {"type": item.get("type", "")}
            if item.get("type") == "message":
                msg = item.get("message", {})
                star["message"] = {
                    "text": msg.get("text", ""),
                    "user": msg.get("user", ""),
                    "ts": msg.get("ts", ""),
                }
                star["channel"] = item.get("channel", "")
            elif item.get("type") == "file":
                f = item.get("file", {})
                star["file"] = {"id": f.get("id", ""), "name": f.get("name", "")}
            stars.append(star)
        return JSONResponse({"ok": True, "stars": stars})
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@server.custom_route("/api/me", methods=["GET"])
async def api_get_me(request: Request):
    """REST API: Get authenticated user info."""
    client = _get_slack_client_from_token(request)
    if not client:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        result = client.auth_test()
        return JSONResponse({
            "ok": True,
            "user_id": result["user_id"],
            "user": result["user"],
            "team_id": result["team_id"],
            "team": result["team"],
        })
    except SlackApiError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# =============================================================================
# OpenAPI Specification Endpoint
# =============================================================================


@server.custom_route("/openapi.json", methods=["GET"])
async def openapi_spec(request: Request):
    """OpenAPI 3.1 specification for Open WebUI External Tools."""
    config = get_oauth_config()
    base_url = config.get_oauth_base_url()

    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "Slack MCP API",
            "description": "Slack workspace access via OAuth 2.1 - channels, messages, users, search",
            "version": "1.0.0",
        },
        "servers": [{"url": base_url}],
        "security": [
            {"oauth2": ["channels:read", "chat:write", "users:read", "search:read"]}
        ],
        "components": {
            "securitySchemes": {
                "oauth2": {
                    "type": "oauth2",
                    "flows": {
                        "authorizationCode": {
                            "authorizationUrl": f"{base_url}/oauth2/authorize",
                            "tokenUrl": f"{base_url}/oauth2/token",
                            "scopes": {
                                "channels:read": "View channels",
                                "channels:history": "Read channel messages",
                                "chat:write": "Send messages",
                                "users:read": "View users",
                                "search:read": "Search messages",
                                "reactions:read": "View reactions",
                                "reactions:write": "Add reactions",
                                "im:history": "Read direct messages",
                                "groups:history": "Read private channels",
                                "files:read": "View shared files",
                                "pins:read": "View pinned messages",
                                "bookmarks:read": "View channel bookmarks",
                                "stars:read": "View starred items",
                            },
                        }
                    },
                }
            }
        },
        "paths": {
            "/api/channels": {
                "get": {
                    "operationId": "listChannels",
                    "summary": "List Slack channels",
                    "description": "List all channels in the workspace",
                    "parameters": [
                        {
                            "name": "types",
                            "in": "query",
                            "schema": {
                                "type": "string",
                                "default": "public_channel,private_channel",
                            },
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "default": 100},
                        },
                    ],
                    "responses": {"200": {"description": "List of channels"}},
                }
            },
            "/api/channels/{channel_id}": {
                "get": {
                    "operationId": "getChannel",
                    "summary": "Get channel info",
                    "parameters": [
                        {
                            "name": "channel_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Channel details"}},
                }
            },
            "/api/channels/{channel_id}/history": {
                "get": {
                    "operationId": "getChannelHistory",
                    "summary": "Get channel message history",
                    "parameters": [
                        {
                            "name": "channel_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "default": 100},
                        },
                    ],
                    "responses": {"200": {"description": "Channel messages"}},
                }
            },
            "/api/messages": {
                "post": {
                    "operationId": "sendMessage",
                    "summary": "Send a message",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["channel", "text"],
                                    "properties": {
                                        "channel": {"type": "string"},
                                        "text": {"type": "string"},
                                        "thread_ts": {"type": "string"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Message sent"}},
                }
            },
            "/api/search": {
                "get": {
                    "operationId": "searchMessages",
                    "summary": "Search messages",
                    "parameters": [
                        {
                            "name": "query",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "count",
                            "in": "query",
                            "schema": {"type": "integer", "default": 20},
                        },
                    ],
                    "responses": {"200": {"description": "Search results"}},
                }
            },
            "/api/users": {
                "get": {
                    "operationId": "listUsers",
                    "summary": "List workspace users",
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "default": 100},
                        },
                    ],
                    "responses": {"200": {"description": "List of users"}},
                }
            },
            "/api/users/{user_id}": {
                "get": {
                    "operationId": "getUser",
                    "summary": "Get user info",
                    "parameters": [
                        {
                            "name": "user_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "User details"}},
                }
            },
            "/api/channels/{channel_id}/threads/{thread_ts}": {
                "get": {
                    "operationId": "getThreadReplies",
                    "summary": "Get all replies in a message thread",
                    "parameters": [
                        {
                            "name": "channel_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "thread_ts",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "default": 100},
                        },
                    ],
                    "responses": {"200": {"description": "Thread messages"}},
                }
            },
            "/api/dms": {
                "get": {
                    "operationId": "listDirectMessages",
                    "summary": "List direct message conversations",
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "default": 100},
                        },
                    ],
                    "responses": {"200": {"description": "List of DM conversations"}},
                }
            },
            "/api/reactions": {
                "post": {
                    "operationId": "addReaction",
                    "summary": "Add emoji reaction to a message",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["channel", "timestamp", "name"],
                                    "properties": {
                                        "channel": {"type": "string"},
                                        "timestamp": {"type": "string"},
                                        "name": {"type": "string", "description": "Emoji name without colons"},
                                    },
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Reaction added"}},
                }
            },
            "/api/files": {
                "get": {
                    "operationId": "listFiles",
                    "summary": "List shared files in workspace or channel",
                    "parameters": [
                        {
                            "name": "channel",
                            "in": "query",
                            "schema": {"type": "string"},
                            "description": "Filter by channel ID",
                        },
                        {
                            "name": "user",
                            "in": "query",
                            "schema": {"type": "string"},
                            "description": "Filter by user ID",
                        },
                        {
                            "name": "types",
                            "in": "query",
                            "schema": {"type": "string", "default": "all"},
                            "description": "File types: all, images, gdocs, zips, pdfs",
                        },
                        {
                            "name": "count",
                            "in": "query",
                            "schema": {"type": "integer", "default": 20},
                        },
                    ],
                    "responses": {"200": {"description": "List of shared files"}},
                }
            },
            "/api/channels/{channel_id}/pins": {
                "get": {
                    "operationId": "getChannelPins",
                    "summary": "Get pinned messages in a channel",
                    "parameters": [
                        {
                            "name": "channel_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Pinned messages"}},
                }
            },
            "/api/channels/{channel_id}/bookmarks": {
                "get": {
                    "operationId": "getChannelBookmarks",
                    "summary": "Get bookmarks saved in a channel",
                    "parameters": [
                        {
                            "name": "channel_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {"200": {"description": "Channel bookmarks"}},
                }
            },
            "/api/stars": {
                "get": {
                    "operationId": "getStarredItems",
                    "summary": "Get the authenticated user's starred items",
                    "parameters": [
                        {
                            "name": "count",
                            "in": "query",
                            "schema": {"type": "integer", "default": 20},
                        },
                    ],
                    "responses": {"200": {"description": "Starred items"}},
                }
            },
            "/api/me": {
                "get": {
                    "operationId": "getMe",
                    "summary": "Get authenticated user's profile and workspace",
                    "responses": {"200": {"description": "Current user info"}},
                }
            },
        },
    }
    return JSONResponse(spec)


@server.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    """Health check endpoint."""
    try:
        version = metadata.version("slack-mcp")
    except metadata.PackageNotFoundError:
        version = "dev"
    return JSONResponse(
        {
            "status": "healthy",
            "service": "slack-mcp",
            "version": version,
        }
    )


@server.custom_route("/", methods=["GET"])
async def root(request: Request):
    """Root endpoint with server information."""
    try:
        version = metadata.version("slack-mcp")
    except metadata.PackageNotFoundError:
        version = "dev"

    config = get_oauth_config()
    base_url = config.get_oauth_base_url()

    return JSONResponse(
        {
            "service": "slack-mcp",
            "version": version,
            "oauth2": {
                "metadata": f"{base_url}/.well-known/oauth-authorization-server",
                "register": f"{base_url}/register",
                "authorize": f"{base_url}/oauth2/authorize",
                "token": f"{base_url}/oauth2/token",
            },
            "mcp_endpoint": f"{base_url}/mcp",
        }
    )


# =============================================================================
# Root MCP Forward (Open WebUI OAuth 2.1 compatibility)
# =============================================================================


@server.custom_route("/", methods=["POST"])
async def root_mcp_forward(request: Request):
    """
    Forward POST requests from root to /mcp endpoint.
    
    Open WebUI with OAuth 2.1 MCP servers ignores the 'path' parameter
    and POSTs to the root URL instead of /mcp. This forwards those requests.
    """
    import httpx
    
    body = await request.body()
    port = int(os.getenv("PORT", "8080"))
    
    # Forward all headers except host
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"http://localhost:{port}/mcp",
                content=body,
                headers=headers,
                timeout=60.0,
            )
            
            from starlette.responses import Response
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.headers.get("content-type"),
            )
        except Exception as e:
            logger.error(f"Error forwarding to /mcp: {e}")
            raise HTTPException(status_code=502, detail=f"MCP forward failed: {e}")


# =============================================================================
# Main Entry Point
# =============================================================================


def main() -> None:
    """Main entry point for the Slack MCP server."""
    safe_print("🔧 Slack MCP Server with OAuth 2.1")
    safe_print("=" * 50)
    safe_print("📋 Server Information:")

    try:
        version = metadata.version("slack-mcp")
    except metadata.PackageNotFoundError:
        version = "dev"

    port = int(os.getenv("PORT", "8080"))
    config = get_oauth_config()
    base_url = config.get_oauth_base_url()

    safe_print(f"   📦 Version: {version}")
    safe_print(f"   🌐 Transport: streamable-http")
    safe_print(f"   🔗 URL: {base_url}")
    safe_print(
        f"   🔐 OAuth Metadata: {base_url}/.well-known/oauth-authorization-server"
    )
    safe_print(f"   🐍 Python: {sys.version.split()[0]}")
    safe_print("")

    # Configuration details
    safe_print("⚙️  Active Configuration:")
    client_id = os.getenv("SLACK_OAUTH_CLIENT_ID", "Not Set")
    client_secret = os.getenv("SLACK_OAUTH_CLIENT_SECRET", "Not Set")

    # Redact client secret for security
    redacted_secret = (
        f"{client_secret[:4]}...{client_secret[-4:]}"
        if len(client_secret) > 8 and client_secret != "Not Set"
        else client_secret
    )

    safe_print(f"   - SLACK_OAUTH_CLIENT_ID: {client_id}")
    safe_print(f"   - SLACK_OAUTH_CLIENT_SECRET: {redacted_secret}")
    safe_print(f"   - SLACK_EXTERNAL_URL: {os.getenv('SLACK_EXTERNAL_URL', 'Not Set')}")
    safe_print(
        f"   - SLACK_CREDS_BUCKET: {os.getenv('SLACK_CREDS_BUCKET', 'slack-mcp-creds-flow-os')}"
    )
    safe_print(f"   - PORT: {port}")
    safe_print("")

    # Import and register tools
    safe_print("🛠️  Loading Slack tools...")
    try:
        from tools import slack_tools  # noqa: F401

        safe_print("   ✓ Slack tools loaded")
    except ModuleNotFoundError as e:
        logger.error(f"Failed to load Slack tools: {e}", exc_info=True)
        safe_print(f"   ⚠️  Failed to load Slack tools: {e}")
    safe_print("")

    # Start server
    safe_print(f"🚀 Starting HTTP server on 0.0.0.0:{port}")
    safe_print("✅ Ready for OAuth 2.1 connections")
    safe_print("")
    safe_print("📝 OAuth 2.1 Endpoints:")
    safe_print(f"   - Metadata: {base_url}/.well-known/oauth-authorization-server")
    safe_print(f"   - Register: {base_url}/register")
    safe_print(f"   - Authorize: {base_url}/oauth2/authorize")
    safe_print(f"   - Token: {base_url}/oauth2/token")
    safe_print(f"   - MCP: {base_url}/mcp")
    safe_print("")

    try:
        server.run(transport="streamable-http", host="0.0.0.0", port=port)
    except KeyboardInterrupt:
        safe_print("\n👋 Server shutdown requested")
        sys.exit(0)
    except Exception as e:
        safe_print(f"\n❌ Server error: {e}")
        logger.error(f"Unexpected error running server: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
