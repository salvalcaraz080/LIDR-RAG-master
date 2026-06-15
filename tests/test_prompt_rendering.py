"""Prompt rendering tests — no API cost, just template logic."""

from app.prompts.loader import render_estimation_prompt
from app.schemas.estimations import (
    DetailLevel,
    EstimationRequest,
    OutputFormat,
    ProjectType,
)

# Distinctive marker per output format that must appear in the rendered system prompt.
OUTPUT_FORMAT_MARKERS = {
    OutputFormat.phases_table: "execution sequence",
    OutputFormat.narrative: "narrative",
}


def _render(request: EstimationRequest) -> tuple[str, str]:
    return render_estimation_prompt(
        description=request.description,
        project_type=request.project_type.value,
        detail_level=request.detail_level.value,
        output_format=request.output_format.value,
    )


def test_user_prompt_contains_description() -> None:
    request = EstimationRequest(
        description="A web SaaS platform to manage software subscriptions for SMEs.",
        project_type=ProjectType.web_saas,
        detail_level=DetailLevel.medium,
        output_format=OutputFormat.phases_table,
    )
    _, user = _render(request)
    assert request.description in user


def test_system_prompt_contains_requested_output_format_instructions() -> None:
    request = EstimationRequest(
        description="An internal tool to track warehouse inventory across sites.",
        project_type=ProjectType.internal_tool,
        detail_level=DetailLevel.summary,
        output_format=OutputFormat.narrative,
    )
    system, _ = _render(request)
    assert OUTPUT_FORMAT_MARKERS[OutputFormat.narrative] in system.lower()


def test_detailed_level_adds_assumptions_block() -> None:
    request = EstimationRequest(
        description="A mobile app for booking fitness classes with payments.",
        project_type=ProjectType.mobile_app,
        detail_level=DetailLevel.detailed,
        output_format=OutputFormat.phases_table,
    )
    system, _ = _render(request)
    assert "assumptions" in system.lower()
    # detailed level now prompts for per-phase confidence rather than an interval
    assert "confidence" in system.lower()


def test_summary_level_has_no_detailed_block() -> None:
    request = EstimationRequest(
        description="A data pipeline to ingest and normalize CSV exports nightly.",
        project_type=ProjectType.data_pipeline,
        detail_level=DetailLevel.summary,
        output_format=OutputFormat.phases_table,
    )
    system, _ = _render(request)
    # The "Additional Detail" section only appears for detailed level
    assert "Additional Detail" not in system


def test_every_output_format_renders_non_empty_instructions() -> None:
    """Every OutputFormat must produce a non-empty instruction block."""
    for output_format, marker in OUTPUT_FORMAT_MARKERS.items():
        request = EstimationRequest(
            description="A generic project description long enough to pass validation.",
            project_type=ProjectType.web_saas,
            detail_level=DetailLevel.medium,
            output_format=output_format,
        )
        system, _ = _render(request)
        assert marker in system, f"missing instructions for {output_format.value}"
