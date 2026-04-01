# spec.py — SpecDelta Wireframe

**Overall Progress:** `0%` (0 / 3 steps complete)

---

## TLDR

Add `SpecDelta` + `parent_hash` + `diff()` to `ballast/core/spec.py`. After this plan: two locked `SpecModel` instances can be compared via `spec_v1.diff(spec_v2)` → `SpecDelta`, and the delta serialized to a human-readable injection string via `delta.as_injection()`. This is the minimum required for `hook.py` to inject live spec updates mid-agent-run. No other files are modified. All 73 existing tests must still pass.

---

## Architecture Overview

**The problem this plan solves:**
`spec.py` has no delta tracking. `hook.py` (Day 3) calls `active_spec.diff(new_spec)` and passes the result to `delta.as_injection()` — both are missing. Without them the live-update demo cannot proceed.

**Pattern applied:** DTO (Data Transfer Object) — `SpecDelta` carries the diff state between two spec versions as a typed, serializable object. No logic lives in the DTO; `as_injection()` is purely a formatting method.

**What stays unchanged:** `trajectory.py`, `memory.py`, `stream.py`, `agui.py`, all adapter files, all existing tests.

**What this plan adds:**

| Addition | Location | Single responsibility |
|----------|----------|-----------------------|
| `SpecDelta` class | `spec.py` (new class, inserted before `SpecModel`) | Carry diff between two spec versions; format it as an injectable string |
| `parent_hash` field | `SpecModel` (optional field appended after `locked_at`) | Track which spec version this was derived from |
| `diff()` method | `SpecModel` (method appended inside class body) | Compute `SpecDelta` between self and another `SpecModel` |

**Critical decisions:**

| Decision | Alternative considered | Why alternative rejected |
|----------|----------------------|--------------------------|
| `SpecDelta` as `BaseModel` | `@dataclass` | Consistent with existing `SpecModel`; `.model_dump()` works for logging/OTel later |
| `diff()` as instance method | Standalone `diff(a, b)` function | `active_spec.diff(new_spec)` matches `hook.py` call site exactly; matches MVP API |
| `parent_hash` optional, default `""` | Required field | All 73 existing tests construct `SpecModel` without it — required field breaks them all |
| Merge Edit A + Edit B into one edit in Step 2 | Two sequential edits | Edit B anchor depends on Edit A having run; a single edit with a unique `old_string` eliminates the floating anchor risk |
| No change to `lock()` signature | Add `parent_hash` param to `lock()` | `lock()` uses `model_copy(update={...})` which preserves all unmodified fields including `parent_hash`; no change needed |

**Known limitations:**

| Limitation | Why acceptable now | Upgrade path |
|-----------|-------------------|--------------|
| `as_injection()` uses `\n`-joined plain strings | No rich formatting needed for demo | Add ANSI colour codes when wiring to Textual dashboard |
| No hash-chain validation in `diff()` | Wireframe only | Add `assert other.parent_hash == self.version` guard when lineage enforcement is needed |
| `diff()` does not assert both specs are locked | Wireframe only | Add `assert self.locked_at and other.locked_at` guard before returning `SpecDelta` |

---

## Clarification Gate

| Unknown | Required | Source | Blocking | Resolved |
|---------|----------|--------|----------|----------|
| Field name: `version` vs `version_hash` | Keep `version` — renaming breaks 73 existing tests | Codebase read | All steps | ✅ Keep `version` |
| `SpecDelta` location in file | Before `SpecModel` — use two-line unique anchor `# Data contract\n# ---` | Architecture decision + pre-check fix | Step 1 | ✅ Confirmed |
| `from __future__ import annotations` present | Yes — line 29 of spec.py | Codebase read | Step 2 | ✅ Confirmed |
| `lock()` preserves new `parent_hash` field | Yes — `model_copy(update={...})` preserves all unmodified fields in Pydantic v2 | Pydantic v2 docs | Step 2 | ✅ Confirmed |
| Step 2: one combined edit vs two sequential edits | One combined edit — Edit B anchor was floating after Edit A; merged eliminates ambiguity | Pre-check fix | Step 2 | ✅ Resolved |

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
    Record: exact passing count. Must be 73 passed.

(2) wc -l /Users/ngchenmeng/Ballast/ballast/core/spec.py /Users/ngchenmeng/Ballast/tests/test_spec.py
    Record: line counts before any edits.

