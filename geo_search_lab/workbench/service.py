"""Planner orchestration, normalization and artifact export; no paid-search execution."""
import asyncio
import json
import shutil
from datetime import datetime
from pathlib import Path

from geo_search_lab.experiments.planner import PROFILE, run_trial
from geo_search_lab.pilot_common import ROOT, artifact_path, load_benchmark, now, read_json, sha256
from geo_search_lab.workbench.contracts import Conflict, ResultDocument, RunRequest
from geo_search_lab.workbench.store import Store, encoded

DEFAULT_STORE = ROOT / "data/workbench"


def snapshot_catalog(root=ROOT / "data/snapshots"):
    entries = {}
    for path in sorted(root.glob("*/*/seed.json")):
        value = read_json(path)
        if value.get("status") not in ("ok", "empty") or value.get("provenance") != "live_native_adapter":
            continue
        identifier = "seed_" + sha256(path.read_bytes())[:24]
        entries[identifier] = {"id": identifier, "query_id": value["query_id"], "prompt": value["query"],
                               "provider": value["provider"], "result_count": len(value["results"]),
                               "captured_at": value["captured_at"], "path": path}
    return entries


def normalize_result(run, trial, directory, *, error=None, origin="workbench"):
    request = run["request"]
    success = trial is not None and trial.get("status") == "ok" and error is None
    mode = run["mode"]
    calls = trial.get("planner_calls", []) if trial else []
    http_records = [read_json(p) for p in sorted((directory / "trace").glob("http-*.json"))]
    usages = []
    for record in http_records:
        if record["status"] == "response":
            try:
                usages.append(read_json(artifact_path(directory / "trace", record["response_file"])).get("usage", {}))
            except (ValueError, UnicodeError):
                pass
    tokens = sum(u["total_tokens"] for u in usages) if usages and all("total_tokens" in u for u in usages) else None
    artifacts = []
    for path in sorted((directory / "trace").iterdir()) if (directory / "trace").exists() else []:
        if path.is_file() and (path.name == "trial.json" or path.name.startswith("http-")):
            artifacts.append({"name": path.name, "relative_path": "trace/" + path.name,
                              "sha256": sha256(path.read_bytes()), "bytes": path.stat().st_size,
                              "media_type": "application/json" if path.suffix == ".json" else "application/octet-stream"})
    profile = trial["profile"] if trial else read_json(PROFILE)
    source = trial.get("upstream") if trial else None
    seed = trial.get("seed", {}) if trial else {}
    status = "succeeded" if success else "failed"
    finished = now()
    started = trial.get("started_at", run["created_at"]) if trial else run["created_at"]
    warnings = []
    if seed.get("mode", request["input_mode"]) in ("empty_ablation",):
        warnings.append("无初搜消融：不代表完整上游搜索流程")
    if calls and any(c.get("strict_list_of_strings") is False for c in calls):
        warnings.append("模型原始输出并非严格JSON；查看原生修复后的结果与原始证据")
    if mode == "replay":
        warnings.append("离线重放，不能作为新的独立统计样本")
    return {
        "schema_version": "1.0", "run_id": run["id"], "kind": "planner", "status": status,
        "mode": mode, "experiment_id": request["experiment_id"], "parent_run_id": run["parent_run_id"],
        "started_at": started, "finished_at": trial.get("finished_at", finished) if trial else finished,
        "input": {"prompt": request["prompt"], "prompt_sha256": sha256(request["prompt"].encode()),
                  "facets": request["facets"], "constraints": request["constraints"],
                  "input_mode": request["input_mode"], "snapshot_id": request["snapshot_id"]},
        "configuration": {"profile": profile, "profile_sha256": sha256(encoded(profile).encode()),
                          "upstream": source, "seed_mode": seed.get("mode", request["input_mode"]),
                          "provider": seed.get("snapshot", {}).get("provider"),
                          "prompt_time": trial.get("prompt_time") if trial else None},
        "output": {"sub_queries": trial.get("sub_queries") if success else None,
                   "planned_execution_queries": trial.get("planned_execution_queries") if success else None,
                   "answer": None, "citations": None},
        "metrics": {"normalized_query_count": trial.get("query_count") if success else None,
                    "exact_duplicate_count": trial.get("duplicate_count") if success else None,
                    "strict_json": all(c.get("strict_list_of_strings") is True for c in calls) if success and calls else None,
                    "initial_result_count": len(seed["results"]) if "results" in seed else None,
                    "model_requests": 0 if mode == "replay" else (trial.get("http_request_count", 0) if trial else 0),
                    "model_tokens": 0 if mode == "replay" else tokens,
                    "model_http_seconds": None if mode == "replay" else sum(h["elapsed_ms"] for h in http_records) / 1000 if http_records else None,
                    "replay_exact_match": trial.get("replay_exact_match") if trial else None},
        "evaluation": {"state": "needs_review", "quality_score": None, "platform_gap": None,
                       "citation_comparison_eligible": False},
        "provenance": {"origin": origin, "trial_schema": trial.get("schema_version") if trial else None,
                       "normalizer_version": "1.0"},
        "error": error or ({"code": trial.get("error_type", "planner_failed"), "message": trial.get("error", "原生规划失败")} if trial and not success else None),
        "warnings": warnings, "artifacts": artifacts}


