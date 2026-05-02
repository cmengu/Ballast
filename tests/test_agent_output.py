"""Unit tests for ballast.core.agent_output.agent_run_result_payload."""

from unittest.mock import MagicMock, Mock

from ballast.core.agent_output import agent_run_result_payload


class _ResultDataOnly:
    def __init__(self, data):
        self.data = data


class _ResultOutputOnly:
    def __init__(self, output):
        self.output = output


def test_real_result_prefers_data_over_output():
    r = _ResultDataOnly("from-data")
    r.output = "from-output"  # both set: data wins via getattr order
    assert agent_run_result_payload(r) == "from-data"


def test_real_result_uses_output_when_no_data_attr():
    r = _ResultOutputOnly("out")
    assert agent_run_result_payload(r) == "out"


def test_real_result_falls_back_to_object_when_no_data_or_output():
    r = object()
    assert agent_run_result_payload(r) is r


def test_mock_prefers_output_from_dict_over_autovivified_data():
    """MagicMock would create .data on getattr; __dict__ must win."""
    m = MagicMock()
    m.output = "assigned-output"
    assert agent_run_result_payload(m) == "assigned-output"


def test_mock_uses_data_from_dict_when_output_not_set():
    m = MagicMock()
    m.data = "assigned-data"
    assert agent_run_result_payload(m) == "assigned-data"


def test_plain_mock_subclass_uses_output_from_dict():
    class Plain(Mock):
        pass

    m = Plain()
    m.output = 42
    assert agent_run_result_payload(m) == 42
