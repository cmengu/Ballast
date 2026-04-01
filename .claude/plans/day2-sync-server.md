# Day 2 — `server.py` + `sync.py`: Live Spec Update Pipeline

**Overall Progress:** `0%` (0 / 4 steps complete)

---

## TLDR

Build the two-machine spec-update pipeline: a FastAPI server (`ballast/core/server.py`) that holds the current spec per job, and a `SpecPoller` (`ballast/core/sync.py`) that polls it at every node boundary and returns a new spec only when the version changes. After this plan: `SpecPoller.poll()` returns a new `SpecModel` when the server receives a POST update, and silently returns `None` when unchanged or unreachable. Both are verified in isolation via `TestClient` and mocked `httpx`. All 81 existing tests still pass.

---

## Architecture Overview

**The problem this plan solves:**
`spec.py` can now produce `SpecDelta` and inject it as a string — but nothing delivers an updated spec to the agent mid-run. `hook.py` (Day 3) needs `SpecPoller.poll()` to exist and work before it can be wired into `Agent.iter`. Without `server.py` there is nowhere to POST the updated spec; without `sync.py` there is no client to detect the change.

**Patterns applied:**

| Pattern | Where | What breaks if violated |
|---------|-------|------------------------|
| **Facade** | `SpecPoller` wraps all HTTP + version-comparison logic behind a single `poll()` method | If `hook.py` calls httpx directly, it must handle errors itself; every caller becomes responsible for the "silent on error" invariant |
| **Null Object / Silent Failure** | `poll()` never raises — network error returns `None` | If `poll()` raises, a transient M5 outage aborts the M2 agent run — violates the architectural invariant that M5 unreachable must not abort M2 execution |
| **Module-level singleton state** | `_current_spec` dict in `server.py` is module-level — simple, no DB | If state is instance-level, two import paths create two dicts and the test fixture clears the wrong one |
| **App/Runner split** | FastAPI `app` lives in `ballast/core/server.py` (importable package); `scripts/server.py` is a 3-line runner | If `app` lives only in `scripts/server.py`, tests can't import it without `sys.path` hacks — `scripts/` is not a Python package |

**What stays unchanged:** `spec.py`, `trajectory.py`, `memory.py`, `stream.py`, all adapters, all existing test files.

**What this plan adds:**

| File | Single responsibility |
|------|-----------------------|
| `ballast/core/server.py` | FastAPI app + 2 endpoints + in-memory `_current_spec` state |
| `scripts/server.py` | 3-line uvicorn runner — calls `ballast.core.server.app` |
| `ballast/core/sync.py` | `SpecPoller` — HTTP poll + version comparison + silent error handling |
| `tests/test_sync.py` | 9 isolation tests: 4 server endpoint tests + 5 SpecPoller mock tests |

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|----------|----------------------|--------------------------|
| FastAPI app in `ballast/core/server.py`, runner in `scripts/server.py` | App directly in `scripts/server.py` | `scripts/` is not a Python package — tests cannot `from scripts.server import app` without `sys.path` mutation. App/runner split is standard FastAPI practice. |
| `poll()` defers `SpecModel` import inside the function body | Top-level `from ballast.core.spec import SpecModel` | `sync.py` is in `ballast/core/` — same package as `spec.py`. No circular import exists; deferred import is unnecessary. Use top-level import for clarity. |
| `_current_spec` cleared by pytest `autouse` fixture per test | `scope="module"` fixture | Module-scope fixture leaks state between tests silently; `autouse` + `function` scope guarantees each test starts from a clean dict |
| `SpecPoller.poll()` updates `self._current` on success | Caller updates `self._current` | If caller forgets to update, the next `poll()` always sees a version difference and returns the same spec repeatedly — violates "returns None when unchanged" |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| `_current_spec` is in-memory, lost on server restart | Demo only — one session | Day 5: add a JSON file or SQLite backend if persistence needed |
| No auth on endpoints | Demo on localhost | Add `Authorization` header check before M2 deployment |
| `SpecPoller` is synchronous (`httpx.get`) | `hook.py` runs in async context | Day 3: switch to `httpx.AsyncClient` + `await` inside `run_with_live_spec` if needed; `poll()` API stays identical |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---------|----------|--------|----------|----------|
| `fastapi` version constraint | `>=0.100` — first version with `Annotated` dependency injection | Plan spec | Step 1 | ✅ |
| `TestClient` import path | `from fastapi.testclient import TestClient` — re-exported from starlette by FastAPI | FastAPI docs | Step 4 | ✅ |
| Circular import risk in `sync.py` | None — `spec.py` does not import from `sync.py`; same pattern as `trajectory.py` | `grep` confirmed: `trajectory.py` imports `from ballast.core.spec import SpecModel` at module level without issue | Step 3 | ✅ |
| Version field name on `SpecModel` | `version` (8-char sha256) — NOT `version_hash` | `spec.py` field definition confirmed | Step 3 | ✅ |
| `_current_spec` module path for fixture clear | `from ballast.core.server import _current_spec` — dict is mutable, `.clear()` modifies it in place | Architecture decision | Step 4 | ✅ |
| `httpx.ConnectError` constructor signature | `httpx.ConnectError("unreachable")` is valid — `request` kwarg defaults to `None` in httpx 0.28.1. `httpx` is already installed and importable in `test_sync.py`. | `inspect.signature(httpx.ConnectError.__init__)` confirmed | Step 4 | ✅ |

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
Run all of the following. Do not change anything. Show full output.

