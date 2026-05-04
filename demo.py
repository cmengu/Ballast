"""demo.py — Full end-to-end demo: audit log shows two spec hash ranges.

Setup (two terminals required):
    Terminal 1:  source venv/bin/activate && python scripts/server.py
    Terminal 2:  source venv/bin/activate && ANTHROPIC_API_KEY=... python demo.py

What it shows:
    - Agent runs freely under spec v1 (no constraints)
    - After 5 seconds, spec v2 is pushed to the server (adds constraint)
    - SpecPoller detects the version change at the next node boundary
    - Injection fires: "[SPEC UPDATE 8a9244a9 → 6ec5f0af] NEW CONSTRAINTS..."
    - Agent output from that node onward avoids OpenAI and Anthropic
    - Audit log shows two distinct spec hash ranges with exact transition node

Spec hash note:
    lock() hashes all non-harness fields (intent, success_criteria, constraints,
    allowed_tools, drift_threshold, etc.).
    spec_v1 and spec_v2 must differ in at least one of these fields
    to produce different version hashes and trigger SpecPoller detection.
"""
from __future__ import annotations

import asyncio

import httpx
from dotenv import load_dotenv
from pydantic_ai import Agent

from ballast.core.hook import run_with_live_spec
from ballast.core.spec import SpecModel, lock
from ballast.core.sync import SpecPoller

load_dotenv()

JOB_ID = "demo-001"
SERVER = "http://localhost:8765"

# When BALLAST_SPEC_SERVER_TOKEN is set (recommended for non-loopback deployments),
# include it on every request to the spec server.
import os as _os
_BALLAST_TOKEN = _os.environ.get("BALLAST_SPEC_SERVER_TOKEN", "")
_AUTH_HEADERS = {"X-Ballast-Token": _BALLAST_TOKEN} if _BALLAST_TOKEN else {}

# ── Spec v1: no constraints on company names ──────────────────────────────
# lock() hashes intent + success_criteria → version = 8a9244a9
spec_v1 = lock(SpecModel(
    intent="Write a comprehensive AI company landscape report",
    success_criteria=[
        "report covers at least 5 major AI companies",
        "each company described in 1-2 sentences",
    ],
    constraints=[],
    allowed_tools=["research_companies"],
))

# ── Spec v2: adds constraint + one success criterion (different hash) ──────
# Adding a success criterion changes the hash → SpecPoller detects the update.
# lock() hashes intent + success_criteria → version = 6ec5f0af
spec_v2 = lock(SpecModel(
    intent="Write a comprehensive AI company landscape report",
    success_criteria=[
        "report covers at least 5 major AI companies",
        "each company described in 1-2 sentences",
        "report adheres to all active constraints",
    ],
    constraints=["do not mention OpenAI or Anthropic in any output"],
    allowed_tools=["research_companies"],
    parent_hash=spec_v1.version_hash,
))


async def push_spec_update() -> None:
    """Simulate M5 developer pushing an updated spec mid-run.

    Sleeps 5 seconds (enough for 2–3 node cycles at typical API latency),
    then POSTs spec_v2 to the server. SpecPoller on the M2 side will detect
    the version change at the next node boundary and fire the injection.
    """
    await asyncio.sleep(5)
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{SERVER}/spec/{JOB_ID}/update",
            json=spec_v2.model_dump(),
            headers=_AUTH_HEADERS,
            timeout=5.0,
        )
    print(f"\n📝 Spec pushed → {spec_v2.version_hash}")
    print(f"   constraint: {spec_v2.constraints[0]}")


async def main() -> None:
    # ── Prime server with spec v1 ─────────────────────────────────────────
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{SERVER}/spec/{JOB_ID}/update",
            json=spec_v1.model_dump(),
            headers=_AUTH_HEADERS,
            timeout=5.0,
        )

    # ── Agent with a minimal tool that returns company data ───────────────
    # The tool always returns company names including OpenAI and Anthropic.
    # Under spec_v1: agent includes them in output.
    # Under spec_v2 (post-injection): agent filters them from written output.
    agent = Agent(
        "claude-sonnet-4-6",
        system_prompt=(
            f"You are a research analyst writing an AI company landscape report.\n"
            f"Spec intent: {spec_v1.intent}\n"
            "Use the research_companies tool to gather data, then write a structured report.\n"
            "When you receive a [SPEC UPDATE] message, apply the new constraints immediately "
            "before writing any further output. Do not mention constrained entities."
        ),
    )

    @agent.tool_plain
    def research_companies(sector: str) -> str:
        """Look up AI companies operating in a given sector."""
        return (
            "Major AI companies:\n"
            "- OpenAI: GPT-4o, ChatGPT, AGI research; dominant in consumer AI\n"
            "- Anthropic: Claude models, Constitutional AI, safety-first research\n"
            "- Google DeepMind: Gemini, AlphaFold, robotics research\n"
            "- Meta AI: LLaMA open-weight models, social AI research\n"
            "- Mistral AI: efficient open-weight models, European AI leader\n"
            "- Cohere: enterprise NLP APIs, retrieval-augmented generation\n"
            "- xAI: Grok models, real-time data integration\n"
            f"(sector queried: {sector})"
        )

    # ── Poller ────────────────────────────────────────────────────────────
    poller = SpecPoller(SERVER, JOB_ID)
    poller.set_initial(spec_v1)

    print(f"\n🚀 Agent starting — spec v1: {spec_v1.version_hash}")
    print(f"   Spec update fires in 5s → spec v2: {spec_v2.version_hash}")
    print(f"   New constraint: \"{spec_v2.constraints[0]}\"\n")

    # ── Run agent + delayed spec push concurrently ────────────────────────
    try:
        results = await asyncio.gather(
            run_with_live_spec(
                agent,
                "Research and write a structured report on major AI companies across all sectors.",
                spec_v1,
                poller,
            ),
            push_spec_update(),
        )
    finally:
        poller.close()
    output, audit_log = results[0]

    # ── Audit log ─────────────────────────────────────────────────────────
    print("\n── AUDIT LOG ──")
    for entry in audit_log:
        marker = "🔄" if entry["delta_injected"] else "  "
        suffix = f"  ← {entry['delta_injected']}" if entry["delta_injected"] else ""
        print(
            f"{marker} node {entry['node_index']:02d}"
            f" | {entry['spec_hash'][:8]}"
            f" | {entry['node_type']}"
            f"{suffix}"
        )

    hashes = sorted({e["spec_hash"][:8] for e in audit_log})
    print(f"\n✓ {len(hashes)} distinct spec hash(es): {hashes}")
    if len(hashes) >= 2:
        print("✓ Two hash ranges confirmed — live spec injection succeeded.")
    else:
        print("⚠  Only one hash in audit log.")
        print("   Possible cause: agent finished before 5s delay fired, or server not running.")


if __name__ == "__main__":
    asyncio.run(main())
