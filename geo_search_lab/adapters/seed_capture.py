"""Capture a single real initial search through the unmodified upstream adapter."""
from __future__ import annotations

import contextlib
import io
import os
import time
from pathlib import Path
from unittest.mock import patch

import requests

from geo_search_lab.adapters.upstream import prepare_upstream
from geo_search_lab.pilot_common import case_by_id, now, sha256, write_json


def capture_seed(query_id, provider, destination: Path):
    source = prepare_upstream()
    case = case_by_id(query_id)
    if provider == "serpapi":
        secret = os.environ.get("SERPAPI_API_KEY", "")
        if not secret:
            raise ValueError("Configure SERPAPI_API_KEY locally; do not paste it in a run artifact")
        from gpt_researcher.retrievers.serpapi.serpapi import SerpApiSearch as Adapter
        allowed = "https://serpapi.com/search.json"
    elif provider == "searx":
        secret = ""
        from gpt_researcher.retrievers.searx.searx import SearxSearch as Adapter
        os.environ["SEARX_URL"] = "http://127.0.0.1:8888"
        allowed = "http://127.0.0.1:8888/search"
    else:
        raise ValueError("Explicit searx or serpapi provider required")
    destination.mkdir(parents=True, exist_ok=False)
    record = {"schema_version": 1, "query_id": query_id, "query": case["query"], "provider": provider,
              "upstream": source, "provenance": "live_native_adapter", "captured_at": now(),
              "status": "failed", "request_count": 0, "max_results": 5}
    session = requests.Session()
    session.trust_env = False
    raw = b""

    def observed_get(url, *args, **kwargs):
        nonlocal raw
        if record["request_count"] >= 1 or url.split("?")[0] != allowed:
            raise RuntimeError("Seed capture permits one request to the explicit provider")
        record["request_count"] += 1
        start = time.perf_counter()
        response = session.get(url, *args, **kwargs, allow_redirects=False)
        raw = response.content
        if secret and secret.encode() in raw:
            # Preserve the response hash while refusing to persist credential-bearing bytes.
            record["original_response_sha256"] = sha256(raw)
            raw = raw.replace(secret.encode(), b"[REDACTED]")
            record["redacted"] = True
        record.update(http_status=response.status_code, elapsed_ms=round((time.perf_counter() - start) * 1000, 3))
        return response

    try:
        # Some native adapters print exception URLs. Capture and discard this text;
        # store typed errors/HTTP status instead, never credentials.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), patch("requests.get", observed_get):
            results = Adapter(case["query"]).search(max_results=5)
        import json
        payload = json.loads(raw) if raw else {}
        if not isinstance(payload, dict) or record.get("http_status") != 200 or payload.get("error"):
            raise RuntimeError("Search request failed; inspect HTTP status and response artifact")
        if provider == "serpapi":
            metadata = payload.get("search_metadata", {})
            if metadata.get("status") != "Success":
                raise RuntimeError("SerpAPI did not report a successful search")
            record["effective_search_parameters"] = payload.get("search_parameters", {})
            record["provider_response_seconds"] = metadata.get("total_time_taken")
        if provider == "searx" and not isinstance(payload.get("results"), list):
            raise RuntimeError("SearXNG response has no result array")
        if provider == "searx" and not results and payload.get("unresponsive_engines"):
            raise RuntimeError("Search engines failed; not a valid empty seed")
        record.update(status="ok" if results else "empty", results=results,
                      result_count=len(results), engines_failed=payload.get("unresponsive_engines", []))
    except Exception as exc:
        record.update(error_type=type(exc).__name__)
    finally:
        session.close()
        (destination / "response.bin").write_bytes(raw)
        record.update(raw_file="response.bin", raw_sha256=sha256(raw))
        write_json(destination / "seed.json", record)
    return record
