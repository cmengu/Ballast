"""tests/test_dashboard.py — Unit tests for ballast/core/dashboard.py.

10 tests total:
    TestLabelStyle   (5) — colour mapping for all known and unknown labels
    TestFormatters   (3) — _fmt_score, _fmt_cost, _fmt_time
    TestPollBehavior (2) — _poll returns None when file missing; skips render when unchanged

No Textual Pilot used — tests target module-level helpers and _poll data logic only.
No App.run() is called — tests never block on a terminal.
All tests are synchronous — no pytest.mark.asyncio needed.
"""
from unittest.mock import patch

from ballast.core.dashboard import (
    _fmt_cost,
    _fmt_score,
    _fmt_time,
    _label_style,
    BallastDashboard,
)
from ballast.core.checkpoint import BallastProgress


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_progress(**overrides) -> BallastProgress:
    defaults = dict(
        spec_hash="abc00001",
        spec_intent="test intent",
        run_id="run-001",
        updated_at="2026-01-01T00:00:01Z",
        started_at="2026-01-01T00:00:00Z",
    )
    defaults.update(overrides)
    return BallastProgress(**defaults)


def _make_dashboard() -> BallastDashboard:
    """Construct BallastDashboard without calling run() — safe for unit tests."""
    d = BallastDashboard.__new__(BallastDashboard)
    d._path = "nonexistent.json"
    d._poll_interval = 2.0
    d._last_updated_at = ""
    return d


# ---------------------------------------------------------------------------
# TestLabelStyle
# ---------------------------------------------------------------------------


class TestLabelStyle:
    def test_progressing_returns_green(self):
        assert _label_style("PROGRESSING") == "green"

    def test_stalled_returns_yellow(self):
        assert _label_style("STALLED") == "yellow"

    def test_violated_returns_red(self):
        assert _label_style("VIOLATED") == "red"

    def test_violated_irreversible_returns_red(self):
        assert _label_style("VIOLATED_IRREVERSIBLE") == "red"

    def test_unknown_label_returns_white(self):
        assert _label_style("SOME_FUTURE_LABEL") == "white"


# ---------------------------------------------------------------------------
# TestFormatters
# ---------------------------------------------------------------------------


class TestFormatters:
    def test_fmt_score_four_decimal_places(self):
        assert _fmt_score(0.9) == "0.9000"
        assert _fmt_score(0.1234) == "0.1234"

    def test_fmt_cost_five_decimal_places(self):
        assert _fmt_cost(0.00123) == "0.00123"
        assert _fmt_cost(0.0) == "0.00000"

    def test_fmt_time_extracts_hhmmss(self):
        assert _fmt_time("2026-01-01T12:34:56Z") == "12:34:56"
        assert _fmt_time("") == ""
        assert _fmt_time(None) == ""


# ---------------------------------------------------------------------------
# TestPollBehavior
# ---------------------------------------------------------------------------


class TestPollBehavior:
    def test_poll_returns_none_when_file_missing(self):
        """_poll must not raise when ballast-progress.json does not exist."""
        d = _make_dashboard()
        result = d._poll()  # file "nonexistent.json" does not exist
        assert result is None

    def test_poll_skips_render_when_updated_at_unchanged(self):
        """_poll must not call _render when updated_at matches _last_updated_at."""
        d = _make_dashboard()
        progress = _make_progress(updated_at="2026-01-01T00:00:01Z")
        d._last_updated_at = "2026-01-01T00:00:01Z"  # same as progress
        with patch("ballast.core.checkpoint.BallastProgress.read", return_value=progress), \
             patch.object(d, "_render") as mock_render:
            d._poll()
        mock_render.assert_not_called()
