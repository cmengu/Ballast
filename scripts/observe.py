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
    from ballast.core.spec import lock_spec, RunPhaseTracker

    print("[observe.py] Locking spec (non-interactive infer path)...")
    spec, questions = lock_spec(OBSERVATION_GOAL, domain="coding", interactive=False)
    print(f"[observe.py] Locked spec:")
    print(f"  success_criteria: {spec.success_criteria}")
    print(f"  scope:            {spec.scope}")
    print(f"  intent signal:    {spec.intent_signal.action_type} / {spec.intent_signal.latent_goal}")
    print(f"  threshold used:   {spec.threshold_used}")
    if spec.inferred_assumptions:
        print(f"  assumptions:      {spec.inferred_assumptions}")

    adapter = AGUIAdapter(model="claude-haiku-4-5-20251001")
    tracker = RunPhaseTracker(spec)
    events = []
    async for event in adapter.stream(OBSERVATION_GOAL, spec=spec.model_dump()):
        tracker.update(event)
        events.append(event)

    print(f"\n[observe.py] Total events: {len(events)}")
    print(f"[observe.py] Final intent: {tracker.intent_summary()}")
    print("[observe.py] Done.")


if __name__ == "__main__":
    asyncio.run(main())
