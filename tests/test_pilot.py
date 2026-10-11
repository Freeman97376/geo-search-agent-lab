import asyncio
import copy
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import requests

from geo_search_lab.adapters.seed_capture import capture_seed
from geo_search_lab.evaluation.metrics import domain_present, js_divergence, normalize_url, source_distribution, wilson_interval
from geo_search_lab.evaluation.observations import compare_file, prepare_web, validate
from geo_search_lab.logging.llm_transport import RecordingTransport, ReplayTransport, TrialAbort
from geo_search_lab.pilot_common import artifact_path, read_json, write_json


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = {"messages": [{"role": "user", "content": "中文问题"}], "model": "qwen3:8b"}

    async def capture(self):
        inner = httpx.MockTransport(lambda request: httpx.Response(200, json={"answer": "证据"}))
        transport = RecordingTransport(self.root, limit=1, inner=inner)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.post("http://127.0.0.1:11434/v1/chat/completions", json=self.payload,
                                         headers={"Authorization": "Bearer secret-not-to-log"})
        return response

    async def test_exact_replay_and_no_headers_persisted(self):
        original = await self.capture()
        replay = ReplayTransport(self.root)
        async with httpx.AsyncClient(transport=replay) as client:
            result = await client.post("http://127.0.0.1:11434/v1/chat/completions", json=self.payload)
        self.assertEqual(result.content, original.content)
        replay.assert_consumed()
        self.assertFalse(any(b"secret-not-to-log" in p.read_bytes() for p in self.root.iterdir()))

    async def test_remote_endpoint_blocked_before_transport(self):
        called = []
        transport = RecordingTransport(self.root, inner=httpx.MockTransport(lambda r: called.append(r)))
        async with httpx.AsyncClient(transport=transport) as client:
            with self.assertRaises(TrialAbort):
                await client.post("https://api.openai.com/v1/chat/completions", json=self.payload)
        self.assertEqual(called, [])

    async def test_live_request_budget_is_hard_limit(self):
        transport = RecordingTransport(self.root, limit=1, inner=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        async with httpx.AsyncClient(transport=transport) as client:
            await client.post("http://127.0.0.1:11434/v1/chat/completions", json=self.payload)
            with self.assertRaises(TrialAbort):
                await client.post("http://127.0.0.1:11434/v1/chat/completions", json=self.payload)
        self.assertEqual(transport.count, 1)

    async def test_mismatched_replay_never_falls_back(self):
        await self.capture()
        async with httpx.AsyncClient(transport=ReplayTransport(self.root)) as client:
            with self.assertRaises(TrialAbort):
                await client.post("http://127.0.0.1:11434/v1/chat/completions", json={"messages": []})

    async def test_tampered_response_rejected(self):
        await self.capture()
        (self.root / "http-001.response.bin").write_bytes(b"{}")
        async with httpx.AsyncClient(transport=ReplayTransport(self.root)) as client:
            with self.assertRaises(TrialAbort):
                await client.post("http://127.0.0.1:11434/v1/chat/completions", json=self.payload)

    async def test_extra_and_unconsumed_exchanges_rejected(self):
        await self.capture()
        replay = ReplayTransport(self.root)
        with self.assertRaises(TrialAbort):
            replay.assert_consumed()
        async with httpx.AsyncClient(transport=replay) as client:
            await client.post("http://127.0.0.1:11434/v1/chat/completions", json=self.payload)
            with self.assertRaises(TrialAbort):
                await client.post("http://127.0.0.1:11434/v1/chat/completions", json=self.payload)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "web"
        self.document = prepare_web(self.root)

    def complete(self, row, citations):
        raw = b"A synthetic test answer with separately reviewed citations."
        path = self.root / row["answer_file"]
        path.write_bytes(raw)
        from geo_search_lab.pilot_common import sha256
        row.update(status="complete", captured_at="2026-09-28T20:00:00+00:00", new_conversation=True,
                   mode_label="synthetic-test-mode", model_label="unknown", plan_label="synthetic",
                   region="unknown", locale="en-US", search_status="observed", provenance="manual_web_capture",
                   citations_reviewed=True, citations=[{"url": url} for url in citations], answer_sha256=sha256(raw))

    def test_preparation_has_36_pending_and_no_fabricated_queries(self):
        self.assertEqual(len(self.document["observations"]), 36)
        self.assertEqual(validate(self.document, self.root), [])
        self.assertTrue(all(r["visible_search_queries"]["queries"] is None for r in self.document["observations"]))

    def test_missing_real_data_has_no_gap_score(self):
        report = compare_file(self.root / "observations.json", self.root / "report.json")
        self.assertEqual(report["status"], "insufficient_data")
        self.assertIsNone(report["local_vs_web_gap"])
        self.assertEqual(report["pairs"], [])

    def test_observed_empty_is_distinct_from_missing(self):
        rows = [r for r in self.document["observations"] if r["query_id"] == "q01" and r["platform"] != "gemini"]
        for row in rows:
            self.complete(row, [] if row["platform"] == "claude" else ["https://www.notion.com/pricing"])
        write_json(self.root / "observations.json", self.document, replace=True)
        report = compare_file(self.root / "observations.json", self.root / "report.json")
        self.assertEqual(report["observed_answers"], 4)
        self.assertEqual(report["pairs"][0]["matched_questions"], 1)
        self.assertEqual(report["pairs"][0]["macro_target_presence_mae"], 0.5)
        self.assertEqual(report["pairs"][0]["macro_citation_host_js_divergence"], 1)

    def test_duplicate_observation_rejected(self):
        self.document["observations"].append(copy.deepcopy(self.document["observations"][0]))
        with self.assertRaises(ValueError):
            validate(self.document, self.root)

    def test_planner_is_not_an_answer(self):
        self.document["observations"][0]["kind"] = "planner_only"
        with self.assertRaises(ValueError):
            validate(self.document, self.root)

    def test_api_samples_cannot_enter_web_cohort(self):
        self.document["observations"][0]["interface"] = "api"
        with self.assertRaises(ValueError):
            validate(self.document, self.root)

    def test_complete_requires_raw_evidence_and_known_citations(self):
        row = self.document["observations"][0]
        self.complete(row, [])
        row["citations"] = None
        with self.assertRaises(ValueError):
            validate(self.document, self.root)
        row["citations"] = []
        (self.root / row["answer_file"]).write_bytes(b"changed")
        with self.assertRaises(ValueError):
            validate(self.document, self.root)

    def test_hidden_query_cannot_be_fabricated(self):
        row = self.document["observations"][0]
        self.complete(row, [])
        row["visible_search_queries"]["queries"] = ["invented internal query"]
        with self.assertRaises(ValueError):
            validate(self.document, self.root)

    def test_snapshot_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            artifact_path(self.root, "../secret.txt")


class MetricTests(unittest.TestCase):
    def test_normalization_preserves_content_query_and_deduplicates_tracking(self):
        self.assertEqual(normalize_url("https://WWW.EXAMPLE.com/a?id=2&utm_source=test#part"), "https://example.com/a?id=2")
        self.assertNotEqual(normalize_url("https://example.com/a?id=1"), normalize_url("https://example.com/a?id=2"))

    def test_domain_suffix_has_boundary(self):
        self.assertTrue(domain_present([{"url": "https://help.slack.com/article"}], "slack.com"))
        self.assertFalse(domain_present([{"url": "https://notslack.com/article"}], "slack.com"))

    def test_js_known_limits_symmetry_and_validation(self):
        self.assertEqual(js_divergence({"a": 1}, {"a": 1}), 0)
        self.assertEqual(js_divergence({"a": 1}, {"b": 1}), 1)
        self.assertAlmostEqual(js_divergence({"a": 0.2, "b": 0.8}, {"a": 1}), js_divergence({"a": 1}, {"a": 0.2, "b": 0.8}))
        for invalid in ({}, {"a": 2}, {"a": math.nan}, {"a": -1, "b": 2}):
            with self.assertRaises(ValueError):
                js_divergence(invalid, {"a": 1})

    def test_no_citation_keeps_probability_mass(self):
        actual = source_distribution([{"citations": []}, {"citations": [{"url": "https://a.example"}]}])
        self.assertEqual(actual, {"__no_citation__": 0.5, "a.example": 0.5})
        with self.assertRaises(ValueError):
            source_distribution([])

    def test_two_repeats_have_wide_uncertainty(self):
        self.assertLess(wilson_interval(2, 2)[0], 0.35)
        self.assertGreater(wilson_interval(0, 2)[1], 0.65)
        with self.assertRaises(ValueError):
            wilson_interval(0, 0)


class NativeSeedTests(unittest.TestCase):
    def capture(self, payload, status=200):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        session = Mock()
        session.get.return_value = response
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name) / "seed"
        with patch.dict(os.environ, {"SERPAPI_API_KEY": "fake-unit-test-secret"}), patch("requests.Session", return_value=session):
            result = capture_seed("q01", "serpapi", directory)
        self.assertEqual(session.get.call_count, 1)
        self.assertEqual(result["request_count"], 1)
        return result, directory

    def test_native_order_retained_and_response_secret_redacted(self):
        result, directory = self.capture({"search_metadata": {"status": "Success"},
            "search_parameters": {"api_key": "fake-unit-test-secret"},
            "organic_results": [{"link": "https://b.example", "position": 2}, {"link": "https://a.example", "position": 1}]})
        self.assertEqual([r["href"] for r in result["results"]], ["https://b.example", "https://a.example"])
        self.assertTrue(result["redacted"])
        self.assertFalse(any(b"fake-unit-test-secret" in p.read_bytes() for p in directory.iterdir()))

    def test_provider_error_not_converted_to_empty_success(self):
        result, _ = self.capture({"error": "Quota error"}, 429)
        self.assertEqual(result["status"], "failed")

    def test_explicit_successful_empty_is_retained(self):
        result, _ = self.capture({"search_metadata": {"status": "Success"}, "organic_results": []})
        self.assertEqual(result["status"], "empty")

    def test_invalid_success_payload_rejected(self):
        result, _ = self.capture({})
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
