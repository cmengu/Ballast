# dashboard.py — Implementation Plan

**Overall Progress:** `0%`

---

## Spec Summary — Dashboard Module

**What this module does.** `ballast/core/dashboard.py` is a standalone Textual 8 TUI that polls `ballast-progress.json` on a configurable interval and renders the live state of a running Ballast job — node-by-node label, drift score, cost, tool name, and probe verification flag. It reads `BallastProgress` and `NodeSummary` from `ballast/core/checkpoint.py` (no new data schema). It is a read-only observer — it never writes to the checkpoint file or interacts with the running agent.

**Input.** `BallastDashboard(path, poll_interval)` accepts a checkpoint file path (default `"ballast-progress.json"`) and a poll interval in seconds (default `2.0`). Both have sensible defaults so `python -m ballast.core.dashboard` works with zero arguments when a run is active in the current directory.

**Output.** A terminal UI with:
- **Header bar** — run ID, spec intent (truncated), spec hash, run status (RUNNING / COMPLETE).
- **Stats bar** — total nodes, drift events, violations, total cost.
- **Node table** — scrollable `DataTable` with columns: `#`, `Tool`, `Label`, `Score`, `Cost ($)`, `Verified`, `Spec`, `Time`. Each row is one `NodeSummary`. Labels are colour-coded: PROGRESSING=green, STALLED=yellow, VIOLATED/VIOLATED_IRREVERSIBLE=red.
- **Footer** — keyboard hint `[q]` to quit, `[r]` to force refresh.

**Architecture.** `BallastDashboard` subclasses `textual.app.App`. A `set_interval` timer calls `_poll()` every `poll_interval` seconds. `_poll()` calls `BallastProgress.read(path)` — if the file changed (detected by comparing `updated_at` string), it calls `_render(progress)` which mutates the `DataTable` and `Static` widgets in-place. No threads, no asyncio wrappers — Textual's event loop drives everything.

**Entry point.** `if __name__ == "__main__":` block and a `__main__.py`-compatible `run()` function so the dashboard can be launched via `python -m ballast.core.dashboard [path] [interval]`.

**Constraints.** No writes to checkpoint file. No imports from `trajectory.py`, `probe.py`, or any module that constructs LLM clients (avoids `AuthenticationError` at TUI launch time). Only imports: `textual`, `ballast.core.checkpoint`, `pathlib`, `sys`, `datetime`.

**Success criteria (eval-derivable).**
1. `BallastDashboard` instantiates without `ANTHROPIC_API_KEY`.
2. `_poll()` returns `None` gracefully when checkpoint file does not exist.
3. `_poll()` updates `_last_updated_at` only when `updated_at` changes.
4. `_render()` clears and repopulates the `DataTable` on each call.
5. `_label_style()` returns `"green"` for PROGRESSING, `"yellow"` for STALLED, `"red"` for VIOLATED and VIOLATED_IRREVERSIBLE.
6. `run()` function exists and is callable without crashing at import time.
7. No regressions in existing 219 tests; test count ≥ 219 + 10 = 229.

---

## Architecture Overview

**The problem this plan solves:**
`ballast-progress.json` accumulates a full audit trail of every node during a run, but there is no real-time human-visible view. Operators watching a long agent run have no way to see drift events, costs, or label changes as they happen without `cat`-ing JSON.

**The pattern(s) applied:**
- **Observer (poll variant)** — `BallastDashboard` observes `ballast-progress.json` via periodic polling rather than a file-watch inotify event. Polling is chosen because the file is written by a separate process; Textual's `set_interval` provides a clean async-compatible poll loop without manual threading.
- **Single Responsibility** — `_poll()` owns file I/O and change detection. `_render()` owns widget mutation. They are never combined.
- **Read-Only Facade** — the dashboard never holds a writable reference to any Ballast internals. It only reads `BallastProgress.read()`.

