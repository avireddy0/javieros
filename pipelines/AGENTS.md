# PIPELINES

## OVERVIEW
Optional AI pipelines (Anthropic/Gemini) and helper tooling; not required for core Open WebUI UX.

## WHERE TO LOOK
| Task | Location | Notes |
|------|----------|-------|
| Anthropic pipeline | `pipelines/anthropic_pipeline.py` | Tool-enabled loop |
| Gemini pipeline | `pipelines/gemini_pipeline.py` | Vertex AI path |
| Open WebUI API helper | `pipelines/openwebui_api.py` | Proxy to Open WebUI |
| WhatsApp tool wrapper | `pipelines/tools/whatsapp.py` | Disabled by default |
| Shared helpers | `pipelines/common.py` | Message normalization |
| DLP filter | `pipelines/dlp_filter.py` | GCP Sensitive Data Protection |
| Dependencies | `pipelines/requirements.txt` | Python deps |

## CONVENTIONS
- Pipelines are optional; production UX should rely on Open WebUI tool servers.
- WhatsApp pipeline tools are gated via `WHATSAPP_PIPELINE_ENABLED`.

## ANTI-PATTERNS
- Do not expose pipeline WhatsApp tools to end users.
