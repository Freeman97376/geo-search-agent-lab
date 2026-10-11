from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Project(Contract):
    name: str = Field(min_length=1, max_length=120)
    website: str = Field(min_length=1, max_length=250)
    competitors: list[str] = Field(default_factory=list, max_length=20)
    pages: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("website")
    @classmethod
    def domain(cls, value):
        parsed = urlsplit(value if "://" in value else "https://" + value)
        host = (parsed.hostname or "").lower().encode("idna").decode()
        if (parsed.scheme not in ("http", "https") or parsed.username or parsed.password
                or parsed.port or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or "." not in host or any(not (c.isalnum() or c in "-.") for c in host)):
            raise ValueError("网站请填域名，例如 example.com；具体页面请放入页面列表")
        return host.rstrip(".")

    @field_validator("competitors")
    @classmethod
    def domains(cls, values):
        return list(dict.fromkeys(cls.domain(v) for v in values))

    @field_validator("pages")
    @classmethod
    def urls(cls, values):
        for value in values:
            parsed = urlsplit(value)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("页面必须是完整 http(s) URL，不能含凭据")
        return list(dict.fromkeys(values))


class Demand(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    text: str = Field(min_length=1, max_length=8000)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("需求不能为空或包含 NUL")
        return value


class Planning(Contract):
    project_id: str
    demands: list[Demand] = Field(min_length=1, max_length=30)
    repeats: int = Field(default=5, ge=1, le=10)
    temperatures: list[Literal[0, 0.4]] = Field(default_factory=lambda: [0, 0.4], min_length=1, max_length=2)

    @field_validator("demands")
    @classmethod
    def unique(cls, values):
        if len({v.id for v in values}) != len(values):
            raise ValueError("同批需求 ID 不得重复")
        return values

    @field_validator("temperatures")
    @classmethod
    def unique_temperatures(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("温度不得重复")
        return values


class SearchSettings(Contract):
    gl: str = Field(default="us", pattern=r"^[a-z]{2}$")
    hl: str = Field(default="en", pattern=r"^[a-z]{2}(-[a-zA-Z]{2})?$")
    location: str = Field(default="", max_length=160)
    device: Literal["desktop", "mobile", "tablet"] = "desktop"
    google_domain: str = Field(default="google.com", pattern=r"^google\.[a-z.]{2,20}$")


class ManualQuery(Demand):
    demand_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    demand_text: str = Field(min_length=1, max_length=8000)


class Search(Contract):
    project_id: str
    manual: list[ManualQuery] = Field(default_factory=list, max_length=300)
    planning_batch_id: str | None = None
    settings: SearchSettings = Field(default_factory=SearchSettings)
    request_cap: int = Field(default=10, ge=1, le=200)


class Key(Contract):
    key: str = Field(max_length=300)

    @field_validator("key")
    @classmethod
    def valid(cls, value):
        if any(not (c.isascii() and (c.isalnum() or c in "_-")) for c in value):
            raise ValueError("密钥格式不正确")
        return value
