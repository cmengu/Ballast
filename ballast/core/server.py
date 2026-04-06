"""ballast/core/server.py — Spec update server (M5 side).

Holds the current locked SpecModel per job_id in memory.
Exposes two endpoints for the M2 SpecPoller to consume.

Import: from ballast.core.server import app
Run via: python scripts/server.py
"""
from __future__ import annotations

from fastapi import FastAPI

from ballast.core.spec import SpecModel

app = FastAPI()

_current_spec: dict[str, dict] = {}  # job_id → SpecModel.model_dump()


@app.get("/spec/{job_id}/current")
def get_spec(job_id: str) -> dict:
    """Return the current spec for this job, or {} if not yet set."""
    return _current_spec.get(job_id, {})


@app.post("/spec/{job_id}/update")
def update_spec(job_id: str, spec: SpecModel) -> dict:
    """Store the new spec for this job. Returns version_hash for confirmation."""
    _current_spec[job_id] = spec.model_dump()
    return {"status": "ok", "version_hash": spec.version_hash}
