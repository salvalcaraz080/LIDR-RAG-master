from enum import Enum

from pydantic import BaseModel, Field


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
    line_items = "line_items"
    narrative = "narrative"


class TokenUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class EstimationResponse(BaseModel):
    estimation: str = Field(..., description="Generated software estimation in markdown")
    model: str = Field(..., description="LLM model used")
    provider: str = Field(..., description="LLM provider used")
    usage: TokenUsage
    cache_hit: bool = Field(..., description="Whether the response was served from cache")


class EstimationRequest(BaseModel):
    """Incoming request: a project description plus typed estimation parameters."""

    description: str = Field(
        ..., min_length=20, max_length=2000, description="Project description to estimate"
    )
    project_type: ProjectType
    detail_level: DetailLevel
    output_format: OutputFormat
