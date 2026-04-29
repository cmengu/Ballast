"""ballast/core/sync.py — SpecPoller (M2 side).

Polls the spec server at every Agent.iter node boundary.
Returns a new SpecModel only when the version_hash field changes.

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

import logging
import re

import httpx

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")

from ballast.core.spec import SpecModel

logger = logging.getLogger(__name__)


class SpecPoller:
    """Client-side poller that detects live spec changes from the server.

    poll() compares SpecModel.version_hash (16-char sha256) — not a timestamp.
    Caller must call set_initial() before poll() or poll() returns None always.

    A single shared httpx.Client is reused across all poll() calls to avoid
    per-call TCP connection setup overhead. It is closed when the poller is
    used as a context manager or when close() is called explicitly.
    """

    def __init__(self, base_url: str, job_id: str) -> None:
        if not _JOB_ID_RE.match(job_id):
            raise ValueError(
                f"Invalid job_id {job_id!r}: must match ^[A-Za-z0-9_-]{{1,128}}$. "
                "Characters like '/', '?', '#' can corrupt URL routing."
            )
        self.url = f"{base_url.rstrip('/')}/spec/{job_id}/current"
        self._current: SpecModel | None = None
        self._client = httpx.Client(timeout=2.0)

    def close(self) -> None:
        """Close the underlying httpx client. Safe to call multiple times."""
        self._client.close()

    def __del__(self) -> None:
        """Best-effort cleanup when GC collects the poller without explicit close().

        Prefer explicit `with SpecPoller(...) as p:` or `p.close()` — __del__ is
        not guaranteed to run promptly (or at all in CPython with reference cycles).
        """
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> "SpecPoller":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def set_initial(self, spec: SpecModel) -> None:
        """Set the baseline spec before polling starts."""
        self._current = spec

    def poll(self) -> SpecModel | None:
        """Check server for a spec update.

        Returns new SpecModel if version_hash changed since last call.
        Returns None if unchanged, server unreachable, or set_initial() not called.
        Never raises.
        """
        if self._current is None:
            return None
        data: dict | None = None
        try:
            r = self._client.get(self.url)
            if r.status_code != 200:
                return None
            data = r.json()
            if not data or data.get("version_hash") == self._current.version_hash:
                return None
            new_spec = SpecModel(**data)
            self._current = new_spec   # update baseline so next poll compares correctly
            return new_spec
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            logger.debug("spec_poll_unreachable url=%s exc=%s", self.url, exc)
            return None  # M5 unreachable — agent continues with current spec
        except Exception as exc:
            # data-shape or validation error — the server returned something unexpected
            logger.warning(
                "spec_poll_invalid_body url=%s version_hash=%s exc=%s",
                self.url,
                (data or {}).get("version_hash", "?"),
                exc,
            )
            return None
