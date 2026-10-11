"""Run/replay the native GPT Researcher planner in an isolated local profile."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from geo_search_lab.adapters.upstream import prepare_upstream
from geo_search_lab.logging.llm_transport import RecordingTransport, ReplayTransport, TrialAbort
from geo_search_lab.pilot_common import ROOT, artifact_path, case_by_id, now, read_json, sha256, write_json


PROFILE = ROOT / "geo_search_lab/config/planner_local.json"


def local_get(path):
    # Fixed local endpoint and no environment proxy; never follow redirects.
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=10) as client:
        response = client.get("http://127.0.0.1:11434" + path)
        response.raise_for_status()
        return response.json()


def verify_model(profile):
    tags = local_get("/api/tags")
    model = next((m for m in tags["models"] if m["name"] == profile["model"]), None)
    if not model or model["digest"] != profile["model_digest"]:
        raise ValueError("Local model is missing or differs from the pinned model digest")
    return {"server": local_get("/api/version"), "model": model}


def seed_context(case, snapshot):
    if snapshot is None:
        return {"mode": "empty_ablation", "results": [], "warning": "No initial search; not full upstream web reproduction"}
    value = read_json(snapshot)
    if value["query"] != case["query"] or value["query_id"] != case["id"]:
        raise ValueError("Seed snapshot belongs to a different question")
    if value.get("status") not in ("ok", "empty") or value.get("provenance") != "live_native_adapter":
        raise ValueError("Seed must be a successful, explicitly identified native live capture")
    if not isinstance(value["results"], list):
        raise ValueError("Invalid initial results")
    raw = artifact_path(snapshot.parent, value["raw_file"]).read_bytes()
    if sha256(raw) != value["raw_sha256"]:
        raise ValueError("Seed raw-response integrity failure")
    return {"mode": "fixed_initial_search", "snapshot": value, "snapshot_sha256": sha256(snapshot.read_bytes()),
            "results": value["results"]}


async def run_trial(query_id, repeat, destination: Path, *, snapshot=None, replay=None, case_override=None, on_event=None):
    emit = on_event or (lambda kind, data: None)
    emit("preflight.started", {})
    destination.mkdir(parents=True, exist_ok=False)
    source = prepare_upstream()
    from gpt_researcher.actions import query_processing as qp
    import gpt_researcher.prompts as prompts

    case = case_override if case_override is not None else case_by_id(query_id)
    if case["id"] != query_id or not isinstance(case["query"], str) or not case["query"].strip():
        raise ValueError("Invalid explicit planning case")
    profile = read_json(PROFILE)
    if profile["base_url"] != "http://127.0.0.1:11434/v1":
        raise ValueError("This profile allows local inference only")
    if replay:
        original = read_json(replay / "trial.json")
        if original["status"] != "ok" or original["query_id"] != query_id:
            raise ValueError("Only matching successful trials can be replayed")
        if original["profile"] != profile or original["upstream"] != source:
            raise ValueError("Replay requires identical profile, upstream, dependencies and Python")
        if original["query"] != case["query"]:
            raise ValueError("Benchmark changed since capture")
        seed, prompt_time = original["seed"], original["prompt_time"]
        identity = original["local_identity"]
        transport = ReplayTransport(replay)
    else:
        seed, prompt_time = seed_context(case, snapshot), now()
        identity = verify_model(profile)
        transport = RecordingTransport(destination, limit=profile["request_limit"])

    record = {"schema_version": 1, "kind": "planner_only", "mode": "replay" if replay else "live_local_llm",
              "query_id": query_id, "repeat": repeat, "query": case["query"],
              "started_at": now(), "prompt_time": prompt_time, "upstream": source,
              "profile": profile, "local_identity": identity, "seed": seed,
              "planner_calls": [], "status": "running", "citations": None,
              "external_comparison_eligible": False, "case": case}
    emit("preflight.completed", {"seed_mode": seed["mode"], "initial_result_count": len(seed["results"])})
    write_json(destination / "trial.json", record)
    fixed = datetime.fromisoformat(prompt_time)

    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

    real_completion = qp.create_chat_completion

    async def observed_completion(*args, **kwargs):
        event = {"model": kwargs.get("model"), "provider": kwargs.get("llm_provider"),
                 "messages": kwargs.get("messages"), "requested_max_tokens": kwargs.get("max_tokens"),
                 "requested_reasoning_effort": kwargs.get("reasoning_effort"), "started_at": now()}
        record["planner_calls"].append(event)
        emit("model.started", {"call_index": len(record["planner_calls"])})
        try:
            text = await real_completion(*args, **kwargs)
            event.update(status="ok", response_text=text)
            try:
                decoded = json.loads(text)
                event["strict_list_of_strings"] = isinstance(decoded, list) and all(isinstance(v, str) for v in decoded)
            except (ValueError, TypeError):
                event["strict_list_of_strings"] = False
            emit("model.completed", {"strict_json": event["strict_list_of_strings"]})
            return text
        except BaseException as exc:
            event.update(status="error", error_type=type(exc).__name__)
            emit("model.failed", {"error_type": type(exc).__name__})
            raise
        finally:
            write_json(destination / "trial.json", record, replace=True)

    # The native helper reads OPENAI_BASE_URL after llm_kwargs, so pin both explicitly.
    with patch.dict(os.environ, {"OPENAI_BASE_URL": profile["base_url"], "OPENAI_API_KEY": "local-only"}):
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                     timeout=profile["request_timeout_seconds"]) as client:
            cfg = SimpleNamespace(
                max_iterations=profile["max_iterations"], strategic_llm_model=profile["model"],
                strategic_llm_provider="openai", smart_llm_model=profile["model"], smart_llm_provider="openai",
                strategic_token_limit=8000, smart_token_limit=12000, temperature=profile["temperature"],
                llm_kwargs={"openai_api_base": profile["base_url"], "openai_api_key": "local-only",
                            "http_async_client": client, "max_retries": 0,
                            "max_tokens": profile["max_tokens"], "temperature": profile["temperature"],
                            "reasoning_effort": profile["reasoning_effort"]})
            try:
                with patch.object(prompts, "datetime", FixedDateTime), patch.object(qp, "create_chat_completion", observed_completion):
                    async with asyncio.timeout(profile["trial_timeout_seconds"]):
                        queries = await qp.plan_research_outline(
                            query=case["query"], search_results=seed["results"], agent_role_prompt="",
                            cfg=cfg, parent_query="", report_type=profile["report_type"],
                            retriever_names=[seed.get("snapshot", {}).get("provider", "fixed_input")])
                if replay:
                    transport.assert_consumed()
                    if queries != original["sub_queries"]:
                        raise TrialAbort("Replay normalized output differs")
                execution = list(queries)
                if profile["report_type"] != "subtopic_report":
                    execution.append(case["query"])
                record.update(status="ok", sub_queries=queries, planned_execution_queries=execution,
                              query_count=len(queries), duplicate_count=len(queries) - len(set(queries)),
                              replay_exact_match=True if replay else None)
                emit("planner.completed", {"query_count": len(queries)})
            except (Exception, TrialAbort) as exc:
                record.update(status="failed", error_type=type(exc).__name__, error=str(exc))
            finally:
                record.update(finished_at=now(), http_request_count=transport.count)
                write_json(destination / "trial.json", record, replace=True)
    return record
