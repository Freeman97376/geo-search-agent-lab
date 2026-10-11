"""Research routes; inherited loopback/origin protections from the workbench."""
import hashlib
import json

from fastapi import APIRouter
from fastapi.responses import Response
from pydantic import Field

from geo_search_lab.pilot_common import now
from geo_search_lab.research.contracts import (ErrorAnnotation, FixtureResponse, Id, OutputInput,
                                              Review, Strict, StudyInput)
from geo_search_lab.research.corpus import draft_corpus
from geo_search_lab.research.grading import Grader
from geo_search_lab.research.reporting import csv_export, summarize


class DemoRequest(Strict):
    study_id: Id
    seed: int = Field(default=20261002, ge=0, le=2**31-1)


class Reconcile(Strict):
    actual_microusd: int = Field(ge=0)
    evidence: str = Field(min_length=1, max_length=2000)


def create_demo(store, study_id, seed=20261002):
    store.create(StudyInput(study_id=study_id, version="draft-v1", seed=seed, data_kind="fixture", cases=draft_corpus()))
    doc = store.study(study_id)
    for slot in doc["slots"]:
        if slot["partition"] != "development" or not slot["reference"]:
            continue
        if store.output(study_id, slot["slot_id"]):
            continue
        # Intentionally simple synthetic queries; no assertion of semantic correctness.
        queries = [i["text"] for i in slot["case"]["rubric"] if i["kind"] == "need"]
        raw = json.dumps(queries, ensure_ascii=False)
        store.import_output(study_id, slot["slot_id"], OutputInput(
            source_run_id="fixture_" + slot["slot_id"], profile_id="synthetic-demo-v1", model_digest="not-a-model",
            source_sha256=hashlib.sha256(raw.encode()).hexdigest(), started_at=now(), finished_at=now(),
            status="succeeded", queries=queries, raw_output=raw))
    return store.overview(study_id)


def router(store):
    routes = APIRouter(prefix="/api/v1/research")
    grader = Grader(store)

    @routes.get("/draft-corpus")
    def drafts():
        return {"schema_version": "research-1", "status": "draft_requires_public_grounding_and_human_review", "cases": draft_corpus()}

    @routes.get("/studies")
    def studies():
        return {"items": store.list_studies()}

    @routes.post("/studies")
    def create(value: StudyInput):
        return store.create(value)

    @routes.post("/demo")
    def demo(value: DemoRequest):
        return create_demo(store, value.study_id, value.seed)

    @routes.get("/studies/{study_id}")
    def overview(study_id: str):
        return store.overview(study_id)

    @routes.get("/studies/{study_id}/slots/{slot_id}")
    def blind(study_id: str, slot_id: str):
        return store.blind(study_id, slot_id)

    @routes.put("/studies/{study_id}/slots/{slot_id}/output")
    def output(study_id: str, slot_id: str, value: OutputInput):
        return store.import_output(study_id, slot_id, value)

    @routes.post("/studies/{study_id}/slots/{slot_id}/reviews")
    def review(study_id: str, slot_id: str, value: Review):
        return store.save_review(study_id, slot_id, value)

    @routes.post("/studies/{study_id}/slots/{slot_id}/grades/{ordinal}/fixture")
    async def grade(study_id: str, slot_id: str, ordinal: int, value: FixtureResponse):
        return await grader.run_fixture(study_id, slot_id, ordinal, value)

    @routes.post("/studies/{study_id}/slots/{slot_id}/critical-errors")
    def annotate(study_id: str, slot_id: str, value: ErrorAnnotation):
        return store.add_error(study_id, slot_id, value)

    @routes.get("/studies/{study_id}/analysis")
    def analysis(study_id: str):
        return summarize(store.analysis_records(study_id))

    @routes.get("/studies/{study_id}/slots/{slot_id}/result")
    def result(study_id: str, slot_id: str):
        store.slot(study_id, slot_id)
        return next(r for r in store.analysis_records(study_id) if r["slot_id"] == slot_id)

    @routes.get("/studies/{study_id}/export/{format}")
    def export(study_id: str, format: str):
        if format not in ("json", "csv"):
            raise ValueError("仅支持 JSON 和 CSV")
        rows = store.analysis_records(study_id)
        data = ({"schema_version": "research-1", "records": rows, "summary": summarize(rows)} if format == "json" else None)
        return Response(json.dumps(data, ensure_ascii=False, indent=2) if data else csv_export(rows),
                        media_type="application/json" if data else "text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{study_id}.{format}"'})

    @routes.get("/budget")
    def budget():
        return grader.budget()

    @routes.post("/attempts/{attempt_id}/reconcile-fixture")
    def reconcile(attempt_id: str, value: Reconcile):
        return grader.reconcile_fixture(attempt_id, value.actual_microusd, value.evidence)

    @routes.get("/schemas")
    def schemas():
        return {cls.__name__: cls.model_json_schema() for cls in (StudyInput, OutputInput, Review, FixtureResponse, ErrorAnnotation)}

    return routes
