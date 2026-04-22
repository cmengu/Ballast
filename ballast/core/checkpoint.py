"""ballast/core/checkpoint.py — Per-run audit state and resume context.

Public interface:
    NodeSummary     — per-node audit stamp: label, score, cost, spec_hash, timestamp
    BallastProgress — full run state: dispatch hash, active hash, transitions, summaries
    CHECKPOINT_FILE — default path for ballast-progress.json

Invariant: NodeSummary.spec_hash is the version_hash of the spec active when
that node executed — not the spec at job dispatch. This per-node stamp is the
training dataset audit trail (projet-overview.md invariant 4).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

CHECKPOINT_FILE = "ballast-progress.json"


@dataclass
class NodeSummary:
    """Audit record for one Agent.iter node.

    spec_hash: version_hash of the spec that was ACTIVE when this node executed.
    This may differ from BallastProgress.spec_hash (dispatch hash) if a live
    spec update arrived mid-run.
    """
    index: int
    tool_name: str
    label: str                  # PROGRESSING | STALLED | VIOLATED | VIOLATED_IRREVERSIBLE
    drift_score: float
    cost_usd: float
    verified: bool              # True if environment probe confirmed the claim
    spec_hash: str              # spec version active at this node — per-node audit stamp
    timestamp: str              # ISO-8601 UTC


@dataclass
class BallastProgress:
    """Full run state. Written to ballast-progress.json at every checkpoint.

    spec_hash:        version_hash at job dispatch (never changes during run).
    active_spec_hash: version_hash currently active (updates on live spec change).
    spec_transitions: ordered log of live spec updates seen during this run.
    """
    spec_hash: str
    active_spec_hash: str = ""
    spec_intent: str = ""
    run_id: str = ""
    started_at: str = ""
    updated_at: str = ""
    last_clean_node_index: int = -1
    completed_node_summaries: list = field(default_factory=list)
    spec_transitions: list = field(default_factory=list)
    total_cost_usd: float = 0.0
    total_drift_events: int = 0
    total_violations: int = 0
    remaining_success_criteria: list = field(default_factory=list)
    last_escalation: str | None = None
    is_complete: bool = False

    def __post_init__(self) -> None:
        if not self.active_spec_hash:
            self.active_spec_hash = self.spec_hash

    def write(self, path: str = CHECKPOINT_FILE) -> None:
        """Serialise to JSON atomically. Crash mid-write cannot corrupt the file.

        Writes to a temp file in the same directory, then os.replace — guaranteed
        atomic on POSIX systems so a partial write never leaves a broken checkpoint.
        """
        data = asdict(self)
        payload = json.dumps(data, indent=2)
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".tmp")
        fd_open = True
        try:
            os.write(fd, payload.encode("utf-8"))
            os.fsync(fd)
            os.close(fd)
            fd_open = False
            os.replace(tmp, dest)
        except BaseException:
            if fd_open:
                os.close(fd)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @classmethod
    def read(cls, path: str = CHECKPOINT_FILE) -> "BallastProgress | None":
        """Deserialise from JSON. Returns None if file does not exist."""
        p = Path(path)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        data["completed_node_summaries"] = [
            NodeSummary(**n) for n in data["completed_node_summaries"]
        ]
        return cls(**data)

    def resume_context(self) -> str:
        """Plain-text summary prepended to the task string on resume."""
        completed = len(self.completed_node_summaries)
        last = (
            self.completed_node_summaries[-1]
            if self.completed_node_summaries
            else None
        )
        last_action = f"{last.tool_name} → {last.label}" if last else "none"
        remaining = "\n".join(f"- {c}" for c in self.remaining_success_criteria)
        return (
            f"[BALLAST RESUME CONTEXT]\n"
            f"Spec at dispatch:  {self.spec_hash[:8]}\n"
            f"Active spec now:   {self.active_spec_hash[:8]}\n"
            f"Spec updates seen: {len(self.spec_transitions)}\n"
            f"Intent: {self.spec_intent}\n"
            f"Progress: {completed} nodes completed\n"
            f"Last clean node: #{self.last_clean_node_index} ({last_action})\n"
            f"Drift events: {self.total_drift_events} | "
            f"Violations: {self.total_violations}\n"
            f"Cost so far: ${self.total_cost_usd:.4f}\n"
            f"Remaining success criteria:\n{remaining}\n"
            f"Resume from node #{self.last_clean_node_index + 1}.\n"
            f"Do not repeat completed work.\n"
            f"[END RESUME CONTEXT]"
        )
