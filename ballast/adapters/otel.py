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
    try:
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
            "emit_drift_span failed node=%d run_id=%s",
            node_index, run_id,
            exc_info=True,
        )
