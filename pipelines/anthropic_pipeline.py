"""
Javier OS — Agentic Claude Pipeline for Open WebUI.

Uses Claude Opus 4.5 with extended thinking and real tool calling.
Tools are dispatched only to the WhatsApp bridge. All other integrations
are now native External Tools in Open WebUI.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Iterator, Union

from pydantic import BaseModel, Field

_pipelines_dir = str(Path(__file__).resolve().parent)
if _pipelines_dir not in sys.path:
    sys.path.insert(0, _pipelines_dir)

from common import split_system_and_messages
from openwebui_api import OpenWebUIAPIError, openwebui_chat_completion
from tools import whatsapp

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

SYSTEM_PROMPT = """You are Javier Barrios's personal AI assistant. Javier is the Head of Real Estate Development at Flow. He is also CEO and Co-founder of MIRA, a real estate investment and development platform. You are fully bilingual in English and Spanish — respond in whichever language the user writes in, and translate seamlessly when asked.

About Javier:
- Head of RE Development at Flow — multifamily residential company founded by Adam Neumann, backed by a16z. Community-driven, tech-integrated living that creates belonging and "ownership" for renters.
- Co-founder and former CEO of MIRA (real estate investment/development platform)
- Former President of ADI (Real Estate Developer Association)
- Industrial Engineering (Universidad Panamericana), MBA (Harvard Business School)
- Expertise: Large-scale urban "community" development, institutional financial vehicles (CerPI, CKD), Mexican real estate dynamics

Current Flow projects:
- South Florida pipeline: 3,000+ apartment units
- Flow House Miami: 40-story condo tower at Miami Worldcenter, 466 residences, sustainable materials, coworking/wellness
- Flow Miami & Flow Fort Lauderdale: Existing properties outperforming market benchmarks
- Aventura: Three-tower mixed-use site
- El Portal: 16-acre site zoned for up to 2,000 units
- Saudi Arabia (Flow Narjis): Furnished apartments with hospitality services

Philosophy:
- Community-centric scale (1,300+ unit projects) over isolated mixed-use buildings
- Urban ring development — existing infrastructure, schools, transportation
- Financial innovation: CerPI for institutional co-investment with discretionary control

You have access to live integrations via Open WebUI External Tools. Use them when available and do not pretend or simulate results.

Key behaviors:
- Be concise and action-oriented. Javier is busy — get to the point.
- Always confirm before sending any message (email, Slack, WhatsApp) or creating events.
- When discussing construction data (RFIs, budgets, submittals), include project names and numbers.
- For financial questions, provide specific dollar amounts and variance analysis.
- When switching languages mid-conversation, follow the user's lead naturally."""

# Build unified tool list for Claude API (WhatsApp only)
ALL_TOOLS = whatsapp.TOOLS

# Map tool names to their call functions
TOOL_DISPATCH = {t["name"]: whatsapp.call_tool for t in whatsapp.TOOLS}

MAX_TOOL_ROUNDS = 5


