"""ballast/core/spec.py — Intent grounding layer.

Public interface:
    parse_spec(path)        — reads spec.md, returns draft SpecModel (locked_at='')
    score_specificity(spec) — LLM: how verifiable is this spec? 0.0–1.0
    clarify(spec)           — LLM: enrich vague fields; raises SpecTooVague if impossible
    lock(spec)              — stamps version_hash + locked_at; returns immutable-by-convention copy
    is_locked(spec)         — True if locked_at is non-empty

Invariants (from projet-overview.md):
    1. spec locks before any agent executes — enforce with is_locked() guard in callers
    2. spec version_hash travels with every job — sha256(json non-harness fields)[:16], set at lock()
    3. locked spec is immutable by convention — never mutate a SpecModel after lock()

spec.md format:
    # spec v1
    ## intent
    one sentence goal
    ## success criteria
    - criterion 1
    ## constraints
    - constraint 1
    ## escalation threshold
    drift confidence floor: 0.4
    timeout before CEO decides: 300 seconds
    ## tools allowed
    - tool_name
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import List, Literal

import anthropic
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# SpecDelta — diff between two locked SpecModel versions
# ---------------------------------------------------------------------------

class HarnessProfile(BaseModel):
    """Model-adaptive harness parameters.

    Excluded from version_hash — tuning changes do not invalidate the spec contract.
    Architectural invariants (spec fields) never change; these tune execution to model capability.
    """
    model: Literal["sonnet", "opus"] = "sonnet"
    context_window_size: int = 8
    checkpoint_every_n_nodes: int = 10
    enable_layer2_judge: bool = True
    escalation_timeout_seconds: int = 300
    enable_environment_probe: bool = True
    spec_poll_interval_nodes: int = 1

    @classmethod
    def for_model(cls, model: str) -> "HarnessProfile":
        if model == "opus":
            return cls(
                model="opus",
                context_window_size=16,
                checkpoint_every_n_nodes=20,
                enable_layer2_judge=False,
                escalation_timeout_seconds=600,
                enable_environment_probe=True,
                spec_poll_interval_nodes=1,
            )
        return cls()


class SpecDelta(BaseModel):
    """Diff between two locked SpecModel versions.

    Produced by SpecModel.diff(other).
    Consumed by hook.py to inject spec changes between Agent.iter nodes.
    """
    from_hash: str
    to_hash: str
    added_constraints: List[str] = Field(default_factory=list)
    removed_constraints: List[str] = Field(default_factory=list)
    added_tools: List[str] = Field(default_factory=list)
    removed_tools: List[str] = Field(default_factory=list)
    intent_changed: bool = False

    def as_injection(self) -> str:
        """Return a plain-text string the agent reads as mid-run context."""
        lines = [f"[BALLAST SPEC UPDATE: {self.from_hash[:8]} → {self.to_hash[:8]}]"]
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

class SpecModel(BaseModel):
    """Specification contract. Draft when locked_at=''; locked otherwise.

    Do not construct directly — use parse_spec() or build fields explicitly,
    then call lock() before passing to any agent execution function.

    version_hash: sha256(all non-harness fields, json-sorted)[:16]. Set by lock(). Empty = draft.
    harness: excluded from version_hash — tuning changes do not invalidate the contract.
    """
    version_hash: str = Field(
        default="",
        description="sha256(all non-harness fields, json-sorted)[:16]. Set by lock(). Empty = draft.",
    )
    intent: str = Field(
        description="One sentence: what the agent is trying to achieve.",
    )
    success_criteria: List[str] = Field(
        default_factory=list,
        description="Verifiable list of done conditions.",
    )
    constraints: List[str] = Field(
        default_factory=list,
        description="What the agent must never do.",
    )
    irreversible_actions: List[str] = Field(
        default_factory=list,
        description="Tool names whose effects cannot be undone. Triggers hard interrupt in guardrails.",
    )
    drift_threshold: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Minimum acceptable drift score. Below this → DriftDetected.",
    )
    allowed_tools: List[str] = Field(
        default_factory=list,
        description="Tool names the agent may call. Empty = all tools allowed.",
    )
    scope: str = Field(
        default="",
        description="Optional scope constraint (e.g. 'this repo only'). Empty = unconstrained.",
    )
    locked_at: str = Field(
        default="",
        description="ISO-8601 UTC timestamp set by lock(). Empty = draft.",
    )
    parent_hash: str = Field(
        default="",
        description="version_hash of the spec this was derived from. Empty = root spec.",
    )
    harness: HarnessProfile = Field(
        default_factory=HarnessProfile,
        description="Execution tuning parameters. Excluded from version_hash.",
    )

    def diff(self, other: "SpecModel") -> "SpecDelta":
        """Return a SpecDelta describing what changed from self to other.

        Caller: trajectory.py — active_spec.diff(new_spec) at every node boundary.
        Both specs should be locked before calling diff().
        """
        return SpecDelta(
            from_hash=self.version_hash,
            to_hash=other.version_hash,
            added_constraints=[c for c in other.constraints if c not in self.constraints],
            removed_constraints=[c for c in self.constraints if c not in other.constraints],
            added_tools=[t for t in other.allowed_tools if t not in self.allowed_tools],
            removed_tools=[t for t in self.allowed_tools if t not in other.allowed_tools],
            intent_changed=self.intent != other.intent,
        )


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class SpecParseError(Exception):
    """Raised by parse_spec() when required sections are missing or file not found."""


class SpecAlreadyLocked(Exception):
    """Raised by lock() when spec.locked_at is already set."""


class SpecTooVague(Exception):
    """Raised by clarify() when LLM cannot infer required fields."""

    def __init__(self, missing_fields: list[str]) -> None:
        self.missing_fields = missing_fields
        super().__init__(
            f"Spec too vague to enrich automatically — "
            f"unclear fields: {missing_fields}"
        )


# ---------------------------------------------------------------------------
# Anthropic client (lazy singleton)
# ---------------------------------------------------------------------------

_spec_client: "anthropic.Anthropic | None" = None
_SPEC_MODEL = "claude-sonnet-4-6"


def _get_client() -> "anthropic.Anthropic":
    global _spec_client
    if _spec_client is None:
        _spec_client = anthropic.Anthropic()
    return _spec_client


# ---------------------------------------------------------------------------
# parse_spec — reads spec.md
# ---------------------------------------------------------------------------

def parse_spec(path: str) -> SpecModel:
    """Read a spec.md file and return a draft SpecModel (locked_at='', version_hash='').

    Parses the ## intent, ## success criteria, ## constraints,
    ## escalation threshold, and ## tools allowed sections.

    Raises SpecParseError if:
        - file not found
        - ## intent section is missing or empty
        - ## success criteria section is missing or has no bullet items
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        raise SpecParseError(f"spec file not found: {path}")

    def _section(name: str) -> str:
        """Text between ## name and the next ## heading (or EOF). Case-insensitive."""
        pattern = rf"##\s+{re.escape(name)}\s*\n(.*?)(?=\n##\s|\Z)"
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        return m.group(1).strip() if m else ""

    def _bullets(section_text: str) -> list[str]:
        """Return lines starting with '-', stripped."""
        items = []
        for line in section_text.splitlines():
            line = line.strip()
            if line.startswith("-"):
                item = line.lstrip("-").strip()
                if item:
                    items.append(item)
        return items

    intent = _section("intent")
    if not intent:
        raise SpecParseError(
            "spec.md is missing required ## intent section"
        )

    criteria_text = _section("success criteria")
    success_criteria = _bullets(criteria_text)
    if not success_criteria:
        raise SpecParseError(
            "spec.md ## success criteria section is missing or has no bullet items"
        )

    constraints = _bullets(_section("constraints"))
    allowed_tools = _bullets(_section("tools allowed"))

    drift_threshold = 0.4
    escalation_timeout = 300
    threshold_text = _section("escalation threshold")
    if threshold_text:
        for line in threshold_text.splitlines():
            line_lower = line.lower()
            if "drift confidence floor" in line_lower:
                m = re.search(r"[\d.]+", line)
                if m:
                    try:
                        drift_threshold = float(m.group())
                    except ValueError:
                        pass
            elif "timeout" in line_lower:
                m = re.search(r"\d+", line)
                if m:
                    try:
                        escalation_timeout = int(m.group())
                    except ValueError:
                        pass

    return SpecModel(
        version_hash="",
        intent=intent,
        success_criteria=success_criteria,
        constraints=constraints,
        irreversible_actions=[],
        drift_threshold=drift_threshold,
        allowed_tools=allowed_tools,
        scope="",
        locked_at="",
        harness=HarnessProfile(escalation_timeout_seconds=escalation_timeout),
    )


