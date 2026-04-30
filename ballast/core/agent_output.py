"""Helpers for pydantic-ai Agent.run / AgentRun result shapes across versions."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock


def agent_run_result_payload(result: Any) -> Any:
    """Best-effort payload from an ``agent.run()`` result (parity with ``run_with_spec``).

    For real pydantic-ai results: prefer ``.data`` (newer), then ``.output``, then the
    object itself.

    For ``unittest.mock.Mock`` / ``MagicMock``, attributes assigned as ``result.output =
    ...`` are stored on ``__dict__`` while auto-vivified children are not. Prefer
    ``output`` then ``data`` from ``__dict__`` so tests that only set ``.output`` are not
    defeated by ``getattr(..., "data")`` creating a nested mock.
    """
    if isinstance(result, Mock):
        d = result.__dict__
        if "output" in d:
            return d["output"]
        if "data" in d:
            return d["data"]

    return getattr(result, "data", getattr(result, "output", result))
