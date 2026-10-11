"""Import the real pinned checkout without modifying or copying its algorithms."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from geo_search_lab.pilot_common import ROOT, read_json, sha256


def prepare_upstream():
    directory = ROOT / "third_party/gpt-researcher"
    lock = next(r for r in read_json(ROOT / "upstreams.lock.json")["repositories"]
                if r["directory"] == "gpt-researcher")
    def git(*args):
        return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()
    if git("rev-parse", "HEAD") != lock["commit"]:
        raise ValueError("Upstream SHA differs from lock; create a new experiment version")
    if git("status", "--porcelain"):
        raise ValueError("Upstream checkout must be clean for this pilot")
    # Do not load neighboring project secrets or enable ambient trace exporters.
    os.environ.update(PYTHON_DOTENV_DISABLED="1", LANGCHAIN_TRACING_V2="false",
                      LANGSMITH_TRACING="false", OTEL_SDK_DISABLED="true")
    source = str(directory)
    if source not in sys.path:
        sys.path.insert(0, source)
    paths = ["gpt_researcher/prompts.py", "gpt_researcher/actions/query_processing.py",
             "gpt_researcher/utils/llm.py", "gpt_researcher/llm_provider/generic/base.py"]
    return {"commit": lock["commit"], "files": {
        p: sha256((directory / p).read_bytes()) for p in paths},
        "dependencies_sha256": sha256((ROOT / "requirements-upstream.lock.txt").read_bytes()),
        "python": sys.version}
