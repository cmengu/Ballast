"""TinyFish adapter stub — implement in Week 1 day 2."""
from ballast.core.stream import AgentStream


class TinyFishAdapter(AgentStream):
    """Bridges TinyFish agent protocol to AG-UI event stream."""

    async def stream(self, goal: str, spec: dict):
        raise NotImplementedError("TinyFishAdapter.stream() not yet implemented")
