"""Public API v1 inputs. No caller-provided filesystem path or provider endpoint."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Label = Annotated[str, Field(min_length=1, max_length=160)]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")]


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["planner"] = "planner"
    prompt: str = Field(min_length=1, max_length=12000)
    title: str = Field(default="", max_length=120)
    experiment_id: Identifier = "default"
    profile_id: Literal["qwen3-8b-planner-pilot-v1"] = "qwen3-8b-planner-pilot-v1"
    input_mode: Literal["snapshot", "empty_ablation"]
    snapshot_id: Identifier | None = None
    facets: list[Label] = Field(default_factory=list, max_length=20)
    constraints: list[Label] = Field(default_factory=list, max_length=20)
    client_request_id: Identifier | None = None

    @field_validator("prompt")
    @classmethod
    def nonempty_prompt(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("问题不能为空或包含空字符")
        return value

    @field_validator("facets", "constraints")
    @classmethod
    def unique_labels(cls, value):
        if any(not v.strip() for v in value) or len(set(value)) != len(value):
            raise ValueError("评价维度必须非空且不重复")
        return value

    @model_validator(mode="after")
    def explicit_seed(self):
        if (self.input_mode == "snapshot") != (self.snapshot_id is not None):
            raise ValueError("快照模式需要 snapshot_id；无初搜实验不能指定快照")
        return self


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    covered: bool | None
    query_indices: list[Annotated[int, Field(ge=1)]] = Field(default_factory=list, max_length=30)
    note: str = Field(default="", max_length=1200)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = "1.0"
    expected_revision: int = Field(ge=0)
    reviewer: str = Field(min_length=1, max_length=100)
    facets: dict[str, ReviewItem]
    constraints: dict[str, ReviewItem]
    notes: str = Field(default="", max_length=4000)

    @field_validator("reviewer")
    @classmethod
    def nonempty_reviewer(cls, value):
        if not value.strip():
            raise ValueError("需要填写标注者名称")
        return value


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_ids: list[Identifier] = Field(min_length=1, max_length=200)

    @field_validator("run_ids")
    @classmethod
    def unique_runs(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("同一次运行不能重复计入评价")
        return value


class ResultInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str
    prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    facets: list[str]
    constraints: list[str]
    input_mode: Literal["snapshot", "empty_ablation"]
    snapshot_id: str | None


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sub_queries: list[str] | None
    planned_execution_queries: list[str] | None
    answer: None
    citations: None


class PlannerMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    normalized_query_count: int | None
    exact_duplicate_count: int | None
    strict_json: bool | None
    initial_result_count: int | None
    model_requests: int
    model_tokens: int | None
    model_http_seconds: float | None
    replay_exact_match: bool | None


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=0)
    media_type: str


class ResultDocument(BaseModel):
    """Stable serialized boundary for readers and future evaluation services."""
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"]
    run_id: Identifier
    kind: Literal["planner"]
    status: Literal["succeeded", "failed"]
    mode: Literal["live_local_llm", "replay"]
    experiment_id: str
    parent_run_id: str | None
    started_at: str
    finished_at: str
    input: ResultInput
    configuration: dict
    output: PlannerOutput
    metrics: PlannerMetrics
    evaluation: dict
    provenance: dict
    error: dict | None
    warnings: list[str]
    artifacts: list[ArtifactRecord]


class Conflict(ValueError):
    """A valid request conflicts with an existing immutable record."""
