"""Open WebUI API helper utilities for pipelines."""

from __future__ import annotations

from typing import Any

import httpx


class OpenWebUIAPIError(RuntimeError):
    """Raised when the Open WebUI API call fails."""


def _clean_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def build_openwebui_headers(
    api_key: str | None = None,
    user: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build headers for Open WebUI API calls."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if user:
        # Trusted user context headers supported by Open WebUI.
        # This is useful when auth is delegated from backend services.
        if user.get("name"):
            headers["X-OpenWebUI-User-Name"] = str(user["name"])
        if user.get("id"):
            headers["X-OpenWebUI-User-Id"] = str(user["id"])
        if user.get("email"):
            headers["X-OpenWebUI-User-Email"] = str(user["email"])
        if user.get("role"):
            headers["X-OpenWebUI-User-Role"] = str(user["role"])
    return headers


def openwebui_chat_completion(
    *,
    base_url: str,
    payload: dict[str, Any],
    api_key: str | None = None,
    user: dict[str, Any] | None = None,
    timeout: float = 120.0,
) -> str:
    """Call Open WebUI /api/chat/completions and return assistant text."""
    url = f"{_clean_base_url(base_url)}/api/chat/completions"
    headers = build_openwebui_headers(api_key=api_key, user=user)

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text
        raise OpenWebUIAPIError(
            f"Open WebUI API error {exc.response.status_code}: {detail}"
        ) from exc
    except httpx.HTTPError as exc:
        raise OpenWebUIAPIError(f"Open WebUI API request failed: {exc}") from exc

    data = response.json()
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise OpenWebUIAPIError(
            f"Unexpected Open WebUI API response format: {data}"
        ) from exc
