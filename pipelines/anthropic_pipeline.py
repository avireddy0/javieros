"""
Javier OS — Agentic Claude Pipeline for Open WebUI.

Uses Claude Opus 4.5 with extended thinking and real tool calling.
Tools are dispatched to Envision-MCP and WhatsApp bridge.
Note: Gmail/Calendar/Drive tools removed - use native MCP integration with OAuth instead.
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Iterator, Union

from pydantic import BaseModel, Field

_pipelines_dir = str(Path(__file__).resolve().parent)
if _pipelines_dir not in sys.path:
    sys.path.insert(0, _pipelines_dir)

from tools import envision, whatsapp

logger = logging.getLogger(__name__)

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

You have access to live integrations — USE THEM when the user asks about emails, calendar, Slack, projects, RFIs, budgets, or WhatsApp. Call the appropriate tool; do not pretend or simulate results.

Key behaviors:
- Be concise and action-oriented. Javier is busy — get to the point.
- Always confirm before sending any message (email, Slack, WhatsApp) or creating events.
- When discussing construction data (RFIs, budgets, submittals), include project names and numbers.
- For financial questions, provide specific dollar amounts and variance analysis.
- When switching languages mid-conversation, follow the user's lead naturally."""

# Build unified tool list for Claude API
ALL_TOOLS = envision.TOOLS + whatsapp.TOOLS

# Map tool names to their call functions
TOOL_DISPATCH = {}
for t in envision.TOOLS:
    TOOL_DISPATCH[t["name"]] = envision.call_tool
for t in whatsapp.TOOLS:
    TOOL_DISPATCH[t["name"]] = whatsapp.call_tool

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
        user_email = (__user__ or {}).get("email")

        messages = []
        system_message = SYSTEM_PROMPT
        for msg in body.get("messages", []):
            if msg["role"] == "system":
                system_message = msg["content"] + "\n\n" + SYSTEM_PROMPT
            else:
                messages.append({"role": msg["role"], "content": msg["content"]})

        return self._agentic_loop(client, model_id, messages, system_message, user_email=user_email)

    def _agentic_loop(self, client, model_id, messages, system_message, user_email: str | None = None):
        """Run Claude with tool use in a loop until it produces a final text response."""
        import anthropic

        tools = _anthropic_tools()
        loop = asyncio.new_event_loop()

        # Per-user ACL pass-through
        envision_tools = envision.TOOL_NAMES

        try:
            for _round in range(MAX_TOOL_ROUNDS):
                # Non-streaming call so we can inspect tool_use blocks
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
                            if user_email and tc.name in envision_tools:
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
                            "content": result_text,
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
            yield f"Error: Anthropic API error — {e.message}"
        except Exception as e:
            logger.exception("Pipeline error")
            yield f"Error: {e}"
        finally:
            loop.close()
