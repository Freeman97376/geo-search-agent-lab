"""Strict research v1 contracts. Never coerce malformed model labels."""
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

Id = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")]
Text = Annotated[str, Field(min_length=1, max_length=12000)]
Category = Literal["fact", "discovery", "screening", "comparison", "time_sensitive", "linked"]
Critical = Literal["entity_relationship", "negation", "hard_constraint", "unsupported_complete"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RubricItem(Strict):
    item_id: Id
    kind: Literal["need", "constraint"]
    text: Text


class Case(Strict):
    family_id: Id
    category: Category
    sector: Literal["manufacturing", "software", "consumer", "logistics", "services"]
    prompt: Text
    rubric: list[RubricItem] = Field(min_length=1, max_length=40)
    provenance: Text
    source_urls: list[str] = Field(default_factory=list, max_length=10)
    source_status: Literal["pending_verification", "verified"] = "pending_verification"
    captured_at: str | None = None
    reference_at: str
    review_status: Literal["draft", "single_reviewed"] = "draft"

    @model_validator(mode="after")
    def valid_items(self):
        from datetime import datetime
        from urllib.parse import urlsplit
        ids = [v.item_id for v in self.rubric]
        if len(set(ids)) != len(ids) or not any(v.kind == "need" for v in self.rubric):
            raise ValueError("评分项 ID 必须唯一，且至少包含一个信息需求")
        if not self.prompt.strip():
            raise ValueError("问题不能为空")
        for text in (self.reference_at, self.captured_at):
            if text is not None and datetime.fromisoformat(text.replace("Z", "+00:00")).tzinfo is None:
                raise ValueError("时间必须包含时区")
        for url in self.source_urls:
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("公开依据必须为不包含凭据的 HTTP(S) URL")
        return self


class StudyInput(Strict):
    study_id: Id
    version: Id
    seed: int = Field(ge=0, le=2**31-1)
    data_kind: Literal["fixture", "imported"]
    cases: list[Case] = Field(min_length=30, max_length=30)


class OutputInput(Strict):
    source_run_id: Id
    profile_id: Id
    model_digest: Text
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    started_at: str
    finished_at: str
    status: Literal["succeeded", "failed", "unassessable"]
    queries: list[Text] = Field(default_factory=list, max_length=30)
    query_origin: Literal["model_only"] = "model_only"
    raw_output: str = Field(default="", max_length=100000)
    error: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def output_state(self):
        from datetime import datetime
        times = [datetime.fromisoformat(v.replace("Z", "+00:00")) for v in (self.started_at, self.finished_at)]
        if any(v.tzinfo is None for v in times) or times[1] < times[0]:
            raise ValueError("需要带时区且顺序正确的开始/结束时间")
        if self.status == "succeeded" and (not self.queries or any(not q.strip() for q in self.queries)):
            raise ValueError("成功输出必须有模型生成的非空 query")
        if self.status != "succeeded" and (self.queries or not self.error):
            raise ValueError("失败或不可评输出需要原因，不能携带可评分 query")
        return self


class Evidence(Strict):
    query_index: int = Field(ge=1, le=30)
    quote: Text


class Label(Strict):
    item_id: Id
    coverage: Literal["complete", "partial", "missing", "uncertain"]
    conflict: Literal["present", "absent", "uncertain"]
    evidence: list[Evidence] = Field(default_factory=list, max_length=30)
    note: str = Field(default="", max_length=2000)


class Grade(Strict):
    labels: list[Label] = Field(min_length=1, max_length=40)


class Review(Grade):
    expected_revision: int = Field(ge=0)
    reviewer_id: Id


class ErrorAnnotation(Strict):
    attempt_id: Id
    item_id: Id
    categories: list[Critical] = Field(min_length=1, max_length=4)
    explanation: Text

    @field_validator("categories")
    @classmethod
    def distinct(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("错误分类不能重复")
        return values


class FixtureResponse(Strict):
    content: str = Field(default="", max_length=100000)
    finish_reason: Literal["stop", "length"] = "stop"
    input_tokens: int = Field(default=100, ge=0, le=100000)
    output_tokens: int = Field(default=100, ge=0, le=4096)
    fault: Literal["none", "timeout", "network", "rate_limit", "account"] = "none"


def strict_json(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError("重复 JSON 字段")
            result[k] = v
        return result
    def invalid(value):
        raise ValueError("非法 JSON 数值")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def validate_grade(value, case, output):
    grade = Grade.model_validate(value)
    expected = {item["item_id"] for item in case["rubric"]}
    ids = [label.item_id for label in grade.labels]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError("评分项必须完整、唯一并匹配冻结规则")
    for label in grade.labels:
        if (label.coverage in ("complete", "partial") or label.conflict == "present") and not label.evidence:
            raise ValueError("覆盖或冲突判断需要 query 证据")
        for ev in label.evidence:
            if ev.query_index > len(output["queries"]) or not ev.quote.strip() or ev.quote not in output["queries"][ev.query_index - 1]:
                raise ValueError("证据必须逐字出现在指定模型 query 中")
    return grade.model_dump()
