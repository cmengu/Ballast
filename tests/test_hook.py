"""tests/test_hook.py — run_with_live_spec unit tests.

All 8 tests are unit tests: no LLM calls, no HTTP calls.
Agent and SpecPoller are replaced with lightweight fakes.
Tests are sync functions using asyncio.run() — matches project convention.

Mock design:
    _MockAgentRun: async-iterable; stable ctx.state.message_history (same list every call);
                   result.output returns configured string.
    _make_agent:   builds a mock Agent whose .iter() is an asynccontextmanager.
    _make_poller:  MagicMock with poll().side_effect returning a list in order.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

from pydantic_ai.messages import ModelRequest

from ballast.core.hook import run_with_live_spec
from ballast.core.spec import SpecModel, lock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _locked_spec(**overrides) -> SpecModel:
    base = dict(
        intent="Write a report",
        success_criteria=["report exists"],
        constraints=[],
        allowed_tools=[],
    )
    base.update(overrides)
    return lock(SpecModel(**base))


class _MockNode:
    """Minimal stand-in for a pydantic-ai node."""


class _MockAgentRun:
    """Fake AgentRun: async-iterable, stable ctx.state.message_history, result.output."""

    def __init__(self, nodes: list, output: str = "done") -> None:
        self._nodes = nodes
        self.message_history: list = []

        # Stable ctx so message_history.append() writes to the same list every call.
        # A new MagicMock() per ctx access would create a fresh message_history each time,
        # causing append() to write to a throwaway list — this is why _ctx is pre-built.
        state = MagicMock()
        state.message_history = self.message_history
        ctx = MagicMock()
        ctx.state = state
        self._ctx = ctx

        result = MagicMock()
        result.output = output
        self.result = result

    @property
    def ctx(self):
        return self._ctx

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for node in self._nodes:
            yield node


def _make_agent(nodes: list, output: str = "done") -> tuple:
    """Return (mock_agent, mock_run). agent.iter is an asynccontextmanager."""
    run = _MockAgentRun(nodes, output)
    agent = MagicMock()

    @asynccontextmanager
    async def _iter(task):
        yield run

    agent.iter = _iter
    return agent, run


def _make_poller(return_values: list) -> MagicMock:
    """SpecPoller mock whose poll() returns values from the list in order."""
    poller = MagicMock()
    poller.poll.side_effect = return_values
    return poller


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_audit_log_length_matches_nodes():
    """audit_log has exactly one entry per node."""
    nodes = [_MockNode(), _MockNode(), _MockNode()]
    agent, _ = _make_agent(nodes)
    poller = _make_poller([None, None, None])
    spec = _locked_spec()

    _, audit_log = asyncio.run(run_with_live_spec(agent, "task", spec, poller))

    assert len(audit_log) == 3


def test_audit_log_entry_fields():
    """Each audit_log entry has correct keys and correct types when no spec change."""
    nodes = [_MockNode()]
    agent, _ = _make_agent(nodes)
    poller = _make_poller([None])
    spec = _locked_spec()

    _, audit_log = asyncio.run(run_with_live_spec(agent, "task", spec, poller))

    entry = audit_log[0]
    assert entry["node_index"] == 0
    assert entry["spec_hash"] == spec.version_hash
    assert entry["node_type"] == "_MockNode"
    assert entry["delta_injected"] is None


def test_spec_update_switches_hash_in_audit_log():
    """Audit log reflects new spec_hash from the node where poller returns a new spec."""
    nodes = [_MockNode(), _MockNode(), _MockNode()]
    # Use different intent + success_criteria to ensure different version hashes
    spec_v1 = _locked_spec(intent="Task A", success_criteria=["done A"])
    spec_v2 = _locked_spec(intent="Task B", success_criteria=["done B"])

    agent, _ = _make_agent(nodes)
    poller = _make_poller([None, spec_v2, None])  # spec changes at node 1

    _, audit_log = asyncio.run(run_with_live_spec(agent, "task", spec_v1, poller))

    assert audit_log[0]["spec_hash"] == spec_v1.version_hash
    assert audit_log[1]["spec_hash"] == spec_v2.version_hash
    assert audit_log[2]["spec_hash"] == spec_v2.version_hash


def test_spec_update_injects_model_request():
    """When spec changes, a ModelRequest containing the constraint is appended to message_history.

    Note: spec_v1 and spec_v2 differ by constraints, so version_hash values differ.
    We're testing injection content (constraint text), not delta hash display.
    """
    nodes = [_MockNode(), _MockNode()]
    spec_v1 = _locked_spec(intent="Task A", success_criteria=["done A"])
    spec_v2 = _locked_spec(
        intent="Task A",
        success_criteria=["done A"],
        constraints=["do not mention X"],
    )

    agent, run = _make_agent(nodes)
    poller = _make_poller([spec_v2, None])  # spec changes at node 0

    asyncio.run(run_with_live_spec(agent, "task", spec_v1, poller))

    assert len(run.message_history) == 1
    injected = run.message_history[0]
    assert isinstance(injected, ModelRequest)
    assert "do not mention X" in injected.parts[0].content


def test_no_injection_when_poller_returns_none():
    """When poller always returns None, message_history stays empty."""
    nodes = [_MockNode(), _MockNode()]
    agent, run = _make_agent(nodes)
    poller = _make_poller([None, None])
    spec = _locked_spec()

    asyncio.run(run_with_live_spec(agent, "task", spec, poller))

    assert run.message_history == []


def test_return_value_tuple():
    """Returns (output, audit_log) where output matches agent result and audit_log is a list."""
    nodes = [_MockNode()]
    agent, _ = _make_agent(nodes, output="my result")
    poller = _make_poller([None])
    spec = _locked_spec()

    output, audit_log = asyncio.run(run_with_live_spec(agent, "task", spec, poller))

    assert output == "my result"
    assert isinstance(audit_log, list)


def test_node_logged_at_debug(caplog):
    """Each node is logged at DEBUG: 'node=00 spec=XXXXXXXX type=_MockNode'."""
    import logging
    nodes = [_MockNode()]
    spec = _locked_spec()
    agent, _ = _make_agent(nodes)
    poller = _make_poller([None])

    with caplog.at_level(logging.DEBUG, logger="ballast.core.hook"):
        asyncio.run(run_with_live_spec(agent, "task", spec, poller))

    assert any(
        f"spec={spec.version_hash[:8]}" in r.message and "_MockNode" in r.message
        for r in caplog.records
    )


def test_on_node_callback_called_with_correct_args():
    """on_node is called once per node with (node_index, node, active_spec, delta)."""
    nodes = [_MockNode(), _MockNode()]
    spec = _locked_spec()
    agent, _ = _make_agent(nodes)
    poller = _make_poller([None, None])

    calls: list = []

    async def on_node(node_index, node, active_spec, delta):
        calls.append((node_index, type(node).__name__, active_spec.version_hash, delta))

    asyncio.run(run_with_live_spec(agent, "task", spec, poller, on_node=on_node))

    assert len(calls) == 2
    assert calls[0][0] == 0
    assert calls[1][0] == 1
    assert calls[0][2] == spec.version_hash
    assert calls[0][3] is None   # no delta at node 0
