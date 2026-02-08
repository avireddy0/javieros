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
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import storage
from google.cloud.exceptions import NotFound
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration — hard-fail on missing required env vars
# ---------------------------------------------------------------------------

MEMORY_API_TOKEN = os.environ.get("MEMORY_API_TOKEN")
if not MEMORY_API_TOKEN:
    raise RuntimeError("MEMORY_API_TOKEN environment variable is required")

GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "javieros-memory")

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
