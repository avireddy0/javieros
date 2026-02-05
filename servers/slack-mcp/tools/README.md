# Slack MCP Tools

Modular implementation of Slack API operations organized by function.

## Structure

```
tools/
├── __init__.py       # Module exports
├── channels.py       # Channel operations
├── messages.py       # Message operations
└── users.py          # User operations
```

## Modules

### channels.py

Channel listing, information, and history retrieval:

- **list_channels()** - List workspace channels with filtering
- **get_channel_info()** - Get detailed channel information
- **get_channel_history()** - Retrieve message history from a channel

### messages.py

Message sending, searching, and thread interactions:

- **send_message()** - Send messages to channels or DMs
- **search_messages()** - Search messages across workspace
- **reply_to_thread()** - Reply to existing message threads

### users.py

User listing and profile information:

- **list_users()** - List workspace members with filtering
- **get_user_info()** - Get detailed user profile

## Usage

All functions accept an authenticated `slack_sdk.WebClient` as the first parameter:

```python
from slack_sdk import WebClient
from tools import list_channels, send_message

client = WebClient(token="xoxb-...")

# List channels
result = list_channels(client, types="public_channel")

# Send message
result = send_message(client, channel="C1234567890", text="Hello!")
```

## Error Handling

All functions return a dict with:
- `ok: bool` - Success/failure status
- `error: str` - Error message (if ok=False)
- Additional data fields (if ok=True)

Example:
```python
result = send_message(client, channel="invalid", text="test")
if not result["ok"]:
    print(f"Error: {result['error']}")
else:
    print(f"Message sent: {result['ts']}")
```

## Integration

These tools are designed to be called from FastAPI endpoints in `main.py`, which handle:
- Authentication via GCS token storage
- Request validation
- HTTP response formatting

See `main.py` for endpoint implementations that use these tools.