class LabService:
    def __init__(self, store: Store, *, runner=run_trial, snapshots=None):
        self.store, self.runner = store, runner
        self.snapshots = snapshots

    def catalog(self):
        return self.snapshots if self.snapshots is not None else snapshot_catalog()

    def submit(self, request: RunRequest):
        value = request.model_dump()
        if value["input_mode"] == "snapshot":
            snapshot = self.catalog().get(value["snapshot_id"])
            if snapshot is None or snapshot["prompt"] != value["prompt"]:
                raise ValueError("初搜快照不存在或与问题不一致；不能将旧问题的结果套到新问题上")
        return self.store.create(value)

    def replay(self, run_id, client_request_id=None):
        parent = self.store.get(run_id)
        result = self.store.result(run_id)
        if result["status"] != "succeeded" or parent["mode"] == "replay":
            raise Conflict("只能重放成功的原始Live运行")
        value = dict(parent["request"], client_request_id=client_request_id)
        # Make replay idempotency distinct from inference by including its source in the stored request.
        value["replay_source"] = run_id
        return self.store.create(value, mode="replay", parent=run_id)

    async def execute(self, run_id):
        if not self.store.start(run_id):
            return
        run = self.store.get(run_id)
        request = run["request"]
        directory = self.store.directory(run_id)
        trial = None
        try:
            snapshot = None
            replay = None
            if run["mode"] == "replay":
                parent_result = self.store.result(run["parent_run_id"])
                for entry in parent_result["artifacts"]:
                    self.store.artifact(run["parent_run_id"], entry["name"])
                replay = self.store.directory(run["parent_run_id"]) / "trace"
                original = read_json(replay / "trial.json")
                case_id = original["query_id"]
            elif request["input_mode"] == "snapshot":
                selected = self.catalog().get(request["snapshot_id"])
                if selected is None:
                    raise ValueError("排队期间初搜快照发生变化")
                snapshot, case_id = selected["path"], selected["query_id"]
            else:
                case_id = "custom_" + sha256(request["prompt"].encode())[:20]
            case = {"id": case_id, "query": request["prompt"], "facets": request["facets"], "constraints": request["constraints"]}
            trial = await self.runner(case_id, 1, directory / "trace", snapshot=snapshot, replay=replay,
                                      case_override=case, on_event=lambda kind, data: self.store.event(run_id, kind, data))
            result = ResultDocument.model_validate(normalize_result(run, trial, directory)).model_dump()
        except Exception as exc:
            error = {"code": type(exc).__name__, "message": str(exc)[:600]}
            self.store.event(run_id, "run.error", error)
            path = directory / "trace/trial.json"
            trial = read_json(path) if path.exists() else None
            result = ResultDocument.model_validate(normalize_result(run, trial, directory, error=error)).model_dump()
        self.store.finish(run_id, result)

    def import_pilot(self, root=ROOT / "data/live_runs/commercial-planner-v1"):
        # Only this project's recorded trials can be indexed. No arbitrary host file import over HTTP.
        root = root.resolve()
        if not root.is_relative_to((ROOT / "data/live_runs").resolve()):
            raise ValueError("只允许导入本实验项目的既有Live记录")
        cases = {c["id"]: c for c in load_benchmark()["cases"]}
        imported = []
        for path in sorted(root.glob("*/trial.json")):
            trial = read_json(path)
            if trial.get("kind") != "planner_only" or trial.get("mode") != "live_local_llm" or trial.get("status") != "ok":
                continue
            case = cases.get(trial["query_id"], {})
            snapshot = next((s for s in self.catalog().values()
                             if s["prompt"] == trial["query"] and s["id"] == "seed_" + trial["seed"].get("snapshot_sha256", "")[:24]), None)
            if trial["seed"]["mode"] == "fixed_initial_search" and snapshot is None:
                raise ValueError("导入前需要保留原初搜快照")
            request = RunRequest(prompt=trial["query"], title=path.parent.name, experiment_id="commercial-pilot-v1",
                input_mode="snapshot" if snapshot else "empty_ablation", snapshot_id=snapshot["id"] if snapshot else None,
                facets=list(case.get("facets", {}).values()), constraints=case.get("constraints", []),
                client_request_id="import_" + sha256(path.read_bytes())[:40])
            run, created = self.store.create(request.model_dump())
            if created:
                self.store.start(run["id"])
                destination = self.store.directory(run["id"]) / "trace"
                destination.mkdir()
                for artifact in path.parent.iterdir():
                    if artifact.is_file() and (artifact.name == "trial.json" or artifact.name.startswith("http-")):
                        shutil.copyfile(artifact, destination / artifact.name)
                self.store.finish(run["id"], normalize_result(run, trial, self.store.directory(run["id"]), origin="imported_pilot"))
            imported.append(run["id"])
        return imported

    def export(self, run_id):
        run = self.store.get(run_id)
        result = self.store.result(run_id) if run["result_hash"] else None
        events, cursor = [], 0
        while True:
            page = self.store.events(run_id, cursor)
            events.extend(page["items"])
            if not page["items"]:
                break
            cursor = page["next_after"]
        return {"schema_version": "1.0", "run": run, "result": result,
                "review": self.store.latest_review(run_id), "review_history": self.store.review_history(run_id), "events": events}
