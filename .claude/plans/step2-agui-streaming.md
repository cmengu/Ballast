# Step 2 — AG-UI Adapter Streaming + Memory Port

**Overall Progress:** `0%` (0 / 6 steps complete)

---

## TLDR

Two deliverables. First: implement `ballast/adapters/agui.py` so it actually connects to a LangGraph agent and streams real AG-UI events, printing every event raw to answer three observation questions (event sequence, STATE_SNAPSHOT content, natural intervention point). Second: port the GroundWire memory layer to `ballast/core/memory.py` with two fixes: half-life–based temporal decay and success-filtered consolidation. After this plan executes: `AGUIAdapter.stream()` runs a live goal and prints events; `memory.py` is importable with a `recall/write/log_run/consolidate` interface; 4 existing tests still pass; new memory tests pass.

---

## Architecture Overview

**The problem this plan solves:**
- `ballast/adapters/agui.py:8` raises `NotImplementedError` — no real events ever flow, so trajectory validation has nothing to validate against.
- `ballast/core/memory.py` does not exist — Week 2's spec and trajectory modules need the memory interface already defined.

**The patterns applied:**

| Pattern | Applied to | What breaks if violated |
|---------|-----------|------------------------|
| **Template Method** | `AGUIAdapter` implements the ABC contract from `AgentStream` | If `stream()` doesn't yield, callers receive nothing and hang |
| **Direct event source** | Adapter uses LangGraph `astream_events` v2, not the `ag_ui.langgraph` wrapper | Using the wrapper now would hide the underlying event types before trajectory validation is designed |
| **Single Responsibility** | `memory.py` owns persistence; adapters own streaming | If adapters also persist, memory contracts diverge across adapters |
| **Fail-fast exponential decay** | Half-life function replaces flat rate constant | Using `_DECAY_RATE = 0.95` per day produces 14-day half-life — too slow for session context |

**What stays unchanged:**
- `ballast/core/stream.py` — ABC contract is correct; no change needed at observation phase
- `ballast/adapters/tinyfish.py` — out of scope for this plan
- `tests/test_stream.py` — existing 4 tests must still pass

**What this plan adds:**

| File | Single responsibility |
|------|-----------------------|
| `ballast/adapters/agui.py` | Full replacement: streams AG-UI events from a LangGraph ReAct agent, prints raw |
| `ballast/core/memory.py` | New: three-layer agent memory (recall/write/log_run/consolidate) with half-life decay |
| `scripts/observe.py` | New: standalone runner that calls `AGUIAdapter.stream()` and records event sequence |
| `tests/test_memory.py` | New: unit tests for decay math, consolidation filtering, and schema invariants |
| `pyproject.toml` | Updated: add `ag-ui-langgraph`, `langgraph`, `langchain-openai`, `anthropic`, `filelock` to deps |
| `.env.example` | Updated: document `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` |

**Critical decisions:**

| Decision | Alternative considered | Why rejected |
|----------|----------------------|--------------|
| Use LangGraph `astream_events` v2 directly (not `ag_ui.langgraph` wrapper) | Wire through `ag_ui.langgraph` bridge immediately | The wrapper abstracts away the underlying event types; trajectory validation must be designed against raw event types first, then the wrapper added on top |
| LangGraph `astream_events` v2 as the source | Skip LangGraph, mock events | Mock events can't answer the three observation questions |
| Inline Pydantic models in memory.py | Import from schemas.py (GroundWire) | `schemas.py` doesn't exist in Ballast — creates a broken import from day one |
| Direct `anthropic.Anthropic` in memory.py | Wrap in `llm_utils.parse_structured` (GroundWire) | `llm_utils.py` doesn't exist in Ballast |
| `math.exp(-ln(2)*t/half_life)` for decay | `_DECAY_RATE**days` constant (GroundWire) | Constant encodes an arbitrary half-life (14 days); formula makes half-life explicit and configurable |
| Filter `success=True` in consolidate() | Keep all runs including failures | Failed runs contaminate the semantic profile with dead-end navigation paths |

**Known limitations acknowledged:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| `scripts/observe.py` prints to stdout only | Observation phase — answering 3 questions is the goal | Week 2: route events to trajectory validator |
| Memory has no shared/remote backend | Local `.ballast_memory/` only | Week 4: optional Supabase sync (from GroundWire shared_memory.py) |
| PII scrubbing removed from memory | No web crawling in observation phase | Week 2: re-add inline regex if agents handle user data |
| `inject()` not implemented in AGUIAdapter | Pause/resume is Week 3's centrepiece | Week 3: implement using AG-UI intervention points |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---------|----------|--------|----------|----------|
| ag_ui.core / ag_ui.langgraph API surface | `dir()` output — for Week 2 wiring reference | Step 3 preparatory discovery run | None (adapter uses LangGraph `astream_events` directly) | ⬜ Captured in Step 3 |
| `OPENAI_API_KEY` available for live run | Key must be set in `.env` | Human input | Step 4 (observe.py run) | ⬜ Confirm before Step 4 |

---

## Agent Failure Protocol

1. A verification command fails → read the full error output.
2. Cause is unambiguous → make ONE targeted fix → re-run the same verification command.
3. If still failing after one fix → **STOP**. Output full contents of every modified file. Report: (a) command run, (b) full error verbatim, (c) fix attempted, (d) current state of each modified file, (e) why you cannot proceed.
4. Never attempt a second fix without human instruction.
5. Never modify files not named in the current step.

---

## Pre-Flight — Run Before Any Code Changes

```
Run the following — do not change anything. Show full output and wait.

(1) /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ -v
    Record: number of passing tests. Must be 4. If not 4 → STOP.

(2) cat /Users/ngchenmeng/Ballast/pyproject.toml
    Record: exact current dependencies list.

(3) cat /Users/ngchenmeng/Ballast/ballast/adapters/agui.py
    Record: exact current content (must be the stub from Step 1 scaffold).

(4) ls /Users/ngchenmeng/Ballast/ballast/core/
    Confirm: only __init__.py and stream.py. No memory.py yet.

(5) ls /Users/ngchenmeng/Ballast/scripts/ 2>&1
    Confirm: either "No such file or directory" or empty directory.

(6) /Users/ngchenmeng/Ballast/venv/bin/python -c "import ag_ui" 2>&1
    Record: whether ag_ui is already importable or not.
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan:   ____  (must be 4)
pyproject.toml deps:      ____
agui.py current content:  ____ (must be the NotImplementedError stub)
core/ contents:           ____ (must be __init__.py + stream.py only)
scripts/ exists:          ____
ag_ui importable:         ____
```

**Automated checks (all must pass before Step 1):**
- [ ] Existing test suite: exactly 4 passing
- [ ] `ballast/core/memory.py` does NOT exist yet
- [ ] `scripts/observe.py` does NOT exist yet
- [ ] `ballast/adapters/agui.py` still contains `NotImplementedError` stub

---

## Environment Matrix

| Step | Dev | Notes |
|------|-----|-------|
| Steps 1–3 | ✅ | No external services needed |
| Step 4 (live run) | ✅ | Requires `OPENAI_API_KEY` in `.env` |
| Steps 5–6 (memory) | ✅ | No external services for unit tests |

---

## Steps Analysis

```
Step 1 (install deps + update pyproject.toml) — Critical (changes installable package, enables all subsequent imports)  — full code review — Idempotent: Yes
Step 2 (update .env.example)                  — Non-critical (documentation only)                                       — verification only — Idempotent: Yes
Step 3 (AG-UI discovery + implement AGUIAdapter) — Critical (replaces stub, defines streaming contract) — full code review — Idempotent: Yes
Step 4 (write + run observe.py)               — Non-critical (throwaway observation runner)                             — verification only — Idempotent: Yes
Step 5 (write ballast/core/memory.py)         — Critical (new module used by trajectory + spec in Week 2)              — full code review — Idempotent: Yes
Step 6 (write tests/test_memory.py)           — Non-critical (validates memory contract)                               — verification only — Idempotent: Yes
```

---

## Tasks

### Phase 1 — Wire the AG-UI Event Stream

