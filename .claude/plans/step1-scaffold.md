# Step 1 — Scaffold the Ballast Repo Structure

**Overall Progress:** `0%` (0 / 6 steps complete)

---

## TLDR

Bootstrap the `ballast` Python package from an empty directory: initialize a standalone git repo, create a virtualenv, lay down the package skeleton (`core/stream.py`, adapter stubs, `tests/`), write `pyproject.toml`, install in editable mode, and verify with one smoke test. After this plan executes, `pip install -e ".[dev]"` works, `AgentStream` is importable, and `pytest tests/ -v` is green. Nothing else is built — no memory layer, no adapters with real logic, no validation.

---

## Architecture Overview

**The problem this plan solves:**
`/Users/ngchenmeng/Ballast/` is an empty directory (only `.claude/` exists). There is no git repo, no package, no installable entry point. Every subsequent Week 1–4 task depends on `from ballast.core.stream import AgentStream` being importable.

**The pattern(s) applied:**
- **Abstract Base Class (Template Method)** — `AgentStream` declares `stream()` as `@abstractmethod`. Adapters (agui, tinyfish) are forced to implement it. This is the contract every future component depends on. If violated (e.g. making it a plain class), adapter authors can skip implementing `stream()` and silently produce broken instances.
- **Editable install (`pip install -e`)** — package is importable from source without re-installing on every change. This is the right pattern for active development; a static `pip install .` would require re-running install on every edit.

**What stays unchanged:**
- `/Users/ngchenmeng/Ballast/.claude/` — plan files and settings. Not touched.
- Home-directory git repo (`/Users/ngchenmeng`) — Ballast gets its own `git init`, completely isolated.

**What this plan adds:**

| File | Single Responsibility |
|------|----------------------|
| `ballast/__init__.py` | Makes `ballast` a package |
| `ballast/core/__init__.py` | Makes `ballast.core` a subpackage |
| `ballast/core/stream.py` | Defines `AgentStream` ABC — the only interface contract |
| `ballast/adapters/__init__.py` | Makes `ballast.adapters` a subpackage |
| `ballast/adapters/agui.py` | Stub — placeholder for AG-UI adapter |
| `ballast/adapters/tinyfish.py` | Stub — placeholder for TinyFish adapter |
| `tests/test_stream.py` | Smoke test — confirms ABC structure is intact |
| `pyproject.toml` | Package metadata and dependency declarations |
| `.env.example` | Documents required env vars (empty for now) |
| `.gitignore` | Excludes venv, `.env`, `__pycache__`, `.pytest_cache` |

**Critical decisions:**

| Decision | Alternative considered | Why rejected |
|----------|----------------------|--------------|
| `AgentStream` as ABC with `@abstractmethod` | Plain class with `raise NotImplementedError` | ABC enforces contract at instantiation time, not at call time. Catches adapter bugs earlier. |
| `hatchling` build backend | `setuptools` | `hatchling` requires zero `setup.cfg` boilerplate; simpler for a new repo. |
| Adapter stubs are empty files with a pass docstring | Fully stubbed classes now | Avoids importing `ag_ui` before it's installed; stubs prove the import path works without requiring the dependency |
| `venv` at repo root (`./venv`) | System Python or conda | Isolated, reproducible, matches the long-term plan's exact command |

**Known limitations acknowledged:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| Adapter stubs do nothing | Week 1 goal is interface + installability, not working adapters | Implement in Week 1 day 2+ |
| No `ag_ui` import in stream.py yet | `ag-ui-protocol` may not be pip-installable on first run; confirm before importing | Add import once `pip install` confirms package name |
| `.env.example` is empty | No secrets needed in scaffold phase | Populate when first external service key is required |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---------|----------|--------|----------|----------|
| Correct PyPI name for ag-ui-protocol | Exact `pip install` name to put in `pyproject.toml` | Step 1 pre-flight: `pip index versions ag-ui-protocol` | Step 4 (pyproject.toml deps) | ⬜ Resolved in pre-flight |

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

(1) ls -la /Users/ngchenmeng/Ballast/
    Confirm: only .claude/ exists. No venv/, no ballast/, no tests/, no pyproject.toml.

(2) git -C /Users/ngchenmeng/Ballast status 2>&1
    Confirm: "not a git repository" (or similar). Ballast must NOT already be a git repo.
    If it IS already a git repo: STOP and report — do not re-init.

