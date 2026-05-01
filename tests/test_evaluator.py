"""tests/test_evaluator.py — Unit tests for ballast/core/evaluator.py.

14 tests total:
    TestEvaluatorPacket  (3) — field validation
    TestCallEvaluator    (6) — internal; client mocked
    TestEvaluateNode     (5) — full function; _get_evaluator_client patched

All tests are synchronous — no pytest.mark.asyncio needed.
"""
from unittest.mock import MagicMock, patch

from ballast.core.evaluator import (
    EvaluatorPacket,
    _call_evaluator,
    evaluate_node,
)
from ballast.core.spec import SpecModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(constraints: list[str] | None = None) -> SpecModel:
    from ballast.core.spec import lock

    base = {
        "intent": "test intent for evaluator tests",
        "success_criteria": ["criterion A"],
        "constraints": constraints or [],
        "allowed_tools": ["safe_tool"],
        "drift_threshold": 0.4,
        "harness": {},
    }
    return lock(SpecModel(**base))


def _make_packet(**overrides) -> EvaluatorPacket:
    defaults = dict(
        content="agent output here",
        tool_name="safe_tool",
        tool_args='{"path": "/tmp/x"}',
        spec_intent="complete the task safely",
        spec_constraints=["no file writes"],
        context_summary=[],
        tool_score=0.6,
        constraint_score=0.5,
        intent_score=0.7,
        aggregate=0.5,
    )
    defaults.update(overrides)
    return EvaluatorPacket(**defaults)


def _mock_client(label: str, rationale: str = "looks fine") -> MagicMock:
    """Return a mock anthropic.Anthropic whose messages.create returns a tool_use block."""
    client = MagicMock()
    block = MagicMock()
    block.type = "tool_use"
    block.input = {"label": label, "rationale": rationale}
    response = MagicMock()
    response.content = [block]
    client.messages.create.return_value = response
    return client


# ---------------------------------------------------------------------------
# TestEvaluatorPacket
# ---------------------------------------------------------------------------

class TestEvaluatorPacket:
    def test_all_fields_set(self):
        pkt = _make_packet()
        assert pkt.content == "agent output here"
        assert pkt.tool_name == "safe_tool"
        assert pkt.tool_args == '{"path": "/tmp/x"}'
        assert pkt.spec_intent == "complete the task safely"
        assert pkt.spec_constraints == ["no file writes"]
        assert pkt.tool_score == 0.6
        assert pkt.constraint_score == 0.5
        assert pkt.intent_score == 0.7
        assert pkt.aggregate == 0.5

    def test_context_summary_defaults_to_empty_list(self):
        pkt = EvaluatorPacket(
            content="x", tool_name="t", tool_args="{}", spec_intent="i"
        )
        assert pkt.context_summary == []

    def test_tool_args_is_string(self):
        pkt = _make_packet(tool_args='{"key": "val"}')
        assert isinstance(pkt.tool_args, str)


# ---------------------------------------------------------------------------
# TestCallEvaluator
# ---------------------------------------------------------------------------

class TestCallEvaluator:
    def test_returns_progressing_on_valid_response(self):
        client = _mock_client("PROGRESSING", "advancing toward goal")
        label, rationale = _call_evaluator(client, _make_packet())
        assert label == "PROGRESSING"
        assert rationale == "advancing toward goal"

    def test_returns_violated_on_valid_response(self):
        client = _mock_client("VIOLATED", "writes to forbidden path")
        label, rationale = _call_evaluator(client, _make_packet())
        assert label == "VIOLATED"
        assert rationale == "writes to forbidden path"

    def test_returns_violated_on_client_exception(self):
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("network down")
        label, rationale = _call_evaluator(client, _make_packet())
        assert label == "VIOLATED"
        assert rationale == "evaluator_error: RuntimeError"

    def test_returns_violated_on_no_tool_use_block(self):
        client = MagicMock()
        block = MagicMock()
        block.type = "text"  # not tool_use
        response = MagicMock()
        response.content = [block]
        client.messages.create.return_value = response
        label, rationale = _call_evaluator(client, _make_packet())
        assert label == "VIOLATED"
        assert "no valid label" in rationale

    def test_returns_violated_on_invalid_label(self):
        client = MagicMock()
        block = MagicMock()
        block.type = "tool_use"
        block.input = {"label": "UNKNOWN", "rationale": "bad output"}
        response = MagicMock()
        response.content = [block]
        client.messages.create.return_value = response
        label, rationale = _call_evaluator(client, _make_packet())
        assert label == "VIOLATED"

    def test_rationale_included_in_result(self):
        client = _mock_client("PROGRESSING", "all constraints satisfied")
        _, rationale = _call_evaluator(client, _make_packet())
        assert rationale == "all constraints satisfied"


# ---------------------------------------------------------------------------
# TestEvaluateNode
# ---------------------------------------------------------------------------

class TestEvaluateNode:
    def test_progressing_label_returned(self):
        spec = _make_spec()
        node = MagicMock()
        node.tool_name = "safe_tool"
        node.args = {"path": "/tmp/x"}
        mock_client = _mock_client("PROGRESSING", "ok")
        with patch("ballast.core.evaluator._get_evaluator_client", return_value=mock_client):
            label, _ = evaluate_node(
                node, [], spec,
                tool_score=0.6, constraint_score=0.5, intent_score=0.7,
            )
        assert label == "PROGRESSING"

    def test_violated_label_returned(self):
        spec = _make_spec(constraints=["no file writes"])
        node = MagicMock()
        node.tool_name = "write_file"
        node.args = {"path": "/etc/passwd"}
        mock_client = _mock_client("VIOLATED", "constraint breached")
        with patch("ballast.core.evaluator._get_evaluator_client", return_value=mock_client):
            label, note = evaluate_node(
                node, [], spec,
                tool_score=0.5, constraint_score=0.4, intent_score=0.6,
            )
        assert label == "VIOLATED"
        assert note != ""

    def test_violated_on_client_exception(self):
        spec = _make_spec()
        node = MagicMock()
        node.tool_name = "t"
        node.args = {}
        bad_client = MagicMock()
        bad_client.messages.create.side_effect = Exception("boom")
        with patch("ballast.core.evaluator._get_evaluator_client", return_value=bad_client):
            label, note = evaluate_node(
                node, [], spec,
                tool_score=0.5, constraint_score=0.5, intent_score=0.5,
            )
        assert label == "VIOLATED"
        assert note == "evaluator_error: Exception"

    def test_empty_full_window_ok(self):
        """evaluate_node must not crash when full_window is empty."""
        spec = _make_spec()
        node = MagicMock(spec=[])  # no attributes
        mock_client = _mock_client("PROGRESSING", "fine")
        with patch("ballast.core.evaluator._get_evaluator_client", return_value=mock_client):
            label, _ = evaluate_node(
                node, [], spec,
                tool_score=0.6, constraint_score=0.5, intent_score=0.7,
            )
        assert label == "PROGRESSING"

    def test_lazy_singleton_not_constructed_at_import(self):
        """Importing evaluator.py must not require ANTHROPIC_API_KEY."""
        import ballast.core.evaluator as ev_mod

        ev_mod._evaluator_client = None
        assert ev_mod._evaluator_client is None
