"""Summarize actual native-planner runs without treating format success as research quality."""
import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geo_search_lab.pilot_common import load_benchmark, now, read_json, sha256, write_json


def summarize(live_root, replay_root):
    report = {"kind": "planner_only", "created_at": now(), "trials": [], "manual_review": [],
              "claims": {"full_agent_baseline": False, "platform_similarity_established": False,
                         "query_quality_established": False, "replay_equals_new_live_generation": False}}
    times = []
    for case in load_benchmark()["cases"]:
        for repeat in (1, 2):
            identifier = f"{case['id']}-r{repeat}"
            directory = live_root / identifier
            path = directory / "trial.json"
            trial = read_json(path)
            replay = read_json(replay_root / identifier / "trial.json")
            http = [read_json(p) for p in sorted(directory.glob("http-*.json"))]
            usage = [json.loads((directory / r["response_file"]).read_bytes()).get("usage", {})
                     for r in http if r["status"] == "response"]
            elapsed = sum(h["elapsed_ms"] for h in http)
            times.append(elapsed)
            report["trials"].append({"id": identifier, "status": trial["status"],
                "sub_queries": trial.get("sub_queries"), "query_count": trial.get("query_count"),
                "strict_json_before_upstream_normalization": all(c.get("strict_list_of_strings", False) for c in trial["planner_calls"]),
                "http_requests": trial["http_request_count"], "http_elapsed_ms": elapsed,
                "local_reported_tokens": sum(u.get("total_tokens", 0) for u in usage),
                "initial_result_count": len(trial["seed"]["results"]),
                "replay_status": replay["status"], "replay_exact_match": replay.get("replay_exact_match"),
                "trial_sha256": sha256(path.read_bytes()), "replay_trial_sha256": sha256((replay_root / identifier / "trial.json").read_bytes())})
            report["manual_review"].append({"id": identifier, "reviewer": None,
                "facets": {key: {"covered": None, "query_indices": [], "note": None} for key in case["facets"]},
                "constraints": {value: {"preserved": None, "note": None} for value in case["constraints"]},
                "semantic_redundancy": None, "unsupported_assumptions": None})
    rows = report["trials"]
    report["summary"] = {"trials": len(rows), "successful_trials": sum(r["status"] == "ok" for r in rows),
        "strict_json_trials": sum(r["strict_json_before_upstream_normalization"] for r in rows),
        "exact_replays": sum(r["replay_exact_match"] is True for r in rows),
        "local_http_requests": sum(r["http_requests"] for r in rows),
        "local_reported_tokens": sum(r["local_reported_tokens"] for r in rows),
        "median_llm_http_seconds": statistics.median(times) / 1000}
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-root", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.live_root, args.replay_root)
    write_json(args.output, result)
    print(json.dumps(result["summary"]))


if __name__ == "__main__":
    main()
