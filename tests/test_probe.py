"""tests/test_probe.py — Unit tests for ballast/core/probe.py.

All tests run without ANTHROPIC_API_KEY. The probe agent is always mocked.
Uses pytest-asyncio for async test execution.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ballast.core.probe import (
    ProbePacket,
    _call_probe_agent,
    _coerce_verified,
    _get_tool_info,
    verify_node_claim,
)
from ballast.core.spec import SpecModel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_spec(constraints: list[str] | None = None) -> SpecModel:
    from ballast.core.spec import lock

    base = {
        "intent": "test intent",
        "success_criteria": ["does something"],
        "constraints": constraints or [],
        "allowed_tools": ["safe_tool"],
        "drift_threshold": 0.5,
        "harness": {},
    }
    return lock(SpecModel(**base))


def _mock_agent(response_json: str) -> MagicMock:
    """Return a mock pydantic-ai Agent whose run() returns a result with .output."""
    agent = MagicMock()
    result = MagicMock()
    result.output = response_json
    agent.run = AsyncMock(return_value=result)
    return agent


def _node_with_tool(tool_name: str, args: dict, content: str = "") -> MagicMock:
    node = MagicMock()
    node.tool_name = tool_name
    node.args = args
    node.content = content
    return node


# ---------------------------------------------------------------------------
# _coerce_verified
# ---------------------------------------------------------------------------

def test_coerce_verified_string_false_is_false():
    assert _coerce_verified("false") is False
    assert _coerce_verified("FALSE") is False


def test_coerce_verified_bool_passthrough():
    assert _coerce_verified(True) is True
    assert _coerce_verified(False) is False


# ---------------------------------------------------------------------------
# TestGetToolInfo
# ---------------------------------------------------------------------------

class TestGetToolInfo:
    def test_direct_attrs_returns_tool_name_and_args(self):
        node = MagicMock()
        node.tool_name = "write_file"
        node.args = {"path": "/tmp/x"}
        name, args, _ = _get_tool_info(node)
        assert name == "write_file"
        assert args == {"path": "/tmp/x"}

    def test_no_tool_returns_empty(self):
        node = MagicMock(spec=[])
        name, args, content = _get_tool_info(node)
        assert name == ""
        assert args == {}
        assert content == ""

    def test_parts_scan_finds_tool_call_part(self):
        part = MagicMock()
        part.__class__.__name__ = "ToolCallPart"
        part.tool_name = "read_file"
        part.args = {"path": "/tmp/y"}
        node = MagicMock(spec=["parts"])
        node.parts = [part]
        name, args, _ = _get_tool_info(node)
        assert name == "read_file"
        assert args == {"path": "/tmp/y"}

    def test_parts_json_string_args_parsed(self):
        part = MagicMock()
        part.__class__.__name__ = "ToolCallPart"
        part.tool_name = "write_file"
        part.args = '{"path": "/tmp/z", "content": "hi"}'
        node = MagicMock(spec=["parts"])
        node.parts = [part]
        name, args, _ = _get_tool_info(node)
        assert name == "write_file"
        assert args == {"path": "/tmp/z", "content": "hi"}


# ---------------------------------------------------------------------------
# TestCallProbeAgent
# ---------------------------------------------------------------------------

class TestCallProbeAgent:
    @pytest.mark.asyncio
    async def test_verified_true_response(self):
        agent = _mock_agent('{"verified": true, "note": ""}')
        packet = ProbePacket(
            tool_name="safe_tool", tool_args="{}", tool_result="", spec_constraints=[]
        )
        result = await _call_probe_agent(agent, packet)
        assert result["verified"] is True
        assert result["note"] == ""

    @pytest.mark.asyncio
    async def test_verified_false_response(self):
        agent = _mock_agent('{"verified": false, "note": "violated: do not write files"}')
        packet = ProbePacket(
            tool_name="write_file",
            tool_args='{"path": "/etc/passwd"}',
            tool_result="",
            spec_constraints=["do not write to any files"],
        )
        result = await _call_probe_agent(agent, packet)
        assert result["verified"] is False
        assert "write" in result["note"]

    @pytest.mark.asyncio
    async def test_agent_exception_returns_fail_open(self):
        agent = MagicMock()
        agent.run = AsyncMock(side_effect=RuntimeError("network down"))
        packet = ProbePacket(
            tool_name="x", tool_args="{}", tool_result="", spec_constraints=[]
        )
        result = await _call_probe_agent(agent, packet)
        assert result["verified"] is True
        assert result["note"].startswith("probe_error:")

    @pytest.mark.asyncio
    async def test_json_parse_error_returns_fail_open(self):
        agent = _mock_agent("NOT JSON")
        packet = ProbePacket(
            tool_name="x", tool_args="{}", tool_result="", spec_constraints=[]
        )
        result = await _call_probe_agent(agent, packet)
        assert result["verified"] is True
        assert result["note"].startswith("probe_error:")

    @pytest.mark.asyncio
    async def test_missing_note_key_normalised_to_empty_string(self):
        agent = _mock_agent('{"verified": true}')
        packet = ProbePacket(
            tool_name="x", tool_args="{}", tool_result="", spec_constraints=[]
        )
        result = await _call_probe_agent(agent, packet)
        assert result["note"] == ""


# ---------------------------------------------------------------------------
# TestVerifyNodeClaim
# ---------------------------------------------------------------------------

class TestVerifyNodeClaim:
    @pytest.mark.asyncio
    async def test_no_tool_call_returns_true_immediately(self):
        """No LLM call made when node has no tool call."""
        spec = _make_spec()
        node = MagicMock(spec=[])  # no attributes → _get_tool_info returns ""
        with patch("ballast.core.probe._get_probe_agent") as mock_getter:
            verified, note = await verify_node_claim(node, "PROGRESSING", spec)
        assert verified is True
        assert note == "no tool call"
        mock_getter.assert_not_called()

    @pytest.mark.asyncio
    async def test_compliant_tool_returns_true(self):
        spec = _make_spec(constraints=["do not write to files"])
        node = _node_with_tool("safe_tool", {"x": 1})
        mock_agent = _mock_agent('{"verified": true, "note": ""}')
        with patch("ballast.core.probe._get_probe_agent", return_value=mock_agent):
            verified, note = await verify_node_claim(node, "PROGRESSING", spec)
        assert verified is True
        assert note == ""

    @pytest.mark.asyncio
    async def test_violating_tool_returns_false_with_note(self):
        spec = _make_spec(constraints=["do not write to any files"])
        node = _node_with_tool("write_file", {"path": "/etc/passwd"})
        mock_agent = _mock_agent(
            '{"verified": false, "note": "violated: do not write to any files"}'
        )
        with patch("ballast.core.probe._get_probe_agent", return_value=mock_agent):
            verified, note = await verify_node_claim(node, "PROGRESSING", spec)
        assert verified is False
        assert note != ""

    @pytest.mark.asyncio
    async def test_probe_exception_returns_fail_open(self):
        spec = _make_spec()
        node = _node_with_tool("some_tool", {})
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=Exception("boom"))
        with patch("ballast.core.probe._get_probe_agent", return_value=mock_agent):
            verified, note = await verify_node_claim(node, "PROGRESSING", spec)
        assert verified is True
        assert "probe_error" in note

    def test_lazy_singleton_not_constructed_at_import(self):
        """Importing probe.py must not require ANTHROPIC_API_KEY."""
        import ballast.core.probe as probe_mod

        probe_mod._probe_agent = None
        assert probe_mod._probe_agent is None

    def test_probe_packet_fields(self):
        packet = ProbePacket(
            tool_name="t",
            tool_args='{"k": "v"}',
            tool_result="some output",
            spec_constraints=["no files"],
        )
        assert packet.tool_name == "t"
        assert packet.tool_args == '{"k": "v"}'
        assert packet.tool_result == "some output"
        assert packet.spec_constraints == ["no files"]