**What stays unchanged:**
- `ballast/core/checkpoint.py` — read-only consumer; no changes.
- All other core modules — dashboard has zero imports from them.
- Existing test suite — 219 tests are unaffected.

**What this plan adds:**
- `ballast/core/dashboard.py` — the complete Textual TUI. Single file, single class `BallastDashboard(App)`.
- `tests/test_dashboard.py` — 10 unit tests; no Textual app pilot needed (tests target the helper methods and data-layer directly).

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|----------|----------------------|--------------------------|
| Polling (set_interval) | inotify / watchdog file watcher | Adds a dependency (`watchdog`), introduces threading complexity, and the 2s poll lag is imperceptible vs. agent node latency (seconds per node) |
| Change detection via `updated_at` string comparison | Hash the file content | `updated_at` is already the canonical freshness signal written by `BallastProgress.write()`; content hashing adds unnecessary I/O |
| Single file `dashboard.py` | Separate `widgets.py` | Only one widget class exists; a separate file adds indirection with no benefit at current scale |
| `DataTable.clear()` + re-add rows | Mutate existing rows | `DataTable` row mutation in Textual 8 requires row keys; clearing and repopulating is simpler and reliable |
| No `asyncio` wrapper around `_poll` | `asyncio.run_in_executor` for file read | `BallastProgress.read()` is synchronous and fast (JSON parse of a small file); blocking the event loop for <1ms is acceptable |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| No live spec-delta log panel | Only node table is in scope for Step 11 | Add `RichLog` widget for spec transitions in Step 14 (OTel + dashboard extension) |
| No WebSocket push from agent | Poll is sufficient given node latency (seconds) | Replace `set_interval` with SSE subscription when Step 13 OTel spans are live |
| `textual` not in `pyproject.toml` dependencies | Installed separately for dev; TUI is optional tooling | Add `textual>=8.0` to `[project.optional-dependencies]` `dev` group in this plan (Step 1) |

---

## Decisions Log

| # | Flaw | Resolution applied |
|---|------|--------------------|
| 1 | `textual` is not in `pyproject.toml` — installing it manually per-developer is fragile. | Step 1 adds `"textual>=8.0"` to `[project.optional-dependencies]` `dev` group. |
| 2 | Textual's `App.run()` blocks the terminal and cannot be called in unit tests without a Pilot. | Tests target only the non-UI helper methods (`_label_style`, `_poll` file logic, `_render` data layer) via direct instantiation — no `App.run()` is called in tests. |
| 3 | `DataTable` in Textual 8 requires `.add_column()` before `.add_row()`. Calling `add_row` before columns exist raises `ColumnDoesNotExist`. | `_render()` calls `table.clear(columns=True)` then re-adds columns before rows on every render cycle. |
| 4 | `DataTable.clear(columns=True)` resets the column header state every poll; this causes visual flicker on fast poll intervals. | Accepted: poll interval default is 2s (imperceptible), and `columns=True` is required for correctness since Textual 8 does not support column mutation after construction. |
| 5 | `from pathlib import Path` and `from textual.reactive import reactive` were imported in `dashboard.py` but never referenced — triggers ruff F401. | Both imports removed from Edit B. `Path` is unnecessary (no `Path` objects constructed); `reactive` is unnecessary (`_last_updated_at` is a plain class attribute, not a `reactive()` descriptor). |
| 6 | `from pathlib import Path` and `from unittest.mock import MagicMock` were imported in `test_dashboard.py` but never used — triggers ruff F401. | Both removed from the test file header. Only `from unittest.mock import patch` is needed. |
| 7 | `_make_node` helper function was defined in `test_dashboard.py` but never called by any test — dead code. | Removed entirely. `NodeSummary` import removed with it (nothing else uses it). |

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
(1) ls ballast/core/dashboard.py        — must NOT exist
(2) ls tests/test_dashboard.py          — must NOT exist
(3) python -c "import textual; print(textual.__version__)"  — must print 8.x
(4) grep -c 'textual' pyproject.toml    — record count (expected 0 or 1)
(5) python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3  — record test count
(6) python -c "from ballast.core.checkpoint import BallastProgress, NodeSummary; print('ok')"