(1) /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ --tb=short 2>&1 | tail -3
    Record: exact count. Must be 81 passed.

(2) wc -l /Users/ngchenmeng/Ballast/pyproject.toml
    Record: line count before edit.

(3) grep -n "fastapi" /Users/ngchenmeng/Ballast/pyproject.toml
    Expected: 0 matches. If any → STOP — already partially applied.

(4) grep -rn "sync.py\|server.py" /Users/ngchenmeng/Ballast/ballast/ /Users/ngchenmeng/Ballast/scripts/ 2>/dev/null | grep -v __pycache__
    Expected: 0 matches for ballast/core/server.py and ballast/core/sync.py.

(5) /Users/ngchenmeng/Ballast/venv/bin/python -c "import fastapi" 2>&1
    Expected: ModuleNotFoundError. If fastapi already importable → record version, skip Step 1 pyproject edit but still run pip install.

(6) grep -n "python-dotenv" /Users/ngchenmeng/Ballast/pyproject.toml
    Record: exact line. This is the Step 1 edit anchor.
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan:           ____   (must be 81)
pyproject.toml line count:        ____
"fastapi" in pyproject.toml:      ____   (must be 0)
server.py/sync.py in ballast/:    ____   (must be 0)
fastapi importable:               ____   (expected: ModuleNotFoundError)
"python-dotenv" at line:          ____
```

---

## Tasks

### Phase 1 — Install dependency + create server + poller

**Goal:** `fastapi` installed, server endpoints reachable via TestClient, `SpecPoller.poll()` returns new spec when version changes.

---

- [ ] 🟥 **Step 1: Add `fastapi` to `pyproject.toml` and install** — *Critical: Steps 2–4 cannot import FastAPI without it*

  **Step Architecture Thinking:**

  **Pattern applied:** Explicit dependency declaration — all runtime dependencies live in `pyproject.toml` so `pip install -e .` always produces a reproducible environment.

  **Why this step exists here in the sequence:** `scripts/server.py` and `tests/test_sync.py` both import `fastapi`. Neither can be created or tested until the package is installed.

  **Why this file is the right location:** `pyproject.toml` is the single source of truth for project dependencies. Adding it only to the venv without updating the toml would make the dependency invisible to anyone who clones the repo.

  **Alternative approach considered and rejected:** Install only via `pip install fastapi` without touching `pyproject.toml`. Rejected — next `pip install -e .` or fresh venv would not include fastapi; the dependency would be invisible to CI and collaborators.

  **What breaks if this step deviates:** If `fastapi` is not in `pyproject.toml`, Step 2's import works locally but breaks for anyone reinstalling from the project file.

  ---

  **Idempotent:** No — adding the line twice produces a duplicate dependency entry. Pre-Read Gate must confirm 0 existing `fastapi` occurrences.

  **Pre-Read Gate:**
  - Run `grep -n "fastapi" /Users/ngchenmeng/Ballast/pyproject.toml`. Must return 0 matches. If any → STOP.
  - Confirm `"python-dotenv"` line number from pre-flight (6). This is the `old_string` anchor.

  **Edit:** In `/Users/ngchenmeng/Ballast/pyproject.toml`, replace:

  ```toml
      "python-dotenv",
  ]
  ```

  with:

  ```toml
      "python-dotenv",
      "fastapi>=0.100",
  ]
  ```

  Then install — show full output, do not truncate:

  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pip install 'fastapi>=0.100'
  ```

  **Assumptions:**
  - `"python-dotenv",` appears exactly once in `pyproject.toml` (confirmed from pre-flight — it is the last item in the dependencies list)
  - `starlette 1.0.0` is already installed as a transitive dep. FastAPI 0.115.x requires `starlette>=0.40.0` — starlette 1.0.0 satisfies this. If pip resolves to an older fastapi that requires a different starlette, the conflict appears in `pip check` output (Step 1 verification catches it).

  **Risks:**
  - pip resolution conflict with existing starlette 1.0.0 → mitigation: run `pip check` immediately after install (in verification below); if conflict found, run `pip install 'fastapi>=0.115,<1.0'` to force a version that explicitly supports starlette 1.x

  **Git Checkpoint:**
  ```bash
  git add /Users/ngchenmeng/Ballast/pyproject.toml
  git commit -m "step 6.1: add fastapi>=0.100 to pyproject.toml dependencies"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "import fastapi; print('fastapi', fastapi.__version__)"
  /Users/ngchenmeng/Ballast/venv/bin/pip check
  ```

  Run both. Both must pass.

  **Pass:**
  - First command prints `fastapi X.Y.Z` with no ImportError
  - Second command prints `No broken requirements` (or empty output)

  **Fail:**
  - `ModuleNotFoundError` → pip install did not succeed → re-run `pip install 'fastapi>=0.100'` and read full output
  - `pip check` reports conflict → starlette version incompatible → run `pip install 'fastapi>=0.115,<1.0'` and re-run `pip check`

