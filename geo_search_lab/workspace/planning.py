"""Named local adapter: native planner/prompt/normalizer, explicit one-call HTTP.

This bypasses LangChain's max_tokens -> max_completion_tokens rewriting. It is
not the original baseline transport, and is fingerprinted separately.
"""
import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import json_repair

from geo_search_lab.adapters.upstream import prepare_upstream
from geo_search_lab.experiments.planner import PROFILE, verify_model
from geo_search_lab.logging.llm_transport import RecordingTransport, TrialAbort
from geo_search_lab.pilot_common import now, read_json, sha256, write_json


async def plan(demand, temperature, repeat, destination: Path, prompt_time):
    destination.mkdir(parents=True, exist_ok=False)
    record = dict(demand=demand, temperature=temperature, repeat=repeat, started_at=now(),
                  prompt_time=prompt_time, status="running", context_mode="empty_ablation",
                  semantic_coverage=None, queries=[], adapter="native-planner-direct-openai-v1")
    try:
        source = prepare_upstream()
        from gpt_researcher.actions import query_processing as qp
        import gpt_researcher.prompts as prompts
        profile = read_json(PROFILE)
        profile.update(profile_id="qwen3-8b-direct-openai-v1", temperature=temperature,
                       top_p=1.0, request_limit=1, max_tokens=2048,
                       top_k="server_default_not_overridden", seed="server_random_default")
        record.update(profile=profile, upstream=source, local_identity=verify_model(profile),
                      adapter_sha256=sha256(Path(__file__).read_bytes()))
        fixed = datetime.fromisoformat(prompt_time)

        class FixedDateTime:
            @staticmethod
            def now(tz=None):
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        transport = RecordingTransport(destination, limit=1)
        async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                     timeout=90) as client:
            async def completion(*args, **kwargs):
                body = dict(model=profile["model"], messages=kwargs["messages"], temperature=temperature,
                            top_p=1.0, max_tokens=2048, reasoning_effort="none", stream=False)
                record["effective_request"] = body
                try:
                    response = await client.post("http://127.0.0.1:11434/v1/chat/completions", json=body)
                    response.raise_for_status()
                    value = response.json()
                    record.update(usage=value.get("usage"), finish_reason=value["choices"][0].get("finish_reason"),
                                  raw_output=value["choices"][0]["message"]["content"])
                    return record["raw_output"]
                except Exception as exc:
                    # Do not let upstream fallback create another unobserved sample.
                    raise TrialAbort(type(exc).__name__) from None

            cfg = SimpleNamespace(max_iterations=3, strategic_llm_model=profile["model"],
                                  strategic_llm_provider="openai", strategic_token_limit=2048,
                                  smart_llm_model=profile["model"], smart_llm_provider="openai",
                                  smart_token_limit=2048, temperature=temperature, llm_kwargs={})
            with patch.object(qp, "create_chat_completion", completion), patch.object(prompts, "datetime", FixedDateTime):
                async with asyncio.timeout(180):
                    queries = await qp.plan_research_outline(demand["text"], [], "", cfg, "", "research_report",
                                                             retriever_names=["fixed_input"])
            raw = record["raw_output"]
            try:
                decoded = json.loads(raw)
                strict = isinstance(decoded, list) and bool(decoded) and all(isinstance(x, str) and x.strip() for x in decoded)
            except ValueError:
                strict = False
            parsed = json_repair.loads(raw)
            # Retain native output but reject its fabricated fallback or coerced objects as valid samples.
            valid_shape = isinstance(parsed, list) and bool(parsed) and all(isinstance(x, str) and x.strip() for x in parsed)
            record.update(queries=queries, strict_json=bool(strict), repaired=not strict,
                          status="ok" if valid_shape and record.get("finish_reason") != "length" else "invalid_output",
                          http_request_count=transport.count)
    except (Exception, TrialAbort) as exc:
        record.update(status="failed", error_type=type(exc).__name__)
    finally:
        record["finished_at"] = now()
        write_json(destination / "trial.json", record)
    return record