(3) python3 --version
    Confirm: 3.11 or higher. Record exact version.

(4) pip index versions ag-ui-protocol 2>&1 | head -5
    Confirm: package exists on PyPI. Record the exact package name returned.
    If not found: try `pip index versions ag_ui_protocol` and record which name works.

(5) Run: echo "Pre-flight complete"
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Python version:         ____
ag-ui PyPI name:        ____
Ballast contents before: ____
Git status before:      ____
Test count before:      0 (no tests exist yet)
```

**Automated checks (all must pass before Step 1):**
- [ ] `/Users/ngchenmeng/Ballast/` contains only `.claude/` — no source files
- [ ] `Ballast/` is NOT already a git repository
- [ ] Python >= 3.11
- [ ] `ag-ui-protocol` (or variant) is findable on PyPI

---

## Steps Analysis

```
Step 1 (git init + .gitignore)          — Non-critical  — verification only  — Idempotent: No (re-init on existing repo changes HEAD)
Step 2 (create venv)                    — Non-critical  — verification only  — Idempotent: No (re-running overwrites venv)
Step 3 (create package skeleton)        — Critical      — full code review   — Idempotent: Yes (writing same files again is safe)
Step 4 (write pyproject.toml)           — Critical      — full code review   — Idempotent: Yes
Step 5 (pip install -e ".[dev]")        — Non-critical  — verification only  — Idempotent: Yes
Step 6 (write + run smoke test)         — Critical      — full code review   — Idempotent: Yes
```

---

## Environment Matrix

| Step | Dev | Notes |
|------|-----|-------|
| All steps | ✅ | Local only — no staging/prod concept at scaffold phase |

---

## Tasks

### Phase 1 — Initialize Repository

**Goal:** A clean, isolated git repo exists at `/Users/ngchenmeng/Ballast/` with a venv and proper ignore rules.

---

- [ ] 🟥 **Step 1: git init + .gitignore** — *Non-critical: no source code, fully reversible*

  **Step Architecture Thinking:**

  **Pattern applied:** None (infrastructure setup, not OOP).

  **Why this step exists here in the sequence:**
  Every subsequent step produces files that git must track. The `.gitignore` must exist before any Python files are created so that `venv/`, `.env`, and `__pycache__/` are never staged accidentally.

  **Why this file / class is the right location:**
  `.gitignore` lives at repo root — git reads it from there automatically with no configuration.

  **Alternative approach considered and rejected:**
  Adding `.gitignore` after the venv is created — rejected because `venv/` would appear as untracked files in `git status`, creating noise and risk of accidental commit.

  **What breaks if this step deviates:**
  If `.gitignore` omits `venv/`, running `git add .` in any later step could stage hundreds of venv files into the commit. Recovery requires `git rm -r --cached venv/`.

  ---

  **Idempotent:** No — `git init` on an existing repo resets the description file but is otherwise safe. Pre-flight confirms this directory is NOT already a repo.

  **Context:** `/Users/ngchenmeng/Ballast/` is a subdirectory of the home-dir repo at `/Users/ngchenmeng/`. We need Ballast to be its own standalone repo, not a subdirectory tracked by the parent. `git init` inside `Ballast/` creates a nested `.git/` which takes precedence — git will treat it as a separate repo.

  **Pre-Read Gate:**
  - Run `git -C /Users/ngchenmeng/Ballast status 2>&1`. Must return "not a git repository". If it does NOT → STOP.

  ```bash
  cd /Users/ngchenmeng/Ballast
  git init
  ```

  Then write `/Users/ngchenmeng/Ballast/.gitignore`:

  ```
  # Python
  __pycache__/
  *.py[cod]
  *.egg-info/
  dist/
  build/
  .eggs/

  # Virtual environment
  venv/
  .venv/
  env/

  # Environment secrets
  .env

  # Testing
  .pytest_cache/
  .coverage
  htmlcov/

  # Editor
  .DS_Store
  .idea/
  .vscode/
  ```

  **What it does:** Creates a git repo and ensures generated/secret files are never committed.

  **Why this approach:** Explicit ignore list (not a template) — only what this project actually produces.

  **Assumptions:**
  - `/Users/ngchenmeng/Ballast/` exists (confirmed by pre-flight)
  - Ballast is not already a git repo (confirmed by pre-flight)

  **Risks:**
  - Parent home-dir repo picks up Ballast's `.git/` as a submodule prompt → mitigation: git treats nested `.git/` as a separate repo by default; no submodule is created unless explicitly run.

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add .gitignore
  git -C /Users/ngchenmeng/Ballast commit -m "step 1: init repo and add .gitignore"
  ```

  **Subtasks:**
  - [ ] 🟥 `git init` inside `/Users/ngchenmeng/Ballast/`
  - [ ] 🟥 Write `.gitignore` with the exact content above
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit (filesystem)

  **Action:**
  ```bash
  git -C /Users/ngchenmeng/Ballast log --oneline
  cat /Users/ngchenmeng/Ballast/.gitignore | grep "venv/"
  ```

  **Expected:**
  - `git log` returns exactly 1 commit: `step 1: init repo and add .gitignore`
  - `grep` returns `venv/`

  **Pass:** Both commands produce the expected output.

  **Fail:**
  - `git log` fails → git init did not run → re-run `git init` inside Ballast/
  - `grep` returns nothing → .gitignore was not written or wrong content → read the file and compare

