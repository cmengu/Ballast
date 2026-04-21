# otel.py — Implementation Plan

**Overall Progress:** `0%`

---

## Spec Summary — OTel Adapter Module

**What this module does.** `ballast/adapters/otel.py` implements Architectural Invariant 11: *"spec_violation is a typed OTel span. every drift event is observable, attributable, and cost-tagged."* It provides a single public function, `emit_drift_span()`, that converts a `NodeAssessment` and its associated metadata into an OpenTelemetry span and emits it to whatever `TracerProvider` is configured in the process — Langfuse OTLP on M5 in production, a NoOp provider in unit tests and during local `pytest` runs where no SDK is wired in.

**Input.** `emit_drift_span(assessment, spec, node_index, run_id, node_cost)` receives the full `NodeAssessment` returned by `score_drift()`, the `SpecModel` active at that node, the zero-based `node_index`, the run's UUID prefix `run_id`, and the float `node_cost` in USD. These are all values already in scope at the call site in `run_with_spec()` — no new data is threaded through.

**Output.** A single OTel span named `"drift_event"`, scoped to the tracer `"ballast.drift"`, emitted to the ambient `TracerProvider`. The span carries eight attributes: `ballast.drift.label`, `ballast.drift.score`, `ballast.drift.rationale`, `ballast.drift.tool_name`, `ballast.drift.spec_version`, `ballast.drift.node_index`, `ballast.drift.run_id`, `ballast.drift.cost_usd`. Span status is set to `StatusCode.ERROR` (with the rationale as description) for `VIOLATED` and `VIOLATED_IRREVERSIBLE` labels, and `StatusCode.OK` for `STALLED`. This lets the Langfuse dashboard filter and alert on hard violations without scanning span bodies.

**The `DriftSpanPacket` dataclass.** All eight span attributes are first packed into a `DriftSpanPacket` dataclass before the OTel call. This separates attribute preparation (pure data, testable) from span emission (I/O, mockable). It follows the same DTO pattern used by `EscalationPacket` and `EvaluatorPacket` elsewhere in Ballast.

**Fail-open contract.** The entire span emission — tracer construction, span creation, attribute setting, status assignment — is wrapped in a single `try/except Exception`. Any OTel error (SDK misconfigured, network unreachable, exporter hung) logs a `logger.warning` with `exc_info=True` and returns `None`. It never raises. Telemetry failure does not stop the agent run.

**API-only dependency.** The adapter imports only from `opentelemetry-api` (`opentelemetry.trace`, `opentelemetry.trace.StatusCode`), which is already installed (`1.39.1`). It does NOT import `opentelemetry-sdk`. This is intentional: Ballast is a library; it emits spans to whatever provider the operator configures. The SDK (and exporter) are the operator's concern, not the library's. When no SDK is configured, `trace.get_tracer()` returns a `ProxyTracer` that delegates to a `NoOpTracer` — spans are silently dropped with zero overhead.

**Circular import prevention.** `trajectory.py` imports `otel.py`, and `otel.py` needs to reference `NodeAssessment` from `trajectory.py` for type annotations. This is broken with a `TYPE_CHECKING` guard: `NodeAssessment` is imported only during static analysis, never at runtime. The function operates on duck-typed `assessment` at runtime (only `.label`, `.score`, `.rationale`, `.tool_name` are accessed). `SpecModel` is imported directly from `ballast.core.spec`, which is safe because `spec.py` does not import `trajectory.py`.

**Wiring.** Two stubs in `trajectory.py` are replaced. The `# TODO Step 13` comment at line 820 (inside the `elif score < threshold:` drift branch) is removed — the emit will happen at the centralized step 7 position instead. The `# ── 7. OTel emit — STUB` block at lines 859–862 is replaced with: `if assessment.label != "PROGRESSING": emit_drift_span(assessment, active_spec, node_index, run_id, node_cost)`. This single emit point covers all three non-PROGRESSING labels — `STALLED`, `VIOLATED`, and `VIOLATED_IRREVERSIBLE` — using the final resolved label (post-probe, post-evaluator mutation).

**Success criteria (eval-derivable).**
1. `DriftSpanPacket` fields match the eight attribute names exactly.
2. `emit_drift_span` sets `StatusCode.ERROR` for `VIOLATED` and `VIOLATED_IRREVERSIBLE`.
3. `emit_drift_span` sets `StatusCode.OK` for `STALLED`.
4. All eight `span.set_attribute` calls occur with correct values.
5. Any exception inside the try block is swallowed; the function returns `None`.
6. `import ballast.adapters.otel` succeeds without `ANTHROPIC_API_KEY`.
7. No regressions in existing 229 tests; total test count ≥ 229 + 10 = 239.

---

## Architecture Overview

**The problem this plan solves:**
`trajectory.py` has two `# TODO Step 13` stubs. Line 820 (inside `elif assessment.score < active_spec.drift_threshold:`) has a dead comment. Lines 859–862 (`# ── 7. OTel emit — STUB`) have commented-out code that was never activated. Drift events are logged to the Python `logger` but never emitted as structured OTel spans. Architectural Invariant 11 is violated: operators cannot observe, filter, or alert on drift events in Langfuse or any OTel-compatible backend.

**The pattern(s) applied:**
- **DTO (DriftSpanPacket)** — attribute preparation is separated from span emission. `DriftSpanPacket` is constructed before entering the `try` block, so its correctness is verifiable without mocking OTel. Span emission is the only I/O.
- **Null Object / Fail-Safe Default** — the `try/except Exception` wrapper ensures OTel failure is always a warning, never a stop condition. Mirrors `_call_probe_agent()` and `_call_evaluator()`.
- **Read-Only Facade** — `emit_drift_span` reads assessment fields and writes spans. It never modifies `assessment`, `spec`, or any mutable Ballast state.
- **TYPE_CHECKING guard** — prevents circular import at runtime while preserving static type safety.