Do not change anything. Show full output and wait.
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan: 219
dashboard.py exists:    no
test_dashboard.py:      no
textual version:        8.2.3
textual in pyproject:   0
```

**Automated checks (all must pass before Step 1):**
- [ ] Existing test suite passes. Document test count: `219`
- [ ] `ballast/core/dashboard.py` does NOT exist yet.
- [ ] `tests/test_dashboard.py` does NOT exist yet.
- [ ] `textual` is installed: `python -c "import textual"` exits 0.
- [ ] `BallastProgress` and `NodeSummary` import cleanly from `ballast.core.checkpoint`.

---

## Environment Matrix

| Step | Dev | Staging | Prod |
|------|-----|---------|------|
| Step 1 (pyproject.toml + dashboard.py) | ✅ | ✅ | ✅ |
| Step 2 (test_dashboard.py) | ✅ | ✅ | ✅ |

---

## Tasks

### Phase 1 — Core Module + Dependency Declaration

**Goal:** `ballast/core/dashboard.py` exists, imports cleanly, and `textual>=8.0` is declared in `pyproject.toml`.

---

- [ ] 🟥 **Step 1: Add `textual>=8.0` to `pyproject.toml` and create `ballast/core/dashboard.py`** — *Critical: new module and dependency declaration*

  **Step Architecture Thinking:**

  **Pattern applied:** Observer (poll variant) + Read-Only Facade + Single Responsibility.

  **Why this step exists here in the sequence:**
  The test file (Step 2) imports from `dashboard.py`. This file must exist and import cleanly before Step 2 can run. The `pyproject.toml` change is bundled here because `textual` is the only new dependency and it is already installed — declaring it is a one-line no-op to existing tests.

  **Why this file is the right location:**
  `ballast/core/` is the kernel layer. The dashboard is a first-class Ballast tool — not an adapter or third-party integration — so it belongs alongside `checkpoint.py`, not in `ballast/adapters/`.

  **Alternative approach considered and rejected:**
  `ballast/adapters/dashboard.py`. Rejected: the adapters directory is for third-party framework adapters (LangGraph, AG-UI). The dashboard is a core Ballast output artifact.

  **What breaks if this step deviates:**
  If `BallastDashboard._poll()` raises instead of returning `None` when the file is missing, launching the dashboard before a run starts will crash. Must guard with `if progress is None: return`.

  ---

  **Idempotent:** Yes — creating a new file is idempotent if it does not exist (pre-flight confirms).

  **Context:** `BallastProgress.read()` already returns `None` when the file is missing — the dashboard relies on this contract.

  **Pre-Read Gate:**
  - Run `ls ballast/core/dashboard.py` — must fail with "No such file". If exists → STOP.
  - Run `grep -c 'textual' pyproject.toml` — record count. If already `≥ 1` → skip the pyproject edit, proceed to dashboard.py creation only.
  - Run `python -c "from ballast.core.checkpoint import BallastProgress, NodeSummary; print('ok')"` — must print `ok`. If ImportError → STOP.

  **Self-Contained Rule:** Code blocks below are complete and immediately runnable.

  **No-Placeholder Rule:** No `<VALUE>` tokens.

  ---

  **Edit A — `pyproject.toml`: add `textual>=8.0` to dev extras**

  Old (exact, lines 25–28):
  ```toml
  [project.optional-dependencies]
  dev = [
      "pytest",
      "pytest-asyncio",
  ]
  ```

  New:
  ```toml
  [project.optional-dependencies]
  dev = [
      "pytest",
      "pytest-asyncio",
      "textual>=8.0",
  ]
  ```

  ---

  **Edit B — Create `ballast/core/dashboard.py`**

  ```python
  """ballast/core/dashboard.py — Textual TUI for real-time Ballast run visibility.

  Polls ballast-progress.json on a configurable interval and renders:
    - Header: run ID, spec intent, spec hash, run status
    - Stats bar: total nodes, drift events, violations, total cost
    - Node table: per-NodeSummary row with label, score, cost, tool, verified
    - Footer: keyboard hints

  Read-only observer — never writes to the checkpoint file or contacts any LLM.
  No imports from trajectory.py, probe.py, evaluator.py, or escalation.py.

  Entry points:
      python -m ballast.core.dashboard [path] [interval_seconds]
      from ballast.core.dashboard import BallastDashboard; BallastDashboard().run()
  """
  from __future__ import annotations

  import sys

  from textual.app import App, ComposeResult
  from textual.widgets import DataTable, Footer, Header, Static

  from ballast.core.checkpoint import BallastProgress

  # ---------------------------------------------------------------------------
  # Colour mapping — DriftLabel → Textual markup colour
  # ---------------------------------------------------------------------------

  _LABEL_COLOUR: dict[str, str] = {
      "PROGRESSING": "green",
      "STALLED": "yellow",
      "VIOLATED": "red",
      "VIOLATED_IRREVERSIBLE": "red",
  }


  def _label_style(label: str) -> str:
      """Return Textual markup colour string for a DriftLabel.

      Unknown labels fall back to 'white' so new label values never crash the UI.
      """
      return _LABEL_COLOUR.get(label, "white")


  def _fmt_score(score: float) -> str:
      """Format drift score as a fixed-width 4-decimal string."""
      return f"{score:.4f}"


  def _fmt_cost(cost: float) -> str:
      """Format cost_usd as a 5-decimal dollar string."""
      return f"{cost:.5f}"


  def _fmt_time(timestamp: str) -> str:
      """Extract HH:MM:SS from an ISO-8601 UTC timestamp string.

      Returns the raw string on any parse failure so the UI never crashes.
      """
      try:
          return timestamp[11:19]   # "2026-01-01T12:34:56Z" → "12:34:56"
      except (IndexError, TypeError):
          return timestamp or ""


  # ---------------------------------------------------------------------------
  # BallastDashboard — the Textual App
  # ---------------------------------------------------------------------------

  class BallastDashboard(App):
      """Real-time TUI for a running Ballast job.

      Args:
          path:          Path to ballast-progress.json (default: "ballast-progress.json").
          poll_interval: Seconds between file polls (default: 2.0).
      """

      CSS = """
      #stats {
          height: 3;
          padding: 0 1;
          background: $surface;
          color: $text;
      }
      DataTable {
          height: 1fr;
      }
      """

      BINDINGS = [
          ("q", "quit", "Quit"),
          ("r", "refresh", "Force refresh"),
      ]

      # Track the last-seen updated_at to avoid unnecessary renders.
      _last_updated_at: str = ""

      def __init__(
          self,
          path: str = "ballast-progress.json",
          poll_interval: float = 2.0,
          **kwargs,
      ) -> None:
          super().__init__(**kwargs)
          self._path = path
          self._poll_interval = poll_interval

      def compose(self) -> ComposeResult:
          """Build the widget tree: Header, stats bar, node table, Footer."""
          yield Header()
          yield Static("Loading…", id="stats")
          table = DataTable(id="nodes")
          table.cursor_type = "row"
          yield table
          yield Footer()

      def on_mount(self) -> None:
          """Start the poll timer immediately after the UI mounts."""
          self.set_interval(self._poll_interval, self._poll)
          # Trigger one immediate poll so the table is populated on first render.
          self.call_later(self._poll)

      # ------------------------------------------------------------------
      # Poll + render helpers
      # ------------------------------------------------------------------

      def _poll(self) -> None:
          """Read checkpoint file; render only when content has changed.

          Returns immediately (None) if the file does not exist — safe to call
          before a run starts.
          """
          progress = BallastProgress.read(self._path)
          if progress is None:
              return
          # Skip re-render if nothing changed since last poll.
          if progress.updated_at == self._last_updated_at:
              return
          self._last_updated_at = progress.updated_at
          self._render(progress)

      def _render(self, progress: BallastProgress) -> None:
          """Mutate widgets to reflect the current BallastProgress state.

          Clears and repopulates the DataTable on every call. Column headers
          are re-added after clear(columns=True) as required by Textual 8.
          """
          # ── Header sub-title ───────────────────────────────────────────
          status = "COMPLETE" if progress.is_complete else "RUNNING"
          intent_short = (progress.spec_intent or "")[:60]
          self.title = f"Ballast — {status}"
          self.sub_title = f"{intent_short}  [{progress.active_spec_hash[:8]}]"

          # ── Stats bar ──────────────────────────────────────────────────
          n_nodes = len(progress.completed_node_summaries)
          stats_text = (
              f"Nodes: {n_nodes}  │  "
              f"Drift events: {progress.total_drift_events}  │  "
              f"Violations: {progress.total_violations}  │  "
              f"Cost: ${progress.total_cost_usd:.5f}  │  "
              f"Run: {progress.run_id or '—'}"
          )
          self.query_one("#stats", Static).update(stats_text)

          # ── Node table ─────────────────────────────────────────────────
          table: DataTable = self.query_one("#nodes", DataTable)
          table.clear(columns=True)
          table.add_columns("#", "Tool", "Label", "Score", "Cost ($)", "Verified", "Spec", "Time")

          for node in progress.completed_node_summaries:
              colour = _label_style(node.label)
              table.add_row(
                  str(node.index),
                  node.tool_name or "—",
                  f"[{colour}]{node.label}[/{colour}]",
                  _fmt_score(node.drift_score),
                  _fmt_cost(node.cost_usd),
                  "✓" if node.verified else "✗",
                  node.spec_hash[:8] if node.spec_hash else "—",
                  _fmt_time(node.timestamp),
              )

      # ------------------------------------------------------------------
      # Actions
      # ------------------------------------------------------------------

      def action_refresh(self) -> None:
          """Force an immediate re-poll (bound to 'r')."""
          self._last_updated_at = ""   # reset so _poll always re-renders
          self._poll()

      def action_quit(self) -> None:
          """Quit the dashboard (bound to 'q')."""
          self.exit()


  # ---------------------------------------------------------------------------
  # Entry point
  # ---------------------------------------------------------------------------

  def run(path: str = "ballast-progress.json", poll_interval: float = 2.0) -> None:
      """Launch the dashboard. Blocks until the user quits."""
      BallastDashboard(path=path, poll_interval=poll_interval).run()


  if __name__ == "__main__":
      _path = sys.argv[1] if len(sys.argv) > 1 else "ballast-progress.json"
      _interval = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
      run(path=_path, poll_interval=_interval)
  ```

  **What it does:** Defines `BallastDashboard(App)` — polls `ballast-progress.json` every `poll_interval` seconds, compares `updated_at` to avoid unnecessary renders, and populates a `DataTable` with one row per `NodeSummary`. Helper functions `_label_style`, `_fmt_score`, `_fmt_cost`, `_fmt_time` are module-level so they can be tested without instantiating the App.

  **Why this approach:** Module-level helpers (not methods) are testable without Textual's Pilot harness. `DataTable.clear(columns=True)` + re-add is the Textual 8 idiomatic reset pattern. `_last_updated_at` string comparison is O(1) and uses the existing canonical freshness signal.

  **Assumptions:**
  - `BallastProgress.read()` returns `None` when the file does not exist. Confirmed from `checkpoint.py:73–78`.
  - `BallastProgress.updated_at` is an ISO-8601 string (non-empty for any active run). Confirmed from dataclass field.
  - Textual 8 `DataTable.clear(columns=True)` resets columns. Confirmed via Textual 8.2.3 API.
  - `NodeSummary` fields: `index`, `tool_name`, `label`, `drift_score`, `cost_usd`, `verified`, `spec_hash`, `timestamp`. Confirmed from `checkpoint.py:29–36`.

  **Risks:**
  - `DataTable.query_one("#nodes", DataTable)` raises if the widget ID is missing → mitigation: `id="nodes"` is set in `compose()` — same file.
  - `node.spec_hash` is empty string for edge-case nodes → mitigation: `if node.spec_hash else "—"` guard in `_render()`.
  - `textual>=8.0` API changes in future versions → mitigation: version pin in `pyproject.toml` lower-bounds to 8.0.

  **Git Checkpoint:**
  ```bash
  git add pyproject.toml ballast/core/dashboard.py
  git commit -m "step 11: add dashboard.py — Textual TUI for real-time Ballast run visibility"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate
  python -c "
  from ballast.core.dashboard import BallastDashboard, _label_style, _fmt_score, _fmt_cost, _fmt_time, run
  import inspect
  assert _label_style('PROGRESSING') == 'green'
  assert _label_style('VIOLATED') == 'red'
  assert _label_style('STALLED') == 'yellow'
  assert _label_style('VIOLATED_IRREVERSIBLE') == 'red'
  assert _label_style('UNKNOWN') == 'white'
  assert _fmt_score(0.9) == '0.9000'
  assert _fmt_cost(0.00123) == '0.00123'
  assert _fmt_time('2026-01-01T12:34:56Z') == '12:34:56'
  assert callable(run)
  d = BallastDashboard.__new__(BallastDashboard)
  d._path = 'nonexistent.json'
  d._last_updated_at = ''
  print('dashboard import OK — all assertions passed')
  "
  ```

  **Expected:**
  - `dashboard import OK — all assertions passed` with exit code 0.
  - No `AuthenticationError`, `ImportError`, or `ModuleNotFoundError`.

  **Pass:** Printed confirmation with exit code 0.

  **Fail:**
  - `ModuleNotFoundError: No module named 'textual'` → `textual` not installed → `pip install textual>=8.0`.
  - `ImportError` on `BallastProgress` → check `from ballast.core.checkpoint import BallastProgress` in dashboard.py.
  - `AssertionError` on `_label_style` → check `_LABEL_COLOUR` dict keys in dashboard.py.

---

### Phase 2 — Tests

**Goal:** `tests/test_dashboard.py` passes with 10 tests; no Textual Pilot needed; 219 → 229 total.

---

- [ ] 🟥 **Step 2: Create `tests/test_dashboard.py`** — *Non-critical: confirms helper functions and data layer*

  **Step Architecture Thinking:**

  **Pattern applied:** Unit testing of module-level helpers — no App instantiation, no Textual Pilot.

  **Why this step exists here in the sequence:**
  Step 1 must exist so imports resolve. Helper functions (`_label_style`, `_fmt_score`, `_fmt_cost`, `_fmt_time`) and the `_poll`/`_render` data layer are testable without launching a terminal.

  **Why this file is the right location:**
  All Ballast tests live in `tests/`. Convention: `test_<module>.py`.

  **Alternative approach considered and rejected:**
  Textual's `app.run_test()` (Pilot harness) for full widget testing. Rejected: requires a headless terminal emulator and async test runner configuration; module-level helpers cover all non-UI logic completely.

  **What breaks if this step deviates:**
  If tests import `BallastDashboard` and call `.run()`, the test process will block waiting for a terminal. Tests must only call module-level functions and construct `BallastDashboard` without calling `.run()`.

  ---

  **Idempotent:** Yes — creating a new test file.

  **Pre-Read Gate:**
  - Run `ls tests/test_dashboard.py` — must fail with "No such file". If exists → STOP.
  - Run `grep -c 'def _label_style' ballast/core/dashboard.py` — must return `1`. If 0 → function renamed in Step 1 → STOP.
  - Run `grep -c 'def _fmt_time' ballast/core/dashboard.py` — must return `1`. If 0 → function renamed → STOP.

  **Self-Contained Rule:** All code is complete and runnable as written.

  ---

  ```python
  """tests/test_dashboard.py — Unit tests for ballast/core/dashboard.py.

  10 tests total:
      TestLabelStyle   (5) — colour mapping for all known and unknown labels
      TestFormatters   (3) — _fmt_score, _fmt_cost, _fmt_time
      TestPollBehavior (2) — _poll returns None when file missing; skips render when unchanged

  No Textual Pilot used — tests target module-level helpers and _poll data logic only.
  No App.run() is called — tests never block on a terminal.
  All tests are synchronous — no pytest.mark.asyncio needed.
  """
  from unittest.mock import patch

  from ballast.core.dashboard import (
      _fmt_cost,
      _fmt_score,
      _fmt_time,
      _label_style,
      BallastDashboard,
  )
  from ballast.core.checkpoint import BallastProgress


  # ---------------------------------------------------------------------------
  # Helpers
  # ---------------------------------------------------------------------------

  def _make_progress(**overrides) -> BallastProgress:
      defaults = dict(
          spec_hash="abc00001",
          spec_intent="test intent",
          run_id="run-001",
          updated_at="2026-01-01T00:00:01Z",
          started_at="2026-01-01T00:00:00Z",
      )
      defaults.update(overrides)
      return BallastProgress(**defaults)


  def _make_dashboard() -> BallastDashboard:
      """Construct BallastDashboard without calling run() — safe for unit tests."""
      d = BallastDashboard.__new__(BallastDashboard)
      d._path = "nonexistent.json"
      d._poll_interval = 2.0
      d._last_updated_at = ""
      return d


  # ---------------------------------------------------------------------------
  # TestLabelStyle
  # ---------------------------------------------------------------------------

  class TestLabelStyle:
      def test_progressing_returns_green(self):
          assert _label_style("PROGRESSING") == "green"

      def test_stalled_returns_yellow(self):
          assert _label_style("STALLED") == "yellow"

      def test_violated_returns_red(self):
          assert _label_style("VIOLATED") == "red"

      def test_violated_irreversible_returns_red(self):
          assert _label_style("VIOLATED_IRREVERSIBLE") == "red"

      def test_unknown_label_returns_white(self):
          assert _label_style("SOME_FUTURE_LABEL") == "white"


  # ---------------------------------------------------------------------------
  # TestFormatters
  # ---------------------------------------------------------------------------

  class TestFormatters:
      def test_fmt_score_four_decimal_places(self):
          assert _fmt_score(0.9) == "0.9000"
          assert _fmt_score(0.1234) == "0.1234"

      def test_fmt_cost_five_decimal_places(self):
          assert _fmt_cost(0.00123) == "0.00123"
          assert _fmt_cost(0.0) == "0.00000"

      def test_fmt_time_extracts_hhmmss(self):
          assert _fmt_time("2026-01-01T12:34:56Z") == "12:34:56"
          assert _fmt_time("") == ""
          assert _fmt_time(None) == ""


  # ---------------------------------------------------------------------------
  # TestPollBehavior
  # ---------------------------------------------------------------------------

  class TestPollBehavior:
      def test_poll_returns_none_when_file_missing(self):
          """_poll must not raise when ballast-progress.json does not exist."""
          d = _make_dashboard()
          result = d._poll()  # file "nonexistent.json" does not exist
          assert result is None

      def test_poll_skips_render_when_updated_at_unchanged(self):
          """_poll must not call _render when updated_at matches _last_updated_at."""
          d = _make_dashboard()
          progress = _make_progress(updated_at="2026-01-01T00:00:01Z")
          d._last_updated_at = "2026-01-01T00:00:01Z"  # same as progress
          with patch("ballast.core.checkpoint.BallastProgress.read", return_value=progress), \
               patch.object(d, "_render") as mock_render:
              d._poll()
          mock_render.assert_not_called()
  ```

  **Git Checkpoint:**
  ```bash
  git add tests/test_dashboard.py
  git commit -m "step 11: add test_dashboard.py — 10 unit tests for dashboard helpers and poll logic"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && source venv/bin/activate
  python -m pytest tests/test_dashboard.py -v --tb=short 2>&1 | tail -20
  python -m pytest tests/ -m 'not integration' -q 2>&1 | tail -3
  ```

  **Expected:**
  - `10 passed` in `test_dashboard.py` output.
  - Full suite: `≥ 229 passed`.
  - No `AuthenticationError`, no `RuntimeError` about event loops.

  **Pass:** `10 passed` and total ≥ 229 with exit code 0.

  **Fail:**
  - `ModuleNotFoundError: No module named 'textual'` → Step 1 not complete → install textual.
  - `TypeError: __init__() missing required argument` on `BallastDashboard.__new__` → check `_make_dashboard()` manually sets `_path`, `_poll_interval`, `_last_updated_at`.
  - `AssertionError` on `test_fmt_time` with `None` input → `_fmt_time` in dashboard.py must guard `except (IndexError, TypeError): return timestamp or ""`.

---

## Regression Guard

**Systems at risk from this plan:**
- None — `dashboard.py` is a new file with zero imports from trajectory/probe/evaluator/escalation. It cannot affect any existing runtime path.
- `pyproject.toml` adds one line to `[project.optional-dependencies]` `dev` — does not affect `pip install ballast` (only `pip install ballast[dev]`).

**Regression verification:**

| System | Pre-change behavior | Post-change verification |
|--------|---------------------|--------------------------|
| Existing 219 tests | All pass | `pytest tests/ -m 'not integration' -q` → ≥ 219 passing |
| `checkpoint.py` | Unchanged | `grep -c 'def write' ballast/core/checkpoint.py` → 1 (untouched) |

**Test count regression check:**
- Tests before plan (from Pre-Flight baseline): `219`
- Tests after plan: run `pytest tests/ -m 'not integration' -q` — must be `≥ 229`

---

## Post-Plan Checklist

- [ ] `ballast/core/dashboard.py` exists and imports cleanly without `ANTHROPIC_API_KEY`.
- [ ] `tests/test_dashboard.py` has 10 tests, all passing.
- [ ] `textual>=8.0` declared in `pyproject.toml` `[project.optional-dependencies]` `dev`.
- [ ] `BallastDashboard` class exists and is a subclass of `textual.app.App`.
- [ ] `run()` function exists and is callable.
- [ ] `pytest tests/ -m 'not integration' -q` passes with ≥ 229 tests; 0 failures.
- [ ] Two git commits made (one per step).

---

## State Manifest (fill after all steps complete)

```
Files modified:
  pyproject.toml              — edited: textual>=8.0 added to dev extras
  ballast/core/dashboard.py   — created (new file)
  tests/test_dashboard.py     — created (new file)

Test count after plan: ____
Regressions: none expected
Next plan: Step 13 — OTel spans (emit_drift_span)
```

---

## Success Criteria

| Criterion | Target | Verification |
|-----------|--------|--------------|
| Import clean | No `AuthenticationError` or `ImportError` | Step 1 import check |
| `_label_style` mapping | All 4 known labels + unknown fallback | `test_progressing_returns_green` … `test_unknown_label_returns_white` |
| Formatters | `_fmt_score`, `_fmt_cost`, `_fmt_time` correct | `TestFormatters` (3 tests) |
| `_poll` no-file | Returns `None` without raising | `test_poll_returns_none_when_file_missing` |
| `_poll` skip render | Does not call `_render` when `updated_at` unchanged | `test_poll_skips_render_when_updated_at_unchanged` |
| `run()` callable | Function exists | Step 1 import check |
| No regressions | ≥ 219 existing tests pass | Full suite run after Step 2 |
| Total test count | ≥ 229 | `pytest tests/ -m 'not integration' -q` |
