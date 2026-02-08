"""
JavierOS Memory Service — GCS-backed per-user markdown file CRUD.

Each user gets a namespace in gs://javieros-memory/{user_id}/ with:
- SOUL.md      — persistent identity, values, and personality traits
- USER.md      — user preferences, contacts, routines
- MEMORY.md    — long-term knowledge and conversation summaries
- HEARTBEAT.md — proactive reminders and scheduled check-ins
- DAILY_LOG_{date}.md — daily activity logs (append-only)
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import storage
from google.cloud.exceptions import NotFound
from pydantic import BaseModel

logger = logging.getLogger("memory-service")

# ---------------------------------------------------------------------------
# Configuration — hard-fail on missing required env vars
# ---------------------------------------------------------------------------

MEMORY_API_TOKEN = os.environ.get("MEMORY_API_TOKEN")
if not MEMORY_API_TOKEN:
    raise RuntimeError("MEMORY_API_TOKEN environment variable is required")

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "javieros-memory")

# Cron system — OPTIONAL: returns 503 if not configured
CRON_TOKEN = (os.environ.get("CRON_TOKEN") or "").strip()
OPENWEBUI_BASE_URL = os.environ.get("OPENWEBUI_BASE_URL", "http://localhost:8080")
OPENWEBUI_API_KEY = os.environ.get("OPENWEBUI_API_KEY", "")
CRON_MODEL = os.environ.get("CRON_MODEL", "gpt-4o-mini")
CRON_DEFAULT_USER = os.environ.get("CRON_DEFAULT_USER", "javier")

ALLOWED_FILENAMES = re.compile(
    r"^(SOUL|USER|MEMORY|HEARTBEAT)\.md$|^DAILY_LOG_\d{4}-\d{2}-\d{2}\.md$"
)

ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "MEMORY_ALLOWED_ORIGINS",
        "https://open-webui-a4bmliuj7q-uc.a.run.app",
    ).split(",")
    if origin.strip()
]

VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# GCS client lifecycle
# ---------------------------------------------------------------------------

gcs_client: Optional[storage.Client] = None
bucket: Optional[storage.Bucket] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global gcs_client, bucket
    gcs_client = storage.Client()
    bucket = gcs_client.bucket(GCS_BUCKET_NAME)
    yield
    gcs_client = None
    bucket = None


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="JavierOS Memory Service",
    version=VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "PUT", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------


def verify_token(authorization: str = Header(...)) -> None:
    """Validate Bearer token using constant-time comparison."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[len("Bearer ") :]
    if not hmac.compare_digest(token.encode(), MEMORY_API_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Invalid token")


def verify_cron_token(authorization: str = Header(...)) -> None:
    """Validate cron Bearer token. Returns 503 if cron system is not configured."""
    if not CRON_TOKEN:
        raise HTTPException(status_code=503, detail="Cron system not configured")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[len("Bearer ") :]
    if not hmac.compare_digest(token.encode(), CRON_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Invalid cron token")


# ---------------------------------------------------------------------------
# LLM helper — calls Open WebUI's chat completions API
# ---------------------------------------------------------------------------


async def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Call Open WebUI's /api/chat/completions endpoint for LLM generation."""
    headers = {}
    if OPENWEBUI_API_KEY:
        headers["Authorization"] = f"Bearer {OPENWEBUI_API_KEY}"

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{OPENWEBUI_BASE_URL}/api/chat/completions",
            json={
                "model": CRON_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def read_file_safe(user_id: str, filename: str) -> str:
    """Read a GCS file, returning empty string if not found."""
    blob = bucket.blob(blob_path(user_id, filename))
    try:
        return blob.download_as_text()
    except NotFound:
        return ""


def validate_filename(filename: str) -> None:
    """Ensure filename matches allowed patterns to prevent path traversal."""
    if not ALLOWED_FILENAMES.match(filename):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid filename: {filename}. "
            "Allowed: SOUL.md, USER.md, MEMORY.md, HEARTBEAT.md, DAILY_LOG_YYYY-MM-DD.md",
        )


def validate_user_id(user_id: str) -> None:
    """Ensure user_id is safe for use as a GCS prefix."""
    if not re.match(r"^[a-zA-Z0-9_\-\.@]{1,128}$", user_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid user_id. Must be 1-128 alphanumeric/underscore/hyphen/dot/@ characters.",
        )


def blob_path(user_id: str, filename: str) -> str:
    return f"{user_id}/{filename}"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class FileContent(BaseModel):
    content: str


class AppendContent(BaseModel):
    content: str
    separator: str = "\n\n---\n\n"


class FileMetadata(BaseModel):
    filename: str
    size: int
    updated: str


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    bucket: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="healthy",
        service="memory-service",
        version=VERSION,
        bucket=GCS_BUCKET_NAME,
    )


@app.get("/files/{user_id}")
async def list_files(user_id: str, authorization: str = Header(...)):
    """List all memory files for a user."""
    verify_token(authorization)
    validate_user_id(user_id)

    prefix = f"{user_id}/"
    blobs = bucket.list_blobs(prefix=prefix)

    files = []
    for blob in blobs:
        filename = blob.name.removeprefix(prefix)
        if ALLOWED_FILENAMES.match(filename):
            files.append(
                FileMetadata(
                    filename=filename,
                    size=blob.size or 0,
                    updated=blob.updated.isoformat() if blob.updated else "",
                )
            )

    return {"user_id": user_id, "files": files}


@app.get("/files/{user_id}/{filename}")
async def read_file(user_id: str, filename: str, authorization: str = Header(...)):
    """Read a specific memory file."""
    verify_token(authorization)
    validate_user_id(user_id)
    validate_filename(filename)

    blob = bucket.blob(blob_path(user_id, filename))
    try:
        content = blob.download_as_text()
    except NotFound:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    return {
        "user_id": user_id,
        "filename": filename,
        "content": content,
        "size": blob.size,
        "updated": blob.updated.isoformat() if blob.updated else "",
    }


@app.put("/files/{user_id}/{filename}")
async def write_file(
    user_id: str,
    filename: str,
    body: FileContent,
    authorization: str = Header(...),
):
    """Create or overwrite a memory file."""
    verify_token(authorization)
    validate_user_id(user_id)
    validate_filename(filename)

    blob = bucket.blob(blob_path(user_id, filename))
    blob.upload_from_string(body.content, content_type="text/markdown")

    return {
        "user_id": user_id,
        "filename": filename,
        "size": len(body.content.encode("utf-8")),
        "updated": datetime.now(timezone.utc).isoformat(),
        "status": "written",
    }


@app.post("/files/{user_id}/{filename}/append")
async def append_to_file(
    user_id: str,
    filename: str,
    body: AppendContent,
    authorization: str = Header(...),
):
    """Append content to a memory file, creating it if it doesn't exist."""
    verify_token(authorization)
    validate_user_id(user_id)
    validate_filename(filename)

    blob = bucket.blob(blob_path(user_id, filename))

    existing = ""
    try:
        existing = blob.download_as_text()
    except NotFound:
        pass

    if existing:
        new_content = existing + body.separator + body.content
    else:
        new_content = body.content

    blob.upload_from_string(new_content, content_type="text/markdown")

    return {
        "user_id": user_id,
        "filename": filename,
        "size": len(new_content.encode("utf-8")),
        "updated": datetime.now(timezone.utc).isoformat(),
        "status": "appended",
    }


@app.delete("/files/{user_id}/{filename}")
async def delete_file(user_id: str, filename: str, authorization: str = Header(...)):
    """Delete a memory file."""
    verify_token(authorization)
    validate_user_id(user_id)
    validate_filename(filename)

    blob = bucket.blob(blob_path(user_id, filename))
    try:
        blob.reload()
    except NotFound:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    blob.delete()

    return {
        "user_id": user_id,
        "filename": filename,
        "status": "deleted",
    }


# ---------------------------------------------------------------------------
# Cron Endpoints — Cloud Scheduler → memory-service via cron proxy
# ---------------------------------------------------------------------------

MORNING_BRIEFING_SYSTEM = """You are Javier's executive assistant AI. Generate a concise morning briefing based on the user's profile, memory, and any active reminders. Format as a warm, actionable daily brief in markdown. Include:
- A personalized greeting
- Key reminders from HEARTBEAT.md
- Relevant context from recent memory
- Any follow-ups from previous interactions
Keep it under 500 words. Be warm but professional."""

INBOX_SUMMARY_SYSTEM = """You are Javier's executive assistant AI. Summarize today's activity log into key takeaways. Format as a concise end-of-day digest in markdown. Include:
- Conversations and topics covered
- Decisions made or pending
- Action items to carry forward
Keep it under 400 words."""

WEEKLY_REPORT_SYSTEM = """You are Javier's executive assistant AI. Generate a weekly summary report from the past week's daily logs. Format as a structured markdown report. Include:
- Week overview (high-level themes)
- Key accomplishments and decisions
- Unresolved items and carry-forwards
- Patterns or insights noticed
Keep it under 800 words."""


@app.post("/cron/morning-briefing")
async def cron_morning_briefing(authorization: str = Header(...)):
    """Generate a morning briefing from user context and store as daily log."""
    verify_cron_token(authorization)
    user_id = CRON_DEFAULT_USER
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Gather context from memory files
    user_md = await read_file_safe(user_id, "USER.md")
    memory_md = await read_file_safe(user_id, "MEMORY.md")
    heartbeat_md = await read_file_safe(user_id, "HEARTBEAT.md")
    soul_md = await read_file_safe(user_id, "SOUL.md")

    context_parts = []
    if soul_md:
        context_parts.append(f"## SOUL\n{soul_md}")
    if user_md:
        context_parts.append(f"## USER PROFILE\n{user_md}")
    if memory_md:
        context_parts.append(f"## MEMORY\n{memory_md}")
    if heartbeat_md:
        context_parts.append(f"## ACTIVE REMINDERS\n{heartbeat_md}")

    if not context_parts:
        return {
            "status": "skipped",
            "reason": "No memory files found for user",
            "user_id": user_id,
        }

    user_prompt = (
        f"Today is {today}.\n\nHere is the current context:\n\n"
        + "\n\n".join(context_parts)
    )

    try:
        briefing = await call_llm(MORNING_BRIEFING_SYSTEM, user_prompt)
    except httpx.HTTPError as e:
        logger.error("LLM call failed for morning-briefing: %s", e)
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")

    # Store as daily log
    log_filename = f"DAILY_LOG_{today}.md"
    log_entry = f"# Morning Briefing — {today}\n\n{briefing}"
    blob = bucket.blob(blob_path(user_id, log_filename))

    existing = ""
    try:
        existing = blob.download_as_text()
    except NotFound:
        pass

    if existing:
        content = existing + "\n\n---\n\n" + log_entry
    else:
        content = log_entry

    blob.upload_from_string(content, content_type="text/markdown")

    return {
        "status": "generated",
        "job": "morning-briefing",
        "user_id": user_id,
        "date": today,
        "filename": log_filename,
        "briefing_length": len(briefing),
    }


@app.post("/cron/inbox-summary")
async def cron_inbox_summary(authorization: str = Header(...)):
    """Summarize today's daily log into an end-of-day digest."""
    verify_cron_token(authorization)
    user_id = CRON_DEFAULT_USER
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_filename = f"DAILY_LOG_{today}.md"

    daily_log = await read_file_safe(user_id, log_filename)
    if not daily_log:
        return {
            "status": "skipped",
            "reason": "No daily log found for today",
            "user_id": user_id,
        }

    user_prompt = f"Today is {today}.\n\nHere is today's activity log:\n\n{daily_log}"

    try:
        summary = await call_llm(INBOX_SUMMARY_SYSTEM, user_prompt)
    except httpx.HTTPError as e:
        logger.error("LLM call failed for inbox-summary: %s", e)
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")

    # Append summary to the daily log
    blob = bucket.blob(blob_path(user_id, log_filename))
    existing = blob.download_as_text()
    content = existing + "\n\n---\n\n# End-of-Day Summary\n\n" + summary
    blob.upload_from_string(content, content_type="text/markdown")

    return {
        "status": "generated",
        "job": "inbox-summary",
        "user_id": user_id,
        "date": today,
        "filename": log_filename,
        "summary_length": len(summary),
    }


@app.post("/cron/weekly-report")
async def cron_weekly_report(authorization: str = Header(...)):
    """Generate a weekly summary from the past 7 days of daily logs."""
    verify_cron_token(authorization)
    user_id = CRON_DEFAULT_USER
    today = datetime.now(timezone.utc)
    today_str = today.strftime("%Y-%m-%d")

    # Collect past 7 days of daily logs
    logs = []
    for i in range(7):
        day = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        log_filename = f"DAILY_LOG_{day}.md"
        content = await read_file_safe(user_id, log_filename)
        if content:
            logs.append(f"## {day}\n\n{content}")

    if not logs:
        return {
            "status": "skipped",
            "reason": "No daily logs found for past 7 days",
            "user_id": user_id,
        }

    user_prompt = (
        f"Today is {today_str}. Generate a weekly report from these daily logs "
        f"(most recent first):\n\n" + "\n\n---\n\n".join(logs)
    )

    try:
        report = await call_llm(WEEKLY_REPORT_SYSTEM, user_prompt)
    except httpx.HTTPError as e:
        logger.error("LLM call failed for weekly-report: %s", e)
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")

    # Store as today's daily log entry
    log_filename = f"DAILY_LOG_{today_str}.md"
    log_entry = f"# Weekly Report — Week ending {today_str}\n\n{report}"
    blob = bucket.blob(blob_path(user_id, log_filename))

    existing = ""
    try:
        existing = blob.download_as_text()
    except NotFound:
        pass

    if existing:
        content = existing + "\n\n---\n\n" + log_entry
    else:
        content = log_entry

    blob.upload_from_string(content, content_type="text/markdown")

    return {
        "status": "generated",
        "job": "weekly-report",
        "user_id": user_id,
        "date": today_str,
        "filename": log_filename,
        "report_length": len(report),
        "days_with_logs": len(logs),
    }


# ---------------------------------------------------------------------------
# Cron: heartbeat check (every 30 min)
# ---------------------------------------------------------------------------

HEARTBEAT_CHECK_SYSTEM = (
    "You are a proactive personal assistant monitoring a user's HEARTBEAT.md file. "
    "This file contains tasks, reminders, deadlines, and follow-ups with dates. "
    "Analyze the items and today's date. Identify:\n"
    "1. OVERDUE items (past their due date)\n"
    "2. DUE TODAY items\n"
    "3. DUE SOON items (within the next 48 hours)\n\n"
    "For each flagged item, write a brief, actionable notification. "
    "If nothing is due or overdue, respond with exactly: NO_ALERTS\n"
    "Format output as a markdown list of alerts, each with urgency level "
    "(🔴 OVERDUE, 🟡 DUE TODAY, 🟢 DUE SOON) and the original item text."
)


@app.post("/cron/heartbeat-check")
async def cron_heartbeat_check(authorization: str = Header(...)):
    """Check HEARTBEAT.md for overdue/due-soon items and generate alerts."""
    verify_cron_token(authorization)
    user_id = CRON_DEFAULT_USER
    today = datetime.now(timezone.utc)
    today_str = today.strftime("%Y-%m-%d")

    heartbeat = await read_file_safe(user_id, "HEARTBEAT.md")
    if not heartbeat:
        return {
            "status": "skipped",
            "reason": "No HEARTBEAT.md found",
            "user_id": user_id,
        }

    user_prompt = (
        f"Today is {today_str} (UTC). Here is the user's HEARTBEAT.md:\n\n"
        f"```markdown\n{heartbeat}\n```\n\n"
        "Analyze all items with dates and generate alerts for anything "
        "overdue, due today, or due within 48 hours."
    )

    try:
        alerts = await call_llm(HEARTBEAT_CHECK_SYSTEM, user_prompt)
    except httpx.HTTPError as e:
        logger.error("LLM call failed for heartbeat-check: %s", e)
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")

    if alerts.strip() == "NO_ALERTS":
        return {
            "status": "clear",
            "job": "heartbeat-check",
            "user_id": user_id,
            "date": today_str,
            "message": "No overdue or upcoming items",
        }

    # Append alerts to today's daily log
    log_filename = f"DAILY_LOG_{today_str}.md"
    time_str = today.strftime("%H:%M UTC")
    log_entry = f"## Heartbeat Check — {time_str}\n\n{alerts}"
    blob = bucket.blob(blob_path(user_id, log_filename))

    existing = ""
    try:
        existing = blob.download_as_text()
    except NotFound:
        pass

    if existing:
        content = existing + "\n\n---\n\n" + log_entry
    else:
        content = log_entry

    blob.upload_from_string(content, content_type="text/markdown")

    return {
        "status": "alerts_generated",
        "job": "heartbeat-check",
        "user_id": user_id,
        "date": today_str,
        "filename": log_filename,
        "alerts_length": len(alerts),
    }
