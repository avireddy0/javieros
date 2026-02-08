# JavierOS QA Report

**Date**: 2026-02-07
**Tester**: Automated QA via Playwright MCP
**Environment**: Production (Cloud Run)
**App URL**: `https://open-webui-a4bmliuj7q-uc.a.run.app`
**App Version**: Open WebUI v0.7.2
**Test Account**: `avi@envsn.com`

---

## Executive Summary

Full end-to-end QA testing was performed against the production JavierOS deployment on Google Cloud Run. 16 test cases were executed covering authentication, chat/LLM, tool integrations, admin settings, cron system, and security.

**Results**: 10 PASS | 3 FAIL | 3 PARTIAL

**Critical Issues**: 3 P1 bugs blocking core tool integrations (Google Workspace, Slack OAuth, Pipelines sidecar).

---

## Test Results

| # | Test Case | Result | Details |
|---|-----------|--------|---------|
| QA-1 | App Loads | ✅ PASS | SPA renders after ~30s cold start |
| QA-2 | Login | ✅ PASS | `avi@envsn.com` / credentials — "You're now logged in" toast |
| QA-3 | Chat / LLM Response | ✅ PASS | GPT 5.2 answered "2+2=4", auto-titled conversation, follow-up suggestions rendered |
| QA-4 | Model Selection | ✅ PASS | GPT 5.2 shown as default model |
| QA-5 | WhatsApp QR Modal | ⚠️ PARTIAL | Sidecar connected but `/api/v1/whatsapp/status` returns `{"detail":"Unauthorized"}` — token not forwarded |
| QA-6 | Available Tools Dialog | ✅ PASS | 4 tool servers listed in dialog |
| QA-7a | Envision MCP Tool | ✅ PASS | "Connection successful" |
| QA-7b | WhatsApp Tools | ✅ PASS | "Connection successful" (OpenAPI localhost:8000) |
| QA-7c | Google Workspace MCP | ❌ FAIL | URL mismatch in config — points to wrong Cloud Run URL |
| QA-7d | Slack OAuth MCP | ❌ FAIL | Connection verify fails despite URL appearing correct |
| QA-8a | Admin Settings | ✅ PASS | All 13 tabs visible, version 0.7.2 confirmed |
| QA-8b | Pipelines | ❌ FAIL | "Pipelines Not Detected" — sidecar on port 9099 not communicating |
| QA-8c | Web Search | ✅ PASS | Perplexity Search configured with API key |
| QA-9 | Cron Proxy Health | ✅ PASS | Returns `{"status":"ok","proxy":"cron","target":"http://localhost:8003"}` |
| QA-10 | Security Basics | ⚠️ PARTIAL | Protected endpoints return 401 ✅, but `/api/chat/completions` returns 200 unauthenticated ⚠️, no CORS/security headers ⚠️ |

---

## Bug Catalog

### P1 — Critical (Blocking Functionality)

#### BUG-1: Google Workspace MCP Connection Failure

- **Severity**: P1
- **Component**: External Tool Configuration (Admin Settings)
- **Symptom**: "Connection failed" when verifying Google Workspace MCP tool
- **Root Cause**: URL in admin config is `https://workspace-mcp-210087613384.us-central1.run.app/mcp` but actual Cloud Run service URL is `https://workspace-mcp-a4bmliuj7q-uc.a.run.app/mcp`
- **Fix**: Update URL in Admin → Settings → Tools → Google Workspace to correct URL
- **Fix Type**: Browser admin config change

#### BUG-2: Slack OAuth MCP Connection Failure

- **Severity**: P1
- **Component**: External Tool Configuration / Slack MCP Service
- **Symptom**: "Connection failed" when verifying Slack OAuth MCP tool
- **Root Cause**: Needs investigation — service health check, OAuth 2.1 handshake validation, or CORS/auth issue
- **Fix**: Debug Slack MCP service health, verify OAuth configuration
- **Fix Type**: Service debugging + possible config update

#### BUG-3: Pipelines Sidecar Not Detected

- **Severity**: P1
- **Component**: Pipelines sidecar container
- **Symptom**: Admin → Pipelines shows "Pipelines Not Detected"
- **Root Cause**: Port 9099 sidecar not communicating with Open WebUI — likely `PIPELINES_URL` env var missing or misconfigured
- **Fix**: Verify `PIPELINES_URL=http://localhost:9099` in `service.yaml`, check sidecar health
- **Fix Type**: Configuration fix in `service.yaml` + redeployment

### P2 — High (Degraded Functionality)

#### BUG-4: WhatsApp QR Endpoints Return Unauthorized