---

- [ ] 🟥 **Step 2: Create virtualenv** — *Non-critical: isolated, fully reversible (rm -rf venv/)*

  **Step Architecture Thinking:**

  **Pattern applied:** None (environment isolation).

  **Why this step exists here in the sequence:**
  Must exist before Step 4's `pip install -e ".[dev]"`. Must exist after Step 1's `.gitignore` so `venv/` is ignored from the start.

  **Why this location:**
  `venv/` at repo root is the Python community standard and matches the exact path in the long-term plan.

  **Alternative considered and rejected:**
  System Python or conda — rejected because they pollute the global environment and make the project non-reproducible.

  **What breaks if this deviates:**
  If venv is created before `.gitignore`, `git status` shows thousands of untracked venv files.

  ---

  **Idempotent:** No — re-running `python -m venv venv` overwrites the existing venv. Pre-flight confirms it does not exist.

  **Context:** Creates an isolated Python environment. All subsequent `pip install` and `pytest` calls must use this venv.

  ```bash
  cd /Users/ngchenmeng/Ballast
  python3 -m venv venv
  ```

  **What it does:** Creates `venv/` with an isolated Python 3.13 interpreter.

  **Assumptions:**
  - Python >= 3.11 is on PATH (confirmed in pre-flight: 3.13.12)
  - `.gitignore` already excludes `venv/` (Step 1 complete)

  **Risks:**
  - Wrong Python version in venv → mitigation: verify with `venv/bin/python --version` in verification.

  **Git Checkpoint:** None — `venv/` is gitignored. No commit needed.

  **Subtasks:**
  - [ ] 🟥 Run `python3 -m venv venv` inside `/Users/ngchenmeng/Ballast/`

  **✓ Verification Test:**

  **Type:** Unit (filesystem)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python --version
  git -C /Users/ngchenmeng/Ballast status --short | grep venv
  ```

  **Expected:**
  - `python --version` returns `Python 3.13.x`
  - `git status --short` returns nothing (venv is ignored)

  **Pass:** Python version ≥ 3.11 and venv does not appear in git status.

  **Fail:**
  - `python --version` fails → venv was not created → re-run `python3 -m venv venv`
  - venv appears in git status → `.gitignore` missing `venv/` → fix Step 1 first

---

### Phase 2 — Package Skeleton

**Goal:** `from ballast.core.stream import AgentStream` is importable. All files exist with correct content. `pyproject.toml` is valid.

---

- [ ] 🟥 **Step 3: Create package skeleton (all __init__.py + stub files)** — *Critical: defines import paths every future file depends on*

  **Step Architecture Thinking:**

  **Pattern applied:** **Package namespace** — each `__init__.py` makes its directory importable. The stub adapters exist as files now so import paths are valid, even though they contain no logic.

  **Why this step exists here in the sequence:**
  `pyproject.toml` (Step 4) declares the package name `ballast`. If the `ballast/` directory doesn't exist when `pip install -e .` runs, hatchling will error. Package skeleton must precede install.

  **Why this file is the right location:**
  Python's import system requires `__init__.py` to be present in each directory that should be a package. The directory layout mirrors the import path: `ballast.core.stream` → `ballast/core/stream.py`.

  **Alternative considered and rejected:**
  Single flat `ballast.py` module instead of a package directory — rejected because Week 2+ adds `spec.py`, `trajectory.py`, `guardrails.py` at the same level. A flat module cannot be split without breaking imports.

  **What breaks if this deviates:**
  If `ballast/core/__init__.py` is missing, `from ballast.core.stream import AgentStream` raises `ModuleNotFoundError: No module named 'ballast.core'`.

  ---

  **Idempotent:** Yes — writing the same files again produces the same result.

  **Context:** This step creates all Python files. Stubs are intentionally minimal — no imports from `ag_ui` yet (package name not confirmed until pre-flight Step 4).

  **Pre-Read Gate:**
  - Run `ls /Users/ngchenmeng/Ballast/ballast/ 2>&1`. Must return "No such file or directory". If the directory exists → STOP and report what's already there.

  **Self-Contained Rule:** All file contents below are complete and verbatim.

  **No-Placeholder Rule:** No `<VALUE>` tokens below.

  ---

  Write `/Users/ngchenmeng/Ballast/ballast/__init__.py`:
  ```python
  """Ballast — AG-UI agent orchestration library."""
  ```

  Write `/Users/ngchenmeng/Ballast/ballast/core/__init__.py`:
  ```python
  ```
  *(empty file — just makes the directory a package)*

  Write `/Users/ngchenmeng/Ballast/ballast/core/stream.py`:
  ```python
  from abc import ABC, abstractmethod
  from typing import AsyncIterator


  class AgentStream(ABC):
      """Base class for all Ballast agent adapters.

      Every adapter must implement `stream()`. The `inject()` method is
      optional — adapters that support mid-task intervention override it.
      """

      @abstractmethod
      async def stream(self, goal: str, spec: dict) -> AsyncIterator[object]:
          """Stream AG-UI events for a given goal and locked spec.

          Args:
              goal: Natural language task description.
              spec: Locked specification dict produced by spec.py (Week 2).

          Yields:
              AG-UI Event objects (typed once ag-ui-protocol is imported).
          """
          ...

      async def inject(self, thread_id: str, message: str) -> None:
          """Inject a message into a running task (pause/resume flow).

          Adapters that support intervention override this method.
          Default raises NotImplementedError to make the gap explicit.
          """
          raise NotImplementedError("This adapter does not support mid-task injection")
  ```

  Write `/Users/ngchenmeng/Ballast/ballast/adapters/__init__.py`:
  ```python
  ```
  *(empty)*

  Write `/Users/ngchenmeng/Ballast/ballast/adapters/agui.py`:
  ```python
  """AG-UI adapter stub — implement in Week 1 day 2."""
  from ballast.core.stream import AgentStream


  class AGUIAdapter(AgentStream):
      """Streams AG-UI events from a LangGraph agent."""

      async def stream(self, goal: str, spec: dict):
          raise NotImplementedError("AGUIAdapter.stream() not yet implemented")
  ```

  Write `/Users/ngchenmeng/Ballast/ballast/adapters/tinyfish.py`:
  ```python
  """TinyFish adapter stub — implement in Week 1 day 2."""
  from ballast.core.stream import AgentStream


  class TinyFishAdapter(AgentStream):
      """Bridges TinyFish agent protocol to AG-UI event stream."""

      async def stream(self, goal: str, spec: dict):
          raise NotImplementedError("TinyFishAdapter.stream() not yet implemented")
  ```

  Write `/Users/ngchenmeng/Ballast/tests/__init__.py`:
  ```python
  ```
  *(empty)*

  Write `/Users/ngchenmeng/Ballast/.env.example`:
  ```
  # Copy this file to .env and fill in values as needed.
  # No secrets required at scaffold stage.
  ```

  ---

  **What it does:** Creates the complete import namespace. `from ballast.core.stream import AgentStream` will resolve once `pip install -e .` runs in Step 5.

  **Why this approach:** Stub adapters import from `ballast.core.stream` — this validates the internal import path works correctly, not just the top-level package.

  **Assumptions:**
  - `ballast/` directory does not exist yet (confirmed in Pre-Read Gate)
  - `tests/` directory does not exist yet

  **Risks:**
  - Typo in directory name (`balast/` vs `ballast/`) → mitigation: verification grep confirms exact import path works.

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add ballast/ tests/__init__.py .env.example
  git -C /Users/ngchenmeng/Ballast commit -m "step 3: add package skeleton and adapter stubs"
  ```

  **Subtasks:**
  - [ ] 🟥 Write `ballast/__init__.py`
  - [ ] 🟥 Write `ballast/core/__init__.py`
  - [ ] 🟥 Write `ballast/core/stream.py`
  - [ ] 🟥 Write `ballast/adapters/__init__.py`
  - [ ] 🟥 Write `ballast/adapters/agui.py`
  - [ ] 🟥 Write `ballast/adapters/tinyfish.py`
  - [ ] 🟥 Write `tests/__init__.py`
  - [ ] 🟥 Write `.env.example`
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit (import check)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import sys
  sys.path.insert(0, '/Users/ngchenmeng/Ballast')
  from ballast.core.stream import AgentStream
  from ballast.adapters.agui import AGUIAdapter
  from ballast.adapters.tinyfish import TinyFishAdapter
  print('imports OK')
  "
  ```

  **Expected:** `imports OK`

  **Pass:** Prints `imports OK` with exit code 0.

  **Fail:**
  - `ModuleNotFoundError: No module named 'ballast'` → directory named wrong or missing `__init__.py` → check `ls ballast/`
  - `ModuleNotFoundError: No module named 'ballast.core'` → `ballast/core/__init__.py` missing → create it
  - `ModuleNotFoundError: No module named 'ballast.adapters'` → `ballast/adapters/__init__.py` missing → create it

---

- [ ] 🟥 **Step 4: Write pyproject.toml** — *Critical: defines installable package; wrong deps here block pip install*

  **Step Architecture Thinking:**

  **Pattern applied:** **Single source of truth for package metadata** — `pyproject.toml` is the one file that declares what `ballast` is, what Python version it requires, and what it depends on. If the package name here diverges from the directory name, hatchling silently builds the wrong package.

  **Why this step exists here in the sequence:**
  Must come after the `ballast/` directory exists (Step 3) so hatchling can discover the package. Must come before `pip install -e .` (Step 5).

  **Why this file is the right location:**
  `pyproject.toml` at repo root is the PEP 517/518 standard. hatchling reads it from there automatically.

  **Alternative considered and rejected:**
  `setup.py` + `setup.cfg` — rejected as legacy; hatchling + `pyproject.toml` is the modern standard with less boilerplate.

  **What breaks if this deviates:**
  If `name` in `[project]` does not match the directory name `ballast`, `from ballast.core.stream import AgentStream` will fail with `ModuleNotFoundError` even after install.

  ---

  **Idempotent:** Yes — overwriting with same content is harmless.

  **Context:** `ag-ui-protocol` PyPI name must be confirmed from pre-flight before writing dependencies. If the name is wrong, `pip install -e .` will fail with a package-not-found error.

  **Pre-Read Gate:**
  - Confirm pre-flight captured the correct PyPI name for ag-ui-protocol.
  - Run `ls /Users/ngchenmeng/Ballast/pyproject.toml 2>&1`. Must return "No such file". If it exists → read it first, then decide whether to overwrite.

  Write `/Users/ngchenmeng/Ballast/pyproject.toml`:
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

  **What it does:** Declares `ballast` as an installable package with its direct dependencies and optional dev tools.

  **Why this approach:** `[tool.hatch.build.targets.wheel] packages = ["ballast"]` explicitly tells hatchling which directory is the package — prevents it from including `tests/`, `venv/`, or `.claude/` in the build.

  **Assumptions:**
  - `ag-ui-protocol` is the correct PyPI package name (verify in pre-flight)
  - `ballast/` directory exists at repo root (Step 3 complete)

  **Risks:**
  - `ag-ui-protocol` PyPI name is wrong → `pip install` fails → mitigation: pre-flight verifies the name before this step runs.
  - hatchling not available → mitigation: `pip install hatchling` before `pip install -e .` in Step 5.

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add pyproject.toml
  git -C /Users/ngchenmeng/Ballast commit -m "step 4: add pyproject.toml with dependencies"
  ```

  **Subtasks:**
  - [ ] 🟥 Write `pyproject.toml` with exact content above
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit (syntax check)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  import tomllib
  with open('/Users/ngchenmeng/Ballast/pyproject.toml', 'rb') as f:
      data = tomllib.load(f)
  assert data['project']['name'] == 'ballast'
  assert data['project']['requires-python'] == '>=3.11'
  assert 'ag-ui-protocol' in data['project']['dependencies']
  print('pyproject.toml valid')
  "
  ```

  **Expected:** `pyproject.toml valid`

  **Pass:** Prints `pyproject.toml valid` with exit code 0.

  **Fail:**
  - `TOMLDecodeError` → syntax error in pyproject.toml → read the file and check indentation/quotes
  - `AssertionError` on `name` → name field wrong → must be exactly `"ballast"`
  - `AssertionError` on dependency → dependency name wrong → fix to match PyPI name from pre-flight

---

### Phase 3 — Install and Verify

**Goal:** `pip install -e ".[dev]"` completes cleanly. `pytest tests/ -v` returns 1 passing test.

---

- [ ] 🟥 **Step 5: pip install -e ".[dev]"** — *Non-critical: fully reversible (pip uninstall ballast)*

  **Step Architecture Thinking:**

  **Pattern applied:** Editable install — source files are the installed package; no copy step needed on edit.

  **Why this step exists here in the sequence:**
  `stream.py` is readable by Python via `sys.path.insert` (tested in Step 3), but the smoke test (Step 6) must use a proper install so it mirrors how downstream consumers will import it.

  **Why editable install:**
  During active development, files change constantly. Editable install means `import ballast` always reflects the current source without re-running `pip install`.

  **Alternative rejected:**
  `pip install .` (non-editable) — rejected because every source change requires re-install, creating silent stale-import bugs.

  **What breaks if this deviates:**
  If `pyproject.toml` has a wrong package name or missing `packages` declaration, pip installs successfully but `import ballast` still fails.

  ---

  **Idempotent:** Yes — re-running `pip install -e .` on an already-installed editable package is safe.

  **Context:** Installs `ballast`, `ag-ui-protocol`, `pydantic`, `python-dotenv`, `pytest`, and `pytest-asyncio` into the venv.

  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pip install -e "/Users/ngchenmeng/Ballast[dev]"
  ```

  **What it does:** Installs all dependencies and registers `ballast` as an editable package.

  **Assumptions:**
  - `pyproject.toml` exists and is valid (Step 4 complete)
  - `venv/` exists with pip (Step 2 complete)
  - Internet access available for PyPI downloads

  **Risks:**
  - `ag-ui-protocol` install fails → wrong PyPI name → fix `pyproject.toml` dep name and re-run
  - `hatchling` not available in venv → mitigation: pip will install it as a build dep automatically via `[build-system] requires`

  **Git Checkpoint:** None — install artifacts are gitignored.

  **Subtasks:**
  - [ ] 🟥 Run `pip install -e ".[dev]"` using venv pip

  **✓ Verification Test:**

  **Type:** Integration (package import)

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/python -c "
  from ballast.core.stream import AgentStream
  from ballast.adapters.agui import AGUIAdapter
  from ballast.adapters.tinyfish import TinyFishAdapter
  import pydantic, dotenv
  print('all imports OK')
  "
  ```

  **Expected:** `all imports OK`

  **Pass:** Prints `all imports OK` with exit code 0.

  **Fail:**
  - `ModuleNotFoundError: No module named 'ballast'` → editable install did not register the package → check `pip show ballast`, confirm `Location` points to `/Users/ngchenmeng/Ballast`
  - `ModuleNotFoundError: No module named 'pydantic'` → install failed partway → re-run `pip install -e ".[dev]"`
  - `ModuleNotFoundError: No module named 'ag_ui'` or similar → ag-ui-protocol not installed → check package name in `pyproject.toml`

---

- [ ] 🟥 **Step 6: Write smoke test + run pytest** — *Critical: green test = valid foundation for all Week 1–4 work*

  **Step Architecture Thinking:**

  **Pattern applied:** **Contract test** — the test does not test behavior, it tests that the ABC contract (`stream` and `inject` attributes exist as abstract/concrete methods) is structurally intact. This is a regression guard, not a unit test of logic.

  **Why this step exists here in the sequence:**
  This is the final gate. If pytest passes, the repo is in a provably valid state. All Week 1 work builds on top of a confirmed green baseline.

  **Why `tests/test_stream.py`:**
  `tests/` is the conventional pytest discovery directory. The test file name matches the module under test (`stream.py`).

  **Alternative considered and rejected:**
  Testing that `AGUIAdapter().stream()` raises `NotImplementedError` — rejected because that tests stub behavior, not the ABC contract. The contract is what matters here.

  **What breaks if this deviates:**
  If the test imports `AgentStream` from a wrong path (e.g. `ballast.stream` instead of `ballast.core.stream`), it passes but doesn't validate the actual import path consumers will use.

  ---

  **Idempotent:** Yes — running the same test file multiple times produces the same result.

  **Context:** One test, one assertion. Green = the interface exists and is importable via the installed package.

  **Pre-Read Gate:**
  - Run `ls /Users/ngchenmeng/Ballast/tests/test_stream.py 2>&1`. Must return "No such file". If it exists → read it first before overwriting.

  Write `/Users/ngchenmeng/Ballast/tests/test_stream.py`:
  ```python
  import inspect
  import pytest
  from ballast.core.stream import AgentStream


  def test_agentstream_is_abstract():
      """AgentStream cannot be instantiated directly — it must be subclassed."""
      assert inspect.isabstract(AgentStream), (
          "AgentStream must be an ABC with at least one abstractmethod"
      )


  def test_agentstream_has_stream_method():
      """stream() must be declared as an abstractmethod."""
      assert "stream" in AgentStream.__abstractmethods__, (
          "stream() must be in __abstractmethods__"
      )


  def test_agentstream_has_inject_method():
      """inject() must exist as a concrete (non-abstract) method with a default implementation."""
      assert hasattr(AgentStream, "inject"), "inject() method must exist on AgentStream"
      assert "inject" not in AgentStream.__abstractmethods__, (
          "inject() must NOT be abstract — it has a default NotImplementedError implementation"
      )


  def test_agentstream_inject_raises_not_implemented():
      """The default inject() raises NotImplementedError."""

      class ConcreteAdapter(AgentStream):
          async def stream(self, goal: str, spec: dict):
              yield

      import asyncio

      adapter = ConcreteAdapter()
      with pytest.raises(NotImplementedError):
          asyncio.run(adapter.inject("thread-1", "hello"))
  ```

  **What it does:** Four focused assertions: (1) ABC is abstract, (2) `stream` is in `__abstractmethods__`, (3) `inject` exists as concrete, (4) default `inject` raises `NotImplementedError`. Covers the full contract.

  **Why this approach:** Each test is named by what it asserts. Failure messages include the exact violated contract. No mocking, no fixtures — pure structural tests.

  **Assumptions:**
  - `ballast` is installed as editable (Step 5 complete)
  - `pytest` and `pytest-asyncio` are installed (Step 5 complete)

  **Risks:**
  - `asyncio.run()` inside pytest behaves differently on some platforms → mitigation: this is a simple sync wrapper around a coroutine, no `pytest-asyncio` config needed for this pattern.

  **Git Checkpoint:**
  ```bash
  git -C /Users/ngchenmeng/Ballast add tests/test_stream.py
  git -C /Users/ngchenmeng/Ballast commit -m "step 6: add smoke test for AgentStream contract"
  ```

  **Subtasks:**
  - [ ] 🟥 Write `tests/test_stream.py` with exact content above
  - [ ] 🟥 Run `pytest tests/ -v`
  - [ ] 🟥 Confirm 4 tests pass, 0 fail
  - [ ] 🟥 Git checkpoint commit

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ -v
  ```

  **Expected:**
  ```
  tests/test_stream.py::test_agentstream_is_abstract PASSED
  tests/test_stream.py::test_agentstream_has_stream_method PASSED
  tests/test_stream.py::test_agentstream_has_inject_method PASSED
  tests/test_stream.py::test_agentstream_inject_raises_not_implemented PASSED
  4 passed
  ```

  **Pass:** `4 passed` with exit code 0.

  **Fail:**
  - `ImportError` on `from ballast.core.stream import AgentStream` → package not installed → re-run Step 5
  - `AssertionError: AgentStream must be an ABC` → `AgentStream` doesn't inherit from `ABC` → fix `stream.py`
  - `AssertionError: stream() must be in __abstractmethods__` → `@abstractmethod` decorator missing → fix `stream.py`
  - `AssertionError: inject() must NOT be abstract` → `inject` was accidentally decorated with `@abstractmethod` → fix `stream.py`
  - `NotImplementedError not raised` → default `inject` was overridden or removed → fix `stream.py`

