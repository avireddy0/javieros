# Per-User WhatsApp Architecture Plan

**Created**: 2026-02-09
**Status**: Planning
**Impact**: WhatsApp tool only (Slack and Google Workspace unaffected)

## Problem Statement

The current WhatsApp integration uses a single shared session:
- One QR code scan = one WhatsApp account for ALL users
- Any logged-in user can read/send messages from the linked account
- This is a security risk for multi-user deployments

## Current State (Shared)

```
┌─────────────────────────────────────────┐
│ Cloud Run: open-webui                   │
│                                         │
│  ┌──────────┐    ┌──────────────────┐  │
│  │ WhatsApp │───▶│ WhatsApp Bridge  │  │
│  │   API    │    │ (1 session)      │  │
│  └──────────┘    └──────────────────┘  │
│                          │              │
│                          ▼              │
│               GCS: /session/            │
│               (shared by all users)     │
└─────────────────────────────────────────┘
```

## Target State (Per-User)

```
┌─────────────────────────────────────────┐
│ Cloud Run: open-webui                   │
│                                         │
│  ┌──────────┐    ┌──────────────────┐  │
│  │ WhatsApp │───▶│ WhatsApp Bridge  │  │
│  │   API    │    │ (multi-session)  │  │
│  │ +user_id │    │                  │  │
│  └──────────┘    └──────────────────┘  │
│                          │              │
│                          ▼              │
│               GCS: /sessions/{user_id}/ │
│               (isolated per user)       │
└─────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: Bridge Multi-Session Support
**File**: `whatsapp-bridge/index.js`

| Task | Description |
|------|-------------|
| 1.1 | Refactor from single `sock` variable to `Map<userId, WhatsAppSocket>` |
| 1.2 | Change session directory from `/data/whatsapp-session` to `/data/whatsapp-sessions/{userId}` |
| 1.3 | Update `makeWASocket` calls to use per-user auth state |
| 1.4 | Add `userId` parameter to all endpoints (`/qr`, `/status`, `/send`, `/messages`) |
| 1.5 | Implement session lifecycle: create on first QR request, cleanup on disconnect |
| 1.6 | Add `GET /sessions` endpoint to list active user sessions (admin only) |
| 1.7 | Add `DELETE /sessions/{userId}` to force-disconnect a user's WhatsApp |

### Phase 2: API User Context Propagation
**File**: `servers/whatsapp/main.py`

| Task | Description |
|------|-------------|
| 2.1 | Add `X-User-ID` header requirement to all endpoints |
| 2.2 | Pass `user_id` to bridge in all `_bridge_request()` calls |
| 2.3 | Update QR session tokens to be scoped per user |
| 2.4 | Update `/status` to return user-specific connection state |
| 2.5 | Add rate limiting per user (prevent abuse) |

### Phase 3: Open WebUI Proxy Updates
**File**: `webui/whatsapp_qr.py`

| Task | Description |
|------|-------------|
| 3.1 | Extract `user.id` from authenticated session |
| 3.2 | Add `X-User-ID: {user.id}` header to all proxy requests |
| 3.3 | Update QR modal to show user-specific status |
| 3.4 | Add "Disconnect WhatsApp" button for users to unlink their account |

### Phase 4: Message History Isolation
**File**: `whatsapp-bridge/index.js`

| Task | Description |
|------|-------------|
| 4.1 | Change `messageHistory` from single object to `Map<userId, History>` |
| 4.2 | Store history as `/data/whatsapp-sessions/{userId}/history.json` |
| 4.3 | Load/save history per user on connect/disconnect |
| 4.4 | Add history size limits per user (prevent storage abuse) |

### Phase 5: Storage Architecture Fix
**Files**: `service.yaml`, GCS bucket config

| Task | Description |
|------|-------------|
| 5.1 | Keep GCS FUSE but organize by user subdirectories |
| 5.2 | OR migrate to Firestore for session metadata (more reliable than GCS FUSE) |
| 5.3 | Implement write locking to prevent concurrent modification errors |
| 5.4 | Add session cleanup job for inactive users (>30 days) |

### Phase 6: Admin Controls
**New file**: `webui/whatsapp_admin.py`

| Task | Description |
|------|-------------|
| 6.1 | Admin endpoint to list all connected WhatsApp users |
| 6.2 | Admin ability to force-disconnect any user |
| 6.3 | Usage metrics per user (messages sent/received) |
| 6.4 | Storage quota per user |

---

## Data Model Changes

### Current
```javascript
// Single session
const SESSION_DIR = '/data/whatsapp-session'
let sock = null
let messageHistory = {}
```

### Proposed
```javascript
// Multi-session
const SESSIONS_DIR = '/data/whatsapp-sessions'

class UserSession {
  userId: string
  sock: WhatsAppSocket | null
  qrData: string | null
  isReady: boolean
  messageHistory: Record<string, Message[]>
  lastActivity: Date
}

const sessions = new Map<string, UserSession>()
```

---

## API Changes

| Endpoint | Current | Proposed |
|----------|---------|----------|
| `GET /status` | Global status | `GET /status` with `X-User-ID` header |
| `GET /qr` | Global QR | `GET /qr` with `X-User-ID` header |
| `POST /send` | Send from shared account | Send from user's linked account |
| `POST /messages` | Get shared history | Get user's history only |
| NEW | - | `DELETE /session` - User unlinks their WhatsApp |
| NEW | - | `GET /sessions` - Admin lists all sessions |

---

## Effort Estimate

| Phase | Effort | Priority |
|-------|--------|----------|
| Phase 1: Bridge Multi-Session | 4-6 hours | P0 |
| Phase 2: API User Context | 2-3 hours | P0 |
| Phase 3: WebUI Proxy | 1-2 hours | P0 |
| Phase 4: History Isolation | 2-3 hours | P1 |
| Phase 5: Storage Fix | 3-4 hours | P1 |
| Phase 6: Admin Controls | 2-3 hours | P2 |

**Total**: ~14-21 hours

---

## Migration Path

1. Deploy Phase 1-3 (core multi-session)
2. Existing shared session becomes "legacy"
3. New users get isolated sessions automatically
4. Admin can migrate/delete legacy session
5. Deploy Phase 4-6 (history, storage, admin)

---

## Unaffected Components

The following integrations use OAuth 2.1 and are already per-user:
- **Slack MCP** - Each user authenticates with their own Slack workspace
- **Google Workspace MCP** - Each user authenticates with their own Google account

This plan only affects the WhatsApp tool.

---

## Security Considerations

- User sessions must be cryptographically isolated (no user ID guessing)
- Admin endpoints require admin role verification
- Session tokens should be short-lived and refreshable
- Consider encryption at rest for session files in GCS

---

*Plan created: 2026-02-09*
