"""
Javier OS — Agentic Claude Pipeline for Open WebUI.

Uses Claude Opus 4.5 with extended thinking. Tools are registered as
Open WebUI Tools (Gmail, Calendar, Slack, Envision OS, WhatsApp).
"""

import os
import sys
from pathlib import Path
from typing import Iterator, Union

from pydantic import BaseModel, Field

_pipelines_dir = str(Path(__file__).resolve().parent)
if _pipelines_dir not in sys.path:
    sys.path.insert(0, _pipelines_dir)

SYSTEM_PROMPT = """You are Javier's personal AI assistant. You have access to integrations
that the user can toggle on: Gmail, Google Calendar, Slack, Envision OS (Procore), and WhatsApp.

Be concise and helpful. Think carefully about requests before responding.
Always confirm before sending messages or creating events on behalf of the user."""


class Pipeline:
    class Valves(BaseModel):
        ANTHROPIC_API_KEY: str = Field(default="", description="Anthropic API key")

    def __init__(self):
        self.type = "manifold"
        self.name = "Javier OS: "
        self.valves = self.Valves(
            ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", "")
        )

    def pipelines(self) -> list[dict]:
        return [
            {"id": "claude-opus-4-5-20251101", "name": "Claude Opus 4.5"},
        ]

    async def pipe(self, body: dict, **kwargs) -> Union[str, Iterator[str]]:
        try:
            import anthropic
        except ImportError:
            return "Error: anthropic package not installed."

        if not self.valves.ANTHROPIC_API_KEY:
            return "Error: ANTHROPIC_API_KEY not configured."

        client = anthropic.Anthropic(
            api_key=self.valves.ANTHROPIC_API_KEY,
            timeout=300.0,
        )

        model_id = "claude-opus-4-5-20251101"

        messages = []
        system_message = SYSTEM_PROMPT
        for msg in body.get("messages", []):
            if msg["role"] == "system":
                system_message = msg["content"] + "\n\n" + SYSTEM_PROMPT
            else:
                messages.append({"role": msg["role"], "content": msg["content"]})

        try:
            response = client.messages.create(
                model=model_id,
                messages=messages,
                max_tokens=16384,
                system=system_message,
                thinking={
                    "type": "enabled",
                    "budget_tokens": 10000,
                },
            )
        except anthropic.AuthenticationError:
            return "Error: Invalid Anthropic API key."
        except anthropic.RateLimitError:
            return "Error: Rate limit exceeded. Please try again."
        except anthropic.APIError as e:
            return f"Error: Anthropic API error — {e.message}"

        # Extract text blocks (skip thinking blocks)
        texts = []
        for block in response.content:
            if hasattr(block, "text") and block.type == "text":
                texts.append(block.text)
        return "\n".join(texts) if texts else "No response."
