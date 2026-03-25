"""
FastAPI application for `empirica serve` — local daemon for Chrome extension.

Exposes profile operations as REST endpoints on localhost. The extension
POSTs scraped conversation data here for extraction, and queries profile status.

Security: Localhost-only by default. No authentication required for local use.
CORS allows chrome-extension:// origins for browser extension access.
"""

import logging
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Request/Response Models ──────────────────────────────────────────

class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "empirica-serve"
    version: str = "0.1.0"


class ImportRequest(BaseModel):
    """Import artifacts from conversation turns sent by the Chrome extension."""
    turns: list[dict] = Field(..., description="Conversation turns [{role, content, platform}]")
    url: str = Field(..., description="Source URL of the conversation")
    min_confidence: float = Field(0.5, ge=0.0, le=1.0)


class ImportResponse(BaseModel):
    ok: bool
    artifacts_count: int = 0
    findings: int = 0
    decisions: int = 0
    dead_ends: int = 0
    mistakes: int = 0
    unknowns: int = 0
    message: str = ""


class ProfileStatusResponse(BaseModel):
    ok: bool
    artifact_counts: dict = {}
    sync_state: dict = {}
    calibration: dict = {}


class SyncResponse(BaseModel):
    ok: bool
    message: str = ""
    fetched: int = 0
    imported: int = 0


# ── FastAPI App ──────────────────────────────────────────────────────

def create_serve_app() -> FastAPI:
    """Create FastAPI app for the serve daemon."""

    app = FastAPI(
        title="Empirica Serve",
        description="Local daemon for Chrome extension integration",
        version="0.1.0",
    )

    # CORS: Allow chrome-extension:// and localhost origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "chrome-extension://*",
            "http://localhost:*",
            "http://127.0.0.1:*",
        ],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health", response_model=HealthResponse)
    async def health():
        """Health check endpoint."""
        return HealthResponse()

    @app.post("/api/v1/import", response_model=ImportResponse)
    async def import_turns(req: ImportRequest):
        """Import conversation turns and extract epistemic artifacts.

        This is the primary endpoint for the Chrome extension. It receives
        scraped conversation data, runs the extraction pipeline, and stores
        artifacts in the local Empirica database.
        """
        try:
            result = _run_import(req.turns, req.url, req.min_confidence)
            return ImportResponse(ok=True, **result)
        except Exception as e:
            logger.error(f"Import failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/v1/profile/status", response_model=ProfileStatusResponse)
    async def profile_status():
        """Get epistemic profile status — artifact counts, sync state, calibration."""
        try:
            result = _run_profile_status()
            return ProfileStatusResponse(ok=True, **result)
        except Exception as e:
            logger.error(f"Profile status failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/v1/profile/sync", response_model=SyncResponse)
    async def profile_sync():
        """Trigger profile sync (fetch notes, import to SQLite)."""
        try:
            result = _run_profile_sync()
            return SyncResponse(ok=True, **result)
        except Exception as e:
            logger.error(f"Profile sync failed: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=str(e))

    return app


# ── Internal Handlers (wrap existing CLI logic) ──────────────────────

def _run_import(
    turns: list[dict],
    url: str,
    min_confidence: float,
) -> dict:
    """Run artifact extraction on conversation turns.

    Uses the same extraction pipeline as profile-import but operates on
    pre-scraped turns rather than reading from transcript files.
    """
    from empirica.core.canonical.artifact_extractor import extract_artifacts_from_turns

    # Extract artifacts from the turns
    artifacts = extract_artifacts_from_turns(turns, min_confidence=min_confidence)

    # Store artifacts in the database
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    stored = _store_artifacts(db, artifacts, source_url=url)

    # Count by type
    counts = {}
    for a in stored:
        atype = a.get("type", "unknown")
        counts[atype] = counts.get(atype, 0) + 1

    return {
        "artifacts_count": len(stored),
        "findings": counts.get("finding", 0),
        "decisions": counts.get("decision", 0),
        "dead_ends": counts.get("dead_end", 0),
        "mistakes": counts.get("mistake", 0),
        "unknowns": counts.get("unknown", 0),
        "message": f"Extracted {len(stored)} artifacts from {len(turns)} turns",
    }


def _store_artifacts(db, artifacts: list[dict], source_url: str) -> list[dict]:
    """Store extracted artifacts in the database."""
    import uuid
    from datetime import datetime, timezone

    stored = []
    for artifact in artifacts:
        artifact_id = str(uuid.uuid4())
        artifact_type = artifact.get("type", "finding")
        now = datetime.now(timezone.utc).isoformat()

        try:
            if artifact_type == "finding":
                db.execute(
                    "INSERT INTO project_findings (id, project_id, session_id, finding, impact, created_at, source_url) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (artifact_id, artifact.get("project_id", "extension-import"), None,
                     artifact["content"], artifact.get("impact", 0.5), now, source_url)
                )
            elif artifact_type == "decision":
                db.execute(
                    "INSERT INTO project_findings (id, project_id, session_id, finding, impact, created_at, source_url) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (artifact_id, artifact.get("project_id", "extension-import"), None,
                     f"[decision] {artifact['content']}", artifact.get("impact", 0.5), now, source_url)
                )
            elif artifact_type == "dead_end":
                db.execute(
                    "INSERT INTO project_dead_ends (id, project_id, session_id, approach, why_failed, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (artifact_id, artifact.get("project_id", "extension-import"), None,
                     artifact["content"], artifact.get("reason", ""), now)
                )
            elif artifact_type == "mistake":
                db.execute(
                    "INSERT INTO mistakes_made (id, project_id, session_id, mistake, why_wrong, prevention, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (artifact_id, artifact.get("project_id", "extension-import"), None,
                     artifact["content"], artifact.get("reason", ""), artifact.get("prevention", ""), now)
                )
            elif artifact_type == "unknown":
                db.execute(
                    "INSERT INTO project_unknowns (id, project_id, session_id, unknown, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (artifact_id, artifact.get("project_id", "extension-import"), None,
                     artifact["content"], now)
                )

            stored.append({"id": artifact_id, "type": artifact_type, **artifact})
        except Exception as e:
            logger.warning(f"Failed to store artifact {artifact_type}: {e}")

    return stored


def _run_profile_status() -> dict:
    """Get profile status by calling the existing profile status logic."""
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()

    # Count artifacts by type
    counts = {}
    for table, label in [
        ("project_findings", "findings"),
        ("project_unknowns", "unknowns"),
        ("project_dead_ends", "dead_ends"),
        ("mistakes_made", "mistakes"),
        ("goals", "goals"),
    ]:
        try:
            row = db.fetch_one(f"SELECT COUNT(*) as cnt FROM {table}")
            counts[label] = row["cnt"] if row else 0
        except Exception:
            counts[label] = 0

    return {
        "artifact_counts": counts,
        "sync_state": {},
        "calibration": {},
    }


def _run_profile_sync() -> dict:
    """Run profile sync by invoking the existing sync logic."""
    import subprocess
    import json

    result = subprocess.run(
        ["empirica", "profile-sync", "--import-only", "--output", "json"],
        capture_output=True, text=True, timeout=60,
    )

    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            return {
                "message": data.get("message", "Sync complete"),
                "fetched": data.get("fetched", 0),
                "imported": data.get("imported", 0),
            }
        except json.JSONDecodeError:
            return {"message": "Sync complete", "fetched": 0, "imported": 0}
    else:
        raise RuntimeError(f"Profile sync failed: {result.stderr[:200]}")