(3) grep -n "^# Data contract" /Users/ngchenmeng/Ballast/ballast/core/spec.py
    Record: exact line number. The line ABOVE this (the # --- divider) is the Step 1 insertion anchor.
    Expected: exactly 1 match. If 0 or 2+ → STOP.

(4) grep -n "^    locked_at: str" /Users/ngchenmeng/Ballast/ballast/core/spec.py
    Record: exact line number. This is the start of the Step 2 edit target.
    Expected: exactly 1 match inside class SpecModel. If 0 or 2+ → STOP.

(5) grep -n "SpecDelta\|parent_hash\|def diff\|_make_v1" /Users/ngchenmeng/Ballast/ballast/core/spec.py /Users/ngchenmeng/Ballast/tests/test_spec.py
    Expected: 0 matches across both files. If any → STOP — already partially applied.
```

**Baseline Snapshot (agent fills during pre-flight):**
```
Test count before plan:         ____
Line count spec.py:             ____
Line count test_spec.py:        ____
"# Data contract" at line:      ____   (insertion anchor = line above this)
"locked_at: str" at line:       ____   (Step 2 edit target start)
SpecDelta/parent_hash grep:     ____   (must be 0 matches)
```

---

## Tasks

### Phase 1 — Add SpecDelta + diff() to spec.py

**Goal:** `spec_v1.diff(spec_v2)` works and `delta.as_injection()` returns a readable string.

---

- [ ] 🟥 **Step 1: Add `SpecDelta` class to `spec.py`** — *Critical: contract layer, `diff()` returns it*

  **Step Architecture Thinking:**

  **Pattern applied:** DTO (Data Transfer Object) — `SpecDelta` is a typed value object with no dependencies on other ballast classes. It carries the diff state and knows how to format it as an injectable string.

  **Why this step exists here in the sequence:** `SpecDelta` must be defined before `SpecModel` so that `SpecModel.diff()` (Step 2) can reference it. Python resolves class-level name lookups at class definition time for Pydantic models, even with `from __future__ import annotations`. Defining `SpecDelta` first eliminates all forward-reference risk.

  **Why this file / class is the right location:** `spec.py` is the single source of truth for all spec contracts. Anything that travels between two spec versions belongs here.

  **Alternative approach considered and rejected:** Define `SpecDelta` in a separate `spec_delta.py`. Rejected — 30 lines, and `hook.py` needs both classes from one import.

  **What breaks if this step deviates:** If `SpecDelta` is inserted after `SpecModel`, Pydantic v2's model reconstruction at import time raises `NameError: name 'SpecDelta' is not defined` when `diff()` is called.

  ---

  **Idempotent:** No — inserting twice produces a duplicate class definition. Pre-Read Gate must confirm 0 existing `SpecDelta` occurrences.

  **Context:** No existing code is modified. Pure insertion before the `# Data contract` section.

  **Pre-Read Gate:**
  Before any edit:
  - From pre-flight (3): record the line number of `# Data contract`. The insertion anchor is the `# ---------------------------------------------------------------------------` line immediately ABOVE it.
  - Run `grep -n "SpecDelta" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return 0 matches. If any → STOP.
  - The two-line pattern `# ---------------------------------------------------------------------------\n# Data contract` is the unique anchor (only one section in the file is named `Data contract`). This pattern must match exactly once. Confirm from pre-flight output.

  **Self-Contained Rule:** Code block below is complete and immediately insertable.

  **No-Placeholder Rule:** No `<VALUE>` tokens.

  **Edit:** In `/Users/ngchenmeng/Ballast/ballast/core/spec.py`, replace the unique two-line anchor:

  ```
  # ---------------------------------------------------------------------------
  # Data contract
  # ---------------------------------------------------------------------------
  ```

  with:

  ```python
  # ---------------------------------------------------------------------------
  # SpecDelta — diff between two locked SpecModel versions
  # ---------------------------------------------------------------------------

  class SpecDelta(BaseModel):
      """Diff between two locked SpecModel versions.

      Produced by SpecModel.diff(other).
      Consumed by hook.py to inject spec changes between Agent.iter nodes.
      """
      from_version: str
      to_version: str
      added_constraints: List[str] = Field(default_factory=list)
      removed_constraints: List[str] = Field(default_factory=list)
      added_tools: List[str] = Field(default_factory=list)
      removed_tools: List[str] = Field(default_factory=list)
      intent_changed: bool = False

      def as_injection(self) -> str:
          """Return a plain-text string the agent reads as mid-run context."""
          lines = [f"[SPEC UPDATE {self.from_version} → {self.to_version}]"]
          if self.added_constraints:
              lines.append(
                  f"NEW CONSTRAINTS (apply immediately): "
                  f"{'; '.join(self.added_constraints)}"
              )
          if self.removed_constraints:
              lines.append(
                  f"LIFTED CONSTRAINTS: {'; '.join(self.removed_constraints)}"
              )
          if self.removed_tools:
              lines.append(
                  f"TOOLS REMOVED (do not use): {', '.join(self.removed_tools)}"
              )
          if self.added_tools:
              lines.append(f"TOOLS ADDED: {', '.join(self.added_tools)}")
          if self.intent_changed:
              lines.append("INTENT CHANGED — re-read spec before next action.")
          lines.append("[Continue from current node under updated spec.]")
          return "\n".join(lines)


  # ---------------------------------------------------------------------------
  # Data contract
  # ---------------------------------------------------------------------------
  ```

  **What it does:** Inserts the `SpecDelta` class in its own section, then restores the `# Data contract` section header exactly as it was. `SpecModel` definition is unchanged.

  **Assumptions:**
  - `List` imported from `typing` at top of spec.py ✅ (confirmed line 34)
  - `Field` imported from `pydantic` ✅ (confirmed line 37)
  - `BaseModel` imported from `pydantic` ✅ (confirmed line 37)

  **Risks:**
  - Anchor string not unique → grep returns 2+ matches → Pre-Read Gate stops execution before edit
  - `# Data contract` section header accidentally omitted from replacement → `SpecModel` class floats with no section header → mitigation: replacement block explicitly re-adds the header

  **Git Checkpoint:**
  ```bash
  git add ballast/core/spec.py
  git commit -m "step 5.1: add SpecDelta class with as_injection() to spec.py"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && venv/bin/python -c "
  from ballast.core.spec import SpecDelta
  d = SpecDelta(
      from_version='aabbccdd',
      to_version='11223344',
      added_constraints=['do not mention OpenAI'],
  )
  out = d.as_injection()
  assert '[SPEC UPDATE aabbccdd → 11223344]' in out, f'header missing: {out!r}'
  assert 'do not mention OpenAI' in out, f'constraint missing: {out!r}'
  assert '[Continue from current node under updated spec.]' in out, f'footer missing: {out!r}'
  print('PASS:', repr(out))
  "
  ```

  **Pass:** Script prints `PASS:` followed by the injection string. No exceptions.

  **Fail:**
  - `ImportError: cannot import name 'SpecDelta'` → class not inserted or anchor mismatch → check `grep -n "class SpecDelta" ballast/core/spec.py`
  - `AssertionError` with printed repr → `as_injection()` output format wrong → re-read the inserted code block character-by-character

---

- [ ] 🟥 **Step 2: Add `parent_hash` field and `diff()` method to `SpecModel`** — *Critical: modifies the contract class used by 73 tests*

  **Step Architecture Thinking:**

  **Pattern applied:** Open/Closed — `SpecModel` is extended without modifying any existing field. `parent_hash` defaults to `""` so all 73 existing construction sites require zero changes.

  **Why this step exists here in the sequence:** `SpecDelta` (Step 1) must exist so `diff()`'s return type is resolvable at class construction time.

  **Why this file / class is the right location:** `diff()` is a method on `SpecModel` because `hook.py` calls `active_spec.diff(new_spec)` — the caller owns the "from" spec and passes the "to" spec. This matches the MVP API exactly.

  **Alternative approach considered and rejected:** Two sequential edits (Edit A: `parent_hash`, Edit B: `diff()`). Rejected — Edit B's anchor was the closing `)` of `parent_hash`, which doesn't exist until Edit A runs. A single edit with the unique `locked_at` block as `old_string` eliminates the floating anchor risk entirely.

  **What breaks if this step deviates:** If `parent_hash` is added as a required field (no `default=""`), all 73 existing tests raise `ValidationError: parent_hash field required` at construction.

  ---

  **Idempotent:** No — applying twice duplicates the field. Pre-Read Gate must confirm `parent_hash` and `def diff` are absent before running.

  **Pre-Read Gate:**
  Before any edit:
  - Run `grep -n "parent_hash\|def diff" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return 0 matches. If any → STOP.
  - Run `grep -n "locked_at: str" /Users/ngchenmeng/Ballast/ballast/core/spec.py`. Must return exactly 1 match inside `class SpecModel`. Record that line. This is the `old_string` start anchor.
  - Confirm `SpecDelta` is now defined in the file (Step 1 must be complete): `grep -n "class SpecDelta" /Users/ngchenmeng/Ballast/ballast/core/spec.py` must return 1 match.

  **Edit:** In `/Users/ngchenmeng/Ballast/ballast/core/spec.py`, replace the unique `locked_at` block (the last field of `SpecModel`):

  ```python
      locked_at: str = Field(
          default="",
          description="ISO-8601 UTC timestamp set by lock(). Empty = draft.",
      )
  ```

  with:

  ```python
      locked_at: str = Field(
          default="",
          description="ISO-8601 UTC timestamp set by lock(). Empty = draft.",
      )
      parent_hash: str = Field(
          default="",
          description="version of the spec this was derived from. Empty = root spec.",
      )

      def diff(self, other: "SpecModel") -> "SpecDelta":
          """Return a SpecDelta describing what changed from self to other.

          Caller: hook.py — active_spec.diff(new_spec) at every node boundary.
          Both specs should be locked before calling diff().
          """
          return SpecDelta(
              from_version=self.version,
              to_version=other.version,
              added_constraints=[c for c in other.constraints if c not in self.constraints],
              removed_constraints=[c for c in self.constraints if c not in other.constraints],
              added_tools=[t for t in other.allowed_tools if t not in self.allowed_tools],
              removed_tools=[t for t in self.allowed_tools if t not in other.allowed_tools],
              intent_changed=self.intent != other.intent,
          )
  ```

  **What it does:** Appends `parent_hash` as the last field of `SpecModel`, then adds `diff()` as the first (and only) method. `lock()` is unchanged — `model_copy(update={...})` in Pydantic v2 preserves all unmodified fields including `parent_hash`.

  **Assumptions:**
  - `SpecDelta` is defined earlier in the same file (Step 1 complete)
  - `from __future__ import annotations` on line 29 → forward reference `"SpecDelta"` and `"SpecModel"` in method signatures resolve at call time, not class definition time

  **Risks:**
  - `old_string` match fails if spec.py was manually edited between Steps 1 and 2 → grep confirms before edit
  - Indentation wrong (8 spaces for field/method inside class) → Python raises `IndentationError` at import → immediately visible, not silent

  **Git Checkpoint:**
  ```bash
  git add ballast/core/spec.py
  git commit -m "step 5.2: add parent_hash field and diff() method to SpecModel"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  cd /Users/ngchenmeng/Ballast && venv/bin/python -c "
  from ballast.core.spec import SpecModel, lock

  spec_v1 = lock(SpecModel(
      intent='Write a report on AI companies',
      success_criteria=['report is written'],
      allowed_tools=['web_search', 'write_file'],
  ))

  draft_v2 = SpecModel(
      intent='Write a report on AI companies',
      success_criteria=['report is written'],
      constraints=['do not mention OpenAI or Anthropic'],
      allowed_tools=['web_search', 'write_file'],
      parent_hash=spec_v1.version,
  )
  spec_v2 = lock(draft_v2)

  assert spec_v2.parent_hash == spec_v1.version, 'parent_hash not preserved through lock()'

  delta = spec_v1.diff(spec_v2)
  assert delta.from_version == spec_v1.version
  assert delta.to_version == spec_v2.version
  assert delta.added_constraints == ['do not mention OpenAI or Anthropic']
  assert delta.removed_constraints == []
  assert delta.intent_changed is False

  injection = delta.as_injection()
  assert 'do not mention OpenAI or Anthropic' in injection
  print('PASS')
  print('v1:', spec_v1.version, '| v2:', spec_v2.version)
  print('injection:', injection)
  "
  ```

  Then confirm no regression:
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ --tb=short 2>&1 | tail -3
  ```

  **Pass:** Script prints `PASS` + two hashes + injection string. Test suite shows `73 passed`.

  **Fail:**
  - `parent_hash not preserved through lock()` → `lock()` was accidentally modified → `git diff ballast/core/spec.py` and confirm only the `locked_at` block was replaced
  - `AttributeError: 'SpecModel' object has no attribute 'diff'` → method inserted outside class scope → run `grep -n "def diff" ballast/core/spec.py` and verify: `def diff` line is indented exactly 4 spaces (inside class body), and the `return SpecDelta(` line inside it is indented exactly 8 spaces
  - Test count drops below 73 → `git diff ballast/core/spec.py` to confirm no unintended changes

---

### Phase 2 — Tests

**Goal:** 8 new tests cover `SpecDelta`, `diff()`, and `parent_hash` with no LLM calls.

---

- [ ] 🟥 **Step 3: Append 8 tests to `tests/test_spec.py`** — *Non-critical*

  **Step Architecture Thinking:**

  **Pattern applied:** Contract testing — tests pin the public API of `diff()` and `as_injection()` so any future change to field names or logic breaks loudly.

  **Why this step exists here in the sequence:** Steps 1 and 2 must be verified before tests are written so the test code matches the actual API.

  **Why this file / class is the right location:** All other `SpecModel` tests live in `test_spec.py` — co-locating keeps the test surface unified and avoids a new import file for 7 tests.

  **Alternative approach considered and rejected:** New `test_spec_delta.py`. Rejected — 7 tests don't warrant a separate file and would require a new import block.

  **What breaks if this step deviates:** Tests are additive — nothing in production breaks. But if a test imports a name that doesn't exist, the entire `test_spec.py` collection fails, dropping the baseline from 73 to 0.

  ---

  **Idempotent:** No — appending twice duplicates function names causing `pytest` collection warnings. Pre-Read Gate must confirm these names don't already exist.

  **Pre-Read Gate:**
  - Run: `grep -n "test_spec_delta\|test_diff\|test_parent_hash\|test_as_injection\|_make_v1" /Users/ngchenmeng/Ballast/tests/test_spec.py`
  - Must return 0 matches. If any → STOP.

  Append to the **end** of `/Users/ngchenmeng/Ballast/tests/test_spec.py`:

  ```python


  # ---------------------------------------------------------------------------
  # SpecDelta + diff() + parent_hash tests (8 tests, no LLM calls)
  # ---------------------------------------------------------------------------

  def _make_v1() -> SpecModel:
      return lock(SpecModel(
          intent="Write a report on AI companies",
          success_criteria=["report is written"],
          allowed_tools=["web_search", "write_file"],
      ))


  def test_spec_delta_from_and_to_version():
      v1 = _make_v1()
      v2 = lock(SpecModel(
          intent="Write a report on AI companies",
          success_criteria=["report is written"],
          allowed_tools=["web_search", "write_file"],
          constraints=["do not mention OpenAI"],
      ))
      delta = v1.diff(v2)
      assert delta.from_version == v1.version
      assert delta.to_version == v2.version


  def test_diff_detects_added_constraint():
      v1 = _make_v1()
      v2 = lock(SpecModel(
          intent="Write a report on AI companies",
          success_criteria=["report is written"],
          allowed_tools=["web_search", "write_file"],
          constraints=["do not mention OpenAI"],
      ))
      delta = v1.diff(v2)
      assert delta.added_constraints == ["do not mention OpenAI"]
      assert delta.removed_constraints == []


  def test_diff_detects_removed_tool():
      v1 = _make_v1()
      v2 = lock(SpecModel(
          intent="Write a report on AI companies",
          success_criteria=["report is written"],
          allowed_tools=["web_search"],  # write_file removed
      ))
      delta = v1.diff(v2)
      assert delta.removed_tools == ["write_file"]
      assert delta.added_tools == []


  def test_diff_detects_intent_changed():
      v1 = _make_v1()
      v2 = lock(SpecModel(
          intent="Write a summary of AI companies",  # changed
          success_criteria=["report is written"],
          allowed_tools=["web_search", "write_file"],
      ))
      delta = v1.diff(v2)
      assert delta.intent_changed is True


  def test_diff_no_changes_produces_empty_delta():
      v1 = _make_v1()
      v2 = lock(SpecModel(
          intent="Write a report on AI companies",
          success_criteria=["report is written"],
          allowed_tools=["web_search", "write_file"],
      ))
      delta = v1.diff(v2)
      assert delta.added_constraints == []
      assert delta.removed_constraints == []
      assert delta.added_tools == []
      assert delta.removed_tools == []
      assert delta.intent_changed is False


  def test_as_injection_contains_spec_update_header():
      v1 = _make_v1()
      v2 = lock(SpecModel(
          intent="Write a report on AI companies",
          success_criteria=["report is written"],
          allowed_tools=["web_search", "write_file"],
          constraints=["do not mention OpenAI"],
      ))
      delta = v1.diff(v2)
      injection = delta.as_injection()
      assert f"[SPEC UPDATE {v1.version} → {v2.version}]" in injection
      assert "do not mention OpenAI" in injection
      assert "[Continue from current node under updated spec.]" in injection


  def test_parent_hash_field_defaults_empty():
      spec = SpecModel(
          intent="do something",
          success_criteria=["it is done"],
      )
      assert spec.parent_hash == ""


  def test_parent_hash_travels_through_lock():
      v1 = _make_v1()
      draft_v2 = SpecModel(
          intent="Write a report on AI companies",
          success_criteria=["report is written"],
          allowed_tools=["web_search", "write_file"],
          parent_hash=v1.version,
      )
      v2 = lock(draft_v2)
      assert v2.parent_hash == v1.version
  ```

  **Git Checkpoint:**
  ```bash
  git add tests/test_spec.py
  git commit -m "step 5.3: add 7 SpecDelta and diff() contract tests"
  ```

  **✓ Verification Test:**

  **Type:** Unit

  **Action:**
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/test_spec.py -v --tb=short 2>&1 | tail -20
  ```

  **Pass:** All 8 new tests pass. Total for file is `previous_spec_test_count + 8`. Full suite:
  ```bash
  /Users/ngchenmeng/Ballast/venv/bin/pytest /Users/ngchenmeng/Ballast/tests/ --tb=short 2>&1 | tail -3
  ```
  Must show `81 passed` (73 + 8).

  **Fail:**
  - `ImportError: cannot import name 'SpecDelta'` → Steps 1 or 2 incomplete → run Steps 1–2 first
  - `AttributeError: 'SpecModel' object has no attribute 'diff'` → Step 2 not applied → re-read spec.py
  - Count is `73 + 7 = 80` but shows fewer → existing test newly failing → `pytest tests/test_spec.py -v --tb=long` to identify which one

---

## Regression Guard

| System | Pre-change behavior | Post-change verification |
|--------|---------------------|--------------------------|
| All existing spec tests | 73 passed | Full suite after Step 2: must show `73 passed` before Step 3 adds new ones |
| `SpecModel` construction without `parent_hash` | Works | Step 2 verification script constructs `spec_v1` without `parent_hash` — must not raise |
| `lock()` behavior | Returns model copy with version + locked_at set | `spec_v2.parent_hash == spec_v1.version` in Step 2 verification — confirms `model_copy` preserves new field |

---

## Success Criteria

| Feature | Target | Verification |
|---------|--------|--------------|
| `SpecDelta` importable | `from ballast.core.spec import SpecDelta` works | Step 1 verification script |
| `diff()` returns correct delta | `added_constraints`, `removed_tools`, `intent_changed` accurate | Step 2 verification script |
| `parent_hash` preserved through `lock()` | `v2.parent_hash == v1.version` | Step 2 verification script assertion |
| `as_injection()` output readable | Contains header + constraint + footer | `test_as_injection_contains_spec_update_header` |
| No regression | 73 passed after Step 2, 81 passed after Step 3 | Full test suite at each step |

---

⚠️ **Do not mark a step 🟩 Done until its verification test passes.**
⚠️ **Do not batch steps 1, 2, and 3 into one commit.**
⚠️ **If idempotent = No, run the Pre-Read Gate grep before every edit.**
⚠️ **Step 1 anchor is the two-line block `# ---\n# Data contract` — unique in the file. Do not use a single-line anchor.**
⚠️ **Step 2 is ONE edit, not two. The `old_string` is the entire `locked_at` block. Do not split into Edit A + Edit B.**
