# Slack OAuth 2.1 MCP Server

FastAPI-based MCP server implementing Slack OAuth 2.1 flow with GCS token storage.

## Deployment Info

- **Project**: flow-os-1769675656
- **Region**: us-central1
- **Service URL**: https://slack-mcp-210087613384.us-central1.run.app
- **Image**: us-central1-docker.pkg.dev/flow-os-1769675656/javieros/slack-mcp:v1
- **GCS Bucket**: gs://slack-mcp-creds-flow-os

## Setup

### 1. Configure Slack OAuth App

1. Go to https://api.slack.com/apps
2. Create a new app or select an existing one
3. Navigate to **OAuth & Permissions**
4. Add the following **Redirect URL**:
   ```
   https://slack-mcp-210087613384.us-central1.run.app/oauth2callback
   ```
5. Add the following **Bot Token Scopes**:
   - `channels:read`
   - `channels:history`
   - `chat:write`
   - `users:read`
   - `users:read.email`
   - `search:read`
   - `groups:read`
   - `groups:history`
   - `im:read`
   - `im:history`
   - `mpim:read`
   - `mpim:history`
6. Copy the **Client ID** and **Client Secret**

### 2. Update Secrets

Update the placeholder secrets with your real Slack OAuth credentials:

```bash
# Update Client ID
echo -n "YOUR_SLACK_CLIENT_ID" | gcloud secrets versions add slack-oauth-client-id \
  --project=flow-os-1769675656 --data-file=-

# Update Client Secret
echo -n "YOUR_SLACK_CLIENT_SECRET" | gcloud secrets versions add slack-oauth-client-secret \
  --project=flow-os-1769675656 --data-file=-

# Get the API token (for authenticating tool calls)
gcloud secrets versions access latest --secret=slack-mcp-api-token --project=flow-os-1769675656
```

### 3. Redeploy Service

After updating secrets, redeploy the service:

```bash
cd /Users/avireddy/GitHub/javieros/servers/slack-mcp
gcloud run services replace service.yaml --project=flow-os-1769675656 --region=us-central1
```

## Usage

### OAuth Flow

1. **Start Authorization**:
   ```
   GET https://slack-mcp-210087613384.us-central1.run.app/authorize?user_id=YOUR_USER_ID
   ```
   - Redirects to Slack OAuth page
   - User approves and is redirected back to `/oauth2callback`
   - Tokens are saved to GCS at `gs://slack-mcp-creds-flow-os/slack-tokens/{user_id}.json`

2. **Check Auth Status**:
   ```bash
   curl -H "Authorization: Bearer YOUR_API_TOKEN" \
     "https://slack-mcp-210087613384.us-central1.run.app/auth_status?user_id=YOUR_USER_ID"
   ```

### MCP Tools

All tool endpoints require:
- `Authorization: Bearer YOUR_API_TOKEN` header
- `user_id` in request body

#### Send Message
```bash
curl -X POST https://slack-mcp-210087613384.us-central1.run.app/send_message \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "YOUR_USER_ID",
    "channel": "#general",
    "text": "Hello from Slack MCP!"
  }'
```

#### List Channels
```bash
curl -X POST https://slack-mcp-210087613384.us-central1.run.app/list_channels \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "YOUR_USER_ID",
    "types": "public_channel,private_channel",
    "limit": 100
  }'
```

#### Search Messages
```bash
curl -X POST https://slack-mcp-210087613384.us-central1.run.app/search_messages \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "YOUR_USER_ID",
    "query": "project update",
    "count": 20
  }'
```

#### Get Users
```bash
curl -X POST https://slack-mcp-210087613384.us-central1.run.app/get_users \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "YOUR_USER_ID",
    "limit": 100
  }'
```

#### Get Channel History
```bash
curl -X POST https://slack-mcp-210087613384.us-central1.run.app/get_channel_history \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "YOUR_USER_ID",
    "channel": "C1234567890",
    "limit": 100
  }'
```

## Architecture

### OAuth 2.1 Flow
1. User clicks authorize link with `user_id` parameter
2. Server generates random `state` token and redirects to Slack
3. User approves access on Slack
4. Slack redirects back to `/oauth2callback` with `code` and `state`
5. Server validates `state`, exchanges `code` for access token
6. Access token is saved to GCS bucket: `slack-tokens/{user_id}.json`

### Token Storage
Tokens are stored in GCS at: `gs://slack-mcp-creds-flow-os/slack-tokens/{user_id}.json`

Format:
```json
{
  "access_token": "xoxb-...",
  "token_type": "bearer",
  "scope": "channels:read,chat:write,...",
  "team_id": "T1234567890",
  "team_name": "My Workspace",
  "authed_user": {...},
  "created_at": "2026-02-05T01:30:00"
}
```

### Security
- API token required for all tool endpoints
- OAuth state parameter prevents CSRF attacks
- Service account has minimal GCS and Secret Manager permissions
- Tokens stored per-user for multi-tenant support

## Development

### Local Testing
```bash
cd /Users/avireddy/GitHub/javieros/servers/slack-mcp

# Set environment variables
export SLACK_OAUTH_CLIENT_ID="your_client_id"
export SLACK_OAUTH_CLIENT_SECRET="your_client_secret"
export SLACK_REDIRECT_URI="http://localhost:8080/oauth2callback"
export SLACK_CREDS_BUCKET="slack-mcp-creds-flow-os"
export SLACK_API_TOKEN="your_test_token"

# Run locally
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

### Rebuild and Redeploy
```bash
cd /Users/avireddy/GitHub/javieros/servers/slack-mcp

# Build new image
gcloud builds submit --tag us-central1-docker.pkg.dev/flow-os-1769675656/javieros/slack-mcp:v2 \
  --project=flow-os-1769675656

# Update service.yaml with new image tag
# Then redeploy
gcloud run services replace service.yaml --project=flow-os-1769675656 --region=us-central1
```

## Secrets

| Secret | Description |
|--------|-------------|
| `slack-oauth-client-id` | Slack OAuth Client ID |
| `slack-oauth-client-secret` | Slack OAuth Client Secret |
| `slack-mcp-api-token` | API token for authenticating tool calls |

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/authorize` | GET | Start OAuth flow (param: `user_id`) |
| `/oauth2callback` | GET | OAuth callback (params: `code`, `state`) |
| `/auth_status` | GET | Check auth status (param: `user_id`) |
| `/send_message` | POST | Send Slack message |
| `/list_channels` | POST | List channels |
| `/search_messages` | POST | Search messages |
| `/get_users` | POST | Get workspace users |
| `/get_channel_history` | POST | Get channel message history |

## Next Steps

1. **Configure Slack App**: Update OAuth redirect URL and get real credentials
2. **Update Secrets**: Replace placeholder values with real Slack credentials
3. **Test OAuth Flow**: Visit `/authorize?user_id=test` to test the flow
4. **Integrate with MCP**: Add this server to your MCP configuration