def _anthropic_tools() -> list[dict]:
    """Convert tool definitions to Anthropic API format."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        for t in ALL_TOOLS
    ]


class Pipeline:
    class Valves(BaseModel):
        ANTHROPIC_API_KEY: str = Field(default="", description="Anthropic API key")
        USE_OPENWEBUI_API: bool = Field(
            default=_env_bool("ANTHROPIC_USE_OPENWEBUI_API", False),
            description="Route requests through Open WebUI API instead of direct Anthropic SDK",
        )
        OPENWEBUI_API_BASE_URL: str = Field(
            default=os.getenv("OPENWEBUI_API_BASE_URL", "http://localhost:8080"),
            description="Open WebUI base URL",
        )
        OPENWEBUI_API_KEY: str = Field(
            default=os.getenv("OPENWEBUI_API_KEY", ""),
            description="Open WebUI API key",
        )
        OPENWEBUI_MODEL_ID: str = Field(
            default=os.getenv("OPENWEBUI_ANTHROPIC_MODEL_ID", ""),
            description="Optional Open WebUI model ID override",
        )

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

    def pipe(self, body: dict, __user__: dict | None = None, **kwargs) -> Union[str, Iterator[str]]:
        model_id = body.get("model", "claude-opus-4-5-20251101")
        if "." in model_id:
            model_id = model_id.split(".", 1)[1]

        system_message, messages = split_system_and_messages(
            body.get("messages", []),
            SYSTEM_PROMPT,
        )

        if self._should_use_openwebui_api():
            try:
                return self._call_openwebui_api(
                    model_id=model_id,
                    body=body,
                    system_message=system_message,
                    messages=messages,
                    user=__user__ or {},
                )
            except OpenWebUIAPIError as exc:
                logger.warning("Open WebUI API fallback to Anthropic SDK: %s", exc)

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

        user_email = (__user__ or {}).get("email")

        return self._agentic_loop(client, model_id, messages, system_message, user_email=user_email)

    def _should_use_openwebui_api(self) -> bool:
        return (
            self.valves.USE_OPENWEBUI_API
            and bool(self.valves.OPENWEBUI_API_BASE_URL)
            and bool(self.valves.OPENWEBUI_API_KEY)
        )

    def _call_openwebui_api(
        self,
        *,
        model_id: str,
        body: dict[str, Any],
        system_message: str,
        messages: list[dict[str, str]],
        user: dict[str, Any],
    ) -> str:
        openwebui_model = self.valves.OPENWEBUI_MODEL_ID or model_id
        payload = {
            "model": openwebui_model,
            "messages": [
                {"role": "system", "content": system_message},
                *messages,
            ],
            "temperature": body.get("temperature", 0.7),
            "max_tokens": body.get("max_tokens", 4096),
            "stream": False,
        }
        return openwebui_chat_completion(
            base_url=self.valves.OPENWEBUI_API_BASE_URL,
            payload=payload,
            api_key=self.valves.OPENWEBUI_API_KEY,
            user=user,
            timeout=120.0,
        )

    def _agentic_loop(
        self,
        client,
        model_id: str,
        messages: list[dict[str, Any]],
        system_message: str,
        user_email: str | None = None,
    ):
        """Run Claude with tool use in a loop until it produces a final text response."""
        import anthropic

        tools = _anthropic_tools()
        loop = asyncio.new_event_loop()

        whatsapp_tools = whatsapp.TOOL_NAMES

        try:
            for _round in range(MAX_TOOL_ROUNDS):
                # Non-streaming call so we can inspect tool_use blocks
                response = None
                for attempt in range(2):
                    try:
                        response = client.messages.create(
                            model=model_id,
                            messages=messages,
                            max_tokens=16384,
                            system=system_message,
                            tools=tools,
                            thinking={
                                "type": "enabled",
                                "budget_tokens": 10000,
                            },
                        )
                        break
                    except anthropic.APIError as exc:
                        status = getattr(exc, "status_code", 500)
                        if attempt == 1 or (status is not None and status < 500):
                            raise
                if response is None:
                    yield "Error: No response from Anthropic."
                    return

                # Collect text to yield and tool calls to execute
                text_parts = []
                tool_calls = []

                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)
                    elif block.type == "tool_use":
                        tool_calls.append(block)
                    # Skip thinking blocks

                # Yield any text produced so far
                for part in text_parts:
                    yield part

                # If no tool calls, we're done
                if response.stop_reason != "tool_use" or not tool_calls:
                    return

                # Execute tool calls and build tool results
                assistant_content = response.content
                tool_results = []

                for tc in tool_calls:
                    call_fn = TOOL_DISPATCH.get(tc.name)
                    if call_fn is None:
                        result_text = f"Error: Unknown tool '{tc.name}'"
                    else:
                        try:
                            call_kwargs = {}
                            if user_email and tc.name in whatsapp_tools:
                                call_kwargs["user_email"] = user_email
                            result_text = loop.run_until_complete(
                                call_fn(tc.name, tc.input, **call_kwargs)
                            )
                        except Exception as e:
                            logger.exception("Tool call failed: %s", tc.name)
                            result_text = f"Error calling {tc.name}: {e}"

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": str(result_text),
                        }
                    )

                # Append assistant message and tool results, then loop
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({"role": "user", "content": tool_results})

        except anthropic.AuthenticationError:
            yield "Error: Invalid Anthropic API key."
        except anthropic.RateLimitError:
            yield "Error: Rate limit exceeded. Please try again."
        except anthropic.APIError as e:
            message = getattr(e, "message", str(e))
            yield f"Error: Anthropic API error — {message}"
        except Exception as e:
            logger.exception("Pipeline error")
            yield f"Error: {e}"
        finally:
            loop.close()
