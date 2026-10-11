"""Lossless local LLM HTTP capture and strict offline replay at the same boundary."""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from geo_search_lab.pilot_common import artifact_path, now, read_json, sha256, write_json


class TrialAbort(BaseException):
    """Escape upstream Exception-based retry loops after a hard safety boundary."""


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, directory: Path, *, limit=3, inner=None):
        self.directory, self.limit, self.count = directory, limit, 0
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        if (request.url.scheme, request.url.host, request.url.port, request.url.path) != (
                "http", "127.0.0.1", 11434, "/v1/chat/completions") or request.url.query:
            raise TrialAbort("Only the explicit local chat-completions endpoint is allowed")
        if self.count >= self.limit:
            raise TrialAbort("Local model request budget exhausted")
        self.count += 1
        request_body = await request.aread()
        prefix = f"http-{self.count:03}"
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{prefix}.request.bin").write_bytes(request_body)
        record = {"index": self.count, "started_at": now(), "method": request.method,
                  "endpoint": str(request.url), "request_sha256": sha256(request_body),
                  "request_file": f"{prefix}.request.bin"}
        started = time.perf_counter()
        try:
            response = await self.inner.handle_async_request(request)
            body = await response.aread()
            record.update(status="response", http_status=response.status_code,
                          response_sha256=sha256(body), response_file=f"{prefix}.response.bin",
                          content_type=response.headers.get("content-type", "application/json"))
            (self.directory / record["response_file"]).write_bytes(body)
            await response.aclose()
            return httpx.Response(response.status_code, content=body,
                                  headers={"content-type": record["content_type"]}, request=request)
        except BaseException as exc:
            # No request headers/credentials or arbitrary exception URL are persisted.
            record.update(status="transport_error", error_type=type(exc).__name__)
            raise
        finally:
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
            write_json(self.directory / f"{prefix}.json", record)

    async def aclose(self):
        await self.inner.aclose()


class ReplayTransport(httpx.AsyncBaseTransport):
    def __init__(self, directory: Path):
        self.directory = directory
        self.records = [read_json(p) for p in sorted(directory.glob("http-*.json"))]
        self.count = 0
        if not self.records:
            raise ValueError("Snapshot has no recorded LLM exchanges")

    async def handle_async_request(self, request):
        if self.count >= len(self.records):
            raise TrialAbort("Replay missing response; live fallback is forbidden")
        record = self.records[self.count]
        incoming = await request.aread()
        saved = artifact_path(self.directory, record["request_file"]).read_bytes()
        if sha256(saved) != record["request_sha256"]:
            raise TrialAbort("Recorded request integrity failure")
        if (str(request.url) != record["endpoint"] or request.method != record["method"]
                or json.loads(incoming) != json.loads(saved)):
            raise TrialAbort("Replay request does not match recorded request")
        self.count += 1
        if record["status"] != "response":
            raise TrialAbort("Transport-error replay is not implemented; refusing live fallback")
        body = artifact_path(self.directory, record["response_file"]).read_bytes()
        if sha256(body) != record["response_sha256"]:
            raise TrialAbort("Recorded response integrity failure")
        return httpx.Response(record["http_status"], content=body,
                              headers={"content-type": record["content_type"]}, request=request)

    def assert_consumed(self):
        if self.count != len(self.records):
            raise TrialAbort("Replay left unconsumed responses")
