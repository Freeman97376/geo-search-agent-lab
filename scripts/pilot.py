"""CLI: bounded pilot preparation, native planning, and exact offline replay."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")
from geo_search_lab.pilot_common import ROOT, load_benchmark, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-web")
    prepare.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("plan")
    run.add_argument("--query", required=True)
    run.add_argument("--repeat", type=int, choices=(1, 2), default=1)
    run.add_argument("--output", type=Path, required=True)
    mode = run.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot", type=Path)
    mode.add_argument("--empty-ablation", action="store_true")
    replay = sub.add_parser("replay")
    replay.add_argument("--source", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    capture = sub.add_parser("capture-seed")
    capture.add_argument("--query", required=True)
    capture.add_argument("--provider", choices=("searx", "serpapi"), required=True)
    capture.add_argument("--output", type=Path, required=True)
    report = sub.add_parser("compare")
    report.add_argument("--observations", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare-web":
        from geo_search_lab.evaluation.observations import prepare_web
        prepare_web(args.output)
        print("Prepared 36 pending web observations; no platform calls made")
    elif args.command == "capture-seed":
        from geo_search_lab.adapters.seed_capture import capture_seed
        result = capture_seed(args.query, args.provider, args.output)
        print(result["status"], result.get("result_count", 0))
        return 0 if result["status"] in ("ok", "empty") else 1
    elif args.command in ("plan", "replay"):
        from geo_search_lab.experiments.planner import run_trial
        if args.command == "replay":
            original = read_json(args.source / "trial.json")
            result = asyncio.run(run_trial(original["query_id"], original["repeat"], args.output, replay=args.source))
        else:
            result = asyncio.run(run_trial(args.query, args.repeat, args.output, snapshot=args.snapshot))
        print(result["status"], result.get("sub_queries", result.get("error_type")))
        return 0 if result["status"] == "ok" else 1
    else:
        from geo_search_lab.evaluation.observations import compare_file
        result = compare_file(args.observations, args.output)
        print(result["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