---

- [ ] 🟥 **Step 2: Create `ballast/core/server.py` and `scripts/server.py`** — *Critical: API contract — tests and hook.py depend on endpoint paths*

  **Step Architecture Thinking:**

  **Pattern applied:** App/Runner split (Facade) — the FastAPI `app` object lives in an importable package module (`ballast/core/server.py`); `scripts/server.py` is a 3-line entrypoint that imports and runs it.

  **Why this step exists here in the sequence:** `sync.py` (Step 3) and `test_sync.py` (Step 4) both need the server to exist. The server must be created before the client that talks to it, and before the tests that verify both.

  **Why `ballast/core/server.py` is the right location:** `scripts/` is not a Python package (no `__init__.py`). Tests cannot do `from scripts.server import app`. Placing `app` in `ballast/core/` makes it importable as `from ballast.core.server import app` — the same pattern used by `TestClient` in Step 4.

  **Alternative approach considered and rejected:** `app` directly in `scripts/server.py`, tests use `sys.path.insert(0, ...)` to import it. Rejected — path mutation in tests is fragile and breaks when test runner CWD changes.

  **What breaks if this step deviates:** If `_current_spec` is not module-level in `ballast/core/server.py`, the pytest fixture in Step 4 (`_current_spec.clear()`) will clear a different dict than the one the `TestClient` sees — tests will leak state silently.

  ---

  **Idempotent:** Yes — creating a new file is idempotent if the file doesn't exist. Pre-Read Gate must confirm neither file exists.

  **Pre-Read Gate:**
  - Run `ls /Users/ngchenmeng/Ballast/ballast/core/server.py 2>&1`. Must return `No such file`. If file exists → STOP.
  - Run `ls /Users/ngchenmeng/Ballast/scripts/server.py 2>&1`. Must return `No such file`. If file exists → STOP.
  - Run `python -c "from fastapi import FastAPI; print('ok')"` with venv python. Must print `ok`. If not → Step 1 incomplete → STOP.

  **Create `/Users/ngchenmeng/Ballast/ballast/core/server.py`:**

  ```python
  """ballast/core/server.py — Spec update server (M5 side).

  Holds the current locked SpecModel per job_id in memory.
  Exposes two endpoints for the M2 SpecPoller to consume.

  Import: from ballast.core.server import app
  Run via: python scripts/server.py
  """
  from __future__ import annotations

  from fastapi import FastAPI

  from ballast.core.spec import SpecModel

  app = FastAPI()

  _current_spec: dict[str, dict] = {}  # job_id → SpecModel.model_dump()


  @app.get("/spec/{job_id}/current")
  def get_spec(job_id: str) -> dict:
      """Return the current spec for this job, or {} if not yet set."""
      return _current_spec.get(job_id, {})


  @app.post("/spec/{job_id}/update")
  def update_spec(job_id: str, spec: SpecModel) -> dict:
      """Store the new spec for this job. Returns version for confirmation."""
      _current_spec[job_id] = spec.model_dump()
      return {"status": "ok", "version": spec.version}
  ```

  **Create `/Users/ngchenmeng/Ballast/scripts/server.py`:**

  ```python
  """scripts/server.py — Run the ballast spec server.

  Usage: python scripts/server.py
  Default: http://0.0.0.0:8765
  """
  import uvicorn

  from ballast.core.server import app

  if __name__ == "__main__":
      uvicorn.run(app, host="0.0.0.0", port=8765)
  ```

  **What it does:** `ballast/core/server.py` defines the FastAPI app with module-level `_current_spec` dict and two endpoints. `scripts/server.py` is a 3-line runner that imports and launches it.

  **Assumptions:**
  - `fastapi` is installed (Step 1 complete)
  - `ballast/core/spec.py` exports `SpecModel` — confirmed from codebase

  **Risks:**
  - `SpecModel.model_dump()` includes `locked_at` and `version` — both set by `lock()`. If an unlocked spec is POSTed, `version` will be `""`. The server stores it without validation. Acceptable for demo — callers must lock before posting.

  **Git Checkpoint:**
  ```bash
  git add ballast/core/server.py scripts/server.py
  git commit -m "step 6.2: add FastAPI spec server (ballast/core/server.py + scripts/server.py)"
  ```

  **✓ Verification Test:**

  **Type:** Unit (no live server)

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && venv/bin/python -c "
  from fastapi.testclient import TestClient
  from ballast.core.server import app, _current_spec
  from ballast.core.spec import SpecModel, lock

  _current_spec.clear()
  client = TestClient(app)

  # GET unknown job → {}
  r = client.get('/spec/test-job/current')
  assert r.status_code == 200 and r.json() == {}, f'GET empty failed: {r.json()}'

  # POST spec → stored
  spec = lock(SpecModel(intent='test intent', success_criteria=['done']))
  r = client.post('/spec/test-job/update', json=spec.model_dump())
  assert r.status_code == 200
  assert r.json()['status'] == 'ok'
  assert r.json()['version'] == spec.version, f'version mismatch: {r.json()}'

  # GET → returns stored spec
  r = client.get('/spec/test-job/current')
  assert r.json()['version'] == spec.version

  _current_spec.clear()
  print('PASS')
  "
  ```

  **Pass:** Prints `PASS`. No exceptions.

  **Fail:**
  - `ImportError: cannot import name 'app'` → `ballast/core/server.py` not created or in wrong location → `ls ballast/core/`
  - `ImportError: fastapi` → Step 1 incomplete → run Step 1 first
  - `AssertionError` on version → `SpecModel` not locked before POST → check `lock()` call in verification script

---

- [ ] 🟥 **Step 3: Create `ballast/core/sync.py`** — *Critical: API contract consumed by hook.py and tests*

  **Step Architecture Thinking:**

  **Pattern applied:** Facade + Null Object — `SpecPoller` hides all HTTP complexity behind `poll()`. The Null Object pattern appears in the error path: every failure returns `None` instead of raising, making the caller's code unconditional (`if new_spec: ...`).

  **Why this step exists here in the sequence:** `test_sync.py` (Step 4) mocks `ballast.core.sync.httpx.get` — the mock target path only exists after this file is created.

  **Why `ballast/core/sync.py` is the right location:** Same package as `spec.py` — follows the pattern established by `trajectory.py` which also imports from `ballast.core.spec`. No circular import: `spec.py` does not import from `sync.py`.

  **Alternative approach considered and rejected:** Deferred `SpecModel` import inside `poll()` body to avoid "circular import". Rejected — no circular import exists (confirmed: `spec.py` has no import of `sync.py`). Deferred imports hide the actual dependency and make it harder to trace.

  **What breaks if this step deviates:** If `self._current` is not updated inside `poll()` on a successful version change, the next call will see the old version again and return the same spec repeatedly — hook.py would inject the same delta on every subsequent node.

  ---

  **Idempotent:** Yes — creating a new file.

  **Pre-Read Gate:**
  - Run `ls /Users/ngchenmeng/Ballast/ballast/core/sync.py 2>&1`. Must return `No such file`. If exists → STOP.
  - Run `grep -n "from ballast.core.spec import" /Users/ngchenmeng/Ballast/ballast/core/trajectory.py`. Must return 1 match — confirms this import pattern works from `ballast/core/`.

  **Create `/Users/ngchenmeng/Ballast/ballast/core/sync.py`:**

  ```python
  """ballast/core/sync.py — SpecPoller (M2 side).

  Polls the spec server at every Agent.iter node boundary.
  Returns a new SpecModel only when the version field changes.

  Never raises — M5 unreachable must not abort an M2 agent run.

  Usage:
      poller = SpecPoller("http://localhost:8765", "job-001")
      poller.set_initial(locked_spec)
      # at every node boundary in hook.py:
      new_spec = poller.poll()
      if new_spec:
          delta = active_spec.diff(new_spec)
          active_spec = new_spec
  """
  from __future__ import annotations

  import httpx

  from ballast.core.spec import SpecModel


  class SpecPoller:
      """Client-side poller that detects live spec changes from the server.

      poll() compares SpecModel.version (8-char sha256) — not a timestamp.
      Caller must call set_initial() before poll() or poll() returns None always.
      """

      def __init__(self, base_url: str, job_id: str) -> None:
          self.url = f"{base_url}/spec/{job_id}/current"
          self._current: SpecModel | None = None

      def set_initial(self, spec: SpecModel) -> None:
          """Set the baseline spec before polling starts."""
          self._current = spec

      def poll(self) -> SpecModel | None:
          """Check server for a spec update.

          Returns new SpecModel if version changed since last call.
          Returns None if unchanged, server unreachable, or set_initial() not called.
          Never raises.
          """
          if self._current is None:
              return None
          try:
              r = httpx.get(self.url, timeout=2.0)
              if r.status_code != 200:
                  return None
              data = r.json()
              if not data or data.get("version") == self._current.version:
                  return None
              new_spec = SpecModel(**data)
              self._current = new_spec   # update baseline so next poll compares correctly
              return new_spec
          except Exception:
              return None  # M5 unreachable — silent, agent continues with current spec
  ```

  **What it does:** Wraps HTTP + version comparison + error handling in a single `poll()` method. Updates `self._current` on every successful version change so the next call compares against the new baseline.

  **Assumptions:**
  - `SpecModel(**data)` works because `data` is a `model_dump()` dict — all fields present with correct types
  - `httpx` is installed (confirmed: 0.28.1 in venv)

  **Risks:**
  - Server returns partial dict (missing required fields) → `SpecModel(**data)` raises `ValidationError` → caught by `except Exception` → returns `None` silently. Acceptable for demo — server always returns `model_dump()`.

  **Git Checkpoint:**
  ```bash
  git add ballast/core/sync.py
  git commit -m "step 6.3: add SpecPoller to ballast/core/sync.py"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && venv/bin/python -c "
  from ballast.core.sync import SpecPoller
  from ballast.core.spec import SpecModel, lock

  # Test: poll() returns None when set_initial() not called
  poller = SpecPoller('http://localhost:9999', 'test-job')
  assert poller.poll() is None, 'expected None before set_initial'

  # Test: poll() returns None on connection refused (server not running)
  spec = lock(SpecModel(intent='test', success_criteria=['done']))
  poller.set_initial(spec)
  result = poller.poll()  # port 9999 nothing listening → exception → None
  assert result is None, f'expected None on connection error, got {result}'

  print('PASS')
  "
  ```

  **Pass:** Prints `PASS`. No exceptions propagate to the caller.

  **Fail:**
  - `ImportError: cannot import name 'SpecPoller'` → file not created → `ls ballast/core/sync.py`
  - `Exception propagated` (anything other than None returned) → `except Exception` block missing or `poll()` raises → re-read `sync.py` error handling

---

### Phase 2 — Isolation Tests

**Goal:** 9 tests confirm server endpoints and SpecPoller behaviour without a live server.

---

- [ ] 🟥 **Step 4: Create `tests/test_sync.py`** — *Non-critical: additive tests only*

  **Step Architecture Thinking:**

  **Pattern applied:** Test Isolation via fixture — `autouse` fixture clears `_current_spec` before and after every test, preventing server state from leaking between tests.

  **Why this step exists here in the sequence:** `server.py` and `sync.py` must both exist (Steps 2–3) so the test imports resolve.

  **Why this file is the right location:** Consistent with `tests/test_spec.py`, `tests/test_trajectory.py` etc. — all tests in one `tests/` directory.

  **Alternative approach considered and rejected:** Integration test that starts uvicorn in a thread and hits real endpoints. Rejected — adds timing, port management, and threading complexity. `TestClient` + mock httpx is deterministic and runs in milliseconds.

  **What breaks if this step deviates:** If the `autouse` fixture is missing, `test_post_overwrites_existing_spec` will see stale state from a prior test and assert the wrong version.

  ---

  **Idempotent:** No — creating the file twice overwrites it (acceptable if content identical). Pre-Read Gate must confirm file does not exist.

  **Pre-Read Gate:**
  - Run `ls /Users/ngchenmeng/Ballast/tests/test_sync.py 2>&1`. Must return `No such file`. If exists → STOP.

  **Create `/Users/ngchenmeng/Ballast/tests/test_sync.py`:**

  ```python
  """Tests for ballast/core/server.py and ballast/core/sync.py.

  Server tests use FastAPI TestClient — no live server needed.
  SpecPoller tests mock httpx.get — no network needed.
  """
  from unittest.mock import MagicMock, patch

  import httpx
  import pytest
  from fastapi.testclient import TestClient

  from ballast.core.server import _current_spec, app
  from ballast.core.spec import SpecModel, lock
  from ballast.core.sync import SpecPoller

  # ---------------------------------------------------------------------------
  # Shared helpers
  # ---------------------------------------------------------------------------

  client = TestClient(app)


  def _make_spec(intent: str = "do something") -> SpecModel:
      return lock(SpecModel(intent=intent, success_criteria=["it is done"]))


  # ---------------------------------------------------------------------------
  # Fixture — reset server state between tests
  # ---------------------------------------------------------------------------

  @pytest.fixture(autouse=True)
  def clear_server_state():
      _current_spec.clear()
      yield
      _current_spec.clear()


  # ---------------------------------------------------------------------------
  # Server endpoint tests (4 tests)
  # ---------------------------------------------------------------------------

  def test_get_unknown_job_returns_empty_dict():
      r = client.get("/spec/unknown-job/current")
      assert r.status_code == 200
      assert r.json() == {}


  def test_post_stores_spec_and_returns_version():
      spec = _make_spec()
      r = client.post("/spec/job-001/update", json=spec.model_dump())
      assert r.status_code == 200
      body = r.json()
      assert body["status"] == "ok"
      assert body["version"] == spec.version


  def test_get_returns_stored_spec():
      spec = _make_spec()
      client.post("/spec/job-001/update", json=spec.model_dump())
      r = client.get("/spec/job-001/current")
      assert r.status_code == 200
      data = r.json()
      assert data["version"] == spec.version
      assert data["intent"] == "do something"


  def test_post_overwrites_existing_spec():
      spec_v1 = _make_spec("do x")
      spec_v2 = _make_spec("do y")
      client.post("/spec/job-001/update", json=spec_v1.model_dump())
      client.post("/spec/job-001/update", json=spec_v2.model_dump())
      r = client.get("/spec/job-001/current")
      assert r.json()["version"] == spec_v2.version


  # ---------------------------------------------------------------------------
  # SpecPoller isolation tests (5 tests, httpx.get mocked)
  # ---------------------------------------------------------------------------

  def test_poller_returns_none_before_set_initial():
      poller = SpecPoller("http://localhost:8765", "job-001")
      assert poller.poll() is None


  def test_poller_returns_none_when_version_unchanged():
      spec = _make_spec()
      poller = SpecPoller("http://localhost:8765", "job-001")
      poller.set_initial(spec)
      mock_r = MagicMock()
      mock_r.status_code = 200
      mock_r.json.return_value = spec.model_dump()
      with patch("ballast.core.sync.httpx.get", return_value=mock_r):
          assert poller.poll() is None


  def test_poller_returns_new_spec_when_version_changed():
      spec_v1 = _make_spec("do x")
      spec_v2 = _make_spec("do y")
      poller = SpecPoller("http://localhost:8765", "job-001")
      poller.set_initial(spec_v1)
      mock_r = MagicMock()
      mock_r.status_code = 200
      mock_r.json.return_value = spec_v2.model_dump()
      with patch("ballast.core.sync.httpx.get", return_value=mock_r):
          result = poller.poll()
      assert result is not None
      assert result.version == spec_v2.version
      assert result.intent == "do y"


  def test_poller_returns_none_on_network_error():
      spec = _make_spec()
      poller = SpecPoller("http://localhost:8765", "job-001")
      poller.set_initial(spec)
      with patch(
          "ballast.core.sync.httpx.get",
          side_effect=httpx.ConnectError("unreachable"),
      ):
          assert poller.poll() is None


  def test_poller_returns_none_on_non_200_status():
      spec = _make_spec()
      poller = SpecPoller("http://localhost:8765", "job-001")
      poller.set_initial(spec)
      mock_r = MagicMock()
      mock_r.status_code = 500
      with patch("ballast.core.sync.httpx.get", return_value=mock_r):
          assert poller.poll() is None
  ```

  **Git Checkpoint:**
  ```bash
  git add tests/test_sync.py
  git commit -m "step 6.4: add server and SpecPoller isolation tests"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/test_sync.py -v --tb=short 2>&1 | tail -20
  ```

  Then full suite regression check:
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ --tb=short 2>&1 | tail -3
  ```

  **Pass:** All 9 new tests pass. Full suite shows `90 passed` (81 + 9).

  **Fail:**
  - `ImportError: cannot import name 'TestClient' from 'fastapi.testclient'` → fastapi not installed or version too old → re-run Step 1
  - `ImportError: cannot import name 'app'` → `ballast/core/server.py` missing → re-run Step 2
  - `ImportError: cannot import name 'SpecPoller'` → `ballast/core/sync.py` missing → re-run Step 3
  - `test_post_overwrites_existing_spec` fails → `autouse` fixture not clearing state → confirm `_current_spec.clear()` in `clear_server_state` is called before yield
  - Count is not 90 → `pytest tests/ -v` to identify which previously passing test regressed

