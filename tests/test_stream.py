import inspect
import pytest
from ballast.core.stream import AgentStream


def test_agentstream_is_abstract():
    """AgentStream cannot be instantiated directly — it must be subclassed."""
    assert inspect.isabstract(AgentStream), (
        "AgentStream must be an ABC with at least one abstractmethod"
    )


def test_agentstream_has_stream_method():
    """stream() must be declared as an abstractmethod."""
    assert "stream" in AgentStream.__abstractmethods__, (
        "stream() must be in __abstractmethods__"
    )


def test_agentstream_has_inject_method():
    """inject() must exist as a concrete (non-abstract) method with a default implementation."""
    assert hasattr(AgentStream, "inject"), "inject() method must exist on AgentStream"
    assert "inject" not in AgentStream.__abstractmethods__, (
        "inject() must NOT be abstract — it has a default NotImplementedError implementation"
    )


def test_agentstream_inject_raises_not_implemented():
    """The default inject() raises NotImplementedError."""

    class ConcreteAdapter(AgentStream):
        async def stream(self, goal: str, spec: dict):
            yield

    import asyncio

    adapter = ConcreteAdapter()
    with pytest.raises(NotImplementedError):
        asyncio.run(adapter.inject("thread-1", "hello"))
