"""Derived, offline contract tests; no human labels or model ability evidence."""
import asyncio
import copy
import csv
import hashlib
import io
import json
import socket
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from geo_search_lab.research.api import create_demo
from geo_search_lab.research.contracts import (FixtureResponse, OutputInput, Review, StudyInput,
                                              ErrorAnnotation, strict_json, validate_grade)
from geo_search_lab.research.corpus import draft_corpus, manifest
from geo_search_lab.research.grading import Grader, PROFILE, cost_microusd, grader_request
from geo_search_lab.research.reporting import coverage, error_bounds, csv_export, summarize
from geo_search_lab.research.store import ResearchStore
from geo_search_lab.workbench.api import create_app
from geo_search_lab.workbench.contracts import Conflict

HEADERS = {"X-Lab-Request": "1"}


def labels(case, state="uncertain", queries=None):
    return [{"item_id": i["item_id"], "coverage": state, "conflict": "absent",
             "evidence": [{"query_index": 1, "quote": queries[0]}] if state in ("complete", "partial") else [], "note": "derived fixture"}
            for i in case["rubric"]]


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ResearchStore(Path(self.tmp.name) / "research")
        self.value = StudyInput(study_id="test", version="v1", seed=7, data_kind="fixture", cases=draft_corpus())
        self.store.create(self.value)
        self.doc = self.store.study("test")
        self.dev = [s for s in self.doc["slots"] if s["partition"] == "development" and s["reference"]]
        self.slot = self.dev[0]
        self.grader = Grader(self.store)
        # Windows creates a loopback socket pair when constructing its event loop.
        # Construct it before the outbound-network guard; model code runs under the guard.
        self.runner = asyncio.Runner()
        self.runner.get_loop()
        self.addCleanup(self.runner.close)
        # Any accidental socket use during these tests is a failure.
        self.addCleanup(patch.stopall)
        patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")).start()

    def output(self, slot=None, status="succeeded"):
        s = slot or self.slot
        raw = '["synthetic search"]'
        output = OutputInput(source_run_id="source-" + s["slot_id"], profile_id="fixture-v1", model_digest="fixture-digest",
                             source_sha256=hashlib.sha256(raw.encode()).hexdigest(), raw_output=raw,
                             started_at="2026-10-02T00:00:00Z", finished_at="2026-10-02T00:00:01Z",
                             status=status, queries=["synthetic search"] if status == "succeeded" else [],
                             error=None if status == "succeeded" else "fixture failure")
        self.store.import_output("test", s["slot_id"], output)
        return output

    def review(self, s=None, revision=0):
        s = s or self.slot
        return self.store.save_review("test", s["slot_id"], Review(expected_revision=revision, reviewer_id="human-1", labels=labels(s["case"])))

    def ready(self):
        for s in self.dev:
            self.output(s)
            self.review(s)

    def run_grade(self, response=None, slot=None, ordinal=1):
        s = slot or self.slot
        response = response or FixtureResponse(content=json.dumps({"labels": labels(s["case"])}))
        return self.runner.run(self.grader.run_fixture("test", s["slot_id"], ordinal, response))

    def test_manifest_reproducible_balanced_and_paired(self):
        self.assertEqual(manifest(self.value), manifest(self.value))
        reversed_value = self.value.model_copy(update={"cases": list(reversed(self.value.cases))})
        self.assertEqual(manifest(self.value)["slots"], manifest(reversed_value)["slots"])
        self.assertEqual(len(self.doc["slots"]), 300)
        self.assertEqual(len(self.dev), 36)
        audit = [s for s in self.doc["slots"] if s["partition"] == "audit" and s["reference"]]
        self.assertEqual(len(audit), 24)
        self.assertEqual(Counter(s["case"]["category"] for s in self.dev), {c: 6 for c in Counter(s["case"]["category"] for s in self.dev)})
        self.assertEqual(sum(s["diagnostic"] for s in self.dev), 12)
        for f in {s["family_id"] for s in self.doc["slots"]}:
            slots = [s for s in self.doc["slots"] if s["family_id"] == f]
            self.assertEqual(len({s["partition"] for s in slots}), 1)
            self.assertEqual(len({s["repeat"] for s in slots if s["reference"]}), 1)
        by_id = {s["slot_id"]: s for s in self.doc["slots"]}
        order = self.doc["execution_order"]
        self.assertEqual(len(set(order)), 300)
        for a, b in zip(order[::2], order[1::2]):
            self.assertEqual(by_id[a]["family_id"], by_id[b]["family_id"])
            self.assertEqual(by_id[a]["generation_seed"], by_id[b]["generation_seed"])
            self.assertNotEqual(by_id[a]["arm"], by_id[b]["arm"])

    def test_manifest_is_frozen_and_imported_drafts_rejected(self):
        self.store.create(self.value)
        with self.assertRaises(Conflict):
            self.store.create(self.value.model_copy(update={"seed": 8}))
        with self.assertRaises(ValueError):
            self.store.create(self.value.model_copy(update={"study_id": "real", "data_kind": "imported"}))
        value = self.value.model_copy(deep=True)
        value.cases[1].family_id = value.cases[0].family_id
        with self.assertRaises(ValueError):
            manifest(value)

    def test_audit_and_nonreference_inaccessible(self):
        for s in self.doc["slots"]:
            if s["partition"] == "audit" or not s["reference"]:
                with self.assertRaises(Conflict):
                    self.store.blind("test", s["slot_id"])
        audit = next(s for s in self.doc["slots"] if s["partition"] == "audit")
        with self.assertRaises(Conflict): self.output(audit)
        with self.assertRaises(Conflict): self.review(audit)
        with self.assertRaises(Conflict): self.run_grade(slot=audit)

    def test_blind_view_and_analysis_gate(self):
        self.output()
        self.run_grade()
        doc = self.store.blind("test", self.slot["slot_id"])
        for hidden in ("arm", "temperature", "profile_id", "model_digest", "raw_output", "grader_attempts", "diagnostic", "family_id"):
            self.assertNotIn(hidden, doc)
        with self.assertRaises(Conflict): self.store.analysis_records("test")
        self.review()
        with self.assertRaises(Conflict): self.store.analysis_records("test")

    def test_versions_and_reviewer_identity(self):
        self.output()
        self.review()
        self.review(revision=1)
        self.assertEqual([r["revision"] for r in self.store.reviews("test", self.slot["slot_id"])], [1, 2])
        with self.assertRaises(Conflict): self.review()
        with self.assertRaises(Conflict):
            self.store.save_review("test", self.slot["slot_id"], Review(expected_revision=2, reviewer_id="other", labels=labels(self.slot["case"])))

    def test_failed_outputs_are_not_replaceable_or_zero_scored(self):
        self.output(status="failed")
        with self.assertRaises(Conflict): self.output()
        with self.assertRaises(Conflict): self.review()
        self.assertEqual(self.store.overview("test")["counts"]["failed"], 1)

    def test_output_integrity_and_timestamp_validation(self):
        out = self.output()
        for changes in ({"started_at": "2026-10-02"}, {"finished_at": "2026-10-01T00:00:00Z"}, {"queries": [" "]}, {"status": "failed"}):
            with self.assertRaises(ValidationError): OutputInput.model_validate({**out.model_dump(), **changes})
        with self.store.connect() as db:
            db.execute("UPDATE outputs SET document='{}'")
        with self.assertRaises(Conflict): self.store.output("test", self.slot["slot_id"])

    def test_same_source_run_cannot_fill_multiple_slots(self):
        value=self.output()
        with self.assertRaises(Conflict): self.store.import_output("test",self.dev[1]["slot_id"],value)

    def test_all_transport_failures_pause_without_retry(self):
        for s,fault in zip(self.dev[:3],("network","rate_limit","account")):
            self.output(s)
            result=self.run_grade(FixtureResponse(fault=fault),s)
            self.assertEqual(result["status"],"failed")
            self.assertTrue(self.grader.budget()["paused"])
            with self.assertRaises(Conflict): self.run_grade(slot=s)
            self.grader.reconcile_fixture(result["attempt_id"],0,"fixture termination and usage verified")

    def test_unexpected_usage_keeps_reservation_and_pauses(self):
        self.output()
        result=self.run_grade(FixtureResponse(input_tokens=100000))
        self.assertEqual(result["error"],"usage_exceeds_reserved_bound")
        self.assertTrue(self.grader.budget()["paused"])
        self.assertGreater(self.grader.budget()["reserved"],0)

    def test_concurrent_reservations_admit_only_one(self):
        from concurrent.futures import ThreadPoolExecutor
        self.output();self.output(self.dev[1])
        def reserve(slot):
            try: return self.grader.reserve("test",slot["slot_id"],1)[0]
            except Conflict: return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(reserve,self.dev[:2]))
        self.assertEqual(sum(v is not None for v in results),1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM grades").fetchone()[0],1)

    def test_strict_labels_and_evidence(self):
        out = self.output().model_dump()
        valid = {"labels": labels(self.slot["case"], "complete", out["queries"])}
        validate_grade(valid, self.slot["case"], out)
        mutations = []
        for field, value in (("coverage", True), ("coverage", "pending"), ("coverage", "ｃｏｍｐｌｅｔｅ"), ("conflict", "missing"), ("item_id", "unknown"), ("evidence", [])):
            m=copy.deepcopy(valid);m["labels"][0][field]=value;mutations.append(m)
        for evidence in ([{"query_index": 99, "quote": "synthetic"}], [{"query_index": 1, "quote": "task-only evidence"}], [{"query_index": True, "quote": "synthetic"}]):
            m=copy.deepcopy(valid);m["labels"][0]["evidence"]=evidence;mutations.append(m)
        mutations.extend([{"labels": valid["labels"][:-1]}, {"labels": valid["labels"]+[valid["labels"][0]]}])
        for m in mutations:
            with self.subTest(m=m):
                with self.assertRaises(ValueError): validate_grade(m, self.slot["case"], out)

    def test_strict_json_malformed_bounded_corpus(self):
        invalid = ['{"labels":[],"labels":[]}', '{"x":NaN}', '{"x":Infinity}', '{"x":1} trailing', '{', '\x00', '```json\n{}\n```']
        for raw in invalid:
            with self.subTest(raw=repr(raw)):
                with self.assertRaises(ValueError): strict_json(raw)

    def test_grader_prompt_is_blind_and_has_fixed_controls(self):
        out=self.output().model_dump();request=grader_request(self.slot["case"],out)
        body=json.loads(request["messages"][1]["content"])
        self.assertEqual(set(body), {"task", "rubric", "reference_at", "queries"})
        self.assertEqual(request["max_tokens"],4096)
        self.assertEqual(request["reasoning_effort"],"low")
        self.assertNotIn("temperature", request)

    def test_first_grade_cannot_retry_and_cost_persists(self):
        self.output();result=self.run_grade()
        self.assertEqual(result["status"],"succeeded")
        self.assertNotIn("grade",result)
        with self.assertRaises(Conflict): self.run_grade()
        self.assertEqual(self.grader.budget()["charged"], cost_microusd(100,100))
        self.assertEqual(Grader(ResearchStore(self.store.root)).budget(),self.grader.budget())

    def test_invalid_and_truncated_grades_keep_original_response(self):
        for s, response in zip(self.dev[:2],[FixtureResponse(content='{"labels":[] }'),FixtureResponse(content='partial',finish_reason="length")]):
            self.output(s);result=self.run_grade(response,s)
            self.assertEqual(result["status"],"failed")
            with self.store.connect() as db:
                row=db.execute("SELECT response FROM grades WHERE id=?",(result["attempt_id"],)).fetchone()
            self.assertEqual(json.loads(row[0])["content"],response.content)
        self.assertFalse(self.grader.budget()["paused"])

    def test_budget_rejection_happens_before_new_attempt(self):
        self.output()
        with self.store.connect() as db: db.execute("UPDATE budget SET ceiling=1")
        with self.assertRaises(Conflict): self.run_grade()
        with self.store.connect() as db: self.assertEqual(db.execute("SELECT COUNT(*) FROM grades").fetchone()[0],0)

    def test_timeout_reservation_pause_reconcile_no_reissue(self):
        self.output();result=self.run_grade(FixtureResponse(fault="timeout"))
        self.assertTrue(self.grader.budget()["paused"])
        self.assertGreater(self.grader.budget()["reserved"],0)
        self.output(self.dev[1])
        with self.assertRaises(Conflict): self.run_grade(slot=self.dev[1])
        self.grader.reconcile_fixture(result["attempt_id"],0,"fixture server confirmed stopped; zero fixture cost")
        self.assertFalse(self.grader.budget()["paused"])
        with self.assertRaises(Conflict): self.run_grade()
        self.assertEqual(self.run_grade(slot=self.dev[1])["status"],"succeeded")

    def test_total_deadline_includes_validation(self):
        self.output()
        with patch("geo_search_lab.research.grading.monotonic", side_effect=[0,181,182]):
            result=self.run_grade()
        self.assertEqual(result["error"],"timeout_completion_or_billing_unknown")

    def test_restart_and_serial_dispatch_guards(self):
        self.output();self.output(self.dev[1]);attempt,*_=self.grader.reserve("test",self.slot["slot_id"],1)
        with self.assertRaises(Conflict): self.run_grade(slot=self.dev[1])
        self.assertEqual(self.grader.recover_interrupted(),1)
        self.assertTrue(self.grader.budget()["paused"])
        self.grader.reconcile_fixture(attempt,10,"offline restart fixture reconciled")
        with self.assertRaises(Conflict): self.run_grade()

    def test_repeat_diagnostic_preserves_failed_primary(self):
        s=next(s for s in self.dev if s["diagnostic"]);self.output(s)
        self.run_grade(FixtureResponse(content="invalid"),s)
        self.run_grade(slot=s,ordinal=2);self.run_grade(slot=s,ordinal=3)
        with self.assertRaises(ValueError): self.run_grade(slot=s,ordinal=4)
        with self.store.connect() as db:
            rows=db.execute("SELECT ordinal,status FROM grades WHERE slot=? ORDER BY ordinal",(s["slot_id"],)).fetchall()
        self.assertEqual([tuple(r) for r in rows],[(1,"failed"),(2,"succeeded"),(3,"succeeded")])
        other=next(s for s in self.dev if not s["diagnostic"]);self.output(other)
        with self.assertRaises(ValueError): self.run_grade(slot=other,ordinal=2)

    def test_summary_export_reference_denominators_and_csv_safety(self):
        self.ready();records=self.store.analysis_records("test");result=summarize(records)
        self.assertEqual(result["reference_slots"],36)
        self.assertIsNone(result["formal_pass"])
        for cohort in result["cohorts"]:
            self.assertEqual(cohort["scores"]["need"]["human_mean_bounds"],{"lower":0.0,"upper":1.0})
            self.assertEqual(cohort["scores"]["need"]["paired_grade_families"],0)
        records[0]["reviews"][0]["labels"][0]["note"]=" =HYPERLINK(\"bad\")"
        data=csv_export(records)
        self.assertIn("' =HYPERLINK",data)
        rows=list(csv.DictReader(io.StringIO(data.lstrip("\ufeff"))))
        self.assertEqual(len({r["slot_id"] for r in rows}),36)
        self.assertTrue(all(r["review_at"] for r in rows))

    def test_analysis_uses_first_labels_and_never_promotes_diagnostic(self):
        self.ready()
        s=next(s for s in self.dev if s["diagnostic"])
        self.run_grade(FixtureResponse(content="invalid"),s)
        self.run_grade(slot=s,ordinal=2)
        result=summarize(self.store.analysis_records("test"))
        self.assertEqual(sum(c["scores"]["need"]["paired_grade_families"] for c in result["cohorts"]),0)
        changed=Review(expected_revision=1,reviewer_id="human-1",labels=labels(s["case"],"complete",["synthetic search"]))
        self.store.save_review("test",s["slot_id"],changed)
        result=summarize(self.store.analysis_records("test"))
        self.assertTrue(all(c["scores"]["need"]["human_mean_bounds"]["lower"]==0 for c in result["cohorts"]))

    def test_paired_bias_and_macro_agreement_have_known_values(self):
        self.ready()
        family=self.slot["family_id"]
        pair=[s for s in self.dev if s["family_id"]==family]
        for s in pair:
            state="missing" if s["arm"]=="A" else "complete"
            self.run_grade(FixtureResponse(content=json.dumps({"labels":labels(s["case"],state,["synthetic search"])})),s)
        result=summarize(self.store.analysis_records("test"))
        effect=result["temperature_effect_grader_discrepancy"][0]
        self.assertEqual(effect["paired_families"],1)
        # Human is unresolved [0,1] in both arms: differential error bound [0,2].
        self.assertEqual(effect["mean_bounds"],{"lower":0.0,"upper":2.0})
        self.assertTrue(all(c["scores"]["need"]["macro_label_agreement"]==0 for c in result["cohorts"]))

    def test_critical_error_requires_review_and_attempt_link(self):
        self.output();result=self.run_grade()
        annotation=ErrorAnnotation(attempt_id=result["attempt_id"],item_id=self.slot["case"]["rubric"][0]["item_id"],
                                   categories=["unsupported_complete"],explanation="synthetic annotation workflow test")
        with self.assertRaises(Conflict): self.store.add_error("test",self.slot["slot_id"],annotation)
        self.ready()
        saved=self.store.add_error("test",self.slot["slot_id"],annotation)
        self.assertEqual(saved["human_review_revision"],1)
        with self.assertRaises(ValueError): self.store.add_error("test",self.dev[1]["slot_id"],annotation)
        summary=summarize(self.store.analysis_records("test"))
        self.assertEqual(summary["critical_error_annotation_counts"],{"unsupported_complete":1})

    def test_uncertainty_bounds_no_partial_credit(self):
        rubric=[{"item_id":str(i),"kind":"need"} for i in range(4)]
        rows=[{"item_id":str(i),"coverage":v,"conflict":"absent"} for i,v in enumerate(("complete","partial","missing","uncertain"))]
        c=coverage(rows,rubric)
        self.assertEqual((c["lower"],c["upper"]),(0.25,0.5))
        self.assertEqual(error_bounds(c,c)["absolute_lower"],0)


class ResearchApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=create_app(Path(self.tmp.name),offline_mode=True)
        self.client=TestClient(self.app);self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)

    def test_demo_routes_blinding_audit_lock_and_mutation_guard(self):
        self.assertEqual(self.client.post("/api/v1/research/demo",json={"study_id":"demo"}).status_code,403)
        r=self.client.post("/api/v1/research/demo",json={"study_id":"demo"},headers=HEADERS)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()["counts"]["pending_review"],36)
        sid=r.json()["slots"][0]["slot_id"]
        blind=self.client.get(f"/api/v1/research/studies/demo/slots/{sid}").json()
        self.assertNotIn("temperature",blind)
        self.assertNotIn("model_digest",blind)
        audit=next(s for s in self.app.state.research_store.study("demo")["slots"] if s["partition"]=="audit")
        self.assertEqual(self.client.get(f"/api/v1/research/studies/demo/slots/{audit['slot_id']}").status_code,409)
        for suffix in ("analysis","export/json","export/csv"):
            self.assertEqual(self.client.get("/api/v1/research/studies/demo/"+suffix).status_code,409)
        self.assertEqual(self.client.get("/").status_code,200)
        self.assertEqual(self.client.get("/research").status_code,200)

    def test_offline_service_cannot_dispatch_legacy_model(self):
        value={"prompt":"test","input_mode":"empty_ablation"}
        self.assertEqual(self.client.post("/api/v1/runs",json=value,headers=HEADERS).status_code,409)
        self.assertEqual(self.client.post("/api/v1/runs/whatever/replay",headers=HEADERS).status_code,409)
        self.assertTrue(self.client.get("/api/v1/health").json()["offline_mode"])


if __name__ == "__main__":
    unittest.main()
