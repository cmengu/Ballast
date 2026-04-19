"""tests/test_otel.py — Unit tests for ballast/adapters/otel.py.

10 tests total:
    TestDriftSpanPacket  (3) — dataclass field correctness
    TestEmitDriftSpan    (7) — attribute setting, status codes, fail-open

All tests are synchronous — no pytest.mark.asyncio needed.
OTel tracer is mocked via patch("opentelemetry.trace.get_tracer") so no
live TracerProvider is required and assertions on span.set_attribute are
possible.
"""
from unittest.mock import MagicMock, patch

from ballast.adapters.otel import DriftSpanPacket, emit_drift_span, _ERROR_LABELS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_assessment(
    label: str = "VIOLATED",
    score: float = 0.1,
    rationale: str = "breach",
    tool_name: str = "write_file",
) -> MagicMock:
    a = MagicMock()
    a.label = label
    a.score = score
    a.rationale = rationale
    a.tool_name = tool_name
    return a


def _make_spec(version_hash: str = "abc12345") -> MagicMock:
    s = MagicMock()
    s.version_hash = version_hash
    return s


def _mock_tracer_ctx():
    """Return (mock_tracer, mock_span) wired for start_as_current_span context manager."""
    mock_span = MagicMock()
    mock_tracer = MagicMock()
    mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
    mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
    return mock_tracer, mock_span


# ---------------------------------------------------------------------------
# TestDriftSpanPacket
# ---------------------------------------------------------------------------


class TestDriftSpanPacket:
    def test_fields_stored_correctly(self):
        p = DriftSpanPacket(
            label="VIOLATED",
            score=0.1,
            rationale="breach",
            tool_name="write_file",
            spec_version="abc12345",
            node_index=3,
            run_id="run-001",
            cost_usd=0.00123,
        )
        assert p.label == "VIOLATED"
        assert p.score == 0.1
        assert p.rationale == "breach"
        assert p.tool_name == "write_file"
        assert p.spec_version == "abc12345"
        assert p.node_index == 3
        assert p.run_id == "run-001"
        assert p.cost_usd == 0.00123

    def test_error_labels_frozenset_contains_violated(self):
        assert "VIOLATED" in _ERROR_LABELS
        assert "VIOLATED_IRREVERSIBLE" in _ERROR_LABELS

    def test_error_labels_frozenset_excludes_stalled(self):
        assert "STALLED" not in _ERROR_LABELS
        assert "PROGRESSING" not in _ERROR_LABELS


# ---------------------------------------------------------------------------
# TestEmitDriftSpan
# ---------------------------------------------------------------------------


class TestEmitDriftSpan:
    def test_all_eight_attributes_set(self):
        """emit_drift_span must call span.set_attribute for all 8 keys."""
        mock_tracer, mock_span = _mock_tracer_ctx()
        with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
            emit_drift_span(
                _make_assessment(label="STALLED", score=0.5, rationale="slow", tool_name="read_file"),
                _make_spec("abc12345"),
                node_index=2,
                run_id="run-xyz",
                node_cost=0.00050,
            )
        set_calls = {c.args[0] for c in mock_span.set_attribute.call_args_list}
        assert "ballast.drift.label" in set_calls
        assert "ballast.drift.score" in set_calls
        assert "ballast.drift.rationale" in set_calls
        assert "ballast.drift.tool_name" in set_calls
        assert "ballast.drift.spec_version" in set_calls
        assert "ballast.drift.node_index" in set_calls
        assert "ballast.drift.run_id" in set_calls
        assert "ballast.drift.cost_usd" in set_calls

    def test_violated_sets_error_status(self):
        from opentelemetry.trace import StatusCode

        mock_tracer, mock_span = _mock_tracer_ctx()
        with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
            emit_drift_span(
                _make_assessment(label="VIOLATED", rationale="hard breach"),
                _make_spec(),
                node_index=0,
                run_id="r",
                node_cost=0.0,
            )
        mock_span.set_status.assert_called_once_with(StatusCode.ERROR, "hard breach")

    def test_violated_irreversible_sets_error_status(self):
        from opentelemetry.trace import StatusCode

        mock_tracer, mock_span = _mock_tracer_ctx()
        with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
            emit_drift_span(
                _make_assessment(label="VIOLATED_IRREVERSIBLE", rationale="irreversible"),
                _make_spec(),
                node_index=1,
                run_id="r",
                node_cost=0.0,
            )
        mock_span.set_status.assert_called_once_with(StatusCode.ERROR, "irreversible")

    def test_stalled_sets_ok_status(self):
        from opentelemetry.trace import StatusCode

        mock_tracer, mock_span = _mock_tracer_ctx()
        with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
            emit_drift_span(
                _make_assessment(label="STALLED"),
                _make_spec(),
                node_index=0,
                run_id="r",
                node_cost=0.0,
            )
        mock_span.set_status.assert_called_once_with(StatusCode.OK)

    def test_attribute_values_match_assessment(self):
        """Spot-check that label and score are passed through correctly."""
        mock_tracer, mock_span = _mock_tracer_ctx()
        with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
            emit_drift_span(
                _make_assessment(label="STALLED", score=0.42, tool_name="bash"),
                _make_spec("hash9999"),
                node_index=7,
                run_id="run-abc",
                node_cost=0.00321,
            )
        attrs = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
        assert attrs["ballast.drift.label"] == "STALLED"
        assert attrs["ballast.drift.score"] == 0.42
        assert attrs["ballast.drift.tool_name"] == "bash"
        assert attrs["ballast.drift.spec_version"] == "hash9999"
        assert attrs["ballast.drift.node_index"] == 7
        assert attrs["ballast.drift.run_id"] == "run-abc"
        assert attrs["ballast.drift.cost_usd"] == 0.00321

    def test_otel_exception_is_swallowed(self):
        """emit_drift_span must return None when the tracer raises."""
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.side_effect = RuntimeError("otel down")
        with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
            result = emit_drift_span(
                _make_assessment(),
                _make_spec(),
                node_index=0,
                run_id="r",
                node_cost=0.0,
            )
        assert result is None  # never raises

    def test_returns_none_on_success(self):
        mock_tracer, _ = _mock_tracer_ctx()
        with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
            result = emit_drift_span(
                _make_assessment(label="STALLED"),
                _make_spec(),
                node_index=0,
                run_id="r",
                node_cost=0.0,
            )
        assert result is None
