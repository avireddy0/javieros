# Slack MCP Server - Deployment Summary

**Deployment Date**: 2026-02-05
**Status**: ✅ Successfully Deployed

## Deployment Details

### Service Information
- **Service Name**: slack-mcp
- **Project**: flow-os-1769675656
- **Region**: us-central1
- **Service URL**: https://slack-mcp-210087613384.us-central1.run.app
- **Service Account**: 210087613384-compute@developer.gserviceaccount.com

### Container Image
- **Registry**: Artifact Registry
- **Image**: us-central1-docker.pkg.dev/flow-os-1769675656/javieros/slack-mcp:v1
- **Tag**: v1
- **Build ID**: f69068b9-af17-46b5-a66e-c33a7d1efd97

### Storage
- **GCS Bucket**: gs://slack-mcp-creds-flow-os
- **Location**: us-central1
- **Purpose**: Store user Slack OAuth tokens
- **Path Format**: `slack-tokens/{user_id}.json`

### Secrets Created
| Secret Name | Status | Purpose |
|-------------|--------|---------|
| `slack-oauth-client-id` | ✅ Created | Slack OAuth Client ID |
| `slack-oauth-client-secret` | ✅ Created | Slack OAuth Client Secret |
| `slack-mcp-api-token` | ✅ Created | API authentication token |

**Note**: All secrets currently contain placeholder values and need to be updated with real Slack credentials.

### IAM Permissions
- Service account has `roles/secretmanager.secretAccessor` on all secrets
- Service has public invoker access (`allUsers` with `roles/run.invoker`)

## Files Created

```
/Users/avireddy/GitHub/javieros/servers/slack-mcp/
├── main.py              # FastAPI server with OAuth 2.1 flow
├── requirements.txt     # Python dependencies
├── Dockerfile          # Container definition
├── service.yaml        # Cloud Run service configuration
├── README.md           # Usage documentation
└── DEPLOYMENT.md       # This file
```

## Verification

### Health Check
```bash
curl https://slack-mcp-210087613384.us-central1.run.app/health
# Expected: {"status":"healthy","service":"slack-mcp"}
```

### Test Result
```json
{"status":"healthy","service":"slack-mcp"}
```
✅ Service is responding correctly

## Configuration Required

Before the service can be used for Slack operations:

### 1. Create/Configure Slack App
1. Go to https://api.slack.com/apps
2. Create new app or select existing
3. Add redirect URL: `https://slack-mcp-210087613384.us-central1.run.app/oauth2callback`
4. Add required scopes (see README.md)
5. Copy Client ID and Client Secret

### 2. Update Secrets
```bash
# Update Client ID
echo -n "YOUR_REAL_CLIENT_ID" | gcloud secrets versions add slack-oauth-client-id \
  --project=flow-os-1769675656 --data-file=-

# Update Client Secret
echo -n "YOUR_REAL_CLIENT_SECRET" | gcloud secrets versions add slack-oauth-client-secret \
  --project=flow-os-1769675656 --data-file=-
```

### 3. Get API Token
```bash
gcloud secrets versions access latest \
  --secret=slack-mcp-api-token \
  --project=flow-os-1769675656
```

### 4. Update Redirect URI in service.yaml (if needed)
If the actual service URL differs, update `SLACK_REDIRECT_URI` in `service.yaml` and redeploy.

## MCP Tools Available

Once configured, the following tools are available:

1. **send_message** - Send messages to Slack channels/threads
2. **list_channels** - List workspace channels
3. **search_messages** - Search messages across workspace
4. **get_users** - Get workspace users
5. **get_channel_history** - Get message history from a channel

## OAuth Flow

```
User → /authorize?user_id=X
  ↓
Slack OAuth Page
  ↓
User Approves
  ↓
/oauth2callback?code=...&state=...
  ↓
Exchange code for token
  ↓
Save to gs://slack-mcp-creds-flow-os/slack-tokens/X.json
```

## Monitoring

### View Logs
```bash
gcloud run services logs read slack-mcp \
  --project=flow-os-1769675656 \
  --region=us-central1 \
  --limit=50
```

### View Metrics
```bash
# Open in browser
gcloud run services browse slack-mcp \
  --project=flow-os-1769675656 \
  --region=us-central1
```

Or visit: https://console.cloud.google.com/run/detail/us-central1/slack-mcp?project=flow-os-1769675656

## Redeploy Instructions

### Rebuild Image
```bash
cd /Users/avireddy/GitHub/javieros/servers/slack-mcp
gcloud builds submit --tag us-central1-docker.pkg.dev/flow-os-1769675656/javieros/slack-mcp:v2 \
  --project=flow-os-1769675656
```

### Update service.yaml
Change image tag from `v1` to `v2` in `service.yaml`

### Deploy
```bash
gcloud run services replace service.yaml \
  --project=flow-os-1769675656 \
  --region=us-central1
```

## Resource Configuration

- **CPU**: 2 cores
- **Memory**: 1Gi
- **Max Instances**: 10
- **Min Instances**: 0 (scale to zero)
- **Container Concurrency**: 80
- **Request Timeout**: 300s
- **Startup CPU Boost**: Enabled

## Next Steps

1. ✅ Service deployed and running
2. ⏳ Configure Slack OAuth app
3. ⏳ Update secrets with real credentials
4. ⏳ Test OAuth flow
5. ⏳ Integrate with MCP client

## Support

For issues or questions:
- Check logs: `gcloud run services logs read slack-mcp --project=flow-os-1769675656 --region=us-central1`
- Verify secrets are set correctly
- Ensure Slack app redirect URL matches service URL
- Check service account has proper permissions