**What stays unchanged:**
- `ballast/core/spec.py` — read-only consumer; no changes.
- `ballast/core/checkpoint.py` — no changes.
- `ballast/core/probe.py`, `evaluator.py`, `escalation.py`, `guardrails.py` — no changes.
- `ballast/adapters/__init__.py` — no changes (otel.py is a new sibling, not a modification).

**What this plan adds:**
- `ballast/adapters/otel.py` — `DriftSpanPacket` (DTO), `emit_drift_span()` (public entry point).
- `tests/test_otel.py` — 10 unit tests; no live OTel SDK needed.

**What this plan modifies:**
- `ballast/core/trajectory.py` — add import, remove stub comment at line 820, replace stub block at lines 859–862, update docstring.

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|----------|----------------------|--------------------------|
| Single emit at step 7 (`# ── 7. OTel emit`) | Emit at line 820 inside `elif` branch | Single centralized emit covers ALL three non-PROGRESSING labels (including `VIOLATED_IRREVERSIBLE` which is handled in the `if` block above and never enters the `elif` branch). Dual emit would cause duplicate spans for VIOLATED. |
| `TYPE_CHECKING` guard for `NodeAssessment` | Import `NodeAssessment` at runtime | Creates a circular import: `trajectory.py → otel.py → trajectory.py`. `TYPE_CHECKING` is the standard Python solution — annotation is a string at runtime, import is analysis-only. |
| API-only OTel (no SDK import) | Import `opentelemetry-sdk` and configure a TracerProvider | Ballast is a library. Library code emits spans; application code configures providers. Importing SDK here would force SDK as a runtime dependency and break setups that use a different OTel SDK distribution. |
| `DriftSpanPacket` DTO | Pass raw fields directly to `span.set_attribute` | DTO separates attribute preparation from I/O, making both independently testable. Consistent with `EscalationPacket`, `EvaluatorPacket`, `ProbePacket` patterns already in the codebase. |
| `StatusCode.ERROR` for VIOLATED\* | `StatusCode.UNSET` | Langfuse and most OTel UIs expose ERROR spans in alert dashboards. Using ERROR for violations enables zero-config alerting. STALLED is a recoverable state — OK is correct. |
| `"ballast.drift"` as tracer name | `"ballast"` | Tracer name maps to the instrumentation scope in Langfuse. A dedicated `"ballast.drift"` scope lets operators filter drift spans independently of any future `"ballast.cost"` or `"ballast.probe"` scopes. |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| `VIOLATED_IRREVERSIBLE` escalation does not emit a separate escalation span | Drift span fires for VIOLATED_IRREVERSIBLE via step 7; escalation result is not separately traced | Add `emit_escalation_span()` in Step 14 once Langfuse dashboards confirm drift spans are flowing |
| Span does not carry the spec delta (if a spec update occurred at this node) | Delta tracking requires threading spec transition state — out of scope for Step 13 | Add `ballast.drift.spec_transition` attribute in Step 14 using `progress.spec_transitions[-1]` |

---

## Decisions Log

| # | Flaw | Resolution applied |
|---|------|--------------------|
| 1 | `otel.py` importing `NodeAssessment` from `trajectory.py` creates a circular import at runtime: `trajectory.py → otel.py → trajectory.py`. | `NodeAssessment` is imported only under `TYPE_CHECKING`. `from __future__ import annotations` makes all annotations strings at runtime. Duck typing is used at runtime (`assessment.label`, `.score`, `.rationale`, `.tool_name`). |
| 2 | The stub at line 820 and the stub at lines 859–862 would both be wired as emit calls, producing duplicate spans for VIOLATED labels (which pass through both the `elif` branch AND the step 7 block). | Only the step 7 block is activated. The line 820 comment is removed (not replaced with a call). Single emit point covers all three labels. |
| 3 | Existing `test_trajectory.py` tests that exercise STALLED/VIOLATED paths (e.g., `test_score_drift_borderline_calls_evaluator`) will now hit `emit_drift_span` after Step 3. Without a configured OTel SDK, the OTel API uses a `NoOpTracer` — spans are silently dropped with no exception. No test modifications are needed. | Confirmed: `opentelemetry-api` without an SDK configured returns a `ProxyTracer` → `NoOpTracer`. `span.set_attribute()` and `span.set_status()` are no-ops. No exception raised. Existing tests are unaffected. |
| 4 | Step 3 verification grep `grep -c "emit_drift_span" trajectory.py` was specified as returning `2`. After Step 3 it returns `3` because line 637 has an existing comment `# Week 3 upgrade: replace with emit_drift_span(result)` that is NOT removed by any edit in this plan. | Corrected to `→ 3` in the verification block: import line + step 7 call + existing comment at line 637. |
| 5 | `from unittest.mock import MagicMock, patch, call` in `tests/test_otel.py` — `call` is never used as `call(...)` in any test body. `call_args_list` is a `MagicMock` attribute, not the `call` object. Triggers ruff F401. | `call` removed from the import. Import is now `from unittest.mock import MagicMock, patch`. |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---------|----------|--------|----------|----------|
| All fields | All resolved per spec above + decisions log | codebase read + design | — | ✅ |

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
Run the following and capture output:
(1) ls ballast/adapters/otel.py                             — must NOT exist
(2) ls tests/test_otel.py                                   — must NOT exist
(3) python -c "from opentelemetry import trace; from opentelemetry.trace import StatusCode; print('otel api ok')"
(4) grep -c "TODO Step 13" ballast/core/trajectory.py       — must return 2
(5) python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3   — record test count
(6) python -c "from ballast.core.spec import SpecModel; print('spec ok')"

