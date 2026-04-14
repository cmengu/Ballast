"""ballast/core/cost.py — Per-agent stacking cost caps with hard global stop.

Public interface:
    HARD_CAP_USD              — absolute ceiling across all agents in a run ($300)
    AgentCapExceeded          — raised when an agent's per-agent cap is breached
    EscalationBudgetExceeded  — raised when escalation pool is exhausted
    HardCapExceeded           — raised when global run hard cap would be exceeded
    AgentCostGuard            — per-agent cap enforcer: check() then record()
    RunCostGuard              — global enforcer; owns all AgentCostGuards for a run

Usage:
    guard = RunCostGuard(hard_cap_usd=300.0)   # defaults to HARD_CAP_USD
    guard.register("worker", cap=0.10, escalation_pool=0.03)
    # at each node boundary in run_with_spec:
    guard.check_and_record("worker", node_cost)   # atomic: raises before committing

Invariant (projet-overview.md invariant 10):
    cost caps are enforced in code from day one.
    never a config option. always a hard stop.
"""
from __future__ import annotations

from dataclasses import dataclass, field

HARD_CAP_USD: float = 300.0


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class AgentCapExceeded(Exception):
    """Raised when an agent's per-agent spend cap would be exceeded."""

    def __init__(self, agent_id: str, spent: float, cap: float, estimated: float) -> None:
        self.agent_id = agent_id
        self.spent = spent
        self.cap = cap
        self.estimated = estimated
        super().__init__(
            f"agent {agent_id!r} cap exceeded: "
            f"spent={spent:.6f} + estimated={estimated:.6f} > cap={cap:.6f}"
        )


class EscalationBudgetExceeded(Exception):
    """Raised when an agent's escalation pool would be exceeded."""

    def __init__(self, agent_id: str, spent: float, pool: float, estimated: float) -> None:
        self.agent_id = agent_id
        self.spent = spent
        self.pool = pool
        self.estimated = estimated
        super().__init__(
            f"agent {agent_id!r} escalation pool exceeded: "
            f"spent={spent:.6f} + estimated={estimated:.6f} > pool={pool:.6f}"
        )


class HardCapExceeded(Exception):
    """Raised when the global run hard cap would be exceeded."""

    def __init__(self, total: float, estimated: float, hard_cap: float = HARD_CAP_USD) -> None:
        self.total = total
        self.estimated = estimated
        self.hard_cap = hard_cap
        super().__init__(
            f"hard run cap exceeded: "
            f"total={total:.6f} + estimated={estimated:.6f} > "
            f"hard_cap={hard_cap:.6f}"
        )


# ---------------------------------------------------------------------------
# AgentCostGuard — per-agent enforcer
# ---------------------------------------------------------------------------

@dataclass
class AgentCostGuard:
    """Tracks and enforces per-agent spend limits.

    _spent and _escalation_spent are internal state — excluded from __init__.
    Always call check() before record() to preserve the invariant.
    """

    agent_id: str
    agent_cap_usd: float
    escalation_pool_usd: float
    _spent: float = field(default=0.0, init=False, repr=False)
    _escalation_spent: float = field(default=0.0, init=False, repr=False)

    def check(self, estimated: float, is_escalation: bool = False) -> None:
        """Raise if recording `estimated` would exceed this agent's cap.

        Does NOT modify state. Always call before record().
        """
        if is_escalation:
            if self._escalation_spent + estimated > self.escalation_pool_usd:
                raise EscalationBudgetExceeded(
                    self.agent_id,
                    self._escalation_spent,
                    self.escalation_pool_usd,
                    estimated,
                )
        else:
            if self._spent + estimated > self.agent_cap_usd:
                raise AgentCapExceeded(
                    self.agent_id,
                    self._spent,
                    self.agent_cap_usd,
                    estimated,
                )

    def record(self, actual: float, is_escalation: bool = False) -> None:
        """Commit actual spend. Only call after check() has passed."""
        if is_escalation:
            self._escalation_spent += actual
        else:
            self._spent += actual

    @property
    def spent(self) -> float:
        """Total non-escalation spend recorded so far."""
        return self._spent

    @property
    def escalation_spent(self) -> float:
        """Total escalation spend recorded so far."""
        return self._escalation_spent


# ---------------------------------------------------------------------------
# RunCostGuard — global enforcer
# ---------------------------------------------------------------------------

class RunCostGuard:
    """Global cost enforcer for a single run. Owns all AgentCostGuards.

    Call register() for every agent_id before passing this guard to run_with_spec.
    check() enforces the global hard_cap_usd first, then the per-agent cap.
    record() advances both the global total and the per-agent total.
    """

    def __init__(self, hard_cap_usd: float = HARD_CAP_USD) -> None:
        self._agents: dict[str, AgentCostGuard] = {}
        self._total: float = 0.0
        self.hard_cap_usd: float = hard_cap_usd

    def register(self, agent_id: str, cap: float, escalation_pool: float) -> None:
        """Register an agent with its spend cap and escalation pool.

        Must be called before check() or record() for this agent_id.
        """
        self._agents[agent_id] = AgentCostGuard(agent_id, cap, escalation_pool)

    def check(
        self, agent_id: str, estimated: float, is_escalation: bool = False
    ) -> None:
        """Raise if recording `estimated` would exceed any cap.

        Order: global hard_cap_usd checked first → per-agent cap second.
        HardCapExceeded fires even if the agent-level cap would allow it.
        Raises KeyError if agent_id was not registered via register().
        Does NOT modify state.
        """
        if self._total + estimated > self.hard_cap_usd:
            raise HardCapExceeded(self._total, estimated, self.hard_cap_usd)
        self._agents[agent_id].check(estimated, is_escalation)

    def record(
        self, agent_id: str, actual: float, is_escalation: bool = False
    ) -> None:
        """Commit actual spend to global total and per-agent guard."""
        self._total += actual
        self._agents[agent_id].record(actual, is_escalation)

    @property
    def total_spent(self) -> float:
        """Total spend across all agents recorded so far."""
        return self._total

    def report(self) -> dict:
        """Return a spend summary dict suitable for logging or the dashboard."""
        return {
            "total_spent": round(self._total, 6),
            "hard_cap": self.hard_cap_usd,
            "remaining": round(self.hard_cap_usd - self._total, 6),
            "agents": {
                aid: {
                    "spent": round(g.spent, 6),
                    "escalation_spent": round(g.escalation_spent, 6),
                    "cap": g.agent_cap_usd,
                    "escalation_pool": g.escalation_pool_usd,
                }
                for aid, g in self._agents.items()
            },
        }
