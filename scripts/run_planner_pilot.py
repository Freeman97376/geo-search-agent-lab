"""Run two local native-planner trials per frozen seed, then replay each offline."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geo_search_lab.experiments.planner import run_trial
from geo_search_lab.pilot_common import load_benchmark, now, write_json


async def run(args):
    cases = load_benchmark()["cases"]
    for case in cases:
        if not (args.seed_root / case["id"] / "seed.json").is_file():
            raise ValueError("Every case requires an explicit seed snapshot; no empty fallback")
    args.output.mkdir(parents=True, exist_ok=False)
    args.replay_output.mkdir(parents=True, exist_ok=False)
    summary = {"kind": "planner_only", "started_at": now(), "trials": []}
    for case in cases:
        for repeat in (1, 2):
            identifier = f"{case['id']}-r{repeat}"
            live = await run_trial(case["id"], repeat, args.output / identifier,
                                   snapshot=args.seed_root / case["id"] / "seed.json")
            row = {"id": identifier, "status": live["status"], "http_request_count": live["http_request_count"]}
            if live["status"] == "ok":
                replay = await run_trial(case["id"], repeat, args.replay_output / identifier,
                                         replay=args.output / identifier)
                row.update(replay_status=replay["status"], replay_exact_match=replay.get("replay_exact_match"))
            summary["trials"].append(row)
            write_json(args.output / "batch.json", summary, replace=True)
            print(identifier, row, flush=True)
    summary["finished_at"] = now()
    write_json(args.output / "batch.json", summary, replace=True)
    return 0 if all(r["status"] == r.get("replay_status") == "ok" for r in summary["trials"]) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay-output", type=Path, required=True)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