# ---------------------------------------------------------------------------
# score_specificity — LLM-based single float
# ---------------------------------------------------------------------------

_SPECIFICITY_SYSTEM = (
    "You are a specification quality reviewer for an AI agent system. "
    "Score how specific and verifiable a given spec is. "
    "A good spec has a clear intent, measurable success criteria, and unambiguous constraints. "
    "A bad spec is vague, unmeasurable, or interpretable multiple ways."
)

_SPECIFICITY_TOOL = {
    "name": "score_specificity",
    "description": "Score how specific and verifiable this spec is.",
    "input_schema": {
        "type": "object",
        "properties": {
            "score": {
                "type": "number",
                "description": (
                    "0.0 = completely vague/unverifiable, "
                    "1.0 = fully specific and verifiable"
                ),
            },
            "rationale": {
                "type": "string",
                "description": "One sentence: why this score.",
            },
            "vague_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Fields that are too vague: any of "
                    "intent, success_criteria, constraints"
                ),
            },
        },
        "required": ["score", "rationale", "vague_fields"],
    },
}


def score_specificity(spec: SpecModel) -> float:
    """LLM-based: how specific and verifiable is this spec?

    Returns float in [0.0, 1.0]. Fail-safe: returns 0.5 on any error.
    Never raises.
    """
    criteria = "\n".join(f"  - {c}" for c in spec.success_criteria)
    constraints = "\n".join(f"  - {c}" for c in spec.constraints)
    prompt = (
        f"Intent: {spec.intent}\n"
        f"Success criteria:\n{criteria}\n"
        f"Constraints:\n{constraints}"
    )
    try:
        response = _get_client().messages.create(
            model=_SPEC_MODEL,
            max_tokens=200,
            system=_SPECIFICITY_SYSTEM,
            tools=[_SPECIFICITY_TOOL],
            tool_choice={"type": "tool", "name": "score_specificity"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use":
                return max(0.0, min(1.0, float(block.input.get("score", 0.5))))
    except Exception:
        pass
    return 0.5


# ---------------------------------------------------------------------------
# clarify — LLM enrichment for vague specs
# ---------------------------------------------------------------------------

_CLARIFY_SYSTEM = (
    "You are a spec enrichment assistant for an AI agent system. "
    "Given a vague specification, enrich it with specific, measurable details. "
    "If a required field is impossible to clarify without human input, "
    "list it in unclear_fields."
)

_CLARIFY_TOOL = {
    "name": "enrich_spec",
    "description": "Return an enriched version of the spec.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "description": "Enriched, specific one-sentence intent.",
            },
            "success_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Enriched list of verifiable done conditions.",
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Enriched constraints (may be empty if none needed).",
            },
            "unclear_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Fields that cannot be enriched without human input. "
                    "Use field names: intent, success_criteria, constraints."
                ),
            },
        },
        "required": ["intent", "success_criteria", "constraints", "unclear_fields"],
    },
}


