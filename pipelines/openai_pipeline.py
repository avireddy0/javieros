"""
Javier OS — Agentic OpenAI Pipeline for Open WebUI.

Uses GPT models with function calling and WhatsApp tools.
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

You have access to WhatsApp tools. Use them when asked to send messages, check status, or retrieve messages.

Key behaviors:
- Be concise and action-oriented. Javier is busy — get to the point.
- Always confirm before sending any message (email, Slack, WhatsApp) or creating events.
- When discussing construction data (RFIs, budgets, submittals), include project names and numbers.
- For financial questions, provide specific dollar amounts and variance analysis.
- When switching languages mid-conversation, follow the user's lead naturally."""

MAX_TOOL_ROUNDS = 5


def _openai_tools() -> list[dict]:
    """Convert tool definitions to OpenAI function calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in whatsapp.TOOLS
    ]


class Pipeline:
    class Valves(BaseModel):
        OPENAI_API_KEY: str = Field(default="", description="OpenAI API key")

    def __init__(self):
        self.type = "manifold"
        self.name = "Javier OS GPT: "
        self.valves = self.Valves(
            OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", "")
        )

    def pipelines(self) -> list[dict]:
        return [
            {"id": "gpt-5.2", "name": "GPT 5.2"},
            {"id": "gpt-4.5-preview", "name": "GPT 4.5 Preview"},
            {"id": "gpt-4o", "name": "GPT-4o"},
        ]

    def pipe(self, body: dict, __user__: dict | None = None, **kwargs) -> Union[str, Iterator[str]]:
        model_id = body.get("model", "gpt-5.2")
        if "." in model_id and model_id.startswith("javier"):
            model_id = model_id.split(".", 1)[1]

        messages = body.get("messages", [])

        # Ensure system message is first
        if not messages or messages[0].get("role") != "system":
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

        try:
            from openai import OpenAI
        except ImportError:
            return "Error: openai package not installed."

        if not self.valves.OPENAI_API_KEY:
            return "Error: OPENAI_API_KEY not configured."

        client = OpenAI(api_key=self.valves.OPENAI_API_KEY)
        user_email = (__user__ or {}).get("email")

        return self._agentic_loop(client, model_id, messages, user_email=user_email)

    def _agentic_loop(
        self,
        client,
        model_id: str,
        messages: list[dict[str, Any]],
        user_email: str | None = None,
    ):
        """Run GPT with function calling in a loop until it produces a final text response."""
        tools = _openai_tools()
        loop = asyncio.new_event_loop()

        try:
            for _round in range(MAX_TOOL_ROUNDS):
                response = None
                for attempt in range(2):
                    try:
                        response = client.chat.completions.create(
                            model=model_id,
                            messages=messages,
                            max_tokens=4096,
                            tools=tools if tools else None,
                            tool_choice="auto" if tools else None,
                        )
                        break
                    except Exception as exc:
                        if attempt == 1:
                            raise
                        logger.warning("OpenAI API retry: %s", exc)

                if response is None:
                    yield "Error: No response from OpenAI."
                    return

                choice = response.choices[0]
                message = choice.message

                # If there are tool calls, execute them
                if message.tool_calls:
                    # First yield any content
                    if message.content:
                        yield message.content

                    # Add assistant message with tool calls to history
                    messages.append({
                        "role": "assistant",
                        "content": message.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in message.tool_calls
                        ],
                    })

                    # Execute each tool call
                    for tc in message.tool_calls:
                        tool_name = tc.function.name
                        try:
                            import json
                            arguments = json.loads(tc.function.arguments)
                        except json.JSONDecodeError:
                            arguments = {}

                        # Call the WhatsApp tool
                        if tool_name in whatsapp.TOOL_NAMES:
                            try:
                                call_kwargs = {}
                                if user_email:
                                    call_kwargs["user_email"] = user_email
                                result = loop.run_until_complete(
                                    whatsapp.call_tool(tool_name, arguments, **call_kwargs)
                                )
                            except Exception as e:
                                result = f"Error calling {tool_name}: {e}"
                        else:
                            result = f"Unknown tool: {tool_name}"

                        # Add tool result to messages
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        })

                    # Continue the loop to get the next response
                    continue

                # No tool calls — yield the final response
                if message.content:
                    yield message.content
                return

            yield "Error: Maximum tool rounds exceeded."
        finally:
            loop.close()
