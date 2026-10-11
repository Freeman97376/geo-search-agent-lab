import asyncio
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import httpx

from fastapi.testclient import TestClient
from pydantic import ValidationError

from geo_search_lab.experiments.planner import PROFILE
from geo_search_lab.pilot_common import now, read_json, sha256, write_json
from geo_search_lab.workbench.api import create_app
from geo_search_lab.workbench.contracts import Conflict, EvaluationRequest, RunRequest
from geo_search_lab.workbench.evaluation import evaluate
from geo_search_lab.workbench.service import LabService
from geo_search_lab.workbench.store import Store

HEADERS = {"X-Lab-Request": "1"}


async def fixture_runner(query_id, repeat, destination, *, snapshot, replay, case_override, on_event):
    destination.mkdir()
    on_event("model.started", {})
    record = {"schema_version": 1, "status": "ok", "kind": "planner_only", "query_id": query_id,
        "query": case_override["query"], "repeat": repeat, "started_at": now(), "finished_at": now(),
        "profile": read_json(PROFILE), "upstream": {"commit": "fixture-commit", "dependencies_sha256": "fixture-deps"},
        "seed": {"mode": "empty_ablation", "results": []}, "planner_calls": [{"strict_list_of_strings": True}],
        "sub_queries": ["first query", "第二个查询"], "planned_execution_queries": ["first query", "第二个查询", case_override["query"]],
        "query_count": 2, "duplicate_count": 0, "http_request_count": 1, "prompt_time": "2026-09-28T12:00:00+00:00",
        "replay_exact_match": True if replay else None}
    if not replay:
        write_json(destination / "http-001.response.bin", {"usage": {"total_tokens": 100}})
        write_json(destination / "http-001.json", {"status": "response", "response_file": "http-001.response.bin", "elapsed_ms": 500})
    write_json(destination / "trial.json", record)
    return record


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.service = LabService(self.store, runner=fixture_runner, snapshots={})
        self.client = TestClient(create_app(service=self.service, start_worker=False))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def request(self, **kwargs):
        return RunRequest(prompt="比较两个产品的价格", input_mode="empty_ablation", facets=["价格"], constraints=["美国"], **kwargs)

    def completed(self, **kwargs):
        run, _ = self.service.submit(self.request(**kwargs))
        asyncio.run(self.service.execute(run["id"]))
        return self.store.get(run["id"])

    def test_custom_prompt_result_events_and_no_paid_search(self):
        row = self.completed()
        result = self.client.get(f"/api/v1/runs/{row['id']}/result").json()
        self.assertEqual(result["input"]["prompt"], "比较两个产品的价格")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["metrics"]["model_tokens"], 100)
        self.assertIsNone(result["evaluation"]["platform_gap"])
        self.assertIsNone(result["output"]["citations"])
        self.assertFalse(self.client.get("/api/v1/health").json()["paid_search_enabled"])
        events = self.store.events(row["id"])
        self.assertEqual(events["items"][-1]["type"], "run.succeeded")
        self.assertEqual(self.store.events(row["id"], events["next_after"])["items"], [])

    def test_request_idempotency_and_conflicting_payload(self):
        body = self.request(client_request_id="integration-001").model_dump()
        first = self.client.post("/api/v1/runs", json=body, headers=HEADERS)
        second = self.client.post("/api/v1/runs", json=body, headers=HEADERS)
        self.assertEqual(first.status_code, 202)
        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(first.json()["run"]["id"], second.json()["run"]["id"])
        body["prompt"] = "a different question"
        self.assertEqual(self.client.post("/api/v1/runs", json=body, headers=HEADERS).status_code, 409)
        self.assertEqual(self.store.listing()["total"], 1)

    def test_local_mutation_requires_header_and_same_origin(self):
        body = self.request().model_dump()
        self.assertEqual(self.client.post("/api/v1/runs", json=body).status_code, 403)
        self.assertEqual(self.client.post("/api/v1/runs", json=body, headers={**HEADERS, "Origin": "https://external.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/health", headers={"Host": "external.example"}).status_code, 400)
        self.assertEqual(self.store.listing()["total"], 0)

    def test_malformed_inputs_and_unsupported_provider_make_no_run(self):
        for changes in ({"prompt": " "}, {"prompt": "a\x00b"}, {"prompt": "x" * 12001},
                        {"input_mode": "live_search"}, {"provider": "serpapi"}, {"schema_version": "2.0"},
                        {"snapshot_id": "../../private"}, {"facets": ["same", "same"]}):
            with self.subTest(changes=list(changes)):
                body = dict(self.request().model_dump(), **changes)
                self.assertEqual(self.client.post("/api/v1/runs", json=body, headers=HEADERS).status_code, 422)
        self.assertEqual(self.client.post("/api/v1/runs", content='{"bad":', headers={**HEADERS, "Content-Type": "application/json"}).status_code, 422)
        self.assertEqual(self.store.listing()["total"], 0)

    def test_snapshot_cannot_be_applied_to_another_prompt(self):
        self.service.snapshots = {"seed_a": {"prompt": "original", "path": Path("unused"), "query_id": "q01"}}
        value = dict(self.request().model_dump(), input_mode="snapshot", snapshot_id="seed_a")
        self.assertEqual(self.client.post("/api/v1/runs", json=value, headers=HEADERS).status_code, 422)

    def test_missing_snapshot_does_not_fallback_to_empty_mode(self):
        value = dict(self.request().model_dump(), input_mode="snapshot", snapshot_id="seed_missing")
        self.assertEqual(self.client.post("/api/v1/runs", json=value, headers=HEADERS).status_code, 422)

    def test_failure_is_persisted_and_visible(self):
        async def broken(*args, **kwargs):
            raise ConnectionError("Local model unavailable")
        self.service.runner = broken
        run, _ = self.service.submit(self.request())
        asyncio.run(self.service.execute(run["id"]))
        self.assertEqual(self.store.get(run["id"])["status"], "failed")
        result = self.store.result(run["id"])
        self.assertIsNone(result["output"]["sub_queries"])
        self.assertIsNone(result["metrics"]["strict_json"])
        self.assertEqual(result["error"]["code"], "ConnectionError")

    def test_cancel_and_restart_do_not_implicitly_retry(self):
        first, _ = self.service.submit(self.request())
        second, _ = self.service.submit(self.request())
        self.store.cancel(first["id"])
        self.store.start(second["id"])
        self.assertEqual(self.store.interrupt_unfinished(), 1)
        self.assertEqual(self.store.get(first["id"])["status"], "cancelled")
        self.assertEqual(self.store.get(second["id"])["status"], "interrupted")
        with self.assertRaises(Conflict):
            self.store.cancel(second["id"])

    def test_pagination_filter_and_safe_sql_values(self):
        self.completed(experiment_id="group-a")
        self.completed(experiment_id="group-b")
        page = self.client.get("/api/v1/runs?limit=1&offset=1").json()
        self.assertEqual(page["total"], 2)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(self.store.listing(experiment_id="group-a")["total"], 1)
        self.assertEqual(self.store.listing(experiment_id="' OR 1=1--")["total"], 0)

    def test_result_and_artifact_tampering_rejected(self):
        run = self.completed()
        artifact = self.store.directory(run["id"]) / "trace/http-001.response.bin"
        artifact.write_text("changed")
        response = self.client.get(f"/api/v1/runs/{run['id']}/artifacts/http-001.response.bin")
        self.assertEqual(response.status_code, 409)
        (self.store.directory(run["id"]) / "result.json").write_text("{}")
        self.assertEqual(self.client.get(f"/api/v1/runs/{run['id']}/result").status_code, 409)

    def test_unlisted_artifact_and_unknown_run_rejected(self):
        run = self.completed()
        self.assertEqual(self.client.get(f"/api/v1/runs/{run['id']}/artifacts/index.sqlite3").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/runs/run_missing").status_code, 404)

    def review(self, revision=0):
        return {"schema_version": "1.0", "expected_revision": revision, "reviewer": "fixture-reviewer",
                "facets": {"价格": {"covered": True, "query_indices": [1], "note": ""}},
                "constraints": {"美国": {"covered": None, "query_indices": [], "note": ""}}, "notes": "test"}

    def test_versioned_review_and_optimistic_conflict(self):
        run = self.completed()
        url = f"/api/v1/runs/{run['id']}/review"
        first = self.client.post(url, json=self.review(), headers=HEADERS)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["review"]["revision"], 1)
        self.assertEqual(self.client.post(url, json=self.review(), headers=HEADERS).status_code, 409)
        self.assertEqual(self.client.post(url, json=self.review(1), headers=HEADERS).status_code, 200)
        self.assertEqual(len(self.store.review_history(run["id"])), 2)

    def test_review_requires_predeclared_rubric_and_evidence(self):
        run = self.completed()
        url = f"/api/v1/runs/{run['id']}/review"
        no_evidence = self.review()
        no_evidence["facets"]["价格"]["query_indices"] = []
        invalid_index = self.review()
        invalid_index["facets"]["价格"]["query_indices"] = [99]
        extra_rubric = self.review()
        extra_rubric["facets"]["额外维度"] = {"covered": None, "query_indices": [], "note": ""}
        for body in (no_evidence, invalid_index, extra_rubric):
            self.assertEqual(self.client.post(url, json=body, headers=HEADERS).status_code, 422)
        self.assertIsNone(self.store.latest_review(run["id"]))

    def test_replay_excluded_and_zero_new_model_tokens(self):
        run = self.completed()
        replay, _ = self.service.replay(run["id"])
        asyncio.run(self.service.execute(replay["id"]))
        result = self.store.result(replay["id"])
        self.assertEqual(result["metrics"]["model_tokens"], 0)
        self.assertEqual(result["metrics"]["model_requests"], 0)
        report = evaluate(self.store, [run["id"], replay["id"]])
        self.assertEqual(report["excluded_replays"], 1)
        self.assertEqual(report["cohorts"][0]["successful_live_runs"], 1)

    def test_unknown_quality_is_null_and_partial_reviews_are_not_zero(self):
        run = self.completed()
        report = evaluate(self.store, [run["id"]])
        scores = report["cohorts"][0]["per_question"][0]["review_scores"]
        self.assertIsNone(scores["facets"]["score"])
        self.store.add_review(run["id"], self.review())
        report = evaluate(self.store, [run["id"]])
        scores = report["cohorts"][0]["per_question"][0]["review_scores"]
        self.assertEqual(scores["facets"]["score"], 1)
        self.assertIsNone(scores["constraints"]["score"])
        self.assertIsNone(report["overall_quality_score"])
        self.assertIsNone(report["platform_gap"])

    def test_duplicate_evaluation_ids_and_failed_denominator(self):
        run = self.completed()
        self.assertEqual(self.client.post("/api/v1/evaluations", json={"run_ids": [run["id"], run["id"]]}, headers=HEADERS).status_code, 422)
        async def broken(*args, **kwargs):
            raise ValueError("fixture failure")
        self.service.runner = broken
        failed = self.completed()
        report = evaluate(self.store, [run["id"], failed["id"]])
        self.assertEqual(report["status_counts"], {"succeeded": 1, "failed": 1})
        self.assertEqual(report["cohorts"][0]["successful_live_runs"], 1)

    def test_changed_profile_is_not_pooled(self):
        first = self.completed()
        async def different(*args, **kwargs):
            record = await fixture_runner(*args, **kwargs)
            record["profile"]["temperature"] = 0.9
            write_json(args[2] / "trial.json", record, replace=True)
            return record
        self.service.runner = different
        second = self.completed()
        self.assertEqual(len(evaluate(self.store, [first["id"], second["id"]])["cohorts"]), 2)

    def test_experiment_groups_are_not_silently_pooled(self):
        first = self.completed(experiment_id="baseline")
        second = self.completed(experiment_id="candidate")
        self.assertEqual(len(evaluate(self.store, [first["id"], second["id"]])["cohorts"]), 2)

    def test_result_schema_is_explicit_and_query_indices_stay_one_based(self):
        schema = self.client.get("/api/v1/schemas/result").json()
        self.assertIn("output", schema["required"])
        self.assertIn("PlannerMetrics", schema["$defs"])
        run = self.completed()
        review = self.review()
        review["facets"]["价格"]["query_indices"] = [0]
        self.assertEqual(self.client.post(f"/api/v1/runs/{run['id']}/review", json=review, headers=HEADERS).status_code, 422)

    def test_single_instance_lock_prevents_conflicting_recovery(self):
        from geo_search_lab.workbench.api import instance_lock
        async def acquire_twice():
            with self.assertRaises(RuntimeError):
                async with instance_lock(self.store.root / "service.lock"):
                    pass
        asyncio.run(acquire_twice())

    def test_jsonl_export_roundtrip_and_evidence_hashes(self):
        first, second = self.completed(), self.completed()
        response = self.client.post("/api/v1/exports", json={"run_ids": [first["id"], second["id"]]}, headers=HEADERS)
        self.assertEqual(response.status_code, 200)
        records = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["result"]["input"]["prompt"], "比较两个产品的价格")
        for entry in records[0]["result"]["artifacts"]:
            raw = self.client.get(f"/api/v1/runs/{first['id']}/artifacts/{entry['name']}").content
            self.assertEqual(sha256(raw), entry["sha256"])

    def test_static_ui_and_openapi_are_served_locally(self):
        html = self.client.get("/")
        self.assertEqual(html.status_code, 200)
        self.assertIn("Query 实验工作台", html.text)
        self.assertIn("script-src 'self'", html.headers["content-security-policy"])
        source = self.client.get("/static/app.js").text
        self.assertNotIn("innerHTML", source)
        self.assertNotIn("https://", source)
        self.assertIn("/api/v1/runs", self.client.get("/openapi.json").json()["paths"])

    def test_prompt_library_is_read_only_and_every_case_is_a_valid_custom_request(self):
        response = self.client.get("/api/v1/prompt-library")
        self.assertEqual(response.status_code, 200)
        library = response.json()
        self.assertEqual(len(library["companies"]), 8)
        self.assertEqual(library["status"], "authored_draft_not_observed")
        identifiers, prompts = set(), set()
        for company in library["companies"]:
            self.assertEqual(len(company["cases"]), 6)
            for case in company["cases"]:
                request = RunRequest.model_validate(case["request"])
                self.assertEqual(request.input_mode, "empty_ablation")
                self.assertIsNone(request.snapshot_id)
                self.assertIsNone(request.client_request_id)
                identifiers.add(case["id"])
                prompts.add(request.prompt)
        self.assertEqual(len(identifiers), 48)
        self.assertEqual(len(prompts), 48)
        self.assertEqual(self.store.listing()["total"], 0)
        self.assertIn("/api/v1/prompt-library", self.client.get("/openapi.json").json()["paths"])

    def test_brand_groups_and_paraphrase_families_are_explicit(self):
        companies = self.client.get("/api/v1/prompt-library").json()["companies"]
        groups = set()
        for company in companies:
            cases = company["cases"]
            self.assertEqual([c["awareness"] for c in cases], ["unbranded"] * 3 + ["branded"] * 2 + ["unbranded"])
            self.assertNotEqual(cases[0]["request"]["experiment_id"], cases[3]["request"]["experiment_id"])
            self.assertEqual(cases[5]["variant_of"], cases[0]["id"])
            for field in ("facets", "constraints"):
                self.assertEqual(cases[0]["request"][field], cases[5]["request"][field])
            groups.update(c["request"]["experiment_id"] for c in cases)
        self.assertEqual(len(groups), 16)

    def test_different_questions_and_repeated_generations_keep_independent_records(self):
        cases = self.client.get("/api/v1/prompt-library").json()["companies"][0]["cases"]
        ids = []
        for case in (cases[0], cases[1], cases[0]):
            response = self.client.post("/api/v1/runs", json=case["request"], headers=HEADERS)
            self.assertEqual(response.status_code, 202)
            identifier = response.json()["run"]["id"]
            ids.append(identifier)
            asyncio.run(self.service.execute(identifier))
        self.assertEqual(len(set(ids)), 3)
        cohort = evaluate(self.store, ids)["cohorts"][0]
        self.assertEqual(cohort["unique_questions"], 2)
        self.assertEqual(sorted(q["runs"] for q in cohort["per_question"]), [1, 2])
        self.assertEqual(self.store.result(ids[0])["input"]["prompt"], cases[0]["request"]["prompt"])

    def test_editing_a_preset_does_not_change_the_library(self):
        before = self.client.get("/api/v1/prompt-library").json()
        body = copy.deepcopy(before["companies"][0]["cases"][0]["request"])
        body["prompt"] = "比较法国与德国采购条件。保留 30ml、±0.02mm 与1000件。"
        self.assertEqual(self.client.post("/api/v1/runs", json=body, headers=HEADERS).status_code, 202)
        self.assertEqual(self.client.get("/api/v1/prompt-library").json(), before)


