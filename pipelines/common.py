"""Shared helpers for Open WebUI pipeline implementations."""

from __future__ import annotations

from typing import Any


def extract_text_content(content: Any) -> str:
    """Normalize Open WebUI message content into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if "text" in item and isinstance(item["text"], str):
                parts.append(item["text"])
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
                continue
            if item.get("type") == "input_text" and isinstance(item.get("input_text"), str):
                parts.append(item["input_text"])
        return "\n".join(p for p in parts if p).strip()
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        if isinstance(content.get("content"), str):
            return content["content"]
    return str(content)


def split_system_and_messages(
    messages: list[dict[str, Any]],
    default_system_prompt: str,
) -> tuple[str, list[dict[str, str]]]:
    """Extract system prompt and normalize user/assistant text messages."""
    system_prompt = default_system_prompt
    normalized: list[dict[str, str]] = []

    for msg in messages:
        role = msg.get("role")
        text = extract_text_content(msg.get("content"))
        if role == "system":
            if text:
                system_prompt = f"{text}\n\n{default_system_prompt}"
            continue
        if role not in {"user", "assistant"}:
            continue
        normalized.append({"role": role, "content": text})

    return system_prompt, normalized
