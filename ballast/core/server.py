"""ballast/core/server.py — Spec update server (M5 side).

Holds the current locked SpecModel per job_id in memory.
Exposes two endpoints for the M2 SpecPoller to consume.

Import: from ballast.core.server import app
Run via: python scripts/server.py
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import FastAPI, Header, HTTPException

from ballast.core.spec import SpecModel

app = FastAPI()

_MAX_JOB_SLOTS = 500  # cap to prevent unbounded memory growth on long-lived servers
_current_spec: dict[str, dict] = {}  # job_id → SpecModel.model_dump()

# When set, GET/POST spec routes require header X-Ballast-Token matching this value.
_SPEC_SERVER_TOKEN = os.environ.get("BALLAST_SPEC_SERVER_TOKEN", "").strip()

if not _SPEC_SERVER_TOKEN:
    logger.warning(
        "BALLAST_SPEC_SERVER_TOKEN is not set — the spec server is running without "
        "authentication. Any client that can reach this process may read or overwrite "
        "specs for any job_id. Set BALLAST_SPEC_SERVER_TOKEN or restrict network access."
    )


def _require_token(x_ballast_token: Optional[str]) -> None:
    if not _SPEC_SERVER_TOKEN:
        return
    if x_ballast_token != _SPEC_SERVER_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/spec/{job_id}/current")
def get_spec(
    job_id: str,
    x_ballast_token: Optional[str] = Header(None, alias="X-Ballast-Token"),
) -> dict:
    """Return the current spec for this job, or {} if not yet set."""
    _require_token(x_ballast_token)
    return _current_spec.get(job_id, {})


@app.post("/spec/{job_id}/update")
def update_spec(
    job_id: str,
    spec: SpecModel,
    x_ballast_token: Optional[str] = Header(None, alias="X-Ballast-Token"),
) -> dict:
    """Store the new spec for this job. Returns version_hash for confirmation."""
    _require_token(x_ballast_token)
    if job_id not in _current_spec and len(_current_spec) >= _MAX_JOB_SLOTS:
        # Evict the oldest entry to prevent unbounded memory growth.
        oldest_key = next(iter(_current_spec))
        del _current_spec[oldest_key]
        logger.warning(
            "server_spec_evicted job_id=%r to make room for %r "
            "(slot cap=%d) — polling clients for the evicted job will see an empty spec",
            oldest_key, job_id, _MAX_JOB_SLOTS,
        )
    _current_spec[job_id] = spec.model_dump()
    return {"status": "ok", "version_hash": spec.version_hash}