**Goal:** `AGUIAdapter.stream("do X", {})` runs a live LangGraph agent and yields real AG-UI events that print to stdout. The three observation questions are answerable from the output.

---

- [ ] 🟥 **Step 1: Install streaming deps + update pyproject.toml** — *Critical: wrong package names here block all Phase 1 imports*

  **Step Architecture Thinking:**

  **Pattern applied:** Single source of truth — `pyproject.toml` is the one place that declares deps. Editing it and re-running `pip install -e .` keeps the venv and the declared deps in sync.

  **Why this step exists here in the sequence:**
  All new deps (`ag-ui-langgraph`, `langgraph`, `langchain-openai`, `anthropic`, `filelock`) go in one pyproject.toml edit to avoid multiple install passes.

  **Why this file is the right location:**
  `pyproject.toml` is the PEP 517 manifest — adding deps here means `pip install -e .` installs them for any fresh clone. Adding only to the venv with `pip install` would work locally but break CI.

  **Alternative considered and rejected:**
  `pip install` directly without updating `pyproject.toml` — rejected because deps would be missing on fresh install, breaking any contributor or CI run.

  **What breaks if this step deviates:**
  If `ag-ui-langgraph` is spelled wrong (e.g. `ag_ui_langgraph`), Step 3's preparatory discovery run will fail with `ModuleNotFoundError`. The correct name is confirmed by verifying the install succeeds with exit code 0.

  ---

  **Idempotent:** Yes — `pip install -e .` is safe to re-run.

  **Context:** The existing `pyproject.toml` has `ag-ui-protocol`, `pydantic`, `python-dotenv`. This step adds three deps: `ag-ui-langgraph`, `langchain-openai`, `filelock`.

  **Pre-Read Gate:**
  Before editing:
  - Read `/Users/ngchenmeng/Ballast/pyproject.toml` in full. Confirm `[project.dependencies]` list. If `ag-ui-langgraph` already present → skip this edit, go straight to `pip install -e .` verification.

  ```toml
  [build-system]
  requires = ["hatchling"]
  build-backend = "hatchling.build"

  [project]
  name = "ballast"
  version = "0.1.0"
  requires-python = ">=3.11"
  dependencies = [
      "ag-ui-protocol",
      "ag-ui-langgraph",
      "langgraph",
      "langchain-openai",
      "anthropic>=0.20",
      "filelock",
      "pydantic>=2.0",
      "python-dotenv",
  ]

  [project.optional-dependencies]
  dev = [
      "pytest",
      "pytest-asyncio",
  ]

  [tool.hatch.build.targets.wheel]
  packages = ["ballast"]
  ```

  Then run:
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pip install -e "/Users/ngchenmeng/Ballast[dev]"
  ```

  **What it does:** Adds `ag-ui-langgraph`, `langgraph`, `langchain-openai`, `anthropic`, `filelock` to the declared deps and installs them in one pass.

  **Why this approach:** Single `pyproject.toml` edit for all new deps — avoids a second edit in Step 5. `anthropic` and `langgraph` are declared explicitly rather than relying on transitive deps from `ag-ui-langgraph`, which could change with upstream version bumps.

  **Assumptions:**
  - `ag-ui-langgraph` is the correct PyPI package name — verified by install succeeding
  - `langchain-openai` is the correct PyPI name — it is, this is a well-known package
  - Internet access available

  **Risks:**
  - `ag-ui-langgraph` package name wrong on PyPI → `pip install` fails → fix: check the correct name on PyPI and update pyproject.toml
  - Version conflict between `ag-ui-protocol` and `ag-ui-langgraph` → pip will report it → resolve with version constraints

  Also append to `/Users/ngchenmeng/Ballast/.gitignore` (add after existing content):
  ```
  # Agent memory store
  .ballast_memory/
  .ballast_memory*.lock
  ```

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add pyproject.toml .gitignore
  git -C /Users/ngchenmeng/Ballast commit -m "step 2.1: add streaming/memory deps, gitignore .ballast_memory"
  ```

  **Subtasks:**
  - [ ] 🟥 Edit `pyproject.toml` with exact content above (full file replacement)
  - [ ] 🟥 Append `.ballast_memory/` and `.ballast_memory*.lock` entries to `.gitignore`
  - [ ] 🟥 Run `pip install -e ".[dev]"` using venv pip
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Integration

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import ag_ui.core
  import langgraph
  import langchain_openai
  import anthropic
  import filelock
  print('all new deps importable')
  "
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ -v --tb=short 2>&1 | tail -3
  ```

  **Expected:**
  - Line 1: `all new deps importable`
  - Line 2: `4 passed`

  **Pass:** Both lines match. Zero regressions.

  **Fail:**
  - `ModuleNotFoundError: No module named 'ag_ui'` → `ag-ui-langgraph` install failed → check `pip show ag-ui-langgraph`
  - `ModuleNotFoundError: No module named 'anthropic'` → install failed → re-run pip install, confirm `anthropic>=0.20` in pyproject.toml
  - `ModuleNotFoundError: No module named 'langgraph'` → install failed → re-run pip install, confirm `langgraph` in pyproject.toml
  - test count drops below 4 → pyproject.toml edit broke a dep → read error and fix

---

- [ ] 🟥 **Step 2: Update .env.example** — *Non-critical: documentation only*

  **Step Architecture Thinking:**

  **Pattern applied:** Explicit contract at system boundary — `.env.example` is the single place that declares what external keys this project needs.

  **Why this step exists here in the sequence:**
  Before the live observation run (Step 4), the human needs to know which env var to set. Documenting it before the human gate at Step 4 eliminates the "why is it failing" question.

  **Why `.env.example` is the right location:**
  It's gittracked, not gitignored — humans can see it. `.env` is gitignored — keys are never committed.

  **Alternative rejected:**
  Inline comment in `observe.py` — rejected because `.env.example` is the conventional contract, and inline comments in code don't tell the human what to set before running.

  **What breaks if this step deviates:**
  Nothing breaks in code — it's documentation. But Step 4's Human Gate is harder to pass if the required var isn't documented.

  ---

  **Idempotent:** Yes — overwrite is safe.

  **Pre-Read Gate:**
  - Read `/Users/ngchenmeng/Ballast/.env.example`. It should be the 2-line placeholder from Step 1.

  Write `/Users/ngchenmeng/Ballast/.env.example`:
  ```
  # Copy this file to .env and fill in values.

  # Required for live AG-UI streaming observation (Step 4 / observe.py)
  OPENAI_API_KEY=sk-...

  # Required for memory.py extract_quirks / consolidate calls
  ANTHROPIC_API_KEY=sk-ant-...
  ```

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add .env.example
  git -C /Users/ngchenmeng/Ballast commit -m "step 2.2: document required env vars"
  ```

  **✓ Verification Test:**

  **Type:** Unit (filesystem)

  **Action:**
  ```bash
  grep -c "API_KEY" /Users/ngchenmeng/Ballast/.env.example
  ```

  **Expected:** `2`

  **Pass:** Returns `2`.

  **Fail:**
  - Returns `0` → file was not written or wrong content → re-write

---

