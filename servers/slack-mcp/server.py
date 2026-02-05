"""
FastMCP server instance for Slack MCP.
Clean server without GoogleProvider - uses custom OAuth 2.1 for Slack.
"""
import logging

from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Create FastMCP server instance
# Auth is handled via custom OAuth 2.1 endpoints in main.py
server = FastMCP(
    name="slack-mcp",
    instructions="""Slack MCP Server - Access Slack workspace data.

This server provides tools for interacting with Slack:
- List and search channels
- Read channel history
- Send messages and replies
- List users and get user info
- Search messages across the workspace

Authentication is handled via OAuth 2.1 with PKCE.
""",
)
