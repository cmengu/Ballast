# Day 4 — `demo.py`: Full end-to-end, audit log shows two hash ranges

**Overall Progress:** `0%` (0/1 steps)

---

## TLDR

Create `demo.py` at the project root. It wires `run_with_live_spec` (hook.py) to a real
pydantic-ai Agent, pushes spec v1 to the running server, runs the agent concurrently with a
delayed spec update (pushed after 5 seconds), and prints a final audit log showing two distinct
spec version hashes — one for nodes before the update, one for nodes after. This is the
centrepiece demo: the agent is mid-run when the spec changes, the constraint fires without
restarting the job, and the audit log makes the transition point exact and undeniable.

---

## Architecture Overview

**The problem this plan solves:**
`hook.py` exists and is tested with mocks. There is no script that drives a real LLM through
`run_with_live_spec` against a live spec server. Without `demo.py`, the mechanism is proven
only in unit tests — not observable by a human watching a terminal.

**The pattern applied:**
Orchestrator script — `demo.py` owns startup (server priming, agent + poller construction),
concurrent execution (`asyncio.gather`), and teardown (audit log printing). It imports from
`ballast.core.*` and calls them with real values. It contains no library logic — just wiring.

**What stays unchanged:**
- `ballast/core/spec.py` — no edits
- `ballast/core/sync.py` — no edits
- `ballast/core/hook.py` — no edits
- `ballast/core/server.py` — no edits
- All existing tests — no edits

**What this plan adds:**
- `demo.py` — runnable demo script; drives agent with live spec injection end-to-end.

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|----------|----------------------|--------------------------|
| spec_v1 and spec_v2 differ in `success_criteria` | Differ only in `constraints` | `lock()` hashes only `intent + success_criteria`; same criteria → same version hash → `SpecPoller.poll()` sees no change → injection never fires |
| `asyncio.sleep(5)` delay before spec push | Node-count trigger via `on_node` callback | Sleep simulates real M5 developer edit arriving from outside; `on_node` trigger is a circular dependency — the injection path being tested should not be inside the thing triggering it |
| `httpx.AsyncClient` for HTTP calls | `httpx.post` (sync) | Sync calls inside `async def` block the event loop during the server POST; async client keeps both coroutines schedulable |
| `@agent.tool_plain` for `research_companies` | `@agent.tool` (with RunContext) | Tool does not need RunContext; `tool_plain` is simpler and confirmed working in pydantic-ai 0.8.1 |
| One step — create `demo.py` only | Add integration test | `demo.py` is a manual demo script; integration tests require real API key and are already in the project's `integration` mark category — adding one for demo is out of scope for Day 4 |

**Known limitations acknowledged:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| `asyncio.sleep(5)` timing not guaranteed to catch mid-run | API latency at typical Claude response times (1–3s) means 5s fires after at least 1–2 node cycles; if agent finishes faster, audit log still shows correct hashes (all under v1, then v2 fires — 0 v1 nodes is an edge case not a failure) | Replace sleep with a configurable `--update-after-nodes N` flag using `on_node` |
| pydantic-ai 0.8.1 + anthropic SDK version mismatch patched in venv | Patch is a one-line change to `models/anthropic.py` in venv only; confirmed: 98 tests still pass after patch | Pin `pydantic-ai>=1.0` and update pyproject.toml when ready to migrate the full codebase |

---

## Agent Failure Protocol

1. A verification command fails → read the full error output.
2. Cause is unambiguous → make ONE targeted fix → re-run the same verification command.
3. If still failing after one fix → **STOP**. Output full contents of every modified file. Report: (a) command run, (b) full error verbatim, (c) fix attempted, (d) current state of each modified file, (e) why you cannot proceed.
4. Never attempt a second fix without human instruction.
5. Never modify files not named in the current step.

---

## Pre-Flight — Run Before Any Code Changes

