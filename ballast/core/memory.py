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
    MemoryLockTimeout — raised when write/log_run/consolidate cannot acquire the scope lock
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

import anthropic
from filelock import FileLock, Timeout as FileLockTimeout
from pydantic import BaseModel


class MemoryLockTimeout(Exception):
    """The scope file lock could not be acquired after retries (see `_acquire_with_retry`)."""

# CWD-relative default. Override with BALLAST_MEMORY_DIR env var or by calling
# set_memory_dir() before any memory operations in a process.
_MEMORY_DIR_ENV = os.environ.get("BALLAST_MEMORY_DIR")
MEMORY_DIR: Path = Path(_MEMORY_DIR_ENV) if _MEMORY_DIR_ENV else Path(".ballast_memory")

# Semantic consolidation every N real (non-trial) runs.
CONSOLIDATE_EVERY = 3

from ballast.core.constants import SONNET_MODEL as _ANTHROPIC_MODEL  # type: ignore[assignment]

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
    """Map scope to a single file under MEMORY_DIR (no path traversal)."""
    s = str(scope).strip()
    if not s:
        raise ValueError("memory scope must be non-empty")
    if "\x00" in s:
        raise ValueError("memory scope must not contain NUL")
    sep = os.sep
    alt = os.altsep
    if sep in s or (alt and alt in s):
        raise ValueError("memory scope must not contain path separators")
    if ".." in s:
        raise ValueError("memory scope must not contain '..'")
    safe = s.replace(":", "_")
    if not safe or safe in (".", ".."):
        raise ValueError("memory scope is invalid")
    return MEMORY_DIR / f"{safe}.json"


_LOCK_TIMEOUT_SECONDS = 10
_LOCK_RETRY_SLEEP_SECONDS = 1.0


def _scope_lock(path: Path) -> FileLock:
    """Per-scope advisory lock — serializes all read-modify-write operations."""
    return FileLock(str(path.with_suffix(".lock")), timeout=_LOCK_TIMEOUT_SECONDS)


def _acquire_with_retry(lock: FileLock, label: str) -> bool:
    """Try to acquire *lock* once, sleep, then retry once more.

    Returns True if acquired. Returns False after two failures (caller should
    log at ERROR and raise ``MemoryLockTimeout`` — do not drop updates silently).
    """
    for attempt in (1, 2):
        try:
            lock.acquire()
            return True
        except FileLockTimeout:
            if attempt == 1:
                logger.warning(
                    "%s: lock timeout (attempt %d/%d) — retrying in %.1fs",
                    label, attempt, 2, _LOCK_RETRY_SLEEP_SECONDS,
                )
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)
    logger.error(
        "%s: lock timeout after %d attempts — memory not persisted",
        label,
        2,
    )
    return False