class CliTests(unittest.TestCase):
    def invoke(self, final_status, *, timeout=False):
        from scripts import lab
        with tempfile.TemporaryDirectory() as temporary:
            request_file = Path(temporary) / "request.json"
            write_json(request_file, {"prompt": "fixture", "input_mode": "empty_ablation"})
            def respond(request):
                if request.url.path.endswith("/export"):
                    return httpx.Response(200, json={"run": {"id": "run_fixture", "status": final_status}})
                if request.method == "POST":
                    return httpx.Response(202, json={"run": {"id": "run_fixture", "status": "queued"}})
                return httpx.Response(200, json={"id": "run_fixture", "status": final_status})
            client = httpx.Client(base_url="http://127.0.0.1:8765", transport=httpx.MockTransport(respond))
            clock_values = iter((0, 301) if timeout else (0, 1))
            clock = SimpleNamespace(monotonic=lambda: next(clock_values), sleep=lambda duration: None)
            output = io.StringIO()
            with patch("sys.argv", ["lab.py", "run", "--request", str(request_file), "--wait"]), patch("httpx.Client", return_value=client), patch.object(lab, "time", clock), contextlib.redirect_stdout(output):
                code = lab.main()
            return code, json.loads(output.getvalue())

    def test_cli_terminal_exit_codes_support_orchestrators(self):
        self.assertEqual(self.invoke("succeeded")[0], 0)
        for status in ("failed", "cancelled", "interrupted"):
            self.assertEqual(self.invoke(status)[0], 1)

    def test_cli_timeout_retains_run_id_for_later_polling(self):
        code, value = self.invoke("queued", timeout=True)
        self.assertEqual(code, 2)
        self.assertTrue(value["wait_timed_out"])
        self.assertEqual(value["run"]["id"], "run_fixture")


if __name__ == "__main__":
    unittest.main()
