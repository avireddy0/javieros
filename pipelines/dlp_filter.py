"""
DLP Pipeline Filter — GCP Sensitive Data Protection API

Scans user messages (inlet) and LLM responses (outlet) for PII/sensitive data.
Actions: redact, block, or log_only. Admin-configurable via Valves in Open WebUI.

Architecture:
  User message → [inlet() DLP scan] → LLM → [outlet() DLP scan] → User

Requires:
  - google-cloud-dlp>=3.0.0
  - GCP project with DLP API enabled
  - Service account with roles/dlp.user
"""

import logging
import os
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# InfoType presets by sensitivity level
# ---------------------------------------------------------------------------
SENSITIVITY_PRESETS = {
    "low": [
        "CREDIT_CARD_NUMBER",
        "US_SOCIAL_SECURITY_NUMBER",
    ],
    "medium": [
        "CREDIT_CARD_NUMBER",
        "US_SOCIAL_SECURITY_NUMBER",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "US_PASSPORT",
        "IBAN_CODE",
    ],
    "high": [
        "CREDIT_CARD_NUMBER",
        "US_SOCIAL_SECURITY_NUMBER",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "US_PASSPORT",
        "IBAN_CODE",
        "STREET_ADDRESS",
        "DATE_OF_BIRTH",
        "IP_ADDRESS",
        "MAC_ADDRESS",
        "US_DRIVERS_LICENSE_NUMBER",
        "US_INDIVIDUAL_TAXPAYER_IDENTIFICATION_NUMBER",
    ],
}