- **Severity**: P2
- **Component**: `webui/whatsapp_qr.py`
- **Symptom**: `/api/v1/whatsapp/status` returns `{"detail":"Unauthorized"}`
- **Root Cause**: `whatsapp_qr.py` not injecting `whatsapp-api-token` when forwarding requests to the WhatsApp API sidecar on port 8000
- **Fix**: Add token injection in `whatsapp_qr.py` (same pattern as `cron_proxy.py` CRON_TOKEN injection)
- **Fix Type**: Code fix → rebuild → redeploy

#### BUG-5: WebUI URL in Admin Settings

- **Severity**: P2 → Downgraded to INFO
- **Component**: Admin Settings
- **Symptom**: URL shown as `open-webui-a4bmliuj7q-uc.a.run.app`
- **Status**: Verified CORRECT via `gcloud run services list` — this is NOT a bug
- **Fix**: None required

### P3 — Medium (Security Hardening)

#### BUG-6: JWT Expiration Set to -1 (No Expiration)

- **Severity**: P3
- **Component**: Admin → Settings → General
- **Symptom**: JWT tokens never expire — security risk for session hijacking
- **Fix**: Set JWT Expiration to `30d` (30 days) in admin settings
- **Fix Type**: Browser admin config change

#### BUG-7: Missing Security Response Headers

- **Severity**: P3
- **Component**: HTTP response headers
- **Symptom**: No `X-Frame-Options`, `X-Content-Type-Options`, `Strict-Transport-Security`, or `Content-Security-Policy` headers
- **Fix**: Add security headers middleware or configure in reverse proxy / `Dockerfile` CMD
- **Fix Type**: Code fix → rebuild → redeploy

#### BUG-8: Cron Proxy Lacks OIDC Caller Validation

- **Severity**: P3
- **Component**: `webui/cron_proxy.py`
- **Symptom**: Cron proxy accepts any request with valid CRON_TOKEN — doesn't validate that caller is Cloud Scheduler via OIDC
- **Fix**: Add OIDC token validation in `cron_proxy.py`
- **Fix Type**: Code fix → rebuild → redeploy

### INFO — Low Priority

#### BUG-9: SPA Cold Start ~30 Seconds

- **Severity**: INFO
- **Component**: Cloud Run infrastructure
- **Symptom**: First load takes ~30 seconds on cold start
- **Status**: Normal Cloud Run behavior with min-instances=0
- **Fix**: Set `min-instances: 1` in `service.yaml` if budget allows (increases cost)

#### BUG-10: Console Warning — MCP Server 'google-workspace' Failed

- **Severity**: INFO
- **Component**: Browser console
- **Symptom**: Console warning about google-workspace MCP connection failure
- **Status**: Same root cause as BUG-1 — will be resolved when URL is corrected

---

## Remediation Plan

### Phase 1: P1 Bug Fixes (Immediate)

1. **BUG-1**: Update Google Workspace MCP URL in admin settings via browser
2. **BUG-2**: Debug Slack MCP service health, fix OAuth configuration
3. **BUG-3**: Add/fix `PIPELINES_URL` env var in `service.yaml` and `docker-compose.yml`

### Phase 2: P2 Bug Fixes

4. **BUG-4**: Code fix in `webui/whatsapp_qr.py` — add `WHATSAPP_API_TOKEN` injection

### Phase 3: P3 Security Hardening

5. **BUG-6**: Set JWT expiration to 30d via admin settings
6. **BUG-7**: Add security response headers
7. **BUG-8**: Add OIDC validation to cron proxy

### Phase 4: Rebuild & Redeploy

8. Rebuild Docker images for code changes (BUG-4, BUG-7, BUG-8)
9. Push to Artifact Registry
10. Deploy to Cloud Run
11. Re-run QA on all fixed items

---

## Cloud Run Service Health (At Time of Testing)

| Service | URL | Status |
|---------|-----|--------|
| open-webui | `https://open-webui-a4bmliuj7q-uc.a.run.app` | ✅ Active |
| slack-mcp | `https://slack-mcp-a4bmliuj7q-uc.a.run.app` | ✅ Active |
| utilities-mcp | `https://utilities-mcp-a4bmliuj7q-uc.a.run.app` | ✅ Active |
| whatsapp-api | `https://whatsapp-api-a4bmliuj7q-uc.a.run.app` | ✅ Active |
| whatsapp-bridge | `https://whatsapp-bridge-a4bmliuj7q-uc.a.run.app` | ✅ Active |
| workspace-mcp | `https://workspace-mcp-a4bmliuj7q-uc.a.run.app` | ✅ Active |

## Cloud Scheduler Jobs (At Time of Testing)

| Job | Schedule | Status |
|-----|----------|--------|
| morning-briefing | `0 7 * * *` | ✅ Enabled |
| inbox-summary | `0 18 * * *` | ✅ Enabled |
| weekly-report | `0 8 * * 1` | ✅ Enabled |
| heartbeat-check | `*/30 * * * *` | ✅ Enabled |

---

*Report generated from automated Playwright MCP testing session.*
