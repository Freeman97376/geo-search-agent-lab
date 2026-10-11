"""Offline research CLI: draft preparation, demo, import and export. No network."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")


def main():
    from geo_search_lab.research.api import create_demo
    from geo_search_lab.research.contracts import StudyInput, OutputInput, Review, strict_json
    from geo_search_lab.research.corpus import draft_corpus
    from geo_search_lab.research.reporting import csv_export, summarize
    from geo_search_lab.research.store import ResearchStore
    from geo_search_lab.workbench.service import DEFAULT_STORE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE / "research")
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft")
    draft.add_argument("--output", required=True, type=Path)
    demo = commands.add_parser("demo")
    demo.add_argument("study_id")
    create = commands.add_parser("create")
    create.add_argument("--input", required=True, type=Path)
    for name in ("status", "export", "summary"):
        sub = commands.add_parser(name)
        sub.add_argument("study_id")
        if name == "export":
            sub.add_argument("--format", choices=["json", "csv"], default="json")
            sub.add_argument("--output", required=True, type=Path)
    for name in ("import-output", "review"):
        sub = commands.add_parser(name)
        sub.add_argument("study_id")
        sub.add_argument("slot_id")
        sub.add_argument("--input", required=True, type=Path)
    args = parser.parse_args()

    def write_new(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="") as f:
            f.write(content)

    if args.command == "draft":
        value = {"study_id": "planner-draft-v1", "version": "draft-v1", "seed": 20261002,
                 "data_kind": "fixture", "cases": draft_corpus()}
        write_new(args.output, json.dumps(value, ensure_ascii=False, indent=2))
        print(json.dumps({"written": str(args.output), "cases": 30, "status": "draft"}))
        return
    store = ResearchStore(args.store)
    if args.command == "demo":
        result = create_demo(store, args.study_id)
    elif args.command == "create":
        result = store.create(StudyInput.model_validate(strict_json(args.input.read_text(encoding="utf-8-sig"))))
    elif args.command in ("import-output", "review"):
        value = strict_json(args.input.read_text(encoding="utf-8-sig"))
        result = (store.import_output(args.study_id, args.slot_id, OutputInput.model_validate(value))
                  if args.command == "import-output" else store.save_review(args.study_id, args.slot_id, Review.model_validate(value)))
    elif args.command == "status":
        result = store.overview(args.study_id)
    else:
        records = store.analysis_records(args.study_id)
        result = summarize(records)
        if args.command == "export":
            content = csv_export(records) if args.format == "csv" else json.dumps({"schema_version": "research-1", "records": records, "summary": result}, ensure_ascii=False, indent=2)
            write_new(args.output, content)
            result = {"written": str(args.output), "reference_slots": len(records)}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