Do not change anything. Show full output and wait.
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan:     229
adapters/otel.py exists:    no
tests/test_otel.py exists:  no
opentelemetry-api version:  1.39.1
TODO Step 13 count:         2
```

**Automated checks (all must pass before Step 1):**
- [ ] Existing test suite passes. Document test count: `229`
- [ ] `ballast/adapters/otel.py` does NOT exist yet.
- [ ] `tests/test_otel.py` does NOT exist yet.
- [ ] `grep -c "TODO Step 13" ballast/core/trajectory.py` returns `2`.
- [ ] `from opentelemetry import trace` exits 0.
- [ ] `from ballast.core.spec import SpecModel` exits 0.

---

## Environment Matrix

| Step | Dev | Staging | Prod |
|------|-----|---------|------|
| Step 1 (adapters/otel.py) | ✅ | ✅ | ✅ |
| Step 2 (tests/test_otel.py) | ✅ | ✅ | ✅ |
| Step 3 (trajectory.py wiring) | ✅ | ✅ | ✅ |

---

## Tasks

### Phase 1 — OTel Adapter Module

**Goal:** `ballast/adapters/otel.py` exists, imports cleanly without `ANTHROPIC_API_KEY`, and `emit_drift_span` correctly builds `DriftSpanPacket` and sets span attributes + status.

---

- [ ] 🟥 **Step 1: Create `ballast/adapters/otel.py`** — *Critical: new adapter module*

  **Step Architecture Thinking:**

  **Pattern applied:** DTO (DriftSpanPacket) + Null Object / Fail-Safe Default + Read-Only Facade.

  **Why this step exists here in the sequence:**
  The test file (Step 2) imports from `otel.py`. Trajectory wiring (Step 3) imports `emit_drift_span`. Both steps require this file to exist and import cleanly before they can run.

  **Why this file is the right location:**
  `ballast/adapters/` is the layer for third-party framework integrations (AG-UI, tinyfish). OTel is an observability framework integration, not a core Ballast concern. Placing it here keeps `ballast/core/` clean and signals that operators can swap or omit OTel by not configuring a TracerProvider.

  **Alternative approach considered and rejected:**
  `ballast/core/otel.py`. Rejected: core modules contain Ballast-specific logic (spec, drift, escalation). OTel emission is an I/O side-effect adapter — it belongs in `adapters/` alongside `agui.py` and `tinyfish.py`.

  **What breaks if this step deviates from the described pattern:**
  If `NodeAssessment` is imported at runtime (not under `TYPE_CHECKING`), `trajectory.py → otel.py → trajectory.py` produces a circular import and `from ballast.core.trajectory import ...` will raise `ImportError`.

  ---

  **Idempotent:** Yes — creating a new file; pre-flight confirms it does not exist.

  **Context:** `opentelemetry-api 1.39.1` is already installed. `opentelemetry-sdk` is NOT installed — the adapter must not import it. `trace.get_tracer()` returns a `ProxyTracer` (NoOp without SDK) so all tests run safely without a configured provider.

  **Pre-Read Gate:**
  - Run `ls ballast/adapters/otel.py` — must fail. If exists → STOP.
  - Run `python -c "from opentelemetry import trace; from opentelemetry.trace import StatusCode; print('ok')"` — must print `ok`. If ImportError → STOP.
  - Run `python -c "from ballast.core.spec import SpecModel; print('ok')"` — must print `ok`. If ImportError → STOP.

  **Self-Contained Rule:** Code below is complete and immediately runnable.

  **No-Placeholder Rule:** No `<VALUE>` tokens.

  ---

  **Edit A — Create `ballast/adapters/otel.py`**

  ```python
  """ballast/adapters/otel.py — OpenTelemetry span emission for Ballast drift events.

  Implements Architectural Invariant 11:
      "spec_violation is a typed OTel span. every drift event is observable,
       attributable, and cost-tagged."

  Public interface:
      emit_drift_span(assessment, spec, node_index, run_id, node_cost) -> None
          Emits a "drift_event" span to the ambient TracerProvider.
          Returns None on any OTel failure (fail-open).

  Design:
      - API-only: imports opentelemetry-api, never opentelemetry-sdk.
        Ballast is a library; the operator configures the TracerProvider.
        Without an SDK, trace.get_tracer() returns a NoOpTracer.
      - DriftSpanPacket DTO: attribute preparation is separated from span
        emission so both are independently testable.
      - TYPE_CHECKING guard on NodeAssessment prevents circular import:
        trajectory.py → otel.py → trajectory.py at runtime.

  No imports from trajectory.py, probe.py, evaluator.py, or escalation.py
  at runtime (only under TYPE_CHECKING for static analysis).
  """
  from __future__ import annotations

  import logging
  from dataclasses import dataclass
  from typing import TYPE_CHECKING

  from opentelemetry import trace
  from opentelemetry.trace import StatusCode

  from ballast.core.spec import SpecModel

  if TYPE_CHECKING:
      # Imported only during static analysis — prevents circular import at runtime.
      # trajectory.py imports otel.py; otel.py must not import trajectory.py at runtime.
      from ballast.core.trajectory import NodeAssessment

  logger = logging.getLogger(__name__)

  # OTel instrumentation scope name — maps to a dedicated scope in Langfuse,
  # allowing drift spans to be filtered independently of future cost/probe scopes.
  _TRACER_NAME = "ballast.drift"

  # Span operation name — stable identifier for Langfuse dashboards and alerts.
  _DRIFT_EVENT_SPAN = "drift_event"

  # Labels that map to StatusCode.ERROR — hard violations the operator must see.
  _ERROR_LABELS = frozenset({"VIOLATED", "VIOLATED_IRREVERSIBLE"})


  # ---------------------------------------------------------------------------
  # DriftSpanPacket — DTO for span attribute preparation
  # ---------------------------------------------------------------------------


  @dataclass
  class DriftSpanPacket:
      """All eight OTel span attributes for a single drift event.

      Constructed from NodeAssessment + call-site metadata before entering the
      OTel try block. Separates attribute preparation (pure data, testable) from
      span emission (I/O, mockable).

      Fields map 1:1 to span attribute names:
          label         → ballast.drift.label
          score         → ballast.drift.score
          rationale     → ballast.drift.rationale
          tool_name     → ballast.drift.tool_name
          spec_version  → ballast.drift.spec_version
          node_index    → ballast.drift.node_index
          run_id        → ballast.drift.run_id
          cost_usd      → ballast.drift.cost_usd
      """

      label: str
      score: float
      rationale: str
      tool_name: str
      spec_version: str
      node_index: int
      run_id: str
      cost_usd: float


  # ---------------------------------------------------------------------------
  # emit_drift_span — public entry point
  # ---------------------------------------------------------------------------


  def emit_drift_span(
      assessment: NodeAssessment,
      spec: SpecModel,
      node_index: int,
      run_id: str,
      node_cost: float,
  ) -> None:
      """Emit a typed OTel span for a non-PROGRESSING drift event.

      Packs NodeAssessment + call-site metadata into a DriftSpanPacket, then
      emits a "drift_event" span to the ambient TracerProvider. Sets
      StatusCode.ERROR for VIOLATED and VIOLATED_IRREVERSIBLE; StatusCode.OK
      for STALLED.

      Fail-open: any OTel error is logged as a warning and the function returns
      None. Telemetry failure never stops the agent run.

      Args:
          assessment:  Scored NodeAssessment from score_drift(). Duck-typed at
                       runtime — only .label, .score, .rationale, .tool_name
                       are accessed.
          spec:        SpecModel active at this node boundary.
          node_index:  Zero-based index of the current node in the run.
          run_id:      8-character UUID prefix for the current run.
          node_cost:   Cost in USD for this node, from NodeSummary.cost_usd.
      """
      packet = DriftSpanPacket(
          label=assessment.label,
          score=assessment.score,
          rationale=assessment.rationale,
          tool_name=assessment.tool_name,
          spec_version=spec.version_hash,
          node_index=node_index,
          run_id=run_id,
          cost_usd=node_cost,
      )
      try:
          with trace.get_tracer(_TRACER_NAME).start_as_current_span(_DRIFT_EVENT_SPAN) as span:
              span.set_attribute("ballast.drift.label", packet.label)
              span.set_attribute("ballast.drift.score", packet.score)
              span.set_attribute("ballast.drift.rationale", packet.rationale)
              span.set_attribute("ballast.drift.tool_name", packet.tool_name)
              span.set_attribute("ballast.drift.spec_version", packet.spec_version)
              span.set_attribute("ballast.drift.node_index", packet.node_index)
              span.set_attribute("ballast.drift.run_id", packet.run_id)
              span.set_attribute("ballast.drift.cost_usd", packet.cost_usd)
              if packet.label in _ERROR_LABELS:
                  span.set_status(StatusCode.ERROR, packet.rationale)
              else:
                  span.set_status(StatusCode.OK)
      except Exception:
          logger.warning(
              "emit_drift_span failed label=%s node=%d run_id=%s",
              packet.label, packet.node_index, packet.run_id,
              exc_info=True,
          )
  ```

  **What it does:** Defines `DriftSpanPacket` (attribute DTO) and `emit_drift_span()` (public entry point). Packs all eight span attributes from the assessment and call-site metadata, emits a `"drift_event"` span to the ambient `TracerProvider`, and swallows any OTel exception with a warning log.

  **Why this approach:** API-only OTel lets Ballast run as a library without mandating a specific SDK distribution. `DriftSpanPacket` separates attribute preparation from I/O for testability. The `TYPE_CHECKING` guard prevents the circular import that would occur if `NodeAssessment` were imported at runtime.

  **Assumptions:**
  - `opentelemetry-api >= 1.30` is installed (confirmed: `1.39.1`).
  - `SpecModel.version_hash` is a non-empty string. Confirmed from `ballast/core/spec.py`.
  - `NodeAssessment.label` is one of `PROGRESSING | STALLED | VIOLATED | VIOLATED_IRREVERSIBLE`. Confirmed from `trajectory.py:399`.

  **Risks:**
  - `assessment.label` is an unknown future label → mitigation: `_ERROR_LABELS` frozenset check; unknown labels default to `StatusCode.OK` (conservative fallback).
  - OTel API breaking change in future version → mitigation: `opentelemetry-api>=1.30` lower-bound to be added in Step 1 (Edit B below).

  ---

  **Edit B — `pyproject.toml`: declare `opentelemetry-api` in core dependencies**

  Old (exact):
  ```toml
  dependencies = [
      "ag-ui-protocol",
      "ag-ui-langgraph",
      "langgraph",
      "langchain-openai",
      "langchain-anthropic",
      "anthropic>=0.20",
      "filelock",
      "pydantic>=2.0",
      "pydantic-ai>=0.0.13,<1.0",
      "python-dotenv",
      "fastapi>=0.100",
      "uvicorn>=0.27",
  ]
  ```

  New:
  ```toml
  dependencies = [
      "ag-ui-protocol",
      "ag-ui-langgraph",
      "langgraph",
      "langchain-openai",
      "langchain-anthropic",
      "anthropic>=0.20",
      "filelock",
      "opentelemetry-api>=1.30",
      "pydantic>=2.0",
      "pydantic-ai>=0.0.13,<1.0",
      "python-dotenv",
      "fastapi>=0.100",
      "uvicorn>=0.27",
  ]
  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/adapters/otel.py pyproject.toml
  git commit -m "step 13: add adapters/otel.py — emit_drift_span typed OTel span for drift events"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate
  python -c "
  from ballast.adapters.otel import DriftSpanPacket, emit_drift_span
  from ballast.core.spec import SpecModel

  # Verify DriftSpanPacket fields
  p = DriftSpanPacket(
      label='VIOLATED',
      score=0.1,
      rationale='test',
      tool_name='write_file',
      spec_version='abc12345',
      node_index=3,
      run_id='run-001',
      cost_usd=0.00123,
  )
  assert p.label == 'VIOLATED'
  assert p.score == 0.1
  assert p.node_index == 3

  # Verify emit_drift_span does not raise (NoOp tracer)
  from unittest.mock import MagicMock
  assessment = MagicMock()
  assessment.label = 'STALLED'
  assessment.score = 0.5
  assessment.rationale = 'borderline'
  assessment.tool_name = 'read_file'
  spec = MagicMock()
  spec.version_hash = 'abc12345'
  emit_drift_span(assessment, spec, node_index=0, run_id='run-x', node_cost=0.0)
  print('otel import OK — all assertions passed')
  "
  ```

  **Expected:**
  - `otel import OK — all assertions passed` with exit code 0.
  - No `ImportError`, no `AuthenticationError`, no `RuntimeError`.

  **Pass:** Printed confirmation with exit code 0.

  **Fail:**
  - `ImportError: cannot import name 'NodeAssessment'` → circular import at runtime; confirm `TYPE_CHECKING` guard is in place.
  - `ModuleNotFoundError: No module named 'opentelemetry'` → `opentelemetry-api` not installed → `pip install opentelemetry-api`.
  - `AssertionError` on `DriftSpanPacket` fields → check field names in dataclass definition.

---

### Phase 2 — Tests

**Goal:** `tests/test_otel.py` passes with 10 tests; total suite ≥ 239.

---

- [ ] 🟥 **Step 2: Create `tests/test_otel.py`** — *Non-critical: confirms adapter logic*

  **Step Architecture Thinking:**

  **Pattern applied:** Unit testing with mock OTel tracer — tests target `DriftSpanPacket` construction and `emit_drift_span` behavior without a live TracerProvider.

  **Why this step exists here in the sequence:**
  Step 1 must exist so imports resolve. Step 3 wires `emit_drift_span` into `trajectory.py` — having tests first catches bugs before they can be triggered in the orchestration loop.

  **Why this file is the right location:**
  All Ballast tests live in `tests/`. Convention: `test_<module_path_last_segment>.py`.

  **Alternative approach considered and rejected:**
  Using the real OTel NoOp tracer (no mocking). Rejected: NoOp span methods are genuine no-ops — `span.set_attribute` does nothing and we cannot assert it was called with the correct values. Mock is required to verify attribute-setting behavior.

  **What breaks if this step deviates:**
  If tests call `emit_drift_span` without mocking `trace.get_tracer`, the NoOp tracer runs — tests pass vacuously without verifying any attribute. The tests must mock the tracer to be meaningful.

  ---

  **Idempotent:** Yes — creating a new test file.

  **Pre-Read Gate:**
  - Run `ls tests/test_otel.py` — must fail. If exists → STOP.
  - Run `grep -c "def emit_drift_span" ballast/adapters/otel.py` — must return `1`. If 0 → Step 1 not complete → STOP.
  - Run `grep -c "class DriftSpanPacket" ballast/adapters/otel.py` — must return `1`. If 0 → Step 1 not complete → STOP.

  **Self-Contained Rule:** All code below is complete and runnable.

  ---

  ```python
  """tests/test_otel.py — Unit tests for ballast/adapters/otel.py.

  10 tests total:
      TestDriftSpanPacket  (3) — dataclass field correctness
      TestEmitDriftSpan    (7) — attribute setting, status codes, fail-open

  All tests are synchronous — no pytest.mark.asyncio needed.
  OTel tracer is mocked via patch("opentelemetry.trace.get_tracer") so no
  live TracerProvider is required and assertions on span.set_attribute are
  possible.
  """
  from unittest.mock import MagicMock, patch

  from ballast.adapters.otel import DriftSpanPacket, emit_drift_span, _ERROR_LABELS


  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------


  def _make_assessment(
      label: str = "VIOLATED",
      score: float = 0.1,
      rationale: str = "breach",
      tool_name: str = "write_file",
  ) -> MagicMock:
      a = MagicMock()
      a.label = label
      a.score = score
      a.rationale = rationale
      a.tool_name = tool_name
      return a


  def _make_spec(version_hash: str = "abc12345") -> MagicMock:
      s = MagicMock()
      s.version_hash = version_hash
      return s


  def _mock_tracer_ctx():
      """Return (mock_tracer, mock_span) wired for start_as_current_span context manager."""
      mock_span = MagicMock()
      mock_tracer = MagicMock()
      mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
      mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
      return mock_tracer, mock_span


  # ---------------------------------------------------------------------------
  # TestDriftSpanPacket
  # ---------------------------------------------------------------------------


  class TestDriftSpanPacket:
      def test_fields_stored_correctly(self):
          p = DriftSpanPacket(
              label="VIOLATED",
              score=0.1,
              rationale="breach",
              tool_name="write_file",
              spec_version="abc12345",
              node_index=3,
              run_id="run-001",
              cost_usd=0.00123,
          )
          assert p.label == "VIOLATED"
          assert p.score == 0.1
          assert p.rationale == "breach"
          assert p.tool_name == "write_file"
          assert p.spec_version == "abc12345"
          assert p.node_index == 3
          assert p.run_id == "run-001"
          assert p.cost_usd == 0.00123

      def test_error_labels_frozenset_contains_violated(self):
          assert "VIOLATED" in _ERROR_LABELS
          assert "VIOLATED_IRREVERSIBLE" in _ERROR_LABELS

      def test_error_labels_frozenset_excludes_stalled(self):
          assert "STALLED" not in _ERROR_LABELS
          assert "PROGRESSING" not in _ERROR_LABELS


  # ---------------------------------------------------------------------------
  # TestEmitDriftSpan
  # ---------------------------------------------------------------------------


  class TestEmitDriftSpan:
      def test_all_eight_attributes_set(self):
          """emit_drift_span must call span.set_attribute for all 8 keys."""
          mock_tracer, mock_span = _mock_tracer_ctx()
          with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
              emit_drift_span(
                  _make_assessment(label="STALLED", score=0.5, rationale="slow", tool_name="read_file"),
                  _make_spec("abc12345"),
                  node_index=2,
                  run_id="run-xyz",
                  node_cost=0.00050,
              )
          set_calls = {c.args[0] for c in mock_span.set_attribute.call_args_list}
          assert "ballast.drift.label" in set_calls
          assert "ballast.drift.score" in set_calls
          assert "ballast.drift.rationale" in set_calls
          assert "ballast.drift.tool_name" in set_calls
          assert "ballast.drift.spec_version" in set_calls
          assert "ballast.drift.node_index" in set_calls
          assert "ballast.drift.run_id" in set_calls
          assert "ballast.drift.cost_usd" in set_calls

      def test_violated_sets_error_status(self):
          from opentelemetry.trace import StatusCode
          mock_tracer, mock_span = _mock_tracer_ctx()
          with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
              emit_drift_span(
                  _make_assessment(label="VIOLATED", rationale="hard breach"),
                  _make_spec(),
                  node_index=0, run_id="r", node_cost=0.0,
              )
          mock_span.set_status.assert_called_once_with(StatusCode.ERROR, "hard breach")

      def test_violated_irreversible_sets_error_status(self):
          from opentelemetry.trace import StatusCode
          mock_tracer, mock_span = _mock_tracer_ctx()
          with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
              emit_drift_span(
                  _make_assessment(label="VIOLATED_IRREVERSIBLE", rationale="irreversible"),
                  _make_spec(),
                  node_index=1, run_id="r", node_cost=0.0,
              )
          mock_span.set_status.assert_called_once_with(StatusCode.ERROR, "irreversible")

      def test_stalled_sets_ok_status(self):
          from opentelemetry.trace import StatusCode
          mock_tracer, mock_span = _mock_tracer_ctx()
          with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
              emit_drift_span(
                  _make_assessment(label="STALLED"),
                  _make_spec(),
                  node_index=0, run_id="r", node_cost=0.0,
              )
          mock_span.set_status.assert_called_once_with(StatusCode.OK)

      def test_attribute_values_match_assessment(self):
          """Spot-check that label and score are passed through correctly."""
          mock_tracer, mock_span = _mock_tracer_ctx()
          with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
              emit_drift_span(
                  _make_assessment(label="STALLED", score=0.42, tool_name="bash"),
                  _make_spec("hash9999"),
                  node_index=7, run_id="run-abc", node_cost=0.00321,
              )
          attrs = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
          assert attrs["ballast.drift.label"] == "STALLED"
          assert attrs["ballast.drift.score"] == 0.42
          assert attrs["ballast.drift.tool_name"] == "bash"
          assert attrs["ballast.drift.spec_version"] == "hash9999"
          assert attrs["ballast.drift.node_index"] == 7
          assert attrs["ballast.drift.run_id"] == "run-abc"
          assert attrs["ballast.drift.cost_usd"] == 0.00321

      def test_otel_exception_is_swallowed(self):
          """emit_drift_span must return None when the tracer raises."""
          mock_tracer = MagicMock()
          mock_tracer.start_as_current_span.side_effect = RuntimeError("otel down")
          with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
              result = emit_drift_span(
                  _make_assessment(),
                  _make_spec(),
                  node_index=0, run_id="r", node_cost=0.0,
              )
          assert result is None  # never raises

      def test_returns_none_on_success(self):
          mock_tracer, _ = _mock_tracer_ctx()
          with patch("opentelemetry.trace.get_tracer", return_value=mock_tracer):
              result = emit_drift_span(
                  _make_assessment(label="STALLED"),
                  _make_spec(),
                  node_index=0, run_id="r", node_cost=0.0,
              )
          assert result is None
  ```

  **Git Checkpoint:**
  ```bash
  git add tests/test_otel.py
  git commit -m "step 13: add test_otel.py — 10 unit tests for emit_drift_span and DriftSpanPacket"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate
  python -m pytest tests/test_otel.py -v --tb=short 2>&1 | tail -20
  python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  **Expected:**
  - `10 passed` in `test_otel.py` output.
  - Full suite: `≥ 239 passed`.
  - No `ImportError`, no `RuntimeError`.

  **Pass:** `10 passed` with exit code 0 and total ≥ 239.

  **Fail:**
  - `ImportError: cannot import name '_ERROR_LABELS'` → exported from `otel.py` but not present → check `_ERROR_LABELS` definition in Step 1.
  - `AssertionError` on `set_attribute` calls → mock wiring issue → check `_mock_tracer_ctx()` context manager setup.
  - `AssertionError` on `set_status` → check `_ERROR_LABELS` membership test in `emit_drift_span`.