```bash
# 1. Confirm test baseline (must be 98 — patch already applied)
source venv/bin/activate && pytest tests/ -m "not integration" -q --tb=no 2>&1 | tail -3
# Expected: 98 passed, 3 deselected

# 2. Confirm demo.py does NOT exist
ls demo.py 2>&1
# Expected: No such file or directory

# 3. Confirm all imports demo.py will use
source venv/bin/activate && python -c "
from ballast.core.spec import SpecModel, lock
from ballast.core.sync import SpecPoller
from ballast.core.hook import run_with_live_spec
import httpx, asyncio
from dotenv import load_dotenv
print('all imports ok')
"
# Expected: all imports ok

# 4. Confirm pydantic-ai anthropic patch is applied — derive path dynamically
#    (avoids hardcoding python3.12 which would silently fail on other versions)
source venv/bin/activate && python -c "
import pydantic_ai, os
path = os.path.join(os.path.dirname(pydantic_ai.__file__), 'models', 'anthropic.py')
with open(path) as f:
    hits = [l.rstrip() for l in f if 'UserLocation' in l]
print('UserLocation lines:', hits)
assert any('BetaUserLocationParam as UserLocation' in h for h in hits), 'PATCH MISSING'
print('patch confirmed')
"
# Expected:
#   UserLocation lines: [\"    from anthropic.types.beta.beta_user_location_param import BetaUserLocationParam as UserLocation\", ...]
#   patch confirmed

# 5. Confirm Agent can be instantiated with tool_plain (fake key — no LLM call)
source venv/bin/activate && ANTHROPIC_API_KEY=preflight-check python -c "
from pydantic_ai import Agent
a = Agent('claude-sonnet-4-6', system_prompt='test')
@a.tool_plain
def dummy(x: str) -> str: return x
print('Agent + tool_plain ok')
"
# Expected: Agent + tool_plain ok

# 6. Confirm spec hash behaviour: v1 and v2 must produce DIFFERENT hashes
source venv/bin/activate && python -c "
from ballast.core.spec import SpecModel, lock
v1 = lock(SpecModel(
    intent='Write a comprehensive AI company landscape report',
    success_criteria=['report covers at least 5 major AI companies','each company described in 1-2 sentences'],
    constraints=[], allowed_tools=['research_companies'],
))
v2 = lock(SpecModel(
    intent='Write a comprehensive AI company landscape report',
    success_criteria=['report covers at least 5 major AI companies','each company described in 1-2 sentences','report adheres to all active constraints'],
    constraints=['do not mention OpenAI or Anthropic in any output'],
    allowed_tools=['research_companies'], parent_hash=v1.version,
))
print(f'v1={v1.version}  v2={v2.version}  differ={v1.version != v2.version}')
print(v1.diff(v2).as_injection())
"
# Expected:
#   v1=8a9244a9  v2=6ec5f0af  differ=True
#   [SPEC UPDATE 8a9244a9 → 6ec5f0af]
#   NEW CONSTRAINTS (apply immediately): do not mention OpenAI or Anthropic in any output
#   [Continue from current node under updated spec.]
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan:         98 passed
demo.py exists:                 No
all imports ok:                 ok
pydantic-ai patch confirmed:    patch confirmed
Agent + tool_plain ok:          ok
spec hashes differ:             True  (v1=8a9244a9  v2=6ec5f0af)
injection text:                 [SPEC UPDATE 8a9244a9 → 6ec5f0af] ... NEW CONSTRAINTS ...
```

---

## Clarification Gate

All unknowns resolved from codebase inspection. No human input required.

| Unknown | Required | Source | Resolved |
|---------|----------|--------|----------|
| `lock()` hash inputs | Hashes only `intent + success_criteria` — confirmed in spec.py:462 | Codebase read | ✅ |
| pydantic-ai `tool_plain` API | Sync tool, no RunContext arg — confirmed by test run | Codebase + venv test | ✅ |
| `run_with_live_spec` signature | `(agent, task, spec, poller, on_node=None)` — no `job_id` | hook.py:34 | ✅ |
| spec field name | `.version` (not `.version_hash`) — confirmed in spec.py:92 | Codebase read | ✅ |

---

## Steps Analysis

```
Step 8.1 (Create demo.py) — Critical (new public entry point; wires all four modules together
                             with a real LLM; any import error or wrong field name is immediately
                             visible at runtime) — full code review — Idempotent: Yes (new file)
```

---

## Tasks

### Phase 1 — Implementation

**Goal:** `python demo.py` runs (with server live and ANTHROPIC_API_KEY set), produces node-by-node output, fires a spec update mid-run, and prints an audit log with two distinct spec hash ranges.

