"""
Javier OS — Agentic Claude Pipeline for Open WebUI.

Exposes Claude models with tool-calling capabilities.
Tools: workspace-mcp (Gmail, Calendar, Drive), Envision-MCP (Slack, Procore),
WhatsApp bridge.
"""

import os
from typing import Iterator, Union

from pydantic import BaseModel, Field

from tools import workspace, envision, whatsapp

SYSTEM_PROMPT = """You are Javier's personal AI assistant with access to:
- **Gmail**: Search and send emails
- **Google Calendar**: View and create events
- **Google Drive**: Search files
- **Slack**: Search messages, read channels, send messages
- **Procore**: View construction projects, RFIs, budgets
- **WhatsApp**: Send and receive messages

When the user asks you to do something, use the appropriate tool. Be concise and helpful.
Always confirm before sending messages or creating events on behalf of the user."""

ALL_TOOLS = workspace.TOOLS + envision.TOOLS + whatsapp.TOOLS

TOOL_DISPATCH: dict[str, callable] = {}
for _name in workspace.TOOL_NAMES:
    TOOL_DISPATCH[_name] = workspace.call_tool
for _name in envision.TOOL_NAMES:
    TOOL_DISPATCH[_name] = envision.call_tool
for _name in whatsapp.TOOL_NAMES:
    TOOL_DISPATCH[_name] = whatsapp.call_tool

MAX_TOOL_ROUNDS = 10


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
            {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
            {"id": "claude-opus-4-5-20251101", "name": "Claude Opus 4.5"},
            {"id": "claude-haiku-4-20250414", "name": "Claude Haiku 4"},
        ]

    async def pipe(self, body: dict) -> Union[str, Iterator[str]]:
        try:
            import anthropic
        except ImportError:
            return "Error: anthropic package not installed."

        if not self.valves.ANTHROPIC_API_KEY:
            return "Error: ANTHROPIC_API_KEY not configured."

        client = anthropic.Anthropic(
            api_key=self.valves.ANTHROPIC_API_KEY,
            timeout=120.0,
        )

        model_id = body["model"]
        if "." in model_id:
            model_id = model_id.split(".", 1)[1]

        messages = []
        system_message = SYSTEM_PROMPT
        for msg in body.get("messages", []):
            if msg["role"] == "system":
                system_message = msg["content"] + "\n\n" + SYSTEM_PROMPT
            else:
                messages.append({"role": msg["role"], "content": msg["content"]})

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                response = client.messages.create(
                    model=model_id,
                    messages=messages,
                    max_tokens=body.get("max_tokens", 8192),
                    system=system_message,
                    tools=ALL_TOOLS,
                    temperature=body.get("temperature", 1.0),
                )
            except anthropic.AuthenticationError:
                return "Error: Invalid Anthropic API key."
            except anthropic.RateLimitError:
                return "Error: Rate limit exceeded. Please try again."
            except anthropic.APIError as e:
                return f"Error: Anthropic API error — {e.message}"

            if response.stop_reason != "tool_use":
                texts = [
                    block.text
                    for block in response.content
                    if hasattr(block, "text")
                ]
                return "\n".join(texts) if texts else "No response."

            # Process tool calls
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    caller = TOOL_DISPATCH.get(block.name)
                    if caller:
                        try:
                            result = await caller(block.name, block.input)
                        except Exception as e:
                            result = f"Tool error: {e}"
                    else:
                        result = f"Unknown tool: {block.name}"
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        return "Reached maximum tool call rounds. Please try a simpler request."
