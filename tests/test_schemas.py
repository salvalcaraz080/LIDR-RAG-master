"""Schema validator tests — no API cost."""

import pytest
from pydantic import ValidationError

from app.schemas.estimations import (
    OUT_OF_SCOPE_PREFIX,
    EstimationResult,
    Phase,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _phase(name="Backend", weeks=4, cost=5000, confidence=80):
    return Phase(name=name, duration_weeks=weeks, cost_eur=cost, confidence_pct=confidence)


def _result(**overrides):
    defaults = dict(
        summary="A well-scoped project.",
        total_duration_weeks=4,
        total_cost_eur=5000,
        confidence_pct=80,
        phases=[_phase()],
    )
    defaults.update(overrides)
    return EstimationResult(**defaults)


# ---------------------------------------------------------------------------
# total_must_match_sum_of_phases — totals are computed, not validated
# ---------------------------------------------------------------------------

def test_totals_computed_from_phases():
    # LLM provides wrong totals; validator overwrites them with the real sums.
    r = EstimationResult(
        summary="A well-scoped project.",
        total_duration_weeks=99,  # wrong — should become 4
        total_cost_eur=99999,     # wrong — should become 5000
        confidence_pct=80,
        phases=[_phase()],
    )
    assert r.total_duration_weeks == 4
    assert r.total_cost_eur == 5000


def test_totals_sum_multiple_phases():
    phases = [_phase("A", weeks=2, cost=2000), _phase("B", weeks=3, cost=3000)]
    r = EstimationResult(
        summary="Two phases.",
        total_duration_weeks=0,
        total_cost_eur=0,
        confidence_pct=80,
        phases=phases,
    )
    assert r.total_duration_weeks == 5
    assert r.total_cost_eur == 5000


def test_no_phases_skips_computation():
    # When phases=[], validator returns early; LLM-provided totals are kept.
    r = EstimationResult(
        summary=f"{OUT_OF_SCOPE_PREFIX} not enough info.",
        total_duration_weeks=0,
        total_cost_eur=0,
        confidence_pct=0,
        phases=[],
    )
    assert r.total_duration_weeks == 0
    assert r.total_cost_eur == 0


# ---------------------------------------------------------------------------
# low_confidence_must_be_explicit
# ---------------------------------------------------------------------------

def test_low_confidence_with_out_of_scope_prefix_passes():
    r = EstimationResult(
        summary=f"{OUT_OF_SCOPE_PREFIX} no software project detected.",
        total_duration_weeks=0,
        total_cost_eur=0,
        confidence_pct=0,
        phases=[],
    )
    assert r.confidence_pct == 0


def test_low_confidence_without_prefix_raises():
    with pytest.raises(ValidationError, match="Out of scope"):
        EstimationResult(
            summary="This project seems vague.",
            total_duration_weeks=4,
            total_cost_eur=5000,
            confidence_pct=20,  # < 30 without prefix → error
            phases=[_phase()],
        )


def test_confidence_30_without_prefix_passes():
    # Boundary: exactly 30 is OK without prefix
    r = _result(confidence_pct=30)
    assert r.confidence_pct == 30
