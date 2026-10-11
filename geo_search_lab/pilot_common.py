"""Small-pilot contracts and artifact I/O. No network or model side effects."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "data/benchmarks/commercial_pilot_v1.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_path(directory: Path, name: str) -> Path:
    path = (directory / name).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("Artifact path escapes its snapshot directory")
    return path


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value, *, replace=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w" if replace else "x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", value):
        raise ValueError("Invalid artifact identifier")
    return value


def load_benchmark(path=BENCHMARK):
    bench = read_json(Path(path))
    ids = set()
    for case in bench["cases"]:
        safe_id(case["id"])
        if case["id"] in ids or not isinstance(case["query"], str) or not case["query"].strip():
            raise ValueError("Duplicate ID or empty query")
        ids.add(case["id"])
        if not case["facets"] or not case["topic_group"]:
            raise ValueError("Cases need facets and a topic group")
    return bench


def case_by_id(query_id, path=BENCHMARK):
    return next(case for case in load_benchmark(path)["cases"] if case["id"] == query_id)
