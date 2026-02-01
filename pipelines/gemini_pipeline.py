"""
Javier OS — Gemini Pipeline for Open WebUI.

Uses Gemini 3 Pro Preview and Flash Preview via Vertex AI
with Envision's service account credentials.
"""

import json
import os
import sys
from pathlib import Path
from typing import Iterator, Union

from pydantic import BaseModel, Field

_pipelines_dir = str(Path(__file__).resolve().parent)
if _pipelines_dir not in sys.path:
    sys.path.insert(0, _pipelines_dir)


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

You have access to these integrations (toggled on via the tools dropdown):
- **Gmail**: Search and send emails to clients, vendors, architects, and subcontractors
- **Google Calendar**: View and create meetings, site visits, and deadlines
- **Slack**: Search messages, send updates to team channels, read threads
- **Envision OS (Procore)**: List projects, check RFIs, review budgets, track change orders
- **WhatsApp**: Message field teams, subcontractors, and vendors

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

    def __init__(self):
        self.type = "manifold"
        self.name = "Gemini: "
        self.valves = self.Valves()
        self._init_vertex()

    def _init_vertex(self):
        """Initialize Vertex AI with service account credentials."""
        try:
            import vertexai

            sa_json = os.getenv("GOOGLE_SA_JSON", "")
            if sa_json:
                from google.oauth2 import service_account

                creds = service_account.Credentials.from_service_account_info(
                    json.loads(sa_json)
                )
                vertexai.init(
                    project=self.valves.GCP_PROJECT_ID,
                    location=self.valves.GCP_LOCATION,
                    credentials=creds,
                )
            else:
                vertexai.init(
                    project=self.valves.GCP_PROJECT_ID,
                    location=self.valves.GCP_LOCATION,
                )
        except Exception:
            pass

    def pipelines(self) -> list[dict]:
        return [
            {"id": "gemini-3-pro-preview", "name": "Gemini 3 Pro Preview"},
            {"id": "gemini-3-flash-preview", "name": "Gemini 3 Flash Preview"},
        ]

    def pipe(self, body: dict, **kwargs) -> Union[str, Iterator[str]]:
        try:
            from vertexai.generative_models import GenerativeModel
        except ImportError:
            return "Error: google-cloud-aiplatform not installed."

        model_id = body["model"]
        if "." in model_id:
            model_id = model_id.split(".", 1)[1]

        messages = body.get("messages", [])

        # Build Gemini content format
        system_instruction = SYSTEM_PROMPT
        contents = []
        for msg in messages:
            role = msg["role"]
            text = msg["content"]
            if role == "system":
                system_instruction = text + "\n\n" + SYSTEM_PROMPT
            elif role == "user":
                contents.append({"role": "user", "parts": [{"text": text}]})
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": text}]})

        try:
            model = GenerativeModel(
                model_id,
                system_instruction=system_instruction,
            )
            response = model.generate_content(
                contents,
                generation_config={
                    "max_output_tokens": body.get("max_tokens", 8192),
                    "temperature": body.get("temperature", 1.0),
                },
            )
            return response.text
        except Exception as e:
            return f"Error: Gemini API error — {e}"
