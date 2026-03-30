"""AG-UI adapter — LangGraph ReAct agent streaming AG-UI events.

OBSERVATION PHASE: print statements are intentional.
Remove them in Week 2 when routing to trajectory validator.
"""
from __future__ import annotations

import json
import os
from typing import AsyncIterator

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from ballast.core.stream import AgentStream

load_dotenv()


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

    def __init__(self, model: str = "claude-haiku-4-5-20251001") -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set. Copy .env.example to .env and fill it in."
            )
        llm = ChatAnthropic(model=model, api_key=api_key)
        self._graph = create_react_agent(llm, tools=[get_word_count])

    async def stream(self, goal: str, spec: dict) -> AsyncIterator[object]:
        """Run the agent against `goal` and yield raw LangGraph events.

        Prints every event type + key fields to stdout for observation.
        `spec` is accepted but unused in observation phase.
        """
        print(f"\n{'='*60}")
        print(f"[AGUIAdapter] Goal: {goal!r}")
        print(f"{'='*60}\n")

        event_sequence = []
        input_messages = {"messages": [{"role": "user", "content": goal}]}

        async for event in self._graph.astream_events(input_messages, version="v2"):
            event_type = event.get("event", "unknown")
            event_name = event.get("name", "")
            event_sequence.append(event_type)

            print(f"[EVENT] {event_type}  name={event_name!r}")

            # Print data fields that answer the observation questions.
            data = event.get("data", {})
            if data:
                # For STATE_SNAPSHOT equivalents: print full state
                if event_type in ("on_chain_start", "on_chain_end", "on_chain_stream"):
                    chunk = data.get("chunk") or data.get("output") or data.get("input")
                    if chunk is not None:
                        print(f"  data.chunk/output/input: {json.dumps(_truncate(chunk), indent=2)}")
                # For tool calls: print tool name and args
                if event_type in ("on_tool_start", "on_tool_end"):
                    print(f"  data: {json.dumps(_truncate(data), indent=2)}")
                # For LLM events: print token count or message content
                if event_type in ("on_chat_model_start", "on_chat_model_end", "on_chat_model_stream"):
                    chunk = data.get("chunk") or data.get("output")
                    if chunk is not None:
                        print(f"  data.chunk/output: {_truncate_str(str(chunk), 200)}")

            yield event

        print(f"\n[AGUIAdapter] Event sequence ({len(event_sequence)} total):")
        for i, et in enumerate(event_sequence, 1):
            print(f"  {i:3d}. {et}")


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
