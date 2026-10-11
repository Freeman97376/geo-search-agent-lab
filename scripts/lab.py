"""Local workbench server, offline import, and automation-friendly JSON CLI."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")


def main():
    from geo_search_lab.workbench.service import DEFAULT_STORE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--offline", action="store_true", help="Disable legacy model dispatch; open research review")
    serve.add_argument("--workspace", action="store_true", help="Independent local planning and explicit SerpAPI search")
    sub.add_parser("import-pilot")
    listing = sub.add_parser("list")
    listing.add_argument("--experiment")
    run = sub.add_parser("run")
    run.add_argument("--request", type=Path, required=True)
    run.add_argument("--wait", action="store_true")
    for name in ("get", "export", "events", "replay"):
        command = sub.add_parser(name)
        command.add_argument("run_id")
    evaluation = sub.add_parser("evaluate")
    evaluation.add_argument("run_ids", nargs="+")
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        from geo_search_lab.workbench.api import create_app
        uvicorn.run(create_app(args.store, offline_mode=args.offline, workspace_mode=args.workspace), host="127.0.0.1", port=args.port, workers=1)
        return 0
    if args.command == "import-pilot":
        from geo_search_lab.workbench.service import LabService
        from geo_search_lab.workbench.store import Store
        print(json.dumps({"imported_run_ids": LabService(Store(args.store)).import_pilot()}))
        return 0
    from urllib.parse import urlsplit
    parsed = urlsplit(args.url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.username or parsed.password or parsed.query or parsed.path not in ("", "/"):
        raise SystemExit("CLI only connects to an explicit loopback service")
    import httpx
    with httpx.Client(base_url=args.url, trust_env=False, timeout=30, headers={"X-Lab-Request": "1"}) as client:
        def call(method, path, **kwargs):
            response = client.request(method, "/api/v1" + path, **kwargs)
            if response.is_error:
                raise SystemExit(response.text)
            return response.json()
        if args.command == "run":
            value = call("POST", "/runs", json=json.loads(args.request.read_text(encoding="utf-8-sig")))
            if args.wait:
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    state = call("GET", "/runs/" + value["run"]["id"])
                    if state["status"] not in ("queued", "running"):
                        value = call("GET", "/runs/" + state["id"] + "/export")
                        break
                    time.sleep(1)
                else:
                    value["wait_timed_out"] = True
        elif args.command == "list":
            value = call("GET", "/runs", params={"experiment_id": args.experiment} if args.experiment else {})
        elif args.command == "evaluate":
            value = call("POST", "/evaluations", json={"run_ids": args.run_ids})
        else:
            suffix = {"get": "", "export": "/export", "events": "/events", "replay": "/replay"}[args.command]
            value = call("POST" if args.command == "replay" else "GET", "/runs/" + args.run_id + suffix)
        print(json.dumps(value, ensure_ascii=False, indent=2))
        if args.command == "run" and args.wait:
            if value.get("wait_timed_out"):
                return 2
            if value["run"]["status"] != "succeeded":
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
