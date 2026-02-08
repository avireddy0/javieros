# Phase 1.4 — DLP Integration Plan

## Overview

Add enterprise-grade Data Loss Prevention (DLP) to JavierOS using **GCP Sensitive Data Protection API** (formerly Cloud DLP). This is the #1 differentiator vs OpenClaw, which has zero DLP capabilities.

## Architecture Decision

### Option A: Open WebUI Pipeline Filter (CHOSEN ✅)

Implement DLP as an **Open WebUI Pipeline filter function** that runs inside the existing `pipelines` sidecar container. This is the canonical way to intercept messages in Open WebUI.

**Why Pipeline filter wins over sidecar:**
- **Zero network hops** — filter runs in-process with the LLM pipeline
- **Native integration** — Open WebUI discovers it automatically via pipeline protocol
- **Admin toggleable** — users can enable/disable DLP from the Open WebUI admin panel
- **No new container** — saves resources, stays within Cloud Run limits
- **Captures both input AND output** — inlet() for user messages, outlet() for LLM responses

### Option B: Separate DLP Sidecar (REJECTED ❌)

Would require a 6th container, network routing, and custom Dockerfile patching. Overkill for message scanning that Open WebUI's pipeline system was designed for.

## Design

### Component: `dlp_filter.py` (Pipeline Filter)

```
User message → [inlet() DLP scan] → LLM → [outlet() DLP scan] → User
                     ↓                              ↓
              GCP SDP API                    GCP SDP API
              inspect_content()              inspect_content()
                     ↓                              ↓
              Block/Redact/Log               Redact/Log
```

### Pipeline Filter Spec

```python
class Pipeline:
    """Enterprise DLP Filter — GCP Sensitive Data Protection"""

    class Valves(BaseModel):
        """Admin-configurable settings (visible in Open WebUI admin panel)"""
        enabled: bool = True
        gcp_project_id: str = ""
        scan_inbound: bool = True      # Scan user messages
        scan_outbound: bool = True     # Scan LLM responses
        action: str = "redact"         # "redact" | "block" | "log_only"
        sensitivity: str = "medium"    # "low" | "medium" | "high"
        # InfoTypes to detect
        detect_ssn: bool = True
        detect_credit_card: bool = True
        detect_email: bool = True
        detect_phone: bool = True
        detect_iban: bool = True
        detect_passport: bool = False
        # Custom patterns
        custom_regex_patterns: str = ""  # JSON array of {name, regex} pairs
        # Allow-list (reduce false positives)
        allow_list_words: str = ""      # Comma-separated words to ignore

    def inlet(self, body, user):
        """Scan inbound user messages for PII before LLM processing"""
        ...

    def outlet(self, body, user):
        """Scan outbound LLM responses for PII before delivery"""
        ...
```

### GCP SDP API Usage

**InfoType mapping by sensitivity level:**

| Sensitivity | InfoTypes |
|------------|-----------|
| **Low** | CREDIT_CARD_NUMBER, US_SOCIAL_SECURITY_NUMBER |
| **Medium** | Low + EMAIL_ADDRESS, PHONE_NUMBER, US_PASSPORT, IBAN_CODE, PERSON_NAME |
| **High** | Medium + STREET_ADDRESS, DATE_OF_BIRTH, IP_ADDRESS, MAC_ADDRESS, US_DRIVERS_LICENSE_NUMBER, US_BANK_ROUTING_MICR |

**Actions:**
- `log_only` — Detect PII, log findings to structured log, pass message through unchanged
- `redact` — Replace PII with `[REDACTED:TYPE]` tokens (e.g., `[REDACTED:SSN]`)
- `block` — Reject the message entirely with a user-friendly error

**Deidentification strategy:** Use `CharacterMaskConfig` for partial masking (e.g., `***-**-1234`) or `ReplaceWithInfoTypeConfig` for full replacement (e.g., `[CREDIT_CARD_NUMBER]`).

### Latency Budget

