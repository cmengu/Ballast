"""
Observation runner — run AGUIAdapter against a minimal goal and record event sequence.

Answers:
  1. Which event types fire on each step?
  2. What does the agent state contain mid-run?
  3. Which event type is the natural intervention point?

Run with:
  python scripts/observe.py
"""
import asyncio
import sys
from pathlib import Path

# Make ballast importable when run directly from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from ballast.adapters.agui import AGUIAdapter


OBSERVATION_GOAL = "Count the words in this sentence: the quick brown fox"


async def main() -> None:
    adapter = AGUIAdapter(model="claude-haiku-4-5-20251001")
    events = []
    event_types: list[str] = []

    async for event in adapter.stream(OBSERVATION_GOAL, spec={}):
        events.append(event)
        event_types.append(event.get("event", "unknown"))

    print(f"\n[observe.py] Total events: {len(events)}")
    print(f"[observe.py] Event sequence: {event_types}")
    print("[observe.py] Done.")


if __name__ == "__main__":
    asyncio.run(main())
