"""AG-UI adapter — LangGraph ReAct agent streaming AG-UI events.

OBSERVATION PHASE: events are logged at DEBUG level.
Promote to INFO or wire into trajectory validator in a future step.
"""
from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator

from langchain_core.tools import tool
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from ballast.core.stream import AgentStream

logger = logging.getLogger(__name__)


@tool
def get_word_count(text: str) -> int:
    """Count the number of words in a text string."""
    return len(text.split())


class AGUIAdapter(AgentStream):
    """Streams AG-UI events from a LangGraph ReAct agent.

    Uses LangGraph astream_events (v2) as the event source.
    Each LangGraph event is printed raw so the event sequence can be
    observed before trajectory validation logic is built on top.

    Answers on first real run:
      1. Which event types fire on each step?
      2. What does the messages state contain mid-run?
      3. Which event type is the natural intervention point?
    """

    def __init__(self, model: str = "claude-haiku-4-5-20251001", load_env: bool = False) -> None:
        if load_env:
            from dotenv import load_dotenv
            load_dotenv()
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. "
                "Set the env var directly or pass load_env=True to load a .env file."
            )
        llm = ChatAnthropic(model=model, api_key=api_key)
        self._graph = create_react_agent(llm, tools=[get_word_count])

    async def stream(self, goal: str, spec: dict) -> AsyncIterator[object]:
        """Run the agent against `goal` and yield raw LangGraph events.

        Prints every event type + key fields to stdout for observation.
        `spec` is accepted but unused in observation phase.
        """
        logger.debug("[AGUIAdapter] goal=%r", goal)

        event_sequence = []
        input_messages = {"messages": [{"role": "user", "content": goal}]}

        async for event in self._graph.astream_events(input_messages, version="v2"):
            event_type = event.get("event", "unknown")
            event_name = event.get("name", "")
            event_sequence.append(event_type)

            logger.debug("[EVENT] %s  name=%r", event_type, event_name)

            # Log data fields that answer the observation questions.
            data = event.get("data", {})
            if data:
                if event_type in ("on_chain_start", "on_chain_end", "on_chain_stream"):
                    chunk = data.get("chunk") or data.get("output") or data.get("input")
                    if chunk is not None:
                        try:
                            logger.debug("  data.chunk/output/input: %s",
                                         json.dumps(_truncate(chunk), indent=2))
                        except (TypeError, ValueError):
                            logger.debug("  data.chunk/output/input: %r", _truncate(chunk))
                if event_type in ("on_tool_start", "on_tool_end"):
                    try:
                        logger.debug("  data: %s", json.dumps(_truncate(data), indent=2))
                    except (TypeError, ValueError):
                        logger.debug("  data: %r", _truncate(data))
                if event_type in ("on_chat_model_start", "on_chat_model_end", "on_chat_model_stream"):
                    chunk = data.get("chunk") or data.get("output")
                    if chunk is not None:
                        logger.debug("  data.chunk/output: %s", _truncate_str(str(chunk), 200))

            yield event

        logger.debug("[AGUIAdapter] event sequence (%d total): %s",
                     len(event_sequence), event_sequence)


def _truncate(obj: object, max_len: int = 300) -> object:
    """Truncate string values in dicts/lists for readable printing."""
    if isinstance(obj, str):
        return obj[:max_len] + "..." if len(obj) > max_len else obj
    if isinstance(obj, dict):
        return {k: _truncate(v, max_len) for k, v in list(obj.items())[:10]}
    if isinstance(obj, list):
        return [_truncate(v, max_len) for v in obj[:5]]
    return obj


def _truncate_str(s: str, max_len: int) -> str:
    return s[:max_len] + "..." if len(s) > max_len else s