class Pipeline:
    """Open WebUI pipeline filter for Data Loss Prevention via GCP SDP API."""

    class Valves(BaseModel):
        # ── Master toggle ───────────────────────────────────────────────
        enabled: bool = Field(
            default=True,
            description="Enable DLP scanning on messages",
        )

        # ── Direction toggles ───────────────────────────────────────────
        scan_inbound: bool = Field(
            default=True,
            description="Scan user messages before they reach the LLM",
        )
        scan_outbound: bool = Field(
            default=True,
            description="Scan LLM responses before they reach the user",
        )

        # ── Action: what to do when PII is found ───────────────────────
        action: str = Field(
            default="redact",
            description="Action on PII detection: 'redact' (replace with [TYPE]), "
            "'block' (reject message), or 'log_only' (pass through, log finding)",
        )

        # ── Sensitivity level ──────────────────────────────────────────
        sensitivity: str = Field(
            default="medium",
            description="Sensitivity preset: 'low' (SSN, CC only), "
            "'medium' (+email, phone, passport, IBAN), "
            "'high' (+address, DOB, IP, MAC, DL, ITIN)",
        )

        # ── GCP project (auto-detected on Cloud Run) ──────────────────
        gcp_project_id: str = Field(
            default="",
            description="GCP project ID for DLP API. "
            "Leave blank to auto-detect from environment.",
        )

        # ── Allow-list for known-safe values ───────────────────────────
        allow_list_words: str = Field(
            default="",
            description="Comma-separated words to exclude from DLP findings "
            "(e.g. company email domains, test values)",
        )

        # ── Min likelihood threshold ───────────────────────────────────
        min_likelihood: str = Field(
            default="LIKELY",
            description="Minimum likelihood for a finding to trigger action: "
            "POSSIBLE, LIKELY, VERY_LIKELY",
        )

    def __init__(self):
        self.type = "filter"
        self.name = "DLP Filter"
        self.valves = self.Valves()
        self._dlp_client = None

    # ------------------------------------------------------------------
    # Lazy-init DLP client (avoids import failures if lib missing)
    # ------------------------------------------------------------------
    def _get_dlp_client(self):
        if self._dlp_client is None:
            try:
                from google.cloud import dlp_v2

                self._dlp_client = dlp_v2.DlpServiceClient()
            except Exception as exc:
                logger.error("Failed to initialize DLP client: %s", exc)
                raise
        return self._dlp_client

    def _resolve_project_id(self) -> str:
        """Resolve GCP project ID from Valves or environment."""
        if self.valves.gcp_project_id:
            return self.valves.gcp_project_id
        # Cloud Run sets GOOGLE_CLOUD_PROJECT automatically
        project = os.environ.get(
            "GOOGLE_CLOUD_PROJECT",
            os.environ.get("GCP_PROJECT", os.environ.get("GCLOUD_PROJECT", "")),
        )
        if not project:
            raise ValueError(
                "GCP project ID not found. Set it in Valves or "
                "GOOGLE_CLOUD_PROJECT environment variable."
            )
        return project

    def _get_info_types(self) -> list[dict]:
        """Build InfoType list based on sensitivity preset."""
        level = self.valves.sensitivity.lower()
        names = SENSITIVITY_PRESETS.get(level, SENSITIVITY_PRESETS["medium"])
        return [{"name": n} for n in names]

    def _get_likelihood_enum(self) -> int:
        """Convert string likelihood to DLP API enum value."""
        from google.cloud.dlp_v2 import types

        mapping = {
            "POSSIBLE": types.Likelihood.POSSIBLE,
            "LIKELY": types.Likelihood.LIKELY,
            "VERY_LIKELY": types.Likelihood.VERY_LIKELY,
        }
        return mapping.get(self.valves.min_likelihood.upper(), types.Likelihood.LIKELY)

    def _build_inspect_config(self) -> dict:
        """Build the DLP InspectConfig."""
        config = {
            "info_types": self._get_info_types(),
            "min_likelihood": self._get_likelihood_enum(),
            "include_quote": True,
            "limits": {
                "max_findings_per_request": 50,
            },
        }

        # Allow-list exclusion rule
        allow_words = [
            w.strip() for w in self.valves.allow_list_words.split(",") if w.strip()
        ]
        if allow_words:
            config["rule_set"] = [
                {
                    "info_types": self._get_info_types(),
                    "rules": [
                        {
                            "exclusion_rule": {
                                "dictionary": {"word_list": {"words": allow_words}},
                                "matching_type": "MATCHING_TYPE_FULL_MATCH",
                            }
                        }
                    ],
                }
            ]

        return config

    def _build_deidentify_config(self) -> dict:
        """Build deidentification config — replaces PII with [INFO_TYPE]."""
        return {
            "info_type_transformations": {
                "transformations": [
                    {"primitive_transformation": {"replace_with_info_type_config": {}}}
                ]
            }
        }

    # ------------------------------------------------------------------
    # Core scanning logic
    # ------------------------------------------------------------------
    def _scan_text(self, text: str) -> dict:
        """
        Scan text with GCP DLP API.

        Returns:
            {
                "has_findings": bool,
                "findings": [{"info_type": str, "quote": str, "likelihood": str}],
                "redacted_text": str | None,
            }
        """
        if not text or not text.strip():
            return {"has_findings": False, "findings": [], "redacted_text": None}

        try:
            client = self._get_dlp_client()
            project_id = self._resolve_project_id()
            parent = f"projects/{project_id}/locations/global"

            inspect_config = self._build_inspect_config()
            item = {"value": text}

            # Inspect for findings
            inspect_response = client.inspect_content(
                request={
                    "parent": parent,
                    "inspect_config": inspect_config,
                    "item": item,
                }
            )

            findings = []
            for finding in inspect_response.result.findings:
                findings.append(
                    {
                        "info_type": finding.info_type.name,
                        "quote": finding.quote if finding.quote else "",
                        "likelihood": finding.likelihood.name,
                    }
                )

            has_findings = len(findings) > 0
            redacted_text = None

            # Deidentify if action is redact and there are findings
            if has_findings and self.valves.action == "redact":
                deidentify_response = client.deidentify_content(
                    request={
                        "parent": parent,
                        "deidentify_config": self._build_deidentify_config(),
                        "inspect_config": inspect_config,
                        "item": item,
                    }
                )
                redacted_text = deidentify_response.item.value

            return {
                "has_findings": has_findings,
                "findings": findings,
                "redacted_text": redacted_text,
            }

        except Exception as exc:
            # Circuit breaker: if DLP API fails, pass through with warning
            logger.warning(
                "DLP API call failed (passing through): %s", exc, exc_info=True
            )
            return {"has_findings": False, "findings": [], "redacted_text": None}

    def _log_findings(
        self, direction: str, findings: list[dict], user: Optional[dict] = None
    ):
        """Log DLP findings for audit trail."""
        user_id = user.get("id", "unknown") if user else "unknown"
        user_email = user.get("email", "unknown") if user else "unknown"
        for f in findings:
            logger.warning(
                "DLP_FINDING | direction=%s | user_id=%s | user_email=%s | "
                "info_type=%s | likelihood=%s | quote_preview=%s",
                direction,
                user_id,
                user_email,
                f["info_type"],
                f["likelihood"],
                f["quote"][:20] + "..." if len(f["quote"]) > 20 else f["quote"],
            )

    # ------------------------------------------------------------------
    # Pipeline filter methods
    # ------------------------------------------------------------------
    async def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Scan user message BEFORE it reaches the LLM."""
        if not self.valves.enabled or not self.valves.scan_inbound:
            return body

        messages = body.get("messages", [])
        if not messages:
            return body

        # Scan the last user message
        last_msg = messages[-1]
        if last_msg.get("role") != "user":
            return body

        content = last_msg.get("content", "")
        if not isinstance(content, str):
            # Multi-modal content (images etc) — skip text scanning
            return body

        result = self._scan_text(content)

        if result["has_findings"]:
            self._log_findings("inbound", result["findings"], __user__)

            action = self.valves.action.lower()

            if action == "block":
                types_found = ", ".join(set(f["info_type"] for f in result["findings"]))
                raise Exception(
                    f"⚠️ Message blocked by DLP filter. "
                    f"Sensitive data detected: {types_found}. "
                    f"Please remove personal information and try again."
                )

            elif action == "redact" and result["redacted_text"]:
                messages[-1]["content"] = result["redacted_text"]
                body["messages"] = messages
                logger.info(
                    "DLP: Redacted %d finding(s) from inbound message",
                    len(result["findings"]),
                )

            # action == "log_only": findings logged above, pass through

        return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Scan LLM response BEFORE it's sent to the user."""
        if not self.valves.enabled or not self.valves.scan_outbound:
            return body

        messages = body.get("messages", [])
        if not messages:
            return body

        # Scan the last assistant message
        last_msg = messages[-1]
        if last_msg.get("role") != "assistant":
            return body

        content = last_msg.get("content", "")
        if not isinstance(content, str):
            return body

        result = self._scan_text(content)

        if result["has_findings"]:
            self._log_findings("outbound", result["findings"], __user__)

            action = self.valves.action.lower()

            if action in ("redact", "block") and result["redacted_text"]:
                # For outbound, both redact and block modes redact
                # (we don't block LLM responses, just redact them)
                messages[-1]["content"] = result["redacted_text"]
                body["messages"] = messages
                logger.info(
                    "DLP: Redacted %d finding(s) from outbound response",
                    len(result["findings"]),
                )

            # action == "log_only": findings logged above, pass through

        return body