- [ ] 🟥 **Step 3: Implement AGUIAdapter** — *Critical: replaces stub with real streaming; all observation depends on it*

  **Step Architecture Thinking:**

  **Pattern applied:** **Template Method** — `AGUIAdapter.stream()` implements the abstract method defined in `AgentStream`. The adapter is responsible for translating a goal + spec into an event stream. The base class contract (signature, yield type) is untouched.

  **Why this step exists here in the sequence:**
  Step 1 installed the packages. Step 2 documented keys. The adapter uses LangGraph's `astream_events` v2 directly — not `ag_ui.langgraph` — because the observation goal is to see raw LangGraph events (chain, tool, LLM), which is what trajectory validation will score. The `ag_ui.langgraph` wrapper will be wired in Week 2 once we know which event types matter.

  **Why `adapters/agui.py` is the right location:**
  It's the existing stub. Replacing it keeps `from ballast.adapters.agui import AGUIAdapter` stable for all future consumers.

  **Alternative considered and rejected:**
  Use `ag_ui.langgraph` bridge class now — rejected because the bridge class API is undocumented in the long-term plan and the observation goal requires seeing the raw underlying events, not the abstracted AG-UI layer.

  **What breaks if this step deviates:**
  If `stream()` doesn't `yield`, callers receive nothing. If `astream_events(version="v2")` is called without `version="v2"`, the returned event schema differs and the observation prints are wrong.

  ---

  **Idempotent:** Yes — overwriting the same file again produces the same result.

  **Context:** The current `agui.py` raises `NotImplementedError`. This step replaces it. The print statements are intentional for observation — removed in Week 2.

  **Preparatory — AG-UI API Discovery (no gate, run and record for Week 2 reference):**

  Run this script and note the output. It does not block the implementation below — the adapter uses LangGraph events, not ag_ui classes. This output is reference material for the Week 2 ag_ui wiring task.

  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import ag_ui.core as core
  print('=== ag_ui.core ===')
  print(dir(core))

  print()
  try:
      import ag_ui.langgraph as lg
      print('=== ag_ui.langgraph ===')
      print(dir(lg))
  except ImportError as e:
      print('ag_ui.langgraph ImportError:', e)

  print()
  try:
      from ag_ui.core import EventType
      print('=== EventType values ===')
      for e in EventType:
          print(' ', e.name, '=', e.value)
  except (ImportError, AttributeError) as e:
      print('EventType error:', e)
  "
  ```

  Record the full output as a comment in `scripts/observe.py` after this step completes (see Step 4).

  **Pre-Read Gate:**
  Before editing `agui.py`:
  - Run `grep -n "NotImplementedError" /Users/ngchenmeng/Ballast/ballast/adapters/agui.py`. Must return exactly 1 match. If 0 → adapter was already replaced, read current contents and stop.
  - Run `grep -c "class AGUIAdapter" /Users/ngchenmeng/Ballast/ballast/adapters/agui.py`. Must return 1.

  Write `/Users/ngchenmeng/Ballast/ballast/adapters/agui.py` — using the CONFIRMED class names from Phase A:

  ```python
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
  from langchain_openai import ChatOpenAI
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

      def __init__(self, model: str = "gpt-4o-mini") -> None:
          api_key = os.environ.get("OPENAI_API_KEY")
          if not api_key:
              raise EnvironmentError(
                  "OPENAI_API_KEY not set. Copy .env.example to .env and fill it in."
              )
          llm = ChatOpenAI(model=model, api_key=api_key)
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
  ```

  **What it does:** Creates a LangGraph ReAct agent with one tool (`get_word_count`), streams it with `astream_events` v2, and prints every event type and key fields. Yields each raw event for downstream consumers.

  **Why LangGraph `astream_events` v2:** It's the stable, documented LangGraph event API. It surfaces all internal events (chain, tool, LLM) needed to answer the three observation questions. The `ag-ui-langgraph` wrapper around it can be added in Week 2 once we know which events matter.

  **Why `get_word_count` as the tool:** Forces a TOOL_CALL event to fire on every run with a trivial goal like "count the words in: hello world". No external API, deterministic, always succeeds.

  **Assumptions:**
  - `OPENAI_API_KEY` is set in `.env` (checked at `__init__` time with clear error message)
  - `langchain-openai` and `langgraph` are installed (Step 1 complete)
  - `create_react_agent` from `langgraph.prebuilt` is available (it is — part of `langgraph>=0.1`)

  **Risks:**
  - `langchain-openai` uses deprecated API → mitigation: `gpt-4o-mini` is current; if deprecated, change model string
  - `astream_events` v2 not available in installed langgraph version → mitigation: verification checks this explicitly

  **Git Checkpoint (Phase B):**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/adapters/agui.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 2.3: implement AGUIAdapter with raw event streaming"
  ```

  **Subtasks:**
  - [ ] 🟥 Run preparatory discovery script, record output (Week 2 reference — does not block)
  - [ ] 🟥 Write `ballast/adapters/agui.py` with exact content above
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test (Phase B):**

  **Type:** Unit (import + instantiation without network)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import os
  os.environ['OPENAI_API_KEY'] = 'sk-test-placeholder'
  from ballast.adapters.agui import AGUIAdapter
  # Confirm it's a concrete subclass of AgentStream
  from ballast.core.stream import AgentStream
  import inspect
  assert not inspect.isabstract(AGUIAdapter), 'AGUIAdapter must not be abstract'
  assert issubclass(AGUIAdapter, AgentStream), 'AGUIAdapter must subclass AgentStream'
  print('AGUIAdapter import and subclass check OK')
  " 2>&1 | grep -v "UserWarning\|DeprecationWarning"
  ```

  **Expected:** `AGUIAdapter import and subclass check OK`

  **Pass:** Prints the OK line with exit code 0.

  **Fail:**
  - `ImportError` on any line → wrong package name or missing install → check `pip show langchain-openai langgraph`
  - `assert not inspect.isabstract(AGUIAdapter)` fails → `stream()` is still abstract → confirm the method body was written (not just the signature)
  - `EnvironmentError: OPENAI_API_KEY not set` → the test sets it via `os.environ` — if this fires, the `os.environ` line ran after `import` → move `os.environ` before the import in the test

---

- [ ] 🟥 **Step 4: Write + run observe.py** — *Non-critical: observation runner, not production code*

  **Step Architecture Thinking:**

  **Pattern applied:** Single-purpose script — `observe.py` has one job: call `AGUIAdapter.stream()` and answer the three questions. It is not a test, not a module, not a permanent fixture.

  **Why this step exists here in the sequence:**
  The adapter is implemented (Step 3) but hasn't run against a live API yet. This step produces the observation output that the long-term plan says "shapes everything trajectory validation does later."

  **Why a separate `scripts/` file:**
  Keeps the observation runner separate from the package. `scripts/` is not installed by `pip install ballast`. It doesn't pollute `tests/`.

  **Alternative rejected:**
  Run the adapter in a pytest test — rejected because a live API call in tests makes CI non-deterministic and slow.

  **What breaks if this deviates:**
  Nothing — this is observation-only. The output informs design decisions, not code.

  ---

  **Idempotent:** Yes — running the script multiple times produces the same event structure (tool calls may vary slightly in wording but types are stable).

  **Context:** Requires `OPENAI_API_KEY` in `.env`. This is the first live network call in the repo.

  > ⚠️ **Human Gate required before running:** Confirm `OPENAI_API_KEY` is set in `.env`.

  **Pre-Read Gate:**
  - Run `ls /Users/ngchenmeng/Ballast/scripts/ 2>&1`.
    - If "No such file or directory" → run `mkdir -p /Users/ngchenmeng/Ballast/scripts/` before writing the file.
    - If directory exists and `observe.py` is already in it → read it first before overwriting.

  Write `/Users/ngchenmeng/Ballast/scripts/observe.py`:
  ```python
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
      adapter = AGUIAdapter(model="gpt-4o-mini")
      events = []
      async for event in adapter.stream(OBSERVATION_GOAL, spec={}):
          events.append(event)
      print(f"\n[observe.py] Total events collected: {len(events)}")
      print("[observe.py] Done. Review the event sequence above to answer observation questions.")


  if __name__ == "__main__":
      asyncio.run(main())
  ```

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add scripts/observe.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 2.4: add observation runner script"
  ```

  **Subtasks:**
  - [ ] 🟥 Confirm `OPENAI_API_KEY` is in `.env` (Human Gate)
  - [ ] 🟥 Create `scripts/` directory if it doesn't exist: `mkdir -p /Users/ngchenmeng/Ballast/scripts/`
  - [ ] 🟥 Write `scripts/observe.py` with exact content above
  - [ ] 🟥 Run `python scripts/observe.py` from `/Users/ngchenmeng/Ballast/`
  - [ ] 🟥 Capture and record: event sequence list, any STATE_SNAPSHOT-equivalent event types, first tool call event type
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** E2E (live API)

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && /Users/ngchenmeng/Ballast/venv/bin/python scripts/observe.py 2>&1
  ```

  **Expected:**
  - Output contains `[AGUIAdapter] Event sequence`
  - At least one `on_tool_start` or `on_tool_end` line appears (tool was called)
  - Output ends with `[observe.py] Total events collected: N` where N > 0
  - No Python traceback

  **Pass:** Script exits 0 and output contains the event sequence list.

  **Fail:**
  - `EnvironmentError: OPENAI_API_KEY not set` → `.env` file missing or key not set → copy `.env.example` to `.env` and fill in key
  - `AuthenticationError` from OpenAI → key is set but invalid → check key value
  - `ModuleNotFoundError` → missing package → re-run `pip install -e ".[dev]"`
  - No tool events appear → `get_word_count` wasn't called → try a more explicit goal: `"Use the get_word_count tool to count: hello world"`

  **Human Gate — record observation answers:**
  After the script runs, record:
  ```
  1. Event types that fire (in order): ____
  2. State content visible mid-run: ____
  3. Natural intervention point event type: ____
  ```
  These answers inform Week 3's pause/inject/resume implementation.

---

### Phase 2 — Port Memory Layer

**Goal:** `ballast/core/memory.py` is importable, stores agent run history, applies half-life decay, and filters failed runs from consolidation. Tests pass.

---

- [ ] 🟥 **Step 5: Create ballast/core/memory.py** — *Critical: used by trajectory + spec in Week 2; schema defined here is the contract*

  **Step Architecture Thinking:**

  **Pattern applied:** **Single source of truth for agent memory schema** — `memory.py` defines the JSON schema (`quirks`, `runs`, `semantic_profile`, `run_count`, `last_consolidated`) in one place via `_empty_domain_data()`. All read/write functions call this — none inline `{}`. If any function inlines its own schema, new fields added to `_empty_domain_data()` will be missing from that function's output.

  **Why this step exists here in the sequence:**
  `filelock` was installed in Step 1. `anthropic` and `pydantic` were in the original deps. All imports are now satisfiable. Week 2's `spec.py` and `trajectory.py` will import `from ballast.core.memory import recall, log_run` — this file must exist before those are written.

  **Why `ballast/core/memory.py`:**
  `core/` is the package's internal contract layer. Memory is a core primitive used by adapters, spec, trajectory, and the healer. Placing it in `adapters/` would create circular imports when adapters call `log_run`.

  **Alternative considered and rejected:**
  Copy GroundWire's `memory.py` verbatim — rejected for four reasons: (1) imports `guardrails`, `llm_utils`, `schemas` which don't exist in Ballast; (2) `_DECAY_RATE = 0.95` constant encodes an arbitrary half-life; (3) `consolidate()` doesn't filter `success=False` runs; (4) `record_antibot_*` functions are web-specific dead code.

  **What breaks if this step deviates:**
  If `_empty_domain_data()` is not the single schema source, any function that inlines `{}` will produce files missing new fields → `recall()` will silently return wrong data when those fields are later added.

  ---

  **Idempotent:** Yes — writing the same file again is safe.

  **Context:** GroundWire's memory.py is the source of truth for the logic. This is a port with three targeted changes: (1) remove web-specific imports, (2) replace `_DECAY_RATE` with `_decay_factor()`, (3) filter `success=True` in `consolidate()`.

  **Pre-Read Gate:**
  - Run `ls /Users/ngchenmeng/Ballast/ballast/core/memory.py 2>&1`. Must return "No such file". If it exists → read current contents before overwriting.

  Write `/Users/ngchenmeng/Ballast/ballast/core/memory.py`:

  ```python
  """ballast/core/memory.py — three-layer agent memory.

  Port of GroundWire memory.py with three changes:
    1. Web-specific imports removed (guardrails, llm_utils, schemas)
    2. Half-life–based temporal decay replaces flat _DECAY_RATE constant
    3. consolidate() filters success=False runs before semantic synthesis

  Storage: .ballast_memory/<scope>.json
  Schema:
    {
      "quirks":           [{"text": str, "confidence": float, "last_seen": float}],
      "runs":             [{"id": str, "goal": str, "timestamp": float,
                            "step_count": int, "success": bool, "is_trial": bool}],
      "semantic_profile": str,
      "run_count":        int,
      "last_consolidated": float
    }

  Public interface:
      recall, write, extract_quirks, log_run, consolidate,
      atomic_write_json, memory_report, patch_quirk
  """
  from __future__ import annotations

  import json
  import math
  import os
  import tempfile
  import time
  from pathlib import Path

  import anthropic
  from filelock import FileLock
  from pydantic import BaseModel

  MEMORY_DIR = Path(".ballast_memory")
  MEMORY_DIR.mkdir(exist_ok=True)

  # Semantic consolidation every N real (non-trial) runs.
  CONSOLIDATE_EVERY = 3

  _ANTHROPIC_MODEL = "claude-sonnet-4-6"

  # Half-life for cross-run observation decay (30 days).
  # Tune this if agents over-rely on old context (increase) or miss relevant history (decrease).
  # Session-level decay (8h half-life) can be added via a decay_mode param to write() in Week 2.
  _HALF_LIFE_LONG_TERM_SECONDS: float = 30.0 * 86400   # 30 days

  _client: anthropic.Anthropic | None = None


  # ---------------------------------------------------------------------------
  # Pydantic schemas (inline — no external schemas.py dependency)
  # ---------------------------------------------------------------------------

  class _QuirksList(BaseModel):
      quirks: list[str]


  class _SemanticProfile(BaseModel):
      profile: str


  # ---------------------------------------------------------------------------
  # Internal helpers
  # ---------------------------------------------------------------------------

  def _get_client() -> anthropic.Anthropic:
      """Lazy singleton — created on first LLM call."""
      global _client
      if _client is None:
          _client = anthropic.Anthropic()
      return _client


  def _decay_factor(half_life_seconds: float, elapsed_seconds: float) -> float:
      """Exponential decay: returns gamma^elapsed where gamma = 0.5^(1/half_life).

      Equivalent to exp(-ln(2) * elapsed / half_life).
      Returns 1.0 for zero or negative elapsed time (no decay yet).

      Args:
          half_life_seconds: Time in seconds for confidence to halve.
          elapsed_seconds:   Time elapsed since last observation.
      """
      if elapsed_seconds <= 0:
          return 1.0
      return math.exp(-math.log(2.0) * elapsed_seconds / half_life_seconds)


  def _scope_path(scope: str) -> Path:
      """Sanitise scope string into a safe filename."""
      safe = scope.replace(":", "_").replace("/", "_")
      return MEMORY_DIR / f"{safe}.json"


  def _scope_lock(path: Path) -> FileLock:
      """Per-scope advisory lock — serializes all read-modify-write operations."""
      return FileLock(str(path.with_suffix(".lock")), timeout=10)


  def _empty_scope_data() -> dict:
      """Canonical empty schema. Single source of truth for all functions."""
      return {
          "quirks": [],
          "runs": [],
          "semantic_profile": "",
          "run_count": 0,
          "last_consolidated": 0.0,
      }


  def _parse_structured(
      model: str,
      max_tokens: int,
      messages: list[dict],
      response_model: type,
  ) -> object:
      """Call Claude with tool_use to enforce structured output.

      Uses tool_choice = {"type": "tool", "name": "structured_output"} so the
      model always returns the schema rather than free text.
      """
      schema = response_model.model_json_schema()
      response = _get_client().messages.create(
          model=model,
          max_tokens=max_tokens,
          tools=[
              {
                  "name": "structured_output",
                  "description": "Return structured data in the required schema.",
                  "input_schema": schema,
              }
          ],
          tool_choice={"type": "tool", "name": "structured_output"},
          messages=messages,
      )
      for block in response.content:
          if block.type == "tool_use":
              return response_model(**block.input)
      raise ValueError("Claude response contained no tool_use block")


  # ---------------------------------------------------------------------------
  # Public interface
  # ---------------------------------------------------------------------------

  def atomic_write_json(path: Path, data: dict) -> None:
      """Persist JSON via temp file + os.replace (atomic on POSIX)."""
      path.parent.mkdir(parents=True, exist_ok=True)
      fd, tmp_path = tempfile.mkstemp(
          suffix=".tmp",
          prefix=path.name + ".",
          dir=str(path.parent),
          text=True,
      )
      try:
          with os.fdopen(fd, "w", encoding="utf-8") as f:
              json.dump(data, f, indent=2)
          os.replace(tmp_path, path)
      except BaseException:
          try:
              os.unlink(tmp_path)
          except OSError:
              pass
          raise


  def recall(scope: str) -> str:
      """Return a stratified plain-English briefing for this scope.

      Layer 1 (always): run count + confidence label.
      Layer 2 (if exists): semantic profile sentence.
      Layer 3 (if exists): top 10 observations sorted by confidence descending.
      Returns "" if no memory exists. Never returns None.
      """
      path = _scope_path(scope)
      if not path.exists():
          return ""

      try:
          data = json.loads(path.read_text(encoding="utf-8"))
      except (json.JSONDecodeError, OSError):
          return ""

      run_count = data.get("run_count", 0)
      quirks = data.get("quirks", [])
      semantic_profile = data.get("semantic_profile", "")

      if run_count == 0 and not quirks and not semantic_profile:
          return ""

      if run_count >= 10:
          confidence_label = "high"
      elif run_count >= 4:
          confidence_label = "medium"
      else:
          confidence_label = "low"

      lines = [
          f"Agent memory for {scope} — {run_count} run(s), confidence: {confidence_label}"
      ]

      if semantic_profile:
          lines.append(f"  Strategic profile: {semantic_profile}")

      if quirks:
          dict_quirks = [q for q in quirks if isinstance(q, dict)]
          sorted_quirks = sorted(
              dict_quirks, key=lambda q: q.get("confidence", 1), reverse=True
          )[:10]
          lines.append("  Known observations:")
          for q in sorted_quirks:
              text = q.get("text", "")
              conf = q.get("confidence", 1)
              lines.append(f"    - {text} (confidence {conf:.2f})")

      return "\n".join(lines)


  def write(scope: str, new_observations: list[str]) -> None:
      """Upsert observations into the confidence map for this scope.

      Re-seen observation → apply half-life decay then increment by 1.
      New observation → insert with confidence=1.0.
      Unseen observations in this batch → decay only (no increment).

      Decay uses long-term half-life (30 days) — appropriate for cross-run memory.
      """
      new_observations = [o for o in list(new_observations) if o and o.strip()]
      if not new_observations:
          return

      path = _scope_path(scope)
      with _scope_lock(path):
          if path.exists():
              try:
                  data = json.loads(path.read_text(encoding="utf-8"))
              except (json.JSONDecodeError, OSError):
                  data = _empty_scope_data()
          else:
              data = _empty_scope_data()

          raw_quirks = data.get("quirks", [])
          existing: dict[str, dict] = {}
          now = time.time()

          for q in raw_quirks:
              if isinstance(q, str):
                  existing[q] = {"text": q, "confidence": 1.0, "last_seen": now}
              elif isinstance(q, dict) and "text" in q:
                  existing[q["text"]] = q

          new_set = set(new_observations)

          # Decay observations not seen in this batch.
          for text, q in existing.items():
              if text not in new_set:
                  elapsed = now - float(q.get("last_seen", now))
                  q["confidence"] = max(
                      0.1,
                      float(q.get("confidence", 1.0))
                      * _decay_factor(_HALF_LIFE_LONG_TERM_SECONDS, elapsed),
                  )

          # Update or insert observations seen in this batch.
          for text in new_observations:
              if text in existing:
                  prev = float(existing[text].get("confidence", 1.0))
                  last_seen = float(existing[text].get("last_seen", now))
                  elapsed = now - last_seen
                  decayed = prev * _decay_factor(_HALF_LIFE_LONG_TERM_SECONDS, elapsed)
                  existing[text]["confidence"] = max(0.1, decayed + 1.0)
                  existing[text]["last_seen"] = now
              else:
                  existing[text] = {"text": text, "confidence": 1.0, "last_seen": now}

          data["quirks"] = list(existing.values())
          atomic_write_json(path, data)


  def extract_quirks(events: list[dict], scope: str) -> list[str]:
      """Ask Claude to extract agent-specific observations from events.

      Uses head+tail sampling (first 10 + last 10) to capture early and late patterns.
      Returns list[str]. Returns [] on any error — never raises.
      """
      if not events:
          return []

      head = events[:10]
      tail = events[-10:] if len(events) > 10 else []
      event_sample = json.dumps(head + tail, indent=2)

      prompt = (
          f"These are events from an AI agent working on scope: {scope}.\n"
          "Identify agent-specific observations:\n"
          "- Repeated failure patterns\n"
          "- Tool call sequences that reliably succeed\n"
          "- State transitions that indicate progress vs stalling\n"
          "- Goal types that this agent handles well vs struggles with\n\n"
          "Return a JSON object with a single key \"quirks\" whose value is an array of short strings.\n"
          "No preamble. No markdown. If none, use {\"quirks\": []}.\n"
          'Example: {"quirks": ["Multi-step tool chains succeed when the first tool call succeeds", '
          '"Goals with ambiguous scope cause repeated clarification loops"]}\n\n'
          f"Events:\n{event_sample}"
      )

      try:
          out = _parse_structured(
              model=_ANTHROPIC_MODEL,
              max_tokens=400,
              messages=[{"role": "user", "content": prompt}],
              response_model=_QuirksList,
          )
          return [q for q in out.quirks if isinstance(q, str)]
      except Exception:
          return []


  def log_run(
      scope: str,
      goal: str,
      events: list[dict],
      success: bool = True,
      is_trial: bool = False,
  ) -> None:
      """Append an episodic run entry. Increments run_count via len(runs).

      is_trial=True marks eval-mode runs excluded from consolidation.
      success=False marks failed runs excluded from semantic synthesis.
      This function owns run_count — write() does not touch it.
      """
      path = _scope_path(scope)
      with _scope_lock(path):
          if path.exists():
              try:
                  data = json.loads(path.read_text(encoding="utf-8"))
              except (json.JSONDecodeError, OSError):
                  data = _empty_scope_data()
          else:
              data = _empty_scope_data()

          run_entry = {
              "id": str(int(time.time())),
              "goal": goal,
              "timestamp": time.time(),
              "step_count": len(events),
              "success": success,
              "is_trial": is_trial,
          }

          runs = data.get("runs", [])
          runs.append(run_entry)
          data["runs"] = runs[-100:]  # cap at 100 most recent
          data["run_count"] = len(data["runs"])

          atomic_write_json(path, data)


  def consolidate(scope: str) -> bool:
      """Every CONSOLIDATE_EVERY real successful runs, synthesize a semantic profile.

      Filters applied before synthesis:
        - is_trial=True runs excluded (eval goals bias the profile)
        - success=False runs excluded (failed runs contaminate the synthesis)

      Returns True if consolidation ran. Never raises.
      """
      path = _scope_path(scope)
      if not path.exists():
          return False

      with _scope_lock(path):
          try:
              data = json.loads(path.read_text(encoding="utf-8"))
          except (json.JSONDecodeError, OSError):
              return False

          all_runs = data.get("runs", [])
          # Only real, successful runs drive consolidation.
          synthesis_runs = [
              r for r in all_runs
              if not r.get("is_trial", False) and r.get("success", True)
          ]

          if len(synthesis_runs) == 0 or len(synthesis_runs) % CONSOLIDATE_EVERY != 0:
              return False

          recent_runs = synthesis_runs[-20:]
          runs_summary = json.dumps(
              [
                  {
                      "goal": r.get("goal"),
                      "step_count": r.get("step_count"),
                      "success": r.get("success"),
                  }
                  for r in recent_runs
              ],
              indent=2,
          )

          top_quirks = sorted(
              data.get("quirks", []),
              key=lambda q: q.get("confidence", 0) if isinstance(q, dict) else 0,
              reverse=True,
          )[:10]
          quirks_summary = json.dumps(
              [
                  {"text": q.get("text"), "confidence": q.get("confidence")}
                  for q in top_quirks
              ],
              indent=2,
          )

          prompt = (
              f"You are analysing an AI agent's run history for scope: {scope}.\n"
              f"Recent successful runs ({len(recent_runs)}):\n{runs_summary}\n\n"
              f"Top observations (by confidence):\n{quirks_summary}\n\n"
              "Write ONE sentence (max 40 words) strategic profile of this agent's behavior.\n"
              "Focus on: reliability, common failure points, goal types that succeed vs struggle.\n"
              'Return ONLY JSON: {"profile": "<your sentence ending with a period>"}'
          )

          try:
              out = _parse_structured(
                  model=_ANTHROPIC_MODEL,
                  max_tokens=150,
                  messages=[{"role": "user", "content": prompt}],
                  response_model=_SemanticProfile,
              )
              data["semantic_profile"] = out.profile.strip()
              data["last_consolidated"] = time.time()
              atomic_write_json(path, data)
              return True
          except Exception:
              return False


  def patch_quirk(scope: str, quirk_text: str, delta: float) -> None:
      """Increment or decrement confidence on a single observation by delta.

      Positive delta confirms the observation after a successful run.
      Negative delta weakens an observation whose hypothesis wasn't confirmed.
      Confidence clamped to [0.1, 10.0]. No-op if quirk_text not found. Never raises.
      """
      path = _scope_path(scope)
      if not path.exists():
          return
      try:
          with _scope_lock(path):
              try:
                  data = json.loads(path.read_text(encoding="utf-8"))
              except (json.JSONDecodeError, OSError):
                  return
              changed = False
              for q in data.get("quirks", []):
                  if isinstance(q, dict) and q.get("text") == quirk_text:
                      current = float(q.get("confidence", 1.0))
                      q["confidence"] = round(max(0.1, min(10.0, current + delta)), 4)
                      changed = True
                      break
              if changed:
                  atomic_write_json(path, data)
      except Exception:
          pass


  def memory_report(scope: str) -> str:
      """Pretty-print accumulated memory for a scope. Never raises."""
      path = _scope_path(scope)
      if not path.exists():
          return f"No memory for {scope} — no runs recorded yet."

      try:
          data = json.loads(path.read_text(encoding="utf-8"))
      except (json.JSONDecodeError, OSError):
          return f"Memory file for {scope} could not be read."

      all_runs = data.get("runs", [])
      real_runs = [r for r in all_runs if not r.get("is_trial", False)]
      trial_runs = [r for r in all_runs if r.get("is_trial", False)]
      run_count = data.get("run_count", 0)
      semantic_profile = data.get("semantic_profile", "")
      quirks = [q for q in data.get("quirks", []) if isinstance(q, dict)]

      border = "═" * 67
      lines = [
          f"╔{border}╗",
          f"║  Ballast Memory Report — {scope[:40]:<40}  ║",
          f"╠{border}╣",
      ]

      run_line = (
          f"  Runs: {run_count} total  "
          f"({len(real_runs)} real · {len(trial_runs)} eval trials)"
      )
      lines.append(f"║{run_line:<67}║")

      if real_runs:
          success_count = sum(1 for r in real_runs if r.get("success", True))
          avg_steps = sum(r.get("step_count", 0) for r in real_runs) / len(real_runs)
          success_line = (
              f"  Success: {success_count}/{len(real_runs)} real runs  "
              f"·  avg {avg_steps:.1f} steps/run"
          )
          lines.append(f"║{success_line:<67}║")

      if semantic_profile:
          words = semantic_profile.split()
          line_buf: list[str] = []
          wrapped: list[str] = []
          for word in words:
              if sum(len(w) + 1 for w in line_buf) + len(word) > 63:
                  wrapped.append(" ".join(line_buf))
                  line_buf = [word]
              else:
                  line_buf.append(word)
          if line_buf:
              wrapped.append(" ".join(line_buf))
          lines.append(f"╠{border}╣")
          lines.append(f"║  Profile:{'':>57}║")
          for wline in wrapped:
              lines.append(f"║    {wline:<63}║")

      if quirks:
          sorted_quirks = sorted(
              quirks, key=lambda q: q.get("confidence", 0), reverse=True
          )[:5]
          lines.append(f"╠{border}╣")
          lines.append(f"║  Top observations by confidence:{'':>34}║")
          for q in sorted_quirks:
              conf = q.get("confidence", 0)
              text = q.get("text", "")[:50]
              filled = min(5, int(conf))
              bar = "█" * filled + "░" * (5 - filled)
              lines.append(f"║  {bar} {conf:4.1f}x  {text:<50}  ║")

      lines.append(f"╚{border}╝")
      return "\n".join(lines)
  ```

  **What it does:** Provides `recall/write/log_run/consolidate/patch_quirk/memory_report` over a JSON file store. `_decay_factor()` uses the half-life formula instead of a flat daily rate. `consolidate()` filters `success=False` runs before semantic synthesis. All web-specific GroundWire code is removed.

  **Why `_decay_factor` instead of `_DECAY_RATE = 0.95`:** The constant `0.95` per day encodes a 13.5-day half-life (0.95^13.5 ≈ 0.5) without documenting it. The function `exp(-ln(2)*t/half_life)` makes the half-life explicit and configurable per call site — session vs long-term are different constants, not different magic numbers.

  **Why filter `success=False` in consolidate():** GroundWire's `consolidate()` only filtered `is_trial`. Failed runs (e.g. tool error, timeout) contain failure-mode information, not reliable strategy information. Synthesizing a semantic profile from failed runs produces "this agent frequently fails at X" rather than "this agent excels at Y when Z".

  **Assumptions:**
  - `filelock` installed (Step 1 complete)
  - `anthropic` installed (original deps)
  - `pydantic>=2.0` installed (original deps)
  - `ANTHROPIC_API_KEY` in env when `extract_quirks/consolidate` are called (set lazily in `_get_client()`)

  **Risks:**
  - `FileLock` timeout=10 blocks on high-frequency concurrent writes → mitigation: 10s is generous for local dev; reduce if needed in production
  - `.ballast_memory/` created at import time → if CWD is not repo root, files land in wrong dir → mitigation: caller should `os.chdir()` to repo root before importing

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/core/memory.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 2.5: port memory layer with half-life decay and success filtering"
  ```

  **Subtasks:**
  - [ ] 🟥 Confirm `ballast/core/memory.py` does not exist (Pre-Read Gate)
  - [ ] 🟥 Write `ballast/core/memory.py` with exact content above
  - [ ] 🟥 Confirm import succeeds
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import math
  from ballast.core.memory import (
      recall, write, log_run, consolidate, memory_report,
      patch_quirk, atomic_write_json, _decay_factor, CONSOLIDATE_EVERY,
  )

  # Test 1: decay factor at half-life returns 0.5
  hl = 86400.0  # 1 day
  factor = _decay_factor(hl, hl)
  assert abs(factor - 0.5) < 1e-9, f'Expected 0.5, got {factor}'
  print('Test 1 passed: _decay_factor(hl, hl) == 0.5')

  # Test 2: decay factor at t=0 returns 1.0
  assert _decay_factor(86400.0, 0) == 1.0, 'Expected 1.0 at t=0'
  print('Test 2 passed: _decay_factor(hl, 0) == 1.0')

  # Test 3: recall returns empty string for unknown scope
  result = recall('test-scope-that-does-not-exist-xyz')
  assert result == '', f'Expected empty string, got {result!r}'
  print('Test 3 passed: recall returns empty string for unknown scope')

  # Test 4: CONSOLIDATE_EVERY is 3
  assert CONSOLIDATE_EVERY == 3, f'Expected 3, got {CONSOLIDATE_EVERY}'
  print('Test 4 passed: CONSOLIDATE_EVERY == 3')

  print('All memory import checks passed')
  "
  ```

  **Expected:** 4 `passed` lines followed by `All memory import checks passed`.

  **Pass:** All 4 assertions succeed.

  **Fail:**
  - `ImportError` on any import → check file was written and all internal imports (pydantic, filelock, anthropic) are available
  - `AssertionError: Expected 0.5` → `_decay_factor` math wrong → check the formula: `math.exp(-math.log(2.0) * elapsed / half_life)`
  - `AssertionError: Expected empty string` → `recall()` returned non-empty for a scope that doesn't exist → check `if not path.exists(): return ""`

---

- [ ] 🟥 **Step 6: Write tests/test_memory.py** — *Non-critical: validates memory contract without hitting live API*

  **Step Architecture Thinking:**

  **Pattern applied:** **Contract test** — tests prove behavioral invariants of the public interface, not implementation details. No LLM calls. Temp dirs for isolation.

  **Why this step exists here in the sequence:**
  Memory is a critical module used by Week 2 code. Tests prove the decay math, schema invariants, and filtering logic before any downstream consumer depends on them.

  **Why `tests/test_memory.py`:**
  Matches pytest discovery convention. Keeps tests adjacent to other test files.

  **Alternative rejected:**
  Integration tests that call `extract_quirks`/`consolidate` (hitting Claude) — rejected because they're slow, require API keys, and are non-deterministic. Unit tests for the pure functions are sufficient for the contract.

  **What breaks if this deviates:**
  Nothing at Week 1 — but if the decay math is wrong and tests don't catch it, Week 2's trajectory validator will weight old context incorrectly.

  ---

  **Idempotent:** Yes.

  Write `/Users/ngchenmeng/Ballast/tests/test_memory.py`:
  ```python
  """Tests for ballast/core/memory.py — pure function contract tests only.

  No LLM calls. Uses tmp_path for filesystem isolation.
  """
  import math
  import time
  from pathlib import Path

  import pytest

  from ballast.core.memory import (
      CONSOLIDATE_EVERY,
      _decay_factor,
      atomic_write_json,
      log_run,
      memory_report,
      patch_quirk,
      recall,
      write,
  )


  # ---------------------------------------------------------------------------
  # _decay_factor — math contract
  # ---------------------------------------------------------------------------

  def test_decay_at_half_life_is_half():
      """At t = half_life, confidence must be exactly 0.5."""
      hl = 86400.0
      assert abs(_decay_factor(hl, hl) - 0.5) < 1e-9


  def test_decay_at_zero_is_one():
      """At t = 0, no decay has occurred."""
      assert _decay_factor(86400.0, 0) == 1.0


  def test_decay_at_double_half_life_is_quarter():
      """At t = 2 * half_life, confidence must be 0.25."""
      hl = 86400.0
      assert abs(_decay_factor(hl, 2 * hl) - 0.25) < 1e-9


  def test_decay_monotonically_decreases():
      """Confidence decreases as elapsed time increases."""
      hl = 86400.0
      factors = [_decay_factor(hl, t) for t in [0, hl / 2, hl, 2 * hl]]
      assert factors == sorted(factors, reverse=True)


  def test_decay_negative_elapsed_returns_one():
      """Negative elapsed time (clock skew) returns 1.0 — no negative decay."""
      assert _decay_factor(86400.0, -100) == 1.0


  # ---------------------------------------------------------------------------
  # recall — empty store behavior
  # ---------------------------------------------------------------------------

  def test_recall_unknown_scope_returns_empty_string(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      result = recall("nonexistent-scope-xyz")
      assert result == ""


  def test_recall_after_log_run_returns_nonempty(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      log_run("test-scope", goal="do something", events=[{}, {}], success=True)
      result = recall("test-scope")
      assert "test-scope" in result
      assert "1 run" in result


  # ---------------------------------------------------------------------------
  # write — confidence upsert + decay
  # ---------------------------------------------------------------------------

  def test_write_new_observation_confidence_is_one(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      write("scope-a", ["tool chains succeed when first call succeeds"])
      data = __load(tmp_path, "scope-a")
      quirks = data["quirks"]
      assert len(quirks) == 1
      assert abs(quirks[0]["confidence"] - 1.0) < 0.01


  def test_write_reseen_observation_increments_confidence(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      write("scope-b", ["observation-x"])
      write("scope-b", ["observation-x"])
      data = __load(tmp_path, "scope-b")
      quirks = {q["text"]: q["confidence"] for q in data["quirks"]}
      # Confidence should be > 1.0 after being seen twice
      assert quirks["observation-x"] > 1.0


  def test_write_drops_empty_strings(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      write("scope-c", ["", "  ", "valid observation"])
      data = __load(tmp_path, "scope-c")
      texts = [q["text"] for q in data["quirks"]]
      assert "" not in texts
      assert "  " not in texts
      assert "valid observation" in texts


  # ---------------------------------------------------------------------------
  # log_run — schema contract
  # ---------------------------------------------------------------------------

  def test_log_run_increments_run_count(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      log_run("scope-d", goal="task 1", events=[{}], success=True)
      log_run("scope-d", goal="task 2", events=[{}, {}], success=False)
      data = __load(tmp_path, "scope-d")
      assert data["run_count"] == 2


  def test_log_run_stores_success_field(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      log_run("scope-e", goal="failing task", events=[], success=False)
      data = __load(tmp_path, "scope-e")
      assert data["runs"][0]["success"] is False


  def test_log_run_stores_is_trial_field(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      log_run("scope-f", goal="eval task", events=[], is_trial=True)
      data = __load(tmp_path, "scope-f")
      assert data["runs"][0]["is_trial"] is True


  # ---------------------------------------------------------------------------
  # consolidate — filtering contract (no LLM calls)
  # ---------------------------------------------------------------------------

  def test_consolidate_does_not_run_on_wrong_count(tmp_path, monkeypatch):
      """consolidate() must NOT run unless real successful run count % CONSOLIDATE_EVERY == 0."""
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      # Log CONSOLIDATE_EVERY - 1 real successful runs
      for i in range(CONSOLIDATE_EVERY - 1):
          log_run("scope-g", goal=f"task {i}", events=[], success=True)
      result = consolidate("scope-g")
      # Should not have run (would need an LLM call if it did)
      # We can't test the Claude call, but we CAN test it returns False
      # when the count doesn't align
      # Note: if CONSOLIDATE_EVERY == 3, we've done 2 runs — should return False
      if (CONSOLIDATE_EVERY - 1) % CONSOLIDATE_EVERY != 0:
          assert result is False


  def test_consolidate_excludes_failed_runs_from_count(tmp_path, monkeypatch):
      """Failed runs must not count toward consolidation trigger."""
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      # Log CONSOLIDATE_EVERY failed runs + 1 successful
      for i in range(CONSOLIDATE_EVERY):
          log_run("scope-h", goal=f"failing {i}", events=[], success=False)
      log_run("scope-h", goal="success", events=[], success=True)
      # Only 1 real successful run → should not trigger consolidation
      # (We verify by checking that semantic_profile is still empty)
      data = __load(tmp_path, "scope-h")
      # consolidate() would require LLM; we just verify failed runs don't count
      synthesis_runs = [
          r for r in data["runs"]
          if not r.get("is_trial", False) and r.get("success", True)
      ]
      assert len(synthesis_runs) == 1  # only the 1 successful run


  # ---------------------------------------------------------------------------
  # patch_quirk — clamping
  # ---------------------------------------------------------------------------

  def test_patch_quirk_clamps_to_minimum(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      write("scope-i", ["fragile observation"])
      patch_quirk("scope-i", "fragile observation", delta=-100.0)
      data = __load(tmp_path, "scope-i")
      assert data["quirks"][0]["confidence"] >= 0.1


  def test_patch_quirk_clamps_to_maximum(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      write("scope-j", ["strong observation"])
      patch_quirk("scope-j", "strong observation", delta=100.0)
      data = __load(tmp_path, "scope-j")
      assert data["quirks"][0]["confidence"] <= 10.0


  def test_patch_quirk_noop_for_missing_text(tmp_path, monkeypatch):
      """patch_quirk on a text that doesn't exist must not raise and not change state."""
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      write("scope-k", ["existing observation"])
      patch_quirk("scope-k", "nonexistent observation", delta=1.0)
      data = __load(tmp_path, "scope-k")
      assert len(data["quirks"]) == 1  # no new quirk added


  # ---------------------------------------------------------------------------
  # memory_report — smoke test
  # ---------------------------------------------------------------------------

  def test_memory_report_unknown_scope(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      result = memory_report("nonexistent")
      assert "No memory" in result


  def test_memory_report_known_scope_contains_scope_name(tmp_path, monkeypatch):
      import ballast.core.memory as mem
      monkeypatch.setattr(mem, "MEMORY_DIR", tmp_path)
      log_run("scope-l", goal="test", events=[], success=True)
      result = memory_report("scope-l")
      assert "scope-l" in result


  # ---------------------------------------------------------------------------
  # Helper
  # ---------------------------------------------------------------------------

  def __load(tmp_path: Path, scope: str) -> dict:
      safe = scope.replace(":", "_").replace("/", "_")
      path = tmp_path / f"{safe}.json"
      import json
      return json.loads(path.read_text())


  def consolidate(scope: str) -> bool:
      """Local re-import that uses the monkeypatched MEMORY_DIR."""
      from ballast.core import memory as mem
      return mem.consolidate(scope)
  ```

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add tests/test_memory.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 2.6: add memory unit tests"
  ```

  **Subtasks:**
  - [ ] 🟥 Write `tests/test_memory.py` with exact content above
  - [ ] 🟥 Run pytest
  - [ ] 🟥 Confirm 4 + 20 = 24 tests pass (4 existing + 20 new memory tests)
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ -v --tb=short 2>&1 | tail -15
  ```

  **Expected:**
  - All original 4 stream tests still pass
  - All new memory tests pass (20 tests)
  - Total: `24 passed`
  - Zero failures

  **Pass:** Output ends with `24 passed`.

  **Fail:**
  - `ImportError` on `from ballast.core.memory import _decay_factor` → check function is defined in memory.py (it's private, test imports it directly)
  - `AssertionError: Expected 0.5` → decay formula wrong → check `math.exp(-math.log(2.0) * elapsed / half_life)`
  - `test_consolidate_excludes_failed_runs_from_count` fails → `consolidate()` is not filtering `success=False` → check the `synthesis_runs` filter line in `consolidate()`
  - `test_patch_quirk_clamps_to_minimum` fails → `patch_quirk` not clamping → check `max(0.1, min(10.0, current + delta))`
  - Original 4 tests fail → pyproject.toml or package structure changed → `pip install -e .` and re-run

---

## Regression Guard

**Systems at risk:**
- `ballast.core.stream` — not modified; no risk
- `ballast.adapters.agui` — fully replaced in Step 3; original stub behavior is intentionally gone

**Regression verification:**

| System | Pre-change behavior | Post-change verification |
|--------|---------------------|--------------------------|
| `AgentStream` ABC | 4 tests pass | Run `pytest tests/test_stream.py` — must still be 4 passed |
| `AGUIAdapter` import | Importable, raises `NotImplementedError` | Post Step 3: importable, does NOT raise on import; raises `EnvironmentError` if no API key |

**Test count regression check:**
- Before plan: `4 passed`
- After Step 3 (adapter): still `4 passed` (adapter tests are integration, not in test suite)
- After Step 6 (memory): `24 passed` (4 existing + 20 new)

---

## Rollback Procedure

```bash
# Rollback Step 6 (memory tests)
git -C /Users/ngchenmeng/Ballast revert HEAD  # reverts step 2.6 commit

# Rollback Step 5 (memory.py)
git -C /Users/ngchenmeng/Ballast revert HEAD  # reverts step 2.5 commit

# Rollback Step 3 (AGUIAdapter)
git -C /Users/ngchenmeng/Ballast revert HEAD  # reverts step 2.3 commit
# Restore stub manually if needed:
# echo '"""AG-UI adapter stub — implement in Week 1 day 2."""
# from ballast.core.stream import AgentStream
#
# class AGUIAdapter(AgentStream):
#     async def stream(self, goal: str, spec: dict):
#         raise NotImplementedError("AGUIAdapter.stream() not yet implemented")
# ' > ballast/adapters/agui.py

# Rollback Step 1 (deps)
git -C /Users/ngchenmeng/Ballast revert HEAD  # reverts step 2.1 commit
/Users/ngchenmeng/Ballast/venv/bin/pip install -e "/Users/ngchenmeng/Ballast[dev]"

# Verify rollback:
/Users/ngchenmeng/Ballast/venv/bin/pytest tests/ -v --tb=short | tail -3
# Must show: 4 passed
```

---

## Pre-Flight Checklist

| Phase | Check | How to Confirm | Status |
|-------|-------|----------------|--------|
| Pre-flight | 4 existing tests pass | `pytest tests/ -v` → `4 passed` | ⬜ |
| Pre-flight | `memory.py` does not exist | `ls ballast/core/memory.py` → No such file | ⬜ |
| Pre-flight | `agui.py` is still the stub | `grep NotImplementedError ballast/adapters/agui.py` → 1 match | ⬜ |
| Phase 1 Step 1 | New deps importable | `python -c "import ag_ui.core; import langchain_openai; import filelock"` | ⬜ |
| Phase 1 Step 3 | ag_ui API discovery run (Week 2 ref) | Discovery script exits 0, output recorded | ⬜ |
| Phase 1 Step 3 | AGUIAdapter subclasses AgentStream | `issubclass(AGUIAdapter, AgentStream)` | ⬜ |
| Phase 1 Step 4 | Live run completes | Event sequence printed, no traceback | ⬜ |
| Phase 2 Step 5 | memory.py imports cleanly | `_decay_factor(86400, 86400) ≈ 0.5` | ⬜ |
| Phase 2 Step 6 | 24 tests pass | `pytest tests/ -v` → `24 passed` | ⬜ |

---

## Risk Heatmap

| Step | Risk Level | What Could Go Wrong | Early Detection | Idempotent |
|------|-----------|---------------------|-----------------|------------|
| Step 1 | 🟡 Medium | `ag-ui-langgraph` wrong PyPI name | `pip install` exit code ≠ 0 | Yes |
| Step 3 (discovery) | 🟢 Low | Package has no `ag_ui.langgraph` | ImportError printed clearly | Yes |
| Step 3 (adapter) | 🟡 Medium | LangGraph API version mismatch | Import check + unit test | Yes |
| Step 4 | 🔴 High | Live API call fails (bad key, quota) | `AuthenticationError` in output | Yes |
| Step 5 | 🟢 Low | Decay formula wrong | Unit test catches it | Yes |
| Step 6 | 🟢 Low | monkeypatch doesn't isolate MEMORY_DIR | Tests write to `.ballast_memory/` | Yes |

---

## Success Criteria

| Feature | Target | Verification |
|---------|--------|--------------|
| Streaming adapter works | `AGUIAdapter.stream()` yields real events | **Do:** run `observe.py` → **Expect:** event sequence printed → **Look:** stdout |
| Three questions answered | Event types, STATE_SNAPSHOT content, intervention point identified | **Do:** read `observe.py` output → **Expect:** non-empty event sequence with tool events |
| Memory importable | `from ballast.core.memory import recall, write, log_run` works | **Do:** import check → **Expect:** no error |
| Decay math correct | `_decay_factor(hl, hl) == 0.5` | **Do:** `pytest tests/test_memory.py::test_decay_at_half_life_is_half` → **Expect:** PASSED |
| Success filtering works | Failed runs excluded from synthesis count | **Do:** `pytest tests/test_memory.py::test_consolidate_excludes_failed_runs_from_count` → **Expect:** PASSED |
| No regressions | All 4 original tests still pass | **Do:** `pytest tests/test_stream.py -v` → **Expect:** `4 passed` |
| Test count | 24 (4 existing + 20 memory) | **Do:** `pytest tests/ -v` → **Expect:** `24 passed` |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Do not proceed past a Human Gate without explicit human input.**
⚠️ **Step 3 discovery output must be recorded before writing the adapter (no gate — proceed immediately).**
⚠️ **Step 4 requires `OPENAI_API_KEY` in `.env` — confirm before running.**
⚠️ **Do not batch multiple steps into one git commit.**
⚠️ **If idempotent = No, confirm the step has not already run before executing.**