def _empty_scope_data() -> dict:
    """Canonical empty schema. Single source of truth for all functions."""
    return {
        "quirks": [],
        "runs": [],
        "semantic_profile": "",
        "run_count": 0,          # current window count (capped at 100)
        "lifetime_run_count": 0, # monotonic counter; never truncated
        "last_consolidated": 0.0,
        "last_consolidated_synthesis_count": 0,
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
    """Persist JSON via temp file + fsync + os.replace (atomic + durable on POSIX).

    fsync before replace ensures the data reaches disk before the directory
    entry is updated. Without it, a crash between write and replace on some
    filesystems can leave a zero-length or stale file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix=path.name + ".",
        dir=str(path.parent),
    )
    fp = None
    try:
        fp = os.fdopen(fd, "w", encoding="utf-8")
        with fp:
            json.dump(data, fp, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp_path, path)
    except Exception:
        # If os.fdopen failed, fd is still open. If fdopen succeeded, ``with fp``
        # closed the fd — never call os.close(fd) in that case (double-close).
        if fp is None:
            try:
                os.close(fd)
            except OSError:
                pass
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
    except json.JSONDecodeError:
        logger.warning("recall: corrupt JSON in memory file %s — returning empty briefing", path)
        return ""
    except OSError as exc:
        logger.warning("recall: could not read memory file %s: %s — returning empty briefing", path, exc)
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

    Raises:
        MemoryLockTimeout: if the scope lock cannot be acquired after retries.
    """
    new_observations = list(
        dict.fromkeys(o.strip() for o in new_observations if o and o.strip())
    )
    if not new_observations:
        return

    path = _scope_path(scope)
    lock = _scope_lock(path)
    if not _acquire_with_retry(lock, f"write(scope={scope!r})"):
        raise MemoryLockTimeout(f"write(scope={scope!r})")
    try:
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
                try:
                    elapsed = now - float(q.get("last_seen", now))
                except (TypeError, ValueError):
                    logger.warning(
                        "write: corrupt last_seen for quirk %r scope=%r — resetting",
                        text, scope,
                    )
                    elapsed = 0.0
                    q["last_seen"] = now
                try:
                    conf = float(q.get("confidence", 1.0))
                except (TypeError, ValueError):
                    conf = 1.0
                q["confidence"] = max(
                    0.1,
                    conf * _decay_factor(_HALF_LIFE_LONG_TERM_SECONDS, elapsed),
                )

        # Update or insert observations seen in this batch.
        for text in new_observations:
            if text in existing:
                try:
                    prev = float(existing[text].get("confidence", 1.0))
                except (TypeError, ValueError):
                    prev = 1.0
                try:
                    last_seen = float(existing[text].get("last_seen", now))
                except (TypeError, ValueError):
                    last_seen = now
                elapsed = now - last_seen
                decayed = prev * _decay_factor(_HALF_LIFE_LONG_TERM_SECONDS, elapsed)
                existing[text]["confidence"] = max(0.1, decayed + 1.0)
                existing[text]["last_seen"] = now
            else:
                existing[text] = {"text": text, "confidence": 1.0, "last_seen": now}

        data["quirks"] = list(existing.values())
        atomic_write_json(path, data)
    finally:
        lock.release()


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
    except Exception as exc:
        logger.warning(
            "extract_quirks failed exc_type=%s",
            type(exc).__name__,
            exc_info=True,
        )
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

    Raises:
        MemoryLockTimeout: if the scope lock cannot be acquired after retries.
    """
    path = _scope_path(scope)
    lock = _scope_lock(path)
    if not _acquire_with_retry(lock, f"log_run(scope={scope!r})"):
        raise MemoryLockTimeout(f"log_run(scope={scope!r})")
    try:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = _empty_scope_data()
        else:
            data = _empty_scope_data()

        run_entry = {
            "id": f"{int(time.time())}_{uuid.uuid4().hex[:6]}",
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
        try:
            data["lifetime_run_count"] = int(data.get("lifetime_run_count", 0)) + 1
        except (TypeError, ValueError):
            logger.warning(
                "log_run: corrupt lifetime_run_count for scope=%r — resetting to 1", scope
            )
            data["lifetime_run_count"] = 1

        atomic_write_json(path, data)
    finally:
        lock.release()


def consolidate(scope: str) -> bool:
    """Every CONSOLIDATE_EVERY real successful runs, synthesize a semantic profile.

    Filters applied before synthesis:
      - is_trial=True runs excluded (eval goals bias the profile)
      - success=False runs excluded (failed runs contaminate the synthesis)

    Returns True if consolidation ran.

    Raises:
        MemoryLockTimeout: if the scope lock cannot be acquired after retries.
    """
    path = _scope_path(scope)
    if not path.exists():
        return False

    lock = _scope_lock(path)
    if not _acquire_with_retry(lock, f"consolidate(scope={scope!r})"):
        raise MemoryLockTimeout(f"consolidate(scope={scope!r})")
    try:
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

        syn_count = len(synthesis_runs)
        if syn_count == 0 or syn_count % CONSOLIDATE_EVERY != 0:
            return False
        last_done = int(data.get("last_consolidated_synthesis_count", 0))
        if syn_count <= last_done:
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
            [q for q in data.get("quirks", []) if isinstance(q, dict)],
            key=lambda q: q.get("confidence", 0),
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
            data["last_consolidated_synthesis_count"] = syn_count
            atomic_write_json(path, data)
            return True
        except Exception as exc:
            logger.warning(
                "consolidate_failed scope=%r: %s", scope, exc, exc_info=True
            )
            return False
    finally:
        lock.release()


def patch_quirk(scope: str, quirk_text: str, delta: float) -> None:
    """Increment or decrement confidence on a single observation by delta.

    Positive delta confirms the observation after a successful run.
    Negative delta weakens an observation whose hypothesis wasn't confirmed.
    Confidence clamped to [0.1, 10.0]. No-op if quirk_text not found. Never raises.
    """
    path = _scope_path(scope)
    if not path.exists():
        return
    lock = _scope_lock(path)
    if not _acquire_with_retry(lock, f"patch_quirk(scope={scope!r})"):
        logger.warning("patch_quirk: lock timeout for scope=%r — skipping confidence update", scope)
        return
    try:
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
    except (json.JSONDecodeError, OSError, ValueError, TypeError) as exc:
        logger.warning("patch_quirk failed scope=%r: %s", scope, exc)
    finally:
        lock.release()


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
        f"║  Ballast Memory Report — {(scope[:36] + '...' if len(scope) > 39 else scope):<39}  ║",
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


# ---------------------------------------------------------------------------
# Per-domain threshold calibration (used by spec.py ClarificationPolicy)
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLD: float = 0.60
_THRESHOLD_KEY_PREFIX: str = "clarification_threshold:"


def get_domain_threshold(domain: str) -> float:
    """Return the current clarification threshold for this domain.

    Default: 0.60 (conservative — prefer asking over inferring).
    Updated by update_domain_threshold() after each run.

    The threshold is the maximum ambiguity axis score that the policy
    will infer through without asking. Below threshold → infer.
    At or above threshold → ask (up to 2 targeted questions).

    Args:
        domain: Domain key string (e.g. 'coding', 'data-analysis', 'writing').
    Returns:
        float in [0.0, 1.0]. Never raises.
    """
    path = _scope_path(f"{_THRESHOLD_KEY_PREFIX}{domain}")
    if not path.exists():
        return _DEFAULT_THRESHOLD
    try:
        with _scope_lock(path):
            data = json.loads(path.read_text(encoding="utf-8"))
        return float(data.get("threshold", _DEFAULT_THRESHOLD))
    except FileLockTimeout:
        logger.warning(
            "get_domain_threshold: lock timeout for domain=%r — returning default %.2f",
            domain, _DEFAULT_THRESHOLD,
        )
        return _DEFAULT_THRESHOLD
    except (json.JSONDecodeError, OSError, ValueError):
        return _DEFAULT_THRESHOLD


def update_domain_threshold(
    domain: str,
    clarification_asked: bool,
    run_succeeded: bool,
    max_ambiguity_score: float,
) -> None:
    """Calibrate the domain threshold from a completed run outcome.

    Update rule (moving average toward calibrated value):
      If clarification was NOT asked AND run succeeded:
          threshold += 0.05 * (max_ambiguity_score - threshold)
          → score was handled fine without asking; threshold can relax upward
      If clarification was NOT asked AND run failed:
          threshold -= 0.10 * threshold
          → should have asked; threshold tightens downward
      If clarification WAS asked AND run succeeded:
          threshold is unchanged (asking worked — no signal to change)
      If clarification WAS asked AND run failed:
          threshold is unchanged (failure was downstream of spec, not spec itself)

    Threshold clamped to [0.20, 0.90].
    Never raises. Uses same file-lock as all other memory operations.

    Args:
        domain: Domain key string.
        clarification_asked: Whether questions were surfaced before lock.
        run_succeeded: Whether the run completed successfully.
        max_ambiguity_score: The highest per-axis ambiguity score at lock time.
    """
    path = _scope_path(f"{_THRESHOLD_KEY_PREFIX}{domain}")
    try:
        with _scope_lock(path):
            if path.exists():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    current = float(raw.get("threshold", _DEFAULT_THRESHOLD))
                except (json.JSONDecodeError, OSError, ValueError):
                    current = _DEFAULT_THRESHOLD
            else:
                current = _DEFAULT_THRESHOLD

            if not clarification_asked and run_succeeded:
                updated = current + 0.05 * (max_ambiguity_score - current)
            elif not clarification_asked and not run_succeeded:
                updated = current - 0.10 * current
            else:
                updated = current

            updated = round(max(0.20, min(0.90, updated)), 4)
            data = {"threshold": updated, "domain": domain}
            atomic_write_json(path, data)
    except Exception as exc:
        logger.warning(
            "update_domain_threshold failed domain=%r: %s", domain, exc
        )
