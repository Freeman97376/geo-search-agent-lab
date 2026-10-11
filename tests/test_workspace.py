"""Contract tests with deterministic provider fixtures; never call paid services."""
import asyncio
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from geo_search_lab.workspace.contracts import Planning, Project, Search
from geo_search_lab.workspace.service import Workspace, matches, planning_summary, domain_match_type, query_frequencies, views
from geo_search_lab.workspace.planning import plan
from geo_search_lab.workbench.api import create_app


def response(params, key, count=10):
    return httpx.Response(200, json={"search_metadata": {"status": "Success", "total_time_taken": 0.2},
        "search_parameters": params, "organic_results": [dict(position=i+1, title=f"Result {i}",
        link=f"https://{'example.com' if i == 2 else 'competitor.com'}/p/{i}", snippet="Fixture") for i in range(count)],
        "ads": [{"title": "An ad", "position": 1}], "secret_echo": key})


async def fake_planner(demand, temperature, repeat, destination, prompt_time):
    return dict(demand=demand, temperature=temperature, repeat=repeat, status="ok", queries=["same", f"query-{repeat}"],
                profile={"profile_id": "test-fixture", "model_digest": "fixture-not-a-model"}, prompt_time=prompt_time)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        def fetch(params, key):
            self.calls.append(dict(params))
            return response(params, key)
        self.ws = Workspace(Path(self.temp.name) / "workspace", planner=fake_planner, fetcher=fetch)
        self.ws.session_key = "test-secret-fixture"
        self.project = self.ws.store.create("project", Project(name="Test", website="example.com", competitors=["competitor.com"]).model_dump())

    def tearDown(self):
        self.temp.cleanup()

    def search(self, texts=("one",), **kwargs):
        return Search(project_id=self.project["id"], manual=[dict(id=f"q{i}", text=t, demand_id="d1", demand_text="Original requirement") for i,t in enumerate(texts)], **kwargs)

    def execute(self, batch):
        asyncio.run(self.ws.execute(batch["id"]))
        return self.ws.result(batch["id"])

    def test_exact_dedup_origins_and_cap_preview_without_dispatch(self):
        preview = self.ws.preview(self.search(("one", "one", "One", "one ")))
        self.assertEqual((preview["original_occurrences"], preview["unique_queries"], preview["estimated_requests"]), (4,3,6))
        self.assertEqual(len(preview["queries"][0]["origins"]), 2)
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValueError):
            self.ws.search(self.search(("one", "two"), request_cap=3))
        self.assertEqual(self.ws.store.list("search"), [])

    def test_two_pages_ranks_ads_redaction_and_offline_target_reanalysis(self):
        result = self.execute(self.ws.search(self.search()))
        self.assertEqual([c["start"] for c in self.calls], [0,10])
        organic = result["results"][0]["organic"]
        self.assertEqual([r["observed_rank"] for r in organic], list(range(1,21)))
        self.assertEqual(organic[10]["provider_position"], 1)
        self.assertTrue(result["results"][0]["complete_top20"])
        self.assertEqual(result["views"]["query_website_rows"][0]["best_rank"], 3)
        self.assertEqual(result["views"]["demand_website_rows"][0]["total_unique_queries"], 1)
        self.assertIsNone(result["semantic_coverage"])
        self.assertIn("ads", result["results"][0]["pages"][0]["payload"])
        self.assertNotIn(self.ws.session_key, json.dumps(result))
        for file in self.ws.store.root.rglob("*"):
            if file.is_file():
                self.assertNotIn(self.ws.session_key.encode(), file.read_bytes())
        self.ws.store.update(self.project["id"], pages=["https://example.com/p/2"], competitors=["missing.com"])
        updated = self.ws.result(result["id"])
        self.assertEqual(len(self.calls), 2)
        missing = next(r for r in updated["views"]["query_website_rows"] if r["target"] == "missing.com")
        self.assertIsNone(missing["best_rank"])
        self.assertEqual(missing["observation"], "not_found_in_collected_range")
        self.assertEqual(updated["views"]["query_website_rows"][-1]["best_rank"], 3)
        self.assertEqual(updated["views"]["demand_website_rows"][-1]["exact_page_detected_queries"], 1)
        # Duplicate execution or re-reading a batch never spends again.
        self.execute(result)
        self.assertEqual(len(self.calls), 2)

    def test_short_pages_keep_gaps_and_incomplete_flag(self):
        self.ws.fetcher = lambda p,k: response(p,k,3)
        result = self.execute(self.ws.search(self.search()))["results"][0]
        self.assertEqual([r["observed_rank"] for r in result["organic"]], [1,2,3,11,12,13])
        self.assertFalse(result["complete_top20"])
        self.assertEqual(result["status"], "partial")

    def test_provider_error_counts_and_stops_no_retry_or_fallback(self):
        self.ws.fetcher = lambda p,k: httpx.Response(429, json={"error":"limited"})
        result = self.execute(self.ws.search(self.search(("one", "two"))))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["counted_requests"], 1)
        self.assertEqual(result["request_ledger"][0]["status"], "failed_counted")
        self.assertTrue(all(r["best_rank"] is None for r in result["views"]["query_website_rows"]))
        self.assertEqual(result["results"][0]["pages"][0]["http_status"], 429)
        overview=result["overview"]
        self.assertEqual(overview["queries"][0]["state"],"failed_unobserved")
        self.assertEqual(overview["queries"][1]["state"],"not_started")
        self.assertEqual(overview["sponsored_observation"],"unobserved")
        self.assertEqual(overview["request_counts"]["failed"],1)

    def test_transport_exception_does_not_leak_secret_and_stops(self):
        def fail(params,key):
            raise RuntimeError("https://serpapi.com?api_key="+key)
        self.ws.fetcher = fail
        result = self.execute(self.ws.search(self.search()))
        self.assertNotIn(self.ws.session_key, json.dumps(result))
        self.assertEqual(result["counted_requests"], 1)
        self.assertEqual(result["results"][0]["pages"][0]["error_type"], "RuntimeError")

    def test_atomic_reservations_hard_cap_restart_unknowns(self):
        batch = self.ws.search(self.search(request_cap=2))
        self.ws.store.claim(batch["id"])
        def reserve(_):
            try:
                return self.ws.store.reserve(batch["id"], "q", 0)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=6) as executor:
            values = list(executor.map(reserve, range(12)))
        self.assertEqual(sorted(v for v in values if v), [1,2])
        self.ws.store.recover()
        self.assertEqual(self.ws.store.get(batch["id"])["status"], "interrupted")
        self.assertTrue(all(r["status"] == "unknown_after_restart" for r in self.ws.store.ledger(batch["id"])))
        self.execute(batch)
        self.assertEqual(self.calls, [])

    def test_cancel_before_execution_never_dispatches(self):
        batch = self.ws.search(self.search())
        self.ws.store.update(batch["id"], cancel_requested=True)
        result = self.execute(batch)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["counted_requests"], 0)

    def test_planning_separate_and_import_retains_all_repetition_origins(self):
        batch = self.ws.planning(Planning(project_id=self.project["id"], demands=[dict(id="d1", text="Requirement")]))
        result = self.execute(batch)
        self.assertEqual(len(result["trials"]), 10)
        self.assertEqual({t["prompt_time"] for t in result["trials"]}, {batch["prompt_time"]})
        self.assertEqual(self.calls, [])
        preview = self.ws.preview(Search(project_id=self.project["id"], planning_batch_id=batch["id"]))
        self.assertEqual((preview["original_occurrences"], preview["unique_queries"], preview["estimated_requests"]), (20,6,12))
        self.assertFalse(preview["can_start"])
        origins = next(q["origins"] for q in preview["queries"] if q["query"] == "same")
        self.assertEqual(len(origins), 10)
        self.assertEqual({o["temperature"] for o in origins}, {0,0.4})

    def test_model_failure_records_failure_no_synthetic_success(self):
        async def fail(demand, temperature, repeat, destination, prompt_time):
            return dict(demand=demand, temperature=temperature, repeat=repeat, status="failed", queries=[], error_type="ConnectError")
        self.ws.planner = fail
        result = self.execute(self.ws.planning(Planning(project_id=self.project["id"], demands=[dict(id="d1", text="Requirement")])))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["summary"][0]["valid"], 0)
        with self.assertRaises(ValueError):
            self.ws.preview(Search(project_id=self.project["id"], planning_batch_id=result["id"]))

    def test_settings_cohorts_and_cross_project_import_rejected(self):
        a = self.ws.preview(self.search())
        b = self.ws.preview(self.search(settings={"gl":"gb"}))
        self.assertNotEqual(a["cohort_id"], b["cohort_id"])
        batch = self.execute(self.ws.planning(Planning(project_id=self.project["id"], demands=[dict(id="d1",text="a")])))
        other = self.ws.store.create("project", Project(name="Other",website="other.com").model_dump())
        with self.assertRaises(ValueError):
            self.ws.preview(Search(project_id=other["id"], planning_batch_id=batch["id"]))

    def test_mismatched_provider_settings_not_pooled_into_requested_cohort(self):
        def mismatch(params,key):
            payload=response(params,key).json()
            payload["search_parameters"]["gl"]="gb"
            return httpx.Response(200,json=payload)
        self.ws.fetcher=mismatch
        result=self.execute(self.ws.search(self.search()))
        self.assertEqual(result["counted_requests"],1)
        self.assertEqual(result["results"][0]["organic"],[])
        self.assertEqual(result["results"][0]["pages"][0]["effective_settings_mismatch"],{"gl":"gb"})

    def test_native_planner_wire_controls_and_single_attempt_failure(self):
        requests=[]
        def completion(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200,json={"choices":[{"message":{"content":"[\"pump MOQ\",\"pump warranty\",\"pump lead time\"]"},"finish_reason":"stop"}],"usage":{"completion_tokens":20}})
        with patch("geo_search_lab.workspace.planning.verify_model",return_value={"test_fixture":True}), patch(
                "geo_search_lab.logging.llm_transport.httpx.AsyncHTTPTransport",return_value=httpx.MockTransport(completion)):
            result=asyncio.run(plan({"id":"d1","text":"Compare industrial pump lead times and warranty."},0.4,1,
                                   Path(self.temp.name)/"native-success","2026-10-09T12:00:00+00:00"))
        self.assertEqual(result["status"],"ok")
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0]["max_tokens"],2048)
        self.assertNotIn("max_completion_tokens",requests[0])
        self.assertEqual(requests[0]["temperature"],0.4)
        self.assertEqual(requests[0]["reasoning_effort"],"none")
        requests.clear()
        def fail(request):
            requests.append(request)
            raise httpx.ReadTimeout("test-only timeout")
        with patch("geo_search_lab.workspace.planning.verify_model",return_value={"test_fixture":True}), patch(
                "geo_search_lab.logging.llm_transport.httpx.AsyncHTTPTransport",return_value=httpx.MockTransport(fail)):
            result=asyncio.run(plan({"id":"d1","text":"Compare industrial pump lead times and warranty."},0,1,
                                   Path(self.temp.name)/"native-failed","2026-10-09T12:00:00+00:00"))
        self.assertEqual(result["status"],"failed")
        self.assertEqual(len(requests),1)

    def test_domain_boundaries_and_url_normalization(self):
        self.assertTrue(matches("https://shop.example.com/x", "example.com"))
        for link in ("https://example.com.evil.com", "https://notexample.com", "not a url"):
            self.assertFalse(matches(link, "example.com"))
        self.assertTrue(matches("https://www.example.com/x?utm_source=a#f", "https://example.com/x", True))
        self.assertFalse(matches("https://example.com/x?id=2", "https://example.com/x?id=1", True))

    def test_exact_domain_and_subdomain_are_separate(self):
        for url, expected in [("https://example.com/x", "exact_domain"),
                              ("https://www.example.com/x", "subdomain"),
                              ("https://a.shop.example.com", "subdomain"),
                              ("https://EXAMPLE.COM./", "exact_domain"),
                              ("https://goexample.com", None), ("https://example.com.evil.com", None),
                              ("https://user@example.com", None), ("ftp://example.com", None)]:
            self.assertEqual(domain_match_type(url,"example.com"),expected,url)
        self.assertEqual(Project(name="www target",website="www.example.com").website,"www.example.com")
        batch=self.ws.preview(self.search())
        batch["results"]=[dict(query_id=batch["queries"][0]["id"],organic=[
            dict(link="https://shop.example.com/x",observed_rank=1),
            dict(link="https://example.com/x",observed_rank=4),
            dict(link="https://goexample.com/x",observed_rank=2)])]
        result=views(batch,self.project)
        row=result["query_website_rows"][0]
        self.assertEqual((row["exact_domain_best_rank"],row["subdomain_best_rank"],row["best_rank"]),(4,1,1))
        self.assertEqual(len(row["matches"]),2)
        demand=result["demand_website_rows"][0]
        self.assertEqual((demand["exact_domain_detected_queries"],demand["subdomain_detected_queries"],demand["detected_queries"]),(1,1,1))

    def test_frequency_counts_occurrences_trials_arms_and_demand_boundaries(self):
        trials=[dict(demand={"id":"d1","text":"Demand"},temperature=0,repeat=1,trial_id="t1",status="ok",queries=["same","same","Same"]),
                dict(demand={"id":"d1","text":"Demand"},temperature=0.4,repeat=2,trial_id="t2",status="ok",queries=["same"]),
                dict(demand={"id":"d1","text":"Demand"},temperature=0,repeat=3,status="failed",queries=["same"]),
                dict(demand={"id":"d2","text":"Other"},temperature=0,repeat=1,status="ok",queries=["same"])]
        rows=query_frequencies(trials)
        same=next(r for r in rows if r["demand_id"]=="d1" and r["query"]=="same")
        self.assertEqual((same["occurrence_count"],same["trial_count"],same["valid_trials"]),(3,2,2))
        self.assertEqual(same["temperature_counts"]["0.0"],dict(occurrence_count=2,trial_count=1))
        self.assertEqual(len(rows),3)
        batch=self.ws.store.create("planning",dict(project_id=self.project["id"],trials=trials,status="completed"))
        self.assertEqual(self.ws.result(batch["id"])["query_frequencies"],rows)
        self.assertNotIn("query_frequencies",self.ws.store.get(batch["id"]))

    def test_overview_three_queries_short_pages_and_timeout_preserves_unknowns(self):
        calls=[]
        def fetch(params,key):
            calls.append(params)
            if params["q"]=="quotation":
                raise httpx.ReadTimeout("fixture")
            pay=response(params,key,9 if params["start"]==0 else 10).json()
            pay.pop("ads")
            return httpx.Response(200,json=pay)
        self.ws.fetcher=fetch
        result=self.execute(self.ws.search(self.search(("compare","documents","quotation"))))
        summary=result["overview"]
        self.assertEqual((summary["query_count"],summary["queries_with_response"],summary["organic_records"]),(3,2,38))
        self.assertEqual((summary["complete_pagination_queries"],summary["complete_top20_queries"]),(2,0))
        self.assertEqual(summary["request_counts"],dict(counted=5,succeeded=4,failed=1,pending=0,unknown=0))
        self.assertEqual([q["state"] for q in summary["queries"]],["two_pages_short","two_pages_short","failed_unobserved"])
        self.assertIn("读取超时",summary["failures"][0]["message"])
        self.assertEqual(summary["sponsored_observation"],"not_reported")
        self.assertEqual(len(summary["queries"][0]["top_results"]),5)
        self.assertEqual(summary["queries"][2]["top_results"],[])
        self.assertIsNone(summary["semantic_coverage"])
        again=self.ws.result(result["id"])
        self.assertEqual(again["overview"],summary)
        self.assertEqual(len(calls),5)
        self.assertNotIn("overview",self.ws.store.get(result["id"]))

    def test_overview_live_and_successfully_empty_search_are_distinct(self):
        batch=self.ws.search(self.search())
        summary=self.ws.result(batch["id"])["overview"]
        self.assertEqual(summary["queries"][0]["state"],"not_started")
        self.ws.store.claim(batch["id"])
        self.ws.store.reserve(batch["id"],batch["queries"][0]["id"],0)
        summary=self.ws.result(batch["id"])["overview"]
        self.assertEqual(summary["queries"][0]["state"],"requesting")
        self.assertEqual(summary["request_counts"]["pending"],1)
        self.ws.fetcher=lambda p,k: response(p,k,0)
        empty=self.execute(self.ws.search(self.search()))["overview"]
        self.assertTrue(empty["queries"][0]["has_valid_response"])
        self.assertEqual(empty["queries_with_response"],1)
        self.assertEqual(empty["queries_with_results"],0)
        self.assertEqual(empty["queries"][0]["state"],"two_pages_short")

    def test_planning_overview_counts_only_successful_output(self):
        result=self.execute(self.ws.planning(Planning(project_id=self.project["id"],demands=[dict(id="d1",text="Requirement")],repeats=1)))
        overview=result["overview"]
        self.assertEqual((overview["recorded_trials"],overview["valid_trials"],overview["successful_query_occurrences"],overview["unique_demand_queries"]),(2,2,4,2))
        self.assertEqual(overview["demands"][0]["text"],"Requirement")

    def test_malformed_boundary_inputs_rejected_before_side_effects(self):
        mutations = [dict(request_cap=n) for n in (0,-1,201,1.5,True)]
        mutations += [dict(settings={"gl":s}) for s in ("", "USA", "uｓ", "us\x00", "US")]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(ValidationError):
                self.search(**mutation)
        for text in ("", "  ", "a\x00b"):
            with self.assertRaises(ValidationError):
                self.search((text,))
        self.assertEqual(self.ws.store.list("search"), [])
        self.assertEqual(self.calls, [])

    def test_api_origin_secret_errors_and_csv_formula_guard(self):
        app = create_app(Path(self.temp.name)/"api", start_worker=False, workspace=self.ws)
        with TestClient(app) as client:
            url = "/api/v1/workspace"
            self.assertEqual(client.post(url+"/search/preview", json=self.search().model_dump()).status_code,403)
            headers = {"X-Lab-Request":"1"}
            error = client.put(url+"/serpapi-key", json={"key":"private value"}, headers=headers)
            self.assertEqual(error.status_code,422)
            self.assertNotIn("private value", error.text)
            self.assertEqual(client.post(url+"/search", json=self.search().model_dump(), headers=headers).status_code,409)
            result = self.execute(self.ws.search(self.search(("=SUM(1,2)",))))
            exported = client.get(url+f"/batches/{result['id']}/export?format=csv")
            self.assertIn("'=SUM", exported.text)
            self.assertEqual(client.get(url+f"/batches/{result['id']}/export").status_code,200)


if __name__ == "__main__":
    unittest.main()
