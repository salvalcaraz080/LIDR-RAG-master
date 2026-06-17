from enum import Enum

from pydantic import BaseModel, Field, model_validator

# Shared constant: used by the out-of-scope validator AND injected into the prompt
# via the loader so system.j2 never hard-codes the prefix.
OUT_OF_SCOPE_PREFIX = "Out of scope:"


# Parámetros tipados del formulario (Enums str → el JSON viaja con el value exacto).
class ProjectType(str, Enum):
    mobile_app = "mobile_app"
    web_saas = "web_saas"
    internal_tool = "internal_tool"
    data_pipeline = "data_pipeline"


class DetailLevel(str, Enum):
    summary = "summary"
    medium = "medium"
    detailed = "detailed"


class OutputFormat(str, Enum):
    phases_table = "phases_table"
    narrative = "narrative"


class Phase(BaseModel):
    # Una fase del proyecto. duration_weeks ≥ 1 (nunca 0); el LLM define estos valores.
    name: str
    duration_weeks: int = Field(ge=1, le=52)
    cost_eur: int = Field(ge=0)
    confidence_pct: int = Field(ge=0, le=100)
    assumptions: list[str] = Field(default_factory=list)


class EstimationResult(BaseModel):
    """Structured output from the LLM — validated domain object."""

    summary: str
    total_duration_weeks: int = Field(ge=0)
    total_cost_eur: int = Field(ge=0)
    confidence_pct: int = Field(ge=0, le=100)
    phases: list[Phase] = Field(default_factory=list)

    @model_validator(mode="after")
    def total_must_match_sum_of_phases(self) -> "EstimationResult":
        # La aritmética es nuestra, no del LLM: computamos los totales como suma exacta
        # de las fases (los sobreescribimos). Sin fases (out-of-scope) se conservan en 0.
        if not self.phases:
            return self
        self.total_duration_weeks = sum(p.duration_weeks for p in self.phases)
        self.total_cost_eur = sum(p.cost_eur for p in self.phases)
        return self

    @model_validator(mode="after")
    def low_confidence_must_be_explicit(self) -> "EstimationResult":
        # Coherencia: una confianza muy baja obliga a marcar explícitamente out-of-scope
        # (si no, Instructor reintenta). Evita estimaciones "fantasma" con confianza ínfima.
        if self.confidence_pct < 30 and not self.summary.startswith(OUT_OF_SCOPE_PREFIX):
            raise ValueError(
                f"confidence_pct={self.confidence_pct} is below 30 but summary does "
                f"not start with '{OUT_OF_SCOPE_PREFIX}'. Either raise confidence or "
                "mark as out of scope."
            )
        return self


class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class EstimationResponse(BaseModel):
    # Contrato de salida: el resultado de dominio + metadatos de observabilidad/caché.
    result: EstimationResult
    model: str
    provider: str
    usage: TokenUsage
    cache_hit: bool
    prompt_version: str


class EstimationRequest(BaseModel):
    """Incoming request: a project description plus typed estimation parameters."""

    # description: texto libre acotado (20–2000); los 3 parámetros son Enums tipados.
    # Una longitud o un Enum inválidos → 422 automático antes de tocar el negocio.
    description: str = Field(
        ..., min_length=20, max_length=2000, description="Project description to estimate"
    )
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat
