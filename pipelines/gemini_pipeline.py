"""
Javier OS — Gemini Pipeline for Open WebUI.

Uses Gemini 3 Pro Preview and Flash Preview via Vertex AI
with Envision's service account credentials.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, Iterator, Union

import httpx
import google.auth
import google.auth.transport.requests
from pydantic import BaseModel, Field

_pipelines_dir = str(Path(__file__).resolve().parent)
if _pipelines_dir not in sys.path:
    sys.path.insert(0, _pipelines_dir)

from common import split_system_and_messages
from openwebui_api import OpenWebUIAPIError, openwebui_chat_completion

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

You have access to these integrations via Open WebUI External Tools:
- **Google Workspace** (Gmail + Calendar) with OAuth
- **Slack** with OAuth
- **Envision MCP** for construction data (Procore, budgets, RFIs)
- **WhatsApp** via QR login

Key behaviors:
- Be concise and action-oriented. Javier is busy — get to the point.
- Always confirm before sending any message (email, Slack, WhatsApp) or creating events.
- When discussing construction data (RFIs, budgets, submittals), include project names and numbers.
- For financial questions, provide specific dollar amounts and variance analysis.
- When switching languages mid-conversation, follow the user's lead naturally."""


class Pipeline:
    class Valves(BaseModel):
        GCP_PROJECT_ID: str = Field(
            default="flow-os-1769675656", description="GCP Project ID"
        )
        GCP_LOCATION: str = Field(default="global", description="GCP region")
        USE_OPENWEBUI_API: bool = Field(
            default=_env_bool("GEMINI_USE_OPENWEBUI_API", True),
            description="Route requests through Open WebUI API when credentials are provided",
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
            default=os.getenv("OPENWEBUI_GEMINI_MODEL_ID", ""),
            description="Optional Open WebUI model ID override",
        )

    def __init__(self):
        self.type = "manifold"
        self.name = "Gemini: "
        self.valves = self.Valves()

    def pipelines(self) -> list[dict]:
        return [
            {"id": "gemini-3-pro-preview", "name": "Gemini 3 Pro Preview"},
            {"id": "gemini-3-flash-preview", "name": "Gemini 3 Flash Preview"},
        ]

    def pipe(self, body: dict, __user__: dict | None = None, **kwargs) -> Union[str, Iterator[str]]:
        model_id = body.get("model", "gemini-3-pro-preview")
        if "." in model_id:
            model_id = model_id.split(".", 1)[1]

        system_instruction, normalized_messages = split_system_and_messages(
            body.get("messages", []), SYSTEM_PROMPT
        )

        if self._should_use_openwebui_api():
            try:
                return self._call_openwebui_api(
                    model_id=model_id,
                    body=body,
                    system_instruction=system_instruction,
                    messages=normalized_messages,
                    user=__user__ or {},
                )
            except OpenWebUIAPIError as exc:
                # Fall back to direct Vertex path if Open WebUI proxy is unavailable.
                logger.warning("Open WebUI API fallback to Vertex: %s", exc)

        return self._call_vertex_api(
            model_id=model_id,
            body=body,
            system_instruction=system_instruction,
            messages=normalized_messages,
        )

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
        system_instruction: str,
        messages: list[dict[str, str]],
        user: dict[str, Any],
    ) -> str:
        openwebui_model = self.valves.OPENWEBUI_MODEL_ID or model_id
        payload = {
            "model": openwebui_model,
            "messages": [
                {"role": "system", "content": system_instruction},
                *messages,
            ],
            "temperature": body.get("temperature", 1.0),
            "max_tokens": body.get("max_tokens", 8192),
            "stream": False,
        }
        return openwebui_chat_completion(
            base_url=self.valves.OPENWEBUI_API_BASE_URL,
            payload=payload,
            api_key=self.valves.OPENWEBUI_API_KEY,
            user=user,
            timeout=120.0,
        )

    def _call_vertex_api(
        self,
        *,
        model_id: str,
        body: dict[str, Any],
        system_instruction: str,
        messages: list[dict[str, str]],
    ) -> str:
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        try:
            creds, _ = google.auth.default()
            creds.refresh(google.auth.transport.requests.Request())

            location = self.valves.GCP_LOCATION
            project = self.valves.GCP_PROJECT_ID
            # "global" uses aiplatform.googleapis.com; regional uses {region}-aiplatform...
            host = (
                "aiplatform.googleapis.com"
                if location == "global"
                else f"{location}-aiplatform.googleapis.com"
            )
            url = (
                f"https://{host}/v1/projects/{project}"
                f"/locations/{location}/publishers/google/models/{model_id}:generateContent"
            )

            payload = {
                "contents": contents,
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "generationConfig": {
                    "maxOutputTokens": body.get("max_tokens", 8192),
                    "temperature": body.get("temperature", 1.0),
                },
            }

            with httpx.Client(timeout=120.0) as client:
                response = None
                for _ in range(2):
                    response = client.post(
                        url,
                        json=payload,
                        headers={"Authorization": f"Bearer {creds.token}"},
                    )
                    if response.status_code < 500:
                        break
                assert response is not None
                response.raise_for_status()

            data = response.json()
            candidate = (data.get("candidates") or [{}])[0]
            parts = ((candidate.get("content") or {}).get("parts") or [])
            text = "\n".join(
                part.get("text", "") for part in parts if isinstance(part, dict)
            ).strip()
            if text:
                return text
            finish_reason = candidate.get("finishReason", "unknown")
            return f"No text response returned by Gemini (finish reason: {finish_reason})."
        except Exception as e:
            return f"Error: Gemini API error — {e}"