---

## Regression Guard

| System | Pre-change behavior | Post-change verification |
|--------|---------------------|--------------------------|
| All existing tests | 81 passed | Full suite after Step 4 must show `81 passed` in all pre-existing tests |
| `SpecModel` construction | Unaffected | No changes to `spec.py` — confirmed by `grep -n "def lock\|class SpecModel" ballast/core/spec.py` unchanged |
| `pyproject.toml` other deps | All other deps unchanged | `pip check` after Step 1 must show no conflicts |

---

## Rollback Procedure

```bash
# Rollback in reverse order
git revert <step-6.4-commit>   # removes tests/test_sync.py
git revert <step-6.3-commit>   # removes ballast/core/sync.py
git revert <step-6.2-commit>   # removes ballast/core/server.py + scripts/server.py
git revert <step-6.1-commit>   # removes fastapi from pyproject.toml

# Confirm back to baseline:
/Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ --tb=short 2>&1 | tail -3
# Must show: 81 passed
```

---

## Pre-Flight Checklist

| Phase | Check | How to Confirm | Status |
|-------|-------|----------------|--------|
| **Pre-flight** | 81 tests pass | pytest tail -3 shows `81 passed` | ⬜ |
| | `fastapi` absent from pyproject.toml | `grep -n fastapi pyproject.toml` → 0 matches | ⬜ |
| | `server.py` and `sync.py` absent | `ls ballast/core/{server,sync}.py` → No such file | ⬜ |
| **Step 1** | `fastapi` in pyproject.toml | `grep fastapi pyproject.toml` → 1 match | ⬜ |
| | `fastapi` importable | `python -c "import fastapi; print(fastapi.__version__)"` | ⬜ |
| **Step 2** | `app` importable | `python -c "from ballast.core.server import app; print(app)"` | ⬜ |
| | `_current_spec` is module-level dict | `python -c "from ballast.core.server import _current_spec; print(type(_current_spec))"` → `<class 'dict'>` | ⬜ |
| **Step 3** | `SpecPoller` importable | `python -c "from ballast.core.sync import SpecPoller; print('ok')"` | ⬜ |
| **Step 4** | 9 new tests pass | `pytest tests/test_sync.py -v` → 9 passed | ⬜ |
| | No regression | `pytest tests/` → 90 passed | ⬜ |

