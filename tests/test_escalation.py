"""tests/test_escalation.py — Unit tests for ballast/core/escalation.py.

19 tests total:
    TestEscalationPacket   (6)
    TestEscalationFailed   (3)
    TestCallLevel          (4) — internal; patched
    TestEscalate           (6) — full chain; _call_level patched
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ballast.core.escalation import (
    EscalationFailed,
    EscalationPacket,
    _call_level,
    escalate,
)
from ballast.core.spec import SpecModel, lock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(**overrides) -> SpecModel:
    base = dict(
        intent="Test intent for escalation tests",
        success_criteria=["criterion A"],
        constraints=["no irreversible actions"],
        irreversible_actions=["delete_database"],
        drift_threshold=0.7,
        allowed_tools=["read_file"],
        scope="test",
    )
    base.update(overrides)
    return lock(SpecModel(**base))


def _make_assessment(
    tool_name: str | None = "delete_database",
    score: float = 0.1,
    label: str = "VIOLATED_IRREVERSIBLE",
    rationale: str = "Irreversible action detected",
):
    """Return a minimal NodeAssessment-like mock."""
    a = MagicMock()
    a.tool_name = tool_name
    a.score = score
    a.label = label
    a.rationale = rationale
    return a


# ---------------------------------------------------------------------------
# TestEscalationPacket
# ---------------------------------------------------------------------------

class TestEscalationPacket:
    def test_all_fields_set(self):
        spec = _make_spec()
        a = _make_assessment()
        ctx = [{"role": "user", "content": "do something"}]
        pkt = EscalationPacket(
            assessment=a, spec=spec, context=ctx, run_id="r1", node_index=3
        )
        assert pkt.assessment is a
        assert pkt.spec is spec
        assert pkt.context is ctx
        assert pkt.run_id == "r1"
        assert pkt.node_index == 3

    def test_run_id_defaults_to_empty_string(self):
        pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
        assert pkt.run_id == ""

    def test_node_index_defaults_to_zero(self):
        pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
        assert pkt.node_index == 0

    def test_context_is_stored_by_reference(self):
        ctx = [1, 2, 3]
        pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=ctx)
        ctx.append(4)
        assert pkt.context == [1, 2, 3, 4]

    def test_assessment_tool_name_accessible(self):
        a = _make_assessment(tool_name="nuke_prod")
        pkt = EscalationPacket(assessment=a, spec=_make_spec(), context=[])
        assert pkt.assessment.tool_name == "nuke_prod"

    def test_spec_version_hash_accessible(self):
        spec = _make_spec()
        pkt = EscalationPacket(assessment=_make_assessment(), spec=spec, context=[])
        assert len(pkt.spec.version_hash) > 0


# ---------------------------------------------------------------------------
# TestEscalationFailed
# ---------------------------------------------------------------------------

class TestEscalationFailed:
    def test_carries_assessment(self):
        a = _make_assessment()
        spec = _make_spec()
        exc = EscalationFailed(a, spec)
        assert exc.assessment is a

    def test_carries_spec(self):
        a = _make_assessment()
        spec = _make_spec()
        exc = EscalationFailed(a, spec)
        assert exc.spec is spec

    def test_message_contains_tool_name_and_version(self):
        a = _make_assessment(tool_name="nuke_prod")
        spec = _make_spec()
        exc = EscalationFailed(a, spec)
        msg = str(exc)
        assert "nuke_prod" in msg
        assert spec.version_hash[:8] in msg


# ---------------------------------------------------------------------------
# TestCallLevel
# ---------------------------------------------------------------------------

class TestCallLevel:
    @pytest.mark.asyncio
    async def test_returns_dict_on_valid_json_response(self):
        agent = MagicMock()
        result = MagicMock()
        result.output = '{"escalate": false, "resolution": "stop and revert"}'
        agent.run = AsyncMock(return_value=result)
        pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
        out = await _call_level(agent, pkt)
        assert out == {"escalate": False, "resolution": "stop and revert"}

    @pytest.mark.asyncio
    async def test_returns_escalate_true_on_invalid_json(self):
        agent = MagicMock()
        result = MagicMock()
        result.output = "not json at all"
        agent.run = AsyncMock(return_value=result)
        pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
        out = await _call_level(agent, pkt)
        assert out == {"escalate": True}

    @pytest.mark.asyncio
    async def test_returns_escalate_true_on_run_exception(self):
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
        out = await _call_level(agent, pkt)
        assert out == {"escalate": True}

    @pytest.mark.asyncio
    async def test_uses_str_fallback_when_output_attr_missing(self):
        agent = MagicMock()

        class _NoOutput:
            def __str__(self) -> str:
                return '{"escalate": true}'

        result = _NoOutput()  # no .output — str() returns JSON
        agent.run = AsyncMock(return_value=result)
        pkt = EscalationPacket(assessment=_make_assessment(), spec=_make_spec(), context=[])
        out = await _call_level(agent, pkt)
        assert out.get("escalate") is True


# ---------------------------------------------------------------------------
# TestEscalate
# ---------------------------------------------------------------------------

class TestEscalate:
    """escalate() calls _get_broker_agent() / _get_ceo_agent() before _call_level — patch those too."""

    @pytest.fixture(autouse=True)
    def _mock_escalation_agents(self):
        with patch("ballast.core.escalation._get_broker_agent", return_value=MagicMock()), patch(
            "ballast.core.escalation._get_ceo_agent", return_value=MagicMock()
        ):
            yield

    @pytest.mark.asyncio
    async def test_broker_resolves_returns_resolution(self):
        broker_return = {"escalate": False, "resolution": "redirect to safe path"}
        with patch("ballast.core.escalation._call_level", return_value=broker_return):
            result = await escalate(
                _make_assessment(), _make_spec(), [], run_id="r1", node_index=1
            )
        assert result == "redirect to safe path"

    @pytest.mark.asyncio
    async def test_broker_escalates_ceo_resolves(self):
        returns = [
            {"escalate": True},
            {"escalate": False, "resolution": "CEO override: proceed carefully"},
        ]
        with patch("ballast.core.escalation._call_level", side_effect=returns):
            result = await escalate(
                _make_assessment(), _make_spec(), [], run_id="r2", node_index=2
            )
        assert result == "CEO override: proceed carefully"

    @pytest.mark.asyncio
    async def test_both_escalate_raises_escalation_failed(self):
        returns = [{"escalate": True}, {"escalate": True}]
        with patch("ballast.core.escalation._call_level", side_effect=returns):
            with pytest.raises(EscalationFailed) as exc_info:
                await escalate(
                    _make_assessment(), _make_spec(), [], run_id="r3", node_index=3
                )
        assert exc_info.value.assessment is not None
        assert exc_info.value.spec is not None

    @pytest.mark.asyncio
    async def test_escalation_failed_carries_correct_assessment(self):
        a = _make_assessment(tool_name="nuke_prod")
        spec = _make_spec()
        returns = [{"escalate": True}, {"escalate": True}]
        with patch("ballast.core.escalation._call_level", side_effect=returns):
            with pytest.raises(EscalationFailed) as exc_info:
                await escalate(a, spec, [], run_id="r4", node_index=4)
        assert exc_info.value.assessment is a
        assert exc_info.value.spec is spec

    @pytest.mark.asyncio
    async def test_broker_empty_resolution_treated_as_escalate(self):
        """Empty resolution string in broker result → escalate to CEO."""
        returns = [
            {"escalate": False, "resolution": ""},  # empty — treated as escalate
            {"escalate": False, "resolution": "CEO fallback"},
        ]
        with patch("ballast.core.escalation._call_level", side_effect=returns):
            result = await escalate(
                _make_assessment(), _make_spec(), [], run_id="r5", node_index=5
            )
        assert result == "CEO fallback"

    @pytest.mark.asyncio
    async def test_packet_constructed_with_correct_fields(self):
        """Confirm packet passed to _call_level has all five fields."""
        captured: list[EscalationPacket] = []

        async def capture(agent, packet):
            captured.append(packet)
            return {"escalate": False, "resolution": "ok"}

        a = _make_assessment()
        spec = _make_spec()
        ctx = ["msg1", "msg2"]
        with patch("ballast.core.escalation._call_level", side_effect=capture):
            await escalate(a, spec, ctx, run_id="r6", node_index=7)

        assert len(captured) == 1
        pkt = captured[0]
        assert pkt.assessment is a
        assert pkt.spec is spec
        assert pkt.context is ctx
        assert pkt.run_id == "r6"
        assert pkt.node_index == 7