def clarify(spec: SpecModel) -> SpecModel:
    """LLM: enrich vague fields in a draft SpecModel.

    Returns enriched SpecModel (still a draft — locked_at='').
    Never mutates the input spec — returns a new SpecModel.
    Raises SpecTooVague(missing_fields) if LLM cannot infer required fields.

    Caller decides when to call this — typically when score_specificity() < 0.6.
    """
    criteria = "\n".join(f"  - {c}" for c in spec.success_criteria)
    constraints_text = "\n".join(f"  - {c}" for c in spec.constraints)
    prompt = (
        f"Intent: {spec.intent}\n"
        f"Success criteria:\n{criteria}\n"
        f"Constraints:\n{constraints_text}\n\n"
        "Enrich this spec. Make intent specific and measurable. "
        "Add concrete success criteria if vague. "
        "If you cannot determine what the agent should do, list unclear_fields."
    )
    try:
        response = _get_client().messages.create(
            model=_SPEC_MODEL,
            max_tokens=400,
            system=_CLARIFY_SYSTEM,
            tools=[_CLARIFY_TOOL],
            tool_choice={"type": "tool", "name": "enrich_spec"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use":
                raw = block.input
                unclear = raw.get("unclear_fields", [])
                if unclear:
                    raise SpecTooVague(unclear)
                return SpecModel(
                    version_hash="",
                    intent=raw.get("intent", spec.intent),
                    success_criteria=raw.get("success_criteria", spec.success_criteria),
                    constraints=raw.get("constraints", spec.constraints),
                    irreversible_actions=spec.irreversible_actions,
                    drift_threshold=spec.drift_threshold,
                    allowed_tools=spec.allowed_tools,
                    scope=spec.scope,
                    locked_at="",
                    harness=spec.harness,
                )
    except SpecTooVague:
        raise
    except Exception:
        pass
    return spec  # Fail-safe: return original unchanged


# ---------------------------------------------------------------------------
# lock — stamps version + locked_at
# ---------------------------------------------------------------------------

def lock(spec: SpecModel) -> SpecModel:
    """Stamp version_hash and locked_at onto a draft SpecModel. Return locked copy.

    version_hash = sha256(json.dumps of all non-harness non-identity fields, sort_keys=True)[:16]
    locked_at = UTC ISO-8601 timestamp ending in 'Z'

    Fields excluded from hash: version_hash (being computed), locked_at (being set), harness (tuning).
    Changing harness parameters does NOT change spec identity.

    Raises SpecAlreadyLocked if spec.locked_at is already set.
    Returns a new SpecModel — input is never mutated.
    After lock(), treat the returned spec as immutable (invariants 1 + 2).
    """
    if spec.locked_at:
        raise SpecAlreadyLocked(
            f"spec already locked at {spec.locked_at} "
            f"(version_hash={spec.version_hash})"
        )

    hashable = spec.model_dump(exclude={"version_hash", "locked_at", "harness"})
    raw = json.dumps(hashable, sort_keys=True)
    version_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]
    locked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    return spec.model_copy(update={"version_hash": version_hash, "locked_at": locked_at})


# ---------------------------------------------------------------------------
# is_locked — guard used by callers before execution
# ---------------------------------------------------------------------------

def is_locked(spec: SpecModel) -> bool:
    """Return True if this spec has been locked (locked_at is non-empty).

    Callers must check is_locked(spec) before passing spec to any agent
    execution function. Invariant: no agent executes without a locked spec.
    """
    return bool(spec.locked_at)