---

## Regression Guard

No existing code is modified by this plan. No regression risk.

**Test count regression check:**
- Tests before plan: `0` (no tests existed)
- Tests after plan: must be exactly `4` (all passing)

---

## Rollback Procedure

```bash
# Full rollback — remove everything this plan created
rm -rf /Users/ngchenmeng/Ballast/ballast/ \
       /Users/ngchenmeng/Ballast/tests/ \
       /Users/ngchenmeng/Ballast/venv/ \
       /Users/ngchenmeng/Ballast/pyproject.toml \
       /Users/ngchenmeng/Ballast/.gitignore \
       /Users/ngchenmeng/Ballast/.env.example \
       /Users/ngchenmeng/Ballast/.git/

# Confirm rollback:
ls /Users/ngchenmeng/Ballast/
# Expected: only .claude/ remains
```

---

## Pre-Flight Checklist

| Phase | Check | How to Confirm | Status |
|-------|-------|----------------|--------|
| Pre-flight | `Ballast/` contains only `.claude/` | `ls /Users/ngchenmeng/Ballast/` | ⬜ |
| Pre-flight | Ballast is NOT a git repo | `git -C /Users/ngchenmeng/Ballast status` returns "not a git repository" | ⬜ |
| Pre-flight | Python >= 3.11 | `python3 --version` | ⬜ |
| Pre-flight | ag-ui-protocol findable on PyPI | `pip index versions ag-ui-protocol` | ⬜ |
| Phase 1 | `.git/` created | `ls /Users/ngchenmeng/Ballast/.git/` | ⬜ |
| Phase 1 | `.gitignore` has `venv/` | `grep venv/ /Users/ngchenmeng/Ballast/.gitignore` | ⬜ |
| Phase 1 | venv Python >= 3.11 | `venv/bin/python --version` | ⬜ |
| Phase 2 | All 8 source files exist | `find /Users/ngchenmeng/Ballast/ballast/ -name "*.py" \| wc -l` returns 5 | ⬜ |
| Phase 2 | `pyproject.toml` is valid TOML | `python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"` | ⬜ |
| Phase 3 | `pip show ballast` shows editable install | Location = `/Users/ngchenmeng/Ballast` | ⬜ |
| Phase 3 | 4 tests pass | `pytest tests/ -v` → `4 passed` | ⬜ |

