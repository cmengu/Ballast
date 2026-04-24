"""ballast/core/server.py — Spec update server (M5 side).

Holds the current locked SpecModel per job_id in memory.
Exposes two endpoints for the M2 SpecPoller to consume.

Import: from ballast.core.server import app
Run via: python scripts/server.py
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, Header, HTTPException

from ballast.core.spec import SpecModel

app = FastAPI()

_current_spec: dict[str, dict] = {}  # job_id → SpecModel.model_dump()

# When set, GET/POST spec routes require header X-Ballast-Token matching this value.
_SPEC_SERVER_TOKEN = os.environ.get("BALLAST_SPEC_SERVER_TOKEN", "").strip()


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
    _current_spec[job_id] = spec.model_dump()
    return {"status": "ok", "version_hash": spec.version_hash}
