from abc import ABC, abstractmethod
from typing import AsyncIterator


class AgentStream(ABC):
    """Base class for all Ballast agent adapters.

    Every adapter must implement `stream()`. The `inject()` method is
    optional — adapters that support mid-task intervention override it.
    """

    @abstractmethod
    async def stream(self, goal: str, spec: dict) -> AsyncIterator[object]:
        """Stream AG-UI events for a given goal and locked spec.

        Args:
            goal: Natural language task description.
            spec: Locked specification dict produced by spec.py (Week 2).

        Yields:
            AG-UI Event objects (typed once ag-ui-protocol is imported).
        """
        ...

    async def inject(self, thread_id: str, message: str) -> None:
        """Inject a message into a running task (pause/resume flow).

        Adapters that support intervention override this method.
        Default raises NotImplementedError to make the gap explicit.
        """
        raise NotImplementedError("This adapter does not support mid-task injection")
