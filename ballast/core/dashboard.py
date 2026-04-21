"""ballast/core/dashboard.py — Textual TUI for real-time Ballast run visibility.

Polls ballast-progress.json on a configurable interval and renders:
  - Header: run ID, spec intent, spec hash, run status
  - Stats bar: total nodes, drift events, violations, total cost
  - Node table: per-NodeSummary row with label, score, cost, tool, verified
  - Footer: keyboard hints

Read-only observer — never writes to the checkpoint file or contacts any LLM.
No imports from trajectory.py, probe.py, evaluator.py, or escalation.py.

Entry points:
    python -m ballast.core.dashboard [path] [interval_seconds]
    from ballast.core.dashboard import BallastDashboard; BallastDashboard().run()
"""
from __future__ import annotations

import sys

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Footer, Header, Static

from ballast.core.checkpoint import BallastProgress

# ---------------------------------------------------------------------------
# Colour mapping — DriftLabel → Textual markup colour
# ---------------------------------------------------------------------------

_LABEL_COLOUR: dict[str, str] = {
    "PROGRESSING": "green",
    "STALLED": "yellow",
    "VIOLATED": "red",
    "VIOLATED_IRREVERSIBLE": "red",
}


def _label_style(label: str) -> str:
    """Return Textual markup colour string for a DriftLabel.

    Unknown labels fall back to 'white' so new label values never crash the UI.
    """
    return _LABEL_COLOUR.get(label, "white")


def _fmt_score(score: float) -> str:
    """Format drift score as a fixed-width 4-decimal string."""
    return f"{score:.4f}"


def _fmt_cost(cost: float) -> str:
    """Format cost_usd as a 5-decimal dollar string."""
    return f"{cost:.5f}"


def _fmt_time(timestamp: str) -> str:
    """Extract HH:MM:SS from an ISO-8601 UTC timestamp string.

    Uses datetime.fromisoformat for robustness across +00:00, Z, and millisecond
    variants. Returns the raw string on any parse failure so the UI never crashes.
    """
    if not timestamp:
        return ""
    try:
        from datetime import datetime
        # Python 3.11+ handles Z natively; earlier versions need replacement.
        ts = timestamp.replace("Z", "+00:00")
        return datetime.fromisoformat(ts).strftime("%H:%M:%S")
    except (ValueError, TypeError, AttributeError):
        return str(timestamp)


# ---------------------------------------------------------------------------
# BallastDashboard — the Textual App
# ---------------------------------------------------------------------------


class BallastDashboard(App):
    """Real-time TUI for a running Ballast job.

    Args:
        path:          Path to ballast-progress.json (default: "ballast-progress.json").
        poll_interval: Seconds between file polls (default: 2.0).
    """

    CSS = """
    #stats {
        height: 3;
        padding: 0 1;
        background: $surface;
        color: $text;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Force refresh"),
    ]

    def __init__(
        self,
        path: str = "ballast-progress.json",
        poll_interval: float = 2.0,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._path = path
        self._poll_interval = poll_interval
        # Last-seen checkpoint updated_at — per-instance; avoids class-attribute shadowing.
        self._last_updated_at: str = ""

    def compose(self) -> ComposeResult:
        """Build the widget tree: Header, stats bar, node table, Footer."""
        yield Header()
        yield Static("Loading…", id="stats")
        table = DataTable(id="nodes")
        table.cursor_type = "row"
        yield table
        yield Footer()

    def on_mount(self) -> None:
        """Start the poll timer immediately after the UI mounts."""
        self.set_interval(self._poll_interval, self._poll)
        # Trigger one immediate poll so the table is populated on first render.
        self.call_later(self._poll)

    # ------------------------------------------------------------------
    # Poll + render helpers
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        """Read checkpoint file; render only when content has changed.

        Returns immediately (None) if the file does not exist — safe to call
        before a run starts.
        """
        progress = BallastProgress.read(self._path)
        if progress is None:
            return
        # Skip re-render if nothing changed since last poll.
        if progress.updated_at == self._last_updated_at:
            return
        self._last_updated_at = progress.updated_at
        self._render(progress)

    def _render(self, progress: BallastProgress) -> None:
        """Mutate widgets to reflect the current BallastProgress state.

        Clears and repopulates the DataTable on every call. Column headers
        are re-added after clear(columns=True) as required by Textual 8.
        """
        # ── Header sub-title ───────────────────────────────────────────
        status = "COMPLETE" if progress.is_complete else "RUNNING"
        intent_short = (progress.spec_intent or "")[:60]
        self.title = f"Ballast — {status}"
        self.sub_title = f"{intent_short}  [{progress.active_spec_hash[:8]}]"

        # ── Stats bar ──────────────────────────────────────────────────
        n_nodes = len(progress.completed_node_summaries)
        stats_text = (
            f"Nodes: {n_nodes}  │  "
            f"Drift events: {progress.total_drift_events}  │  "
            f"Violations: {progress.total_violations}  │  "
            f"Cost: ${progress.total_cost_usd:.5f}  │  "
            f"Run: {progress.run_id or '—'}"
        )
        self.query_one("#stats", Static).update(stats_text)

        # ── Node table ─────────────────────────────────────────────────
        table: DataTable = self.query_one("#nodes", DataTable)
        table.clear(columns=True)
        table.add_columns("#", "Tool", "Label", "Score", "Cost ($)", "Verified", "Spec", "Time")

        for node in progress.completed_node_summaries:
            colour = _label_style(node.label)
            table.add_row(
                str(node.index),
                node.tool_name or "—",
                f"[{colour}]{node.label}[/{colour}]",
                _fmt_score(node.drift_score),
                _fmt_cost(node.cost_usd),
                "✓" if node.verified else "✗",
                node.spec_hash[:8] if node.spec_hash else "—",
                _fmt_time(node.timestamp),
            )

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_refresh(self) -> None:
        """Force an immediate re-poll (bound to 'r')."""
        self._last_updated_at = ""  # reset so _poll always re-renders
        self._poll()

    def action_quit(self) -> None:
        """Quit the dashboard (bound to 'q')."""
        self.exit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(path: str = "ballast-progress.json", poll_interval: float = 2.0) -> None:
    """Launch the dashboard. Blocks until the user quits."""
    BallastDashboard(path=path, poll_interval=poll_interval).run()


if __name__ == "__main__":
    _path = sys.argv[1] if len(sys.argv) > 1 else "ballast-progress.json"
    _interval = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    run(path=_path, poll_interval=_interval)