---

## Risk Heatmap

| Step | Risk Level | What Could Go Wrong | Early Detection | Idempotent |
|------|-----------|---------------------|-----------------|------------|
| Step 1 | 🟢 Low | Parent repo picks up Ballast git files | `git -C ~ status` check | No |
| Step 2 | 🟢 Low | Wrong Python version in venv | `venv/bin/python --version` | No |
| Step 3 | 🟡 Medium | Missing `__init__.py` breaks import path | Import check in verification | Yes |
| Step 4 | 🟡 Medium | Wrong ag-ui-protocol PyPI name | Pre-flight pip index check | Yes |
| Step 5 | 🟡 Medium | ag-ui-protocol install fails | `pip install` stderr | Yes |
| Step 6 | 🟢 Low | Test structure wrong (import path) | pytest output | Yes |

---

## Success Criteria

| Feature | Target | Verification |
|---------|--------|--------------|
| Git repo initialized | Standalone repo in `Ballast/`, not tracked by home-dir repo | `git -C Ballast log --oneline` shows 3 commits |
| Package installable | `pip install -e ".[dev]"` exits 0 | `pip show ballast` shows Location = Ballast/ |
| AgentStream importable | `from ballast.core.stream import AgentStream` works | Import check in Step 5 verification |
| Adapter stubs importable | Both adapter classes importable | Import check in Step 5 verification |
| Smoke test green | 4 tests pass, 0 fail | `pytest tests/ -v` → `4 passed` |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Do not proceed past a Human Gate without explicit human input.**
⚠️ **If blocked, mark 🟨 In Progress and output the State Manifest before stopping.**
⚠️ **Do not batch multiple steps into one git commit.**
⚠️ **If idempotent = No, confirm the step has not already run before executing.**
