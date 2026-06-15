"""Render versioned Jinja2 prompt templates into (system, user) message strings.

Receives explicit typed primitives (not the HTTP schema) so the prompt layer
stays decoupled from the HTTP edge — same principle as the service returning
plain dicts.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.schemas.estimations import OUT_OF_SCOPE_PREFIX

PROMPTS_DIR = Path(__file__).parent

_env = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    trim_blocks=True,
    lstrip_blocks=True,
    undefined=StrictUndefined,
)


def render_estimation_prompt(
    description: str,
    project_type: str,
    detail_level: str,
    output_format: str,
    version: str = "v1",
) -> tuple[str, str]:
    """Render the estimation system and user prompts. Returns (system, user)."""
    context = {
        "description": description,
        "project_type": project_type,
        "detail_level": detail_level,
        "output_format": output_format,
        "out_of_scope_prefix": OUT_OF_SCOPE_PREFIX,
    }
    system = _env.get_template(f"estimation/{version}/system.j2").render(context)
    user = _env.get_template(f"estimation/{version}/user.j2").render(context)
    return system, user
