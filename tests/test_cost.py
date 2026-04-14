"""Tests for ballast/core/cost.py — cost cap enforcement.

Sections:
    AgentCostGuard  — unit tests; no pydantic-ai dependency
    RunCostGuard    — unit tests; no pydantic-ai dependency
    run_with_spec   — integration: cost_guard wired into orchestration loop
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock, patch

import pytest

from ballast.core.cost import (
    HARD_CAP_USD,
    AgentCapExceeded,
    AgentCostGuard,
    EscalationBudgetExceeded,
    HardCapExceeded,
    RunCostGuard,
)
from ballast.core.spec import SpecModel, lock
from ballast.core.trajectory import NodeAssessment, run_with_spec

_MOCK_A_PROGRESSING = NodeAssessment(
    score=1.0, label="PROGRESSING", rationale="",
    tool_score=1.0, constraint_score=1.0, intent_score=1.0, tool_name="",
)


# ---------------------------------------------------------------------------
# AgentCostGuard
# ---------------------------------------------------------------------------

def test_agent_guard_allows_under_cap():
    g = AgentCostGuard("worker", agent_cap_usd=0.10, escalation_pool_usd=0.03)
    g.check(0.05)  # must not raise


def test_agent_guard_raises_when_spend_plus_estimated_exceeds_cap():
    g = AgentCostGuard("worker", agent_cap_usd=0.10, escalation_pool_usd=0.03)
    g.record(0.10)
    with pytest.raises(AgentCapExceeded):
        g.check(0.001)


def test_agent_guard_check_does_not_mutate_spent():
    g = AgentCostGuard("worker", agent_cap_usd=1.0, escalation_pool_usd=0.1)
    g.check(0.50)
    assert g.spent == 0.0  # check must never change state


def test_agent_guard_record_accumulates_correctly():
    g = AgentCostGuard("worker", agent_cap_usd=1.0, escalation_pool_usd=0.1)
    g.record(0.30)
    g.record(0.40)
    assert round(g.spent, 4) == 0.70


def test_agent_guard_escalation_pool_is_independent_of_main_cap():
    g = AgentCostGuard("worker", agent_cap_usd=0.10, escalation_pool_usd=0.03)
    g.record(0.09, is_escalation=False)
    g.check(0.03, is_escalation=True)  # escalation pool is separate — must not raise


def test_agent_guard_escalation_raises_when_pool_exhausted():
    g = AgentCostGuard("worker", agent_cap_usd=0.10, escalation_pool_usd=0.03)
    g.record(0.03, is_escalation=True)
    with pytest.raises(EscalationBudgetExceeded):
        g.check(0.001, is_escalation=True)


def test_agent_cap_exceeded_carries_agent_id():
    g = AgentCostGuard("broker", agent_cap_usd=0.05, escalation_pool_usd=0.01)
    g.record(0.05)
    with pytest.raises(AgentCapExceeded) as exc_info:
        g.check(0.001)
    assert exc_info.value.agent_id == "broker"


def test_escalation_budget_exceeded_carries_agent_id():
    g = AgentCostGuard("ceo", agent_cap_usd=0.10, escalation_pool_usd=0.02)
    g.record(0.02, is_escalation=True)
    with pytest.raises(EscalationBudgetExceeded) as exc_info:
        g.check(0.001, is_escalation=True)
    assert exc_info.value.agent_id == "ceo"


# ---------------------------------------------------------------------------
# RunCostGuard
# ---------------------------------------------------------------------------

def test_run_guard_allows_under_all_caps():
    rg = RunCostGuard()
    rg.register("worker", cap=0.10, escalation_pool=0.03)
    rg.check("worker", 0.05)  # must not raise


def test_run_guard_raises_hard_cap_before_agent_cap():
    """HardCapExceeded fires even when per-agent cap would allow it."""
    rg = RunCostGuard(hard_cap_usd=1.0)
    rg.register("worker", cap=10.0, escalation_pool=0.0)
    rg.record("worker", 0.999)
    with pytest.raises(HardCapExceeded):
        rg.check("worker", 0.002)


def test_run_guard_hard_cap_exceeded_carries_total_and_estimated():
    rg = RunCostGuard(hard_cap_usd=1.0)
    rg.register("worker", cap=10.0, escalation_pool=0.0)
    rg.record("worker", 0.999)
    with pytest.raises(HardCapExceeded) as exc_info:
        rg.check("worker", 0.002)
    assert exc_info.value.total == pytest.approx(0.999)
    assert exc_info.value.estimated == pytest.approx(0.002)
    assert exc_info.value.hard_cap == pytest.approx(1.0)


def test_run_guard_custom_hard_cap_respected():
    """RunCostGuard(hard_cap_usd=X) enforces X, not the module-level default."""
    rg = RunCostGuard(hard_cap_usd=0.50)
    rg.register("worker", cap=10.0, escalation_pool=0.0)
    rg.record("worker", 0.40)
    with pytest.raises(HardCapExceeded):
        rg.check("worker", 0.20)   # 0.40 + 0.20 = 0.60 > 0.50
    rg.check("worker", 0.09)       # 0.40 + 0.09 = 0.49 < 0.50 — must not raise


def test_run_guard_record_advances_global_total():
    rg = RunCostGuard()
    rg.register("worker", cap=1.0, escalation_pool=0.1)
    rg.record("worker", 0.05)
    rg.record("worker", 0.10)
    assert round(rg.total_spent, 4) == 0.15


def test_run_guard_unregistered_agent_raises_key_error():
    rg = RunCostGuard()
    with pytest.raises(KeyError):
        rg.check("ghost", 0.01)


def test_run_guard_report_contains_expected_keys():
    rg = RunCostGuard()
    rg.register("worker", cap=0.10, escalation_pool=0.03)
    rg.record("worker", 0.05)
    r = rg.report()
    assert set(r.keys()) == {"total_spent", "hard_cap", "remaining", "agents"}
    assert "worker" in r["agents"]
    assert r["agents"]["worker"]["spent"] == pytest.approx(0.05)
    assert r["hard_cap"] == HARD_CAP_USD


def test_run_guard_remaining_decrements_on_record():
    rg = RunCostGuard()
    rg.register("worker", cap=1.0, escalation_pool=0.1)
    rg.record("worker", 0.25)
    assert rg.report()["remaining"] == pytest.approx(HARD_CAP_USD - 0.25)


# ---------------------------------------------------------------------------
# run_with_spec integration — cost_guard wiring
# ---------------------------------------------------------------------------

class _RwsNode:
    """Minimal stand-in for a pydantic-ai node."""


class _RwsAgentRun:
    """Mock AgentRun for cost integration tests.

    _gen sets node.cost_usd before yielding each node so that
    getattr(node, 'cost_usd', 0.0) in trajectory.py returns the test value.
    """

    def __init__(self, nodes, output="done", node_costs=None):
        self._nodes = nodes
        self._output = output
        self._node_costs = node_costs or [0.0] * len(nodes)
        self.message_history = []
        state = MagicMock()
        state.message_history = self.message_history
        ctx = MagicMock()
        ctx.state = state
        self._ctx = ctx

    @property
    def ctx(self):
        return self._ctx

    async def get_output(self):
        return self._output

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for i, node in enumerate(self._nodes):
            node.cost_usd = self._node_costs[i]  # set before yield
            yield node


def _rws_make_agent(nodes, output="done", node_costs=None):
    run = _RwsAgentRun(nodes, output, node_costs)
    agent = MagicMock()

    @asynccontextmanager
    async def _iter(task):
        yield run

    agent.iter = _iter
    return agent, run


def _make_spec():
    return lock(SpecModel(intent="count words", success_criteria=["returns integer"]))


def test_run_with_spec_records_node_costs_in_guard(tmp_path, monkeypatch):
    """RunCostGuard.total_spent equals sum of all node costs after a clean run."""
    monkeypatch.chdir(tmp_path)
    spec = _make_spec()
    nodes = [_RwsNode(), _RwsNode(), _RwsNode()]
    agent, _ = _rws_make_agent(nodes, node_costs=[0.01, 0.02, 0.03])
    rg = RunCostGuard()
    rg.register("worker", cap=1.0, escalation_pool=0.1)
    with patch("ballast.core.trajectory.score_drift", return_value=_MOCK_A_PROGRESSING):
        asyncio.run(run_with_spec(agent, "task", spec, cost_guard=rg, agent_id="worker"))
    assert round(rg.total_spent, 4) == 0.06


def test_run_with_spec_agent_cap_stops_run(tmp_path, monkeypatch):
    """AgentCapExceeded propagates out of run_with_spec when per-agent cap is hit."""
    monkeypatch.chdir(tmp_path)
    spec = _make_spec()
    # cap=0.015: node 0 costs 0.01 (passes), node 1 costs 0.01 (0.01+0.01=0.02 > 0.015 → raises)
    nodes = [_RwsNode(), _RwsNode(), _RwsNode()]
    agent, _ = _rws_make_agent(nodes, node_costs=[0.01, 0.01, 0.01])
    rg = RunCostGuard()
    rg.register("worker", cap=0.015, escalation_pool=0.0)
    with patch("ballast.core.trajectory.score_drift", return_value=_MOCK_A_PROGRESSING):
        with pytest.raises(AgentCapExceeded):
            asyncio.run(run_with_spec(agent, "task", spec, cost_guard=rg, agent_id="worker"))


def test_run_with_spec_hard_cap_stops_run(tmp_path, monkeypatch):
    """HardCapExceeded propagates when global hard cap is hit."""
    monkeypatch.chdir(tmp_path)
    spec = _make_spec()
    # hard_cap=1.0; pre-record 0.5; node costs 0.6 → 1.1 > 1.0 → raises
    nodes = [_RwsNode()]
    agent, _ = _rws_make_agent(nodes, node_costs=[0.6])
    rg = RunCostGuard(hard_cap_usd=1.0)
    rg.register("worker", cap=500.0, escalation_pool=0.0)
    rg.record("worker", 0.5)
    with patch("ballast.core.trajectory.score_drift", return_value=_MOCK_A_PROGRESSING):
        with pytest.raises(HardCapExceeded):
            asyncio.run(run_with_spec(agent, "task", spec, cost_guard=rg, agent_id="worker"))


def test_run_with_spec_no_cost_guard_is_backward_compatible(tmp_path, monkeypatch):
    """Omitting cost_guard entirely — run completes normally, no exceptions raised."""
    monkeypatch.chdir(tmp_path)
    spec = _make_spec()
    nodes = [_RwsNode()]
    agent, _ = _rws_make_agent(nodes)
    with patch("ballast.core.trajectory.score_drift", return_value=_MOCK_A_PROGRESSING):
        out = asyncio.run(run_with_spec(agent, "task", spec))
    assert out == "done"