---

- [ ] 🟥 **Step 8.1: Create `demo.py`** — *Critical: new entry point; wires all modules with real LLM*

  **Step Architecture Thinking:**

  **Pattern applied:** Orchestrator script — pure wiring, zero library logic. Every function called is imported from `ballast.core.*`. `demo.py` owns only: startup sequencing, concurrent scheduling via `asyncio.gather`, and output formatting.

  **Why this step exists here in the sequence:**
  `hook.py` (Step 7.1) and all its dependencies exist and are unit-tested. `demo.py` can now import `run_with_live_spec` and call it with a real agent. Without `demo.py`, the mechanism is proven only in mock tests — not observable end-to-end.

  **Why `demo.py` is the right location:**
  Project root is the conventional location for runnable demo/entry scripts (consistent with `spec.md` at root, `scripts/server.py` for the uvicorn entrypoint). It is not inside `ballast/` because it is not a library module — it is a consumer of the library.

  **Alternative approach considered and rejected:**
  Put the demo in `scripts/demo.py`. Rejected because `scripts/` already contains `server.py` (infrastructure), not demo scripts. A root-level `demo.py` is immediately discoverable and matches the mvp.md instruction "run `python demo.py`".

  **What breaks if this step deviates from the described pattern:**
  If spec_v1 and spec_v2 are given the same `success_criteria` (differing only in `constraints`), `lock()` produces identical version hashes, `SpecPoller.poll()` compares `data["version"] == self._current.version` and returns `None` — injection never fires, audit log shows only one hash range, demo fails to demonstrate the centrepiece feature.

  ---

  **Idempotent:** Yes — creating a new file.

  **Context:** `demo.py` is a new file. No existing file is modified.

  **Pre-Read Gate:**
  Before writing:
  - `ls demo.py` → must return "No such file". If exists → STOP.
  - `grep -n "def run_with_live_spec" ballast/core/hook.py` → must return 1 match (Step 7.1 complete).
  - `grep -n "class SpecPoller" ballast/core/sync.py` → must return 1 match.
  - `grep -n "^def lock" ballast/core/spec.py` → must return 1 match.

  **Self-Contained Rule:** All code below is complete and immediately runnable.

  **No-Placeholder Rule:** No `<VALUE>` tokens appear below.

  ---

  ```python
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
      lock() hashes intent + success_criteria only.
      spec_v1 and spec_v2 must differ in success_criteria (not just constraints)
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
      parent_hash=spec_v1.version,
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
              timeout=5.0,
          )
      print(f"\n📝 Spec pushed → {spec_v2.version}")
      print(f"   constraint: {spec_v2.constraints[0]}")


  async def main() -> None:
      # ── Prime server with spec v1 ─────────────────────────────────────────
      async with httpx.AsyncClient() as client:
          await client.post(
              f"{SERVER}/spec/{JOB_ID}/update",
              json=spec_v1.model_dump(),
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

      print(f"\n🚀 Agent starting — spec v1: {spec_v1.version}")
      print(f"   Spec update fires in 5s → spec v2: {spec_v2.version}")
      print(f"   New constraint: \"{spec_v2.constraints[0]}\"\n")

      # ── Run agent + delayed spec push concurrently ────────────────────────
      results = await asyncio.gather(
          run_with_live_spec(
              agent,
              "Research and write a structured report on major AI companies across all sectors.",
              spec_v1,
              poller,
          ),
          push_spec_update(),
      )
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
  ```

  ---

  **What it does:** Runs a pydantic-ai agent against a real Anthropic model, polls the spec
  server at every node, injects a constraint mid-run via `run_with_live_spec`, and prints an
  audit log showing the exact node where the spec version changed.

  **Why this approach:** The `asyncio.gather` pattern keeps the agent run and the spec push
  fully concurrent — the spec update arrives from "outside" through the server→poller path,
  which is the exact production mechanism being demonstrated.

  **Assumptions:**
  - `python scripts/server.py` is running in another terminal before `python demo.py` is launched.
  - `ANTHROPIC_API_KEY` is set in environment or `.env`.
  - pydantic-ai 0.8.1 venv patch is applied (`BetaUserLocationParam as UserLocation` at line ~90 of `models/anthropic.py`).
  - `spec_v1.version = 8a9244a9` and `spec_v2.version = 6ec5f0af` (verified in pre-flight; deterministic hashes).

  **Risks:**
  - Agent finishes in < 5s (rare, but possible if LLM responds very fast) → audit log shows one hash range → not a code bug; reduce `asyncio.sleep(5)` to `asyncio.sleep(2)` and re-run.
  - Server not running → `httpx.AsyncClient.post` raises `ConnectError` → error is immediate and obvious; start server first.
  - `ANTHROPIC_API_KEY` not set → `pydantic_ai.exceptions.UserError` at agent instantiation → load `.env` or export the key.

  **Verification (two terminals):**
  ```bash
  # Terminal 1 — start server
  source venv/bin/activate && python scripts/server.py

  # Terminal 2 — run demo
  source venv/bin/activate && python demo.py
  ```

  Expected output (exact hashes may vary if spec fields change):
  ```
  🚀 Agent starting — spec v1: 8a9244a9
     Spec update fires in 5s → spec v2: 6ec5f0af
     New constraint: "do not mention OpenAI or Anthropic in any output"

    node 00 | spec:8a9244a9 | ModelRequestNode
    node 01 | spec:8a9244a9 | CallToolsNode
    ...
  📝 Spec pushed → 6ec5f0af
     constraint: do not mention OpenAI or Anthropic in any output
  🔄 node NN | spec:6ec5f0af | ModelRequestNode  ← 8a9244a9→6ec5f0af
    node NN+1 | spec:6ec5f0af | CallToolsNode
    ...

  ── AUDIT LOG ──
    node 00 | 8a9244a9 | ModelRequestNode
    node 01 | 8a9244a9 | CallToolsNode
    ...
  🔄 node NN | 6ec5f0af | ModelRequestNode  ← 8a9244a9→6ec5f0af
    ...

  ✓ 2 distinct spec hash(es): ['6ec5f0af', '8a9244a9']
  ✓ Two hash ranges confirmed — live spec injection succeeded.
  ```

  **Import verification (no server, no API key):**
  ```bash
  source venv/bin/activate && python -c "import demo; print('import ok')"
  ```
  Expected: `import ok` (module-level code is inside `if __name__ == '__main__'`; imports resolve cleanly)

  **Git Checkpoint:**
  ```bash
  git add demo.py
  git commit -m "step 8.1: create demo.py — full end-to-end, audit log shows two hash ranges"
  ```

