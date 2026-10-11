"""One authorized batch: six native initial searches, at most one request per case."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geo_search_lab.pilot_common import load_benchmark, now, write_json
from geo_search_lab.adapters.seed_capture import capture_seed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key-env-file", type=Path)
    parser.add_argument("--key-from-existing-settings", action="store_true",
                        help="Read only the SerpAPI credential from the business SQLite configured by --key-env-file")
    args = parser.parse_args()
    key = os.environ.get("SERPAPI_API_KEY", "")
    if args.key_env_file:
        from dotenv import dotenv_values
        settings = dotenv_values(args.key_env_file, interpolate=False)
        key = settings.get("SERPAPI_API_KEY", "")
        if args.key_from_existing_settings:
            import base64
            import hashlib
            import sqlite3
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            database = Path(settings.get("LOCAL_DATABASE_PATH") or "../database/cosmetics_market.local.sqlite")
            if not database.is_absolute():
                database = args.key_env_file.resolve().parent / database
            with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
                row = connection.execute(
                    "SELECT encrypted_key,key_iv,key_auth_tag FROM api_credentials WHERE provider = ?",
                    ("serpapi",)).fetchone()
            if row and all(row) and settings.get("LOCAL_AUTH_SECRET"):
                encryption_key = hashlib.sha256(settings["LOCAL_AUTH_SECRET"].strip().encode()).digest()
                key = AESGCM(encryption_key).decrypt(base64.b64decode(row[1]),
                    base64.b64decode(row[0]) + base64.b64decode(row[2]), None).decode()
    if not key:
        raise SystemExit("SERPAPI_API_KEY was not found; no requests made")
    cases = load_benchmark()["cases"]
    if len(cases) != 6:
        raise SystemExit("This authorization is limited to the six-case pilot")
    args.output.mkdir(parents=True, exist_ok=False)
    ledger = {"provider": "serpapi", "authorized_request_limit": 6, "started_at": now(), "cases": []}
    write_json(args.output / "batch.json", ledger)
    previous = os.environ.get("SERPAPI_API_KEY")
    os.environ["SERPAPI_API_KEY"] = key
    try:
        for case in cases:
            # A reservation survives interruption; this script never resumes or retries a batch.
            row = {"query_id": case["id"], "status": "reserved"}
            ledger["cases"].append(row)
            write_json(args.output / "batch.json", ledger, replace=True)
            result = capture_seed(case["id"], "serpapi", args.output / case["id"])
            row.update(status=result["status"], request_count=result["request_count"],
                       result_count=result.get("result_count"), raw_sha256=result["raw_sha256"])
            write_json(args.output / "batch.json", ledger, replace=True)
            print(case["id"], row["status"], "requests", row["request_count"], flush=True)
    finally:
        if previous is None:
            os.environ.pop("SERPAPI_API_KEY", None)
        else:
            os.environ["SERPAPI_API_KEY"] = previous
        ledger["finished_at"] = now()
        write_json(args.output / "batch.json", ledger, replace=True)
    return 0 if all(r["status"] in ("ok", "empty") for r in ledger["cases"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