| Operation | Expected Latency | Strategy |
|-----------|-----------------|----------|
| Inlet scan (user msg) | 50-150ms | Sync (blocking) — must complete before LLM call |
| Outlet scan (LLM resp) | 100-300ms | Async with streaming passthrough for long responses |
| GCP SDP API per call | 30-100ms | Single API call per message, batch all content |

**Optimization:** Cache inspection templates server-side. Reuse `InspectTemplate` objects across requests.

## Implementation Plan

### Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `servers/dlp/dlp_filter.py` | **CREATE** | Pipeline filter with GCP SDP integration |
| `servers/dlp/requirements.txt` | **CREATE** | `google-cloud-dlp>=3.0.0` |
| `servers/dlp/Dockerfile` | **CREATE** | Lightweight Python container for pipeline |
| `servers/dlp/AGENTS.md` | **CREATE** | Documentation per project convention |
| `service.yaml` | **MODIFY** | Add DLP pipeline container as sidecar #6 |
| `docker-compose.yml` | **MODIFY** | Add dlp-pipeline service for local dev |
| `OPENCLAW_PARITY.md` | **MODIFY** | Mark Phase 1.4 as complete |

**Wait — Revised approach after further analysis:**

Actually, Open WebUI's pipeline system works differently than a simple sidecar. The `pipelines` container at port 9099 is the **pipeline host**. We need to add our DLP filter TO that pipeline host, not create a separate container.

### Revised Architecture: DLP Filter in Pipelines Container

The `pipelines` sidecar (port 9099) already runs Open WebUI's pipeline framework. We add our DLP filter as a **mounted pipeline function** that the pipelines container auto-discovers.

**Two approaches:**
1. **Build a custom pipelines image** with DLP filter baked in
2. **Mount via volume** — place filter in a GCS-backed volume that pipelines reads at startup

**Chosen: Option 1 — Custom pipelines image.** More reliable, works with Cloud Run.

### Final File Plan

| File | Action | Description |
|------|--------|-------------|
| `pipelines/dlp_filter.py` | **CREATE** | Open WebUI pipeline filter function |
| `pipelines/Dockerfile` | **CREATE** | Extended pipelines image with google-cloud-dlp |
| `pipelines/requirements.txt` | **CREATE** | Additional deps for DLP |
| `service.yaml` | **MODIFY** | Update pipelines container image reference |
| `docker-compose.yml` | **MODIFY** | Update pipelines build context |
| `OPENCLAW_PARITY.md` | **MODIFY** | Mark Phase 1.4 as complete |

## GCP Setup Required

1. ✅ **DLP API enabled** (`dlp.googleapis.com` — already done)
2. **IAM binding** — SA needs `roles/dlp.user`:
   ```bash
   gcloud projects add-iam-policy-binding flow-os-1769675656 \
     --member="serviceAccount:open-webui-sa@flow-os-1769675656.iam.gserviceaccount.com" \
     --role="roles/dlp.user"
   ```
3. **No API key/secret needed** — uses default SA credentials on Cloud Run

## Pricing Estimate

| Operation | Free Tier | Beyond Free |
|-----------|-----------|-------------|
| Content inspection | 1 GB/month free | $1.00/GB |
| Content deidentification | 1 GB/month free | $2.00/GB |

For a typical enterprise chat deployment (~10K messages/day, ~500 bytes avg), monthly data ≈ 150 MB. **Well within free tier.**

## Success Criteria

1. ✅ All user messages scanned for PII before LLM processing
2. ✅ All LLM responses scanned for PII before delivery
3. ✅ Admin can toggle DLP on/off from Open WebUI settings
4. ✅ Admin can configure sensitivity level and action (redact/block/log)
5. ✅ False positives manageable via allow-list
6. ✅ Latency impact < 200ms per message
7. ✅ Structured logging of all DLP findings
8. ✅ Works with all LLM providers (Anthropic, OpenAI, etc.)

## Risk Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| False positives on names/addresses | High | Allow-list, configurable sensitivity, log_only mode |
| Latency impact on chat UX | Medium | Template caching, async outlet scanning |
| DLP API outage blocks all chat | Low | Circuit breaker — if DLP fails, pass through with log warning |
| Cost overrun | Very Low | 150 MB/month << 1 GB free tier |