---

## Completion Checklist

- [ ] `ls demo.py` → file exists
- [ ] `cd /Users/ngchenmeng/Ballast && source venv/bin/activate && python -c "import demo; print('ok')"` → `ok` (no import errors; must run from project root so `demo.py` is on `sys.path`)
- [ ] `source venv/bin/activate && pytest tests/ -m "not integration" -q --tb=no 2>&1 | tail -3` → `98 passed` (no regression — demo.py adds no tests, count unchanged)
- [ ] With server running + `ANTHROPIC_API_KEY` set: `python demo.py` terminates cleanly
- [ ] Audit log in terminal output shows `✓ 2 distinct spec hash(es)` and `✓ Two hash ranges confirmed`

---

## Decisions Log

| Flaw surfaced in pre-flight | Resolution applied in plan |
|-----------------------------|----------------------------|
| `lock()` hashes only `intent + success_criteria` — constraints-only change gives same version hash | spec_v2 adds a third success criterion; confirmed hash differs: `8a9244a9 ≠ 6ec5f0af` |
| pydantic-ai 0.8.1 imports `UserLocation` not present in anthropic 0.86.0–0.87.0 | One-line venv patch applied before plan: `BetaUserLocationParam as UserLocation`; 98 tests still pass |
| `run_with_live_spec` has no `job_id` param (dropped in Day 3) | demo.py does not pass `job_id`; SpecPoller encodes it internally |
| `SpecModel.lock({dict})` from mvp.md is wrong API | demo.py uses `lock(SpecModel(...))` — the actual two-step API in spec.py |
| `run.get_output()` from mvp.md doesn't exist | hook.py uses `run.result.output` — confirmed in Step 7.1 |
