"""ballast/core/sync.py — SpecPoller (M2 side).

Polls the spec server at every Agent.iter node boundary.
Returns a new SpecModel only when the version field changes.

Never raises — M5 unreachable must not abort an M2 agent run.

Usage:
    poller = SpecPoller("http://localhost:8765", "job-001")
    poller.set_initial(locked_spec)
    # at every node boundary in hook.py:
    new_spec = poller.poll()
    if new_spec:
        delta = active_spec.diff(new_spec)
        active_spec = new_spec
"""
from __future__ import annotations

import httpx

from ballast.core.spec import SpecModel


class SpecPoller:
    """Client-side poller that detects live spec changes from the server.

    poll() compares SpecModel.version (8-char sha256) — not a timestamp.
    Caller must call set_initial() before poll() or poll() returns None always.
    """

    def __init__(self, base_url: str, job_id: str) -> None:
        self.url = f"{base_url}/spec/{job_id}/current"
        self._current: SpecModel | None = None

    def set_initial(self, spec: SpecModel) -> None:
        """Set the baseline spec before polling starts."""
        self._current = spec

    def poll(self) -> SpecModel | None:
        """Check server for a spec update.

        Returns new SpecModel if version changed since last call.
        Returns None if unchanged, server unreachable, or set_initial() not called.
        Never raises.
        """
        if self._current is None:
            return None
        try:
            r = httpx.get(self.url, timeout=2.0)
            if r.status_code != 200:
                return None
            data = r.json()
            if not data or data.get("version") == self._current.version:
                return None
            new_spec = SpecModel(**data)
            self._current = new_spec   # update baseline so next poll compares correctly
            return new_spec
        except Exception:
            return None  # M5 unreachable — silent, agent continues with current spec