---

### Phase 3 — Wire into `trajectory.py`

**Goal:** The two `# TODO Step 13` stubs are replaced; `emit_drift_span` is called at step 7 for all non-PROGRESSING labels; `grep -c "TODO Step 13" trajectory.py` returns `0`.

---

- [ ] 🟥 **Step 3: Wire `emit_drift_span` into `trajectory.py`** — *Critical: modifies the orchestration loop*

  **Step Architecture Thinking:**

  **Pattern applied:** Single Responsibility — the emit is placed at the single centralized step 7 position, not duplicated across label branches.

  **Why this step exists here in the sequence:**
  `otel.py` must exist (Step 1) before the import in `trajectory.py` can resolve. Tests must pass (Step 2) before wiring to confirm the function is correct.

  **Why step 7 and not line 820:**
  The `elif assessment.score < active_spec.drift_threshold:` branch (line 815) covers only `STALLED` and `VIOLATED`. `VIOLATED_IRREVERSIBLE` enters the `if` block above (line 790) and never reaches the `elif`. Emitting at line 820 would miss `VIOLATED_IRREVERSIBLE`. The centralized step 7 position is AFTER all branches — the label is fully resolved (including post-probe and post-evaluator mutations) and covers all three non-PROGRESSING labels in one place.

  **Alternative approach considered and rejected:**
  Emit at line 820 (inside `elif`) AND inside the `VIOLATED_IRREVERSIBLE` block (inside `if`). Rejected: `VIOLATED` passes through both the `elif` block (score < threshold) and step 7 — dual emit produces duplicate spans. Single emit at step 7 is correct.

  **What breaks if this step deviates:**
  If `emit_drift_span` is called BEFORE `progress.total_drift_events += 1` at line 825, the span fires before the checkpoint counter is incremented — minor ordering issue, not a correctness bug. The plan places the emit at step 7 (after checkpoint counter updates), which is correct.

  ---

  **Idempotent:** Yes — the stub comment does not exist after this edit; re-running would find 0 matches for the anchor and STOP.

  **Context:** `trajectory.py` already imports from `ballast.core.*` and `ballast.adapters` is not yet imported. This edit adds the first `ballast.adapters.*` import to `trajectory.py`.

  **Pre-Read Gate:**
  - Run `grep -c "TODO Step 13" ballast/core/trajectory.py` — must return `2`. If 0 → already done → STOP. If 1 → partially done → STOP and report.
  - Run `grep -n "# TODO Step 13" ballast/core/trajectory.py` — confirm exactly lines 820 and 860. Record both line numbers. If different → STOP.
  - Run `grep -c "from ballast.adapters.otel" ballast/core/trajectory.py` — must return `0`. If 1 → import already exists → skip Edit A, proceed to Edit B.
  - Run `grep -n "OTel emit" ballast/core/trajectory.py` — record the exact line of `# ── 7. OTel emit — STUB` for anchor verification.

  **Anchor Uniqueness Check:**
  - Anchor for Edit B: `# TODO Step 13: emit_drift_span(node, active_spec, score, label)` (the comment at line 820)
    → `grep -c "TODO Step 13: emit_drift_span" ballast/core/trajectory.py` must return `2`.
  - Anchor for Edit C: `# ── 7. OTel emit — STUB`
    → `grep -c "# ── 7. OTel emit" ballast/core/trajectory.py` must return `1`.

  **Self-Contained Rule:** All code below is complete and runnable.

  ---

  **Edit A — Add import to `trajectory.py`**

  Add after the existing `from ballast.core.sync import SpecPoller` import line (alphabetically, `adapters` before `core`). Exact existing block (lines 34–41):

  ```python
  from ballast.core.checkpoint import BallastProgress, NodeSummary
  from ballast.core.cost import RunCostGuard
  from ballast.core.escalation import EscalationFailed, escalate
  from ballast.core.guardrails import HardInterrupt, build_correction, can_resume
  from ballast.core.evaluator import evaluate_node
  from ballast.core.probe import verify_node_claim
  from ballast.core.spec import SpecModel, is_locked
  from ballast.core.sync import SpecPoller
  ```

  New (add `ballast.adapters.otel` import at top of block — `adapters` sorts before `core`):

  ```python
  from ballast.adapters.otel import emit_drift_span
  from ballast.core.checkpoint import BallastProgress, NodeSummary
  from ballast.core.cost import RunCostGuard
  from ballast.core.escalation import EscalationFailed, escalate
  from ballast.core.guardrails import HardInterrupt, build_correction, can_resume
  from ballast.core.evaluator import evaluate_node
  from ballast.core.probe import verify_node_claim
  from ballast.core.spec import SpecModel, is_locked
  from ballast.core.sync import SpecPoller
  ```

  **Edit B — Remove stub comment at line 820**

  Old (exact — inside `elif assessment.score < active_spec.drift_threshold:` block):
  ```python
                  # TODO Step 13: emit_drift_span(node, active_spec, score, label)
                  logger.warning(
  ```

  New:
  ```python
                  logger.warning(
  ```

  **Edit C — Replace `# ── 7. OTel emit — STUB` block**

  Old (exact, 4 lines):
  ```python
              # ── 7. OTel emit — STUB ─────────────────────────────────────
              # TODO Step 13: emit_drift_span(node, active_spec, score, label)
              # if label in ("VIOLATED", "VIOLATED_IRREVERSIBLE", "STALLED"):
              #     emit_drift_span(node, active_spec, score, label)
  ```

  New:
  ```python
              # ── 7. OTel emit ─────────────────────────────────────────────
              if assessment.label != "PROGRESSING":
                  emit_drift_span(assessment, active_spec, node_index, run_id, node_cost)
  ```

  **Edit D — Update `run_with_spec` docstring**

  Old (exact):
  ```python
          7. OTel emit (stubbed until Step 13)
  ```

  New:
  ```python
          7. OTel emit (emit_drift_span when label != PROGRESSING)
  ```

  **Git Checkpoint:**
  ```bash
  git add ballast/core/trajectory.py
  git commit -m "step 13: wire emit_drift_span into trajectory.py — replace two TODO Step 13 stubs"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate
  # Confirm stubs are gone
  grep -c "TODO Step 13" ballast/core/trajectory.py
  # Confirm emit call is present
  grep -c "emit_drift_span" ballast/core/trajectory.py
  # Confirm import is present
  grep -c "from ballast.adapters.otel import emit_drift_span" ballast/core/trajectory.py
  # Confirm trajectory still imports cleanly
  python -c "from ballast.core.trajectory import run_with_spec, score_drift; print('trajectory import OK')"
  # Full test suite
  python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  **Expected:**
  - `grep -c "TODO Step 13"` → `0`
  - `grep -n "emit_drift_span" ballast/core/trajectory.py` → must show exactly 3 lines: (a) the new import near line 34, (b) the existing comment at line 637, (c) the new call in the step 7 block near line 860
  - `grep -c "from ballast.adapters.otel"` → `1`
  - `trajectory import OK`
  - Full suite: `≥ 239 passed`, 0 failures.

  **Pass:** All greps correct, import clean, full suite ≥ 239 with exit code 0.

  **Fail:**
  - `ImportError: cannot import name 'emit_drift_span' from 'ballast.adapters.otel'` → Edit A applied but Step 1 not complete → confirm `otel.py` exists.
  - `ImportError: circular import` → `TYPE_CHECKING` guard missing in `otel.py` → confirm `if TYPE_CHECKING:` block in `otel.py`.
  - `grep -c "TODO Step 13"` returns `1` → one stub was not removed → re-read the edit anchors and apply the missed edit.
  - Existing test regressions → `emit_drift_span` call path raises in tests → OTel NoOp tracer should not raise; check `try/except` wrapper in `otel.py`.

---

## Regression Guard

**Systems at risk from this plan:**
- `trajectory.py:run_with_spec()` — the orchestration loop now calls `emit_drift_span` on every non-PROGRESSING node. If `emit_drift_span` raises (despite the `try/except`), the run crashes. Mitigation: the fail-open wrapper is tested explicitly in `test_otel_exception_is_swallowed`.
- `trajectory.py` import — adding `from ballast.adapters.otel import emit_drift_span` adds a new import path. If `otel.py` has a circular import at runtime, `trajectory.py` fails to import. Mitigation: `TYPE_CHECKING` guard is verified in Step 1 import check.

**Regression verification:**

| System | Pre-change behavior | Post-change verification |
|--------|---------------------|--------------------------|
| Existing 229 tests | All pass | `pytest tests/ -m 'not integration' -q` → ≥ 239 passing |
| `trajectory.py` import | Clean (no `otel.py` import) | `python -c "from ballast.core.trajectory import run_with_spec"` → exit 0 |
| `grep "TODO Step 13"` | Returns 2 | After Step 3: returns 0 |

**Test count regression check:**
- Tests before plan: `229`
- Tests after Step 2: `≥ 239`
- Tests after Step 3: `≥ 239` (no test file changes in Step 3)

---

## Post-Plan Checklist

- [ ] `ballast/adapters/otel.py` exists and imports cleanly without `ANTHROPIC_API_KEY`.
- [ ] `DriftSpanPacket` is a dataclass with all 8 fields.
- [ ] `emit_drift_span` wraps all OTel calls in `try/except Exception`.
- [ ] `_ERROR_LABELS = frozenset({"VIOLATED", "VIOLATED_IRREVERSIBLE"})` is defined.
- [ ] `opentelemetry-api>=1.30` declared in `pyproject.toml` `[project.dependencies]`.
- [ ] `tests/test_otel.py` has 10 tests, all passing.
- [ ] `grep -c "TODO Step 13" ballast/core/trajectory.py` returns `0`.
- [ ] `grep -c "emit_drift_span" ballast/core/trajectory.py` returns `2` (import + call).
- [ ] `pytest tests/ -m 'not integration' -q` passes with ≥ 239 tests; 0 failures.
- [ ] Three git commits made (one per step).

---

## State Manifest (fill after all steps complete)

```
Files created:
  ballast/adapters/otel.py    — DriftSpanPacket, emit_drift_span, _ERROR_LABELS, _TRACER_NAME
  tests/test_otel.py          — 10 unit tests

