"""Input guardrails — domain-agnostic.

Validates raw user text before it reaches the LLM. Three layers:
  1. OpenAI Moderation API (via litellm.moderation) — hate, self-harm, violence, etc.
  2. Prompt-injection heuristics — Markdown-delimiter hijacking + social-engineering phrases.

Respects GUARDRAILS_ENFORCE from Settings:
  True  → raise InputGuardrailError on any trigger (default in production).
  False → log warning only, never raise (log-only mode for development/monitoring).

PENDING: add a metrics/tracker over guardrails warning logs once volume warrants it.
"""

import re

import litellm
import structlog

from app.config import get_settings

log = structlog.get_logger()

# Markdown delimiters used by system.j2 — injection attempts try to inject these
# to hijack the system prompt structure.
_MARKDOWN_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"^#{1,3}\s+role", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^#{1,3}\s+output\s+format", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^#{1,3}\s+reference\s+examples", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^#{1,3}\s+scope", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^#{1,3}\s+pricing\s+rules", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^#{1,3}\s+additional\s+detail", re.IGNORECASE | re.MULTILINE),
]

# Frases típicas de prompt-injection / jailbreak que intentan reescribir el comportamiento.
_SOCIAL_ENGINEERING_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore (all |previous |above )?instructions?", re.IGNORECASE),
    re.compile(r"disregard (all |previous |above )?instructions?", re.IGNORECASE),
    re.compile(r"you are now", re.IGNORECASE),
    re.compile(r"new (system )?prompt", re.IGNORECASE),
    re.compile(r"act as (if you are|a )", re.IGNORECASE),
    re.compile(r"forget (everything|your instructions)", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"do anything now", re.IGNORECASE),
]


class InputGuardrailError(Exception):
    """Raised when input fails a guardrail check and enforcement is enabled."""


def _enforce(reason: str, detail: str) -> None:
    """Log the trigger and raise if enforce mode is on."""
    log.warning("guardrail_triggered", reason=reason, detail=detail)
    if get_settings().guardrails_enforce:
        raise InputGuardrailError(f"{reason}: {detail}")


async def validate_input(description: str) -> None:
    """Run all input guardrail layers. Raises InputGuardrailError if enforced."""

    # Layer 1: OpenAI Moderation API via litellm (only coupling to OpenAI, contained here)
    try:
        mod_response = litellm.moderation(input=description)
        results = mod_response.results if hasattr(mod_response, "results") else []
        if results and results[0].flagged:
            categories = [k for k, v in vars(results[0].categories).items() if v]
            _enforce("moderation_flagged", f"categories={categories}")
    except InputGuardrailError:
        raise
    except Exception as exc:
        # Moderation API failure is non-fatal: log and continue
        log.warning("moderation_api_failed", error=str(exc))

    # Layer 2a: Markdown delimiter injection
    for pattern in _MARKDOWN_INJECTION_PATTERNS:
        if pattern.search(description):
            _enforce("markdown_injection", f"pattern={pattern.pattern!r}")
            break

    # Layer 2b: Social engineering phrases
    for pattern in _SOCIAL_ENGINEERING_PATTERNS:
        if pattern.search(description):
            _enforce("social_engineering", f"pattern={pattern.pattern!r}")
            break
