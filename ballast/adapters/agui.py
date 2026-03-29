"""AG-UI adapter stub — implement in Week 1 day 2."""
from ballast.core.stream import AgentStream


class AGUIAdapter(AgentStream):
    """Streams AG-UI events from a LangGraph agent."""

    async def stream(self, goal: str, spec: dict):
        raise NotImplementedError("AGUIAdapter.stream() not yet implemented")