Files modified:
  pyproject.toml              — opentelemetry-api>=1.30 added to [project.dependencies]
  ballast/core/trajectory.py  — import added, 2 stubs replaced, docstring updated

Test count after plan: ____
Regressions: none expected
Next plan: Step 14 — OTel dashboard extension (emit_escalation_span + Langfuse filter)
```

---

## Success Criteria

| Criterion | Target | Verification |
|-----------|--------|--------------|
| Import clean | No `ImportError`, `AuthenticationError`, or circular import | Step 1 + Step 3 import checks |
| `DriftSpanPacket` fields | All 8 attributes present and correct | `TestDriftSpanPacket` (3 tests) |
| VIOLATED/VIOLATED_IRREVERSIBLE status | `StatusCode.ERROR` with rationale | `test_violated_sets_error_status`, `test_violated_irreversible_sets_error_status` |
| STALLED status | `StatusCode.OK` | `test_stalled_sets_ok_status` |
| All 8 attributes set | `span.set_attribute` called for all 8 keys | `test_all_eight_attributes_set` |
| Fail-open | OTel exception → `None` return, no raise | `test_otel_exception_is_swallowed` |
| Stubs removed | `grep -c "TODO Step 13" trajectory.py` → `0` | Step 3 verification |
| Single emit point | `grep -c "emit_drift_span" trajectory.py` → `2` | Step 3 verification |
| No regressions | ≥ 229 existing tests pass | Full suite run after Step 3 |
| Total test count | ≥ 239 | `pytest tests/ -m 'not integration' -q` |