---

## Risk Heatmap

| Step | Risk Level | What Could Go Wrong | Early Detection | Idempotent |
|------|-----------|---------------------|-----------------|------------|
| Step 1 | 🟡 **Medium** | pip resolves fastapi version incompatible with existing starlette 1.0.0 | `pip check` in Step 1 verification — must show `No broken requirements` | No |
| Step 2 | 🟢 **Low** | `_current_spec` not module-level → fixture clears wrong dict | Verification script uses `_current_spec.clear()` explicitly | Yes |
| Step 3 | 🟢 **Low** | `self._current` not updated inside `poll()` → repeated delta injection | `test_poller_returns_none_when_version_unchanged` would fail | Yes |
| Step 4 | 🟢 **Low** | `autouse` fixture missing → test state leaks | `test_post_overwrites_existing_spec` fails nondeterministically | No |

---

## Success Criteria

| Feature | Target | Verification |
|---------|--------|--------------|
| `fastapi` installed | `import fastapi` works, version ≥ 0.100 | Step 1 verification |
| Server GET `/spec/{id}/current` | Returns `{}` for unknown job | `test_get_unknown_job_returns_empty_dict` |
| Server POST `/spec/{id}/update` | Stores spec, returns `{"status": "ok", "version": ...}` | `test_post_stores_spec_and_returns_version` |
| `SpecPoller.poll()` detects version change | Returns new `SpecModel` when version differs | `test_poller_returns_new_spec_when_version_changed` |
| `SpecPoller.poll()` silent on error | Returns `None` on network error or non-200 | `test_poller_returns_none_on_network_error`, `test_poller_returns_none_on_non_200_status` |
| No regression | 81 existing tests still pass | Full suite shows 90 passed after Step 4 |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Do not batch multiple steps into one git commit.**
⚠️ **If idempotent = No, run Pre-Read Gate grep before every edit.**
⚠️ **Step 2 creates TWO files — both in the same commit.**
⚠️ **The mock target for SpecPoller tests is `ballast.core.sync.httpx.get` — not `httpx.get`. Wrong target = mock does not intercept.**
