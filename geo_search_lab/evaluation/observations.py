"""Prepare public web prompts and validate manually captured answer/citation observations."""
from collections import Counter
from datetime import datetime
from itertools import combinations
from pathlib import Path

from geo_search_lab.evaluation.metrics import domain_present, js_divergence, normalize_url, source_distribution, wilson_interval
from geo_search_lab.pilot_common import BENCHMARK, load_benchmark, now, read_json, sha256, write_json


def prepare_web(directory: Path):
    benchmark = load_benchmark()
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "answers").mkdir()
    observations = []
    # Alternate the first platform to reduce systematic collection-order bias.
    for index, case in enumerate(benchmark["cases"]):
        (directory / f"{case['id']}-prompt.txt").write_text(case["query"], encoding="utf-8")
        for repeat in (1, 2):
            platforms = benchmark["platforms"]
            shift = (index + repeat - 1) % len(platforms)
            for platform in platforms[shift:] + platforms[:shift]:
                identifier = f"{case['id']}-{platform}-r{repeat}"
                observations.append({
                    "id": identifier, "kind": "answer", "query_id": case["id"], "repeat": repeat,
                    "query_sha256": sha256(case["query"].encode()), "platform": platform,
                    "interface": "web", "status": "pending", "captured_at": None,
                    "new_conversation": None, "mode_label": None, "model_label": None,
                    "plan_label": None, "locale": None, "region": None, "search_status": None,
                    "provenance": None, "answer_file": f"answers/{identifier}.txt", "answer_sha256": None,
                    "citations_reviewed": False, "citations": None,
                    "visible_search_queries": {"visibility": "unobserved", "queries": None},
                    "notes": None})
    document = {"schema_version": 1, "benchmark_id": benchmark["benchmark_id"],
                "benchmark_sha256": sha256(BENCHMARK.read_bytes()), "created_at": now(),
                "observations": observations}
    write_json(directory / "observations.json", document)
    return document


def validate(document, directory):
    benchmark = load_benchmark()
    if document.get("benchmark_sha256") != sha256(BENCHMARK.read_bytes()):
        raise ValueError("Observation benchmark differs from the pinned pilot")
    cases = {case["id"]: case for case in benchmark["cases"]}
    identities, keys, valid, modes = set(), set(), [], {}
    for row in document["observations"]:
        if row["id"] in identities:
            raise ValueError("Duplicate observation ID")
        identities.add(row["id"])
        if row["platform"] not in (*benchmark["platforms"], "local_agent") or row["query_id"] not in cases:
            raise ValueError("Unknown platform or question")
        key = (row["platform"], row["query_id"], row["repeat"])
        if key in keys or type(row["repeat"]) is not int or row["repeat"] not in (1, 2):
            raise ValueError("Duplicate or invalid repeated observation")
        keys.add(key)
        if row["kind"] != "answer":
            raise ValueError("Planner trials are not answer/citation observations")
        expected_interface = "local" if row["platform"] == "local_agent" else "web"
        if row["interface"] != expected_interface:
            raise ValueError("Web and API samples must not be mixed")
        if row["query_sha256"] != sha256(cases[row["query_id"]]["query"].encode()):
            raise ValueError("Changed question")
        if row["status"] not in ("pending", "blocked", "failed", "complete"):
            raise ValueError("Unknown observation status")
        if row["status"] != "complete":
            continue
        if not row["citations_reviewed"] or not isinstance(row["citations"], list):
            raise ValueError("Unknown citations are not an observed empty citation list")
        if row["new_conversation"] is not True or not row["mode_label"] or not row["plan_label"]:
            raise ValueError("Record a new conversation, actual mode and plan before comparison")
        if row["search_status"] not in ("observed", "not_observed") or row["provenance"] not in ("manual_web_capture", "browser_web_capture", "local_live_agent"):
            raise ValueError("Record actual search visibility and capture provenance")
        if (row["provenance"] == "local_live_agent") != (row["platform"] == "local_agent"):
            raise ValueError("Capture provenance does not match platform")
        if not row.get("model_label") or not row.get("locale") or not row.get("region"):
            raise ValueError("Record model, locale and region; use 'unknown' when unavailable")
        timestamp = datetime.fromisoformat(row["captured_at"])
        if timestamp.tzinfo is None:
            raise ValueError("Capture time needs an explicit time zone")
        path = (directory / row["answer_file"]).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("Answer artifact must remain inside the observation directory")
        raw = path.read_bytes()
        if not raw.strip() or sha256(raw) != row["answer_sha256"]:
            raise ValueError("Missing/changed raw answer evidence")
        for citation in row["citations"]:
            normalize_url(citation["url"])
        visible = row["visible_search_queries"]
        if visible["visibility"] == "unobserved":
            if visible["queries"] is not None:
                raise ValueError("Hidden platform queries must remain unknown")
        elif visible["visibility"] == "visible_partial":
            if not isinstance(visible["queries"], list) or not all(isinstance(q, str) for q in visible["queries"]):
                raise ValueError("Record only queries actually exposed by the platform")
        else:
            raise ValueError("Web UI cannot certify the complete internal query set")
        cohort = tuple(row[k] for k in ("mode_label", "model_label", "plan_label", "locale", "region"))
        if modes.setdefault(row["platform"], cohort) != cohort:
            raise ValueError("Separate changed model/mode/account-tier/locale cohorts into different files")
        valid.append(row)
    return valid


def compare_file(path: Path, output: Path):
    document = read_json(path)
    rows = validate(document, path.parent)
    benchmark = load_benchmark()
    result = {"schema_version": 1, "status": "insufficient_data", "created_at": now(),
              "observed_answers": len(rows), "expected_web_answers": 36,
              "status_counts": dict(Counter(r["status"] for r in document["observations"])),
              "pairs": [], "local_vs_web_gap": None,
              "inference": "descriptive_only; no equivalence test or strategy-effect claim",
              "limitations": ["Only 6 topic groups and 2 repeats; broad uncertainty and low power",
                              "Unobserved internal search queries remain unknown",
                              "Wilson intervals assume independent within-question repeats; dependence can invalidate them",
                              "Citation similarity does not establish factual correctness or transferable GEO effects"]}
    platforms = sorted({r["platform"] for r in rows})
    for left, right in combinations(platforms, 2):
        per_question = []
        for case in benchmark["cases"]:
            a = [r for r in rows if r["platform"] == left and r["query_id"] == case["id"]]
            b = [r for r in rows if r["platform"] == right and r["query_id"] == case["id"]]
            # Requiring both repeats prevents one-sided missingness from changing the denominator.
            if len(a) != 2 or len(b) != 2:
                continue
            targets = []
            for domain in case["target_domains"]:
                hits_a = sum(domain_present(r["citations"], domain) for r in a)
                hits_b = sum(domain_present(r["citations"], domain) for r in b)
                targets.append({"domain": domain, "left_hits": hits_a, "right_hits": hits_b, "n_each": 2,
                                "absolute_probability_gap": abs(hits_a / 2 - hits_b / 2),
                                "left_wilson95": wilson_interval(hits_a, 2), "right_wilson95": wilson_interval(hits_b, 2)})
            per_question.append({"query_id": case["id"], "targets": targets,
                                 "target_presence_mae": sum(t["absolute_probability_gap"] for t in targets) / len(targets),
                                 "citation_host_js_divergence_base2": js_divergence(source_distribution(a), source_distribution(b))})
        if per_question:
            result["pairs"].append({"left": left, "right": right, "matched_questions": len(per_question),
                                    "missing_questions": 6 - len(per_question), "per_question": per_question,
                                    "macro_target_presence_mae": sum(q["target_presence_mae"] for q in per_question) / len(per_question),
                                    "macro_citation_host_js_divergence": sum(q["citation_host_js_divergence_base2"] for q in per_question) / len(per_question)})
    if result["pairs"]:
        result["status"] = "pilot_descriptive_only"
    local_pairs = [p for p in result["pairs"] if "local_agent" in (p["left"], p["right"])]
    if local_pairs:
        result["local_vs_web_gap"] = local_pairs
    write_json(output, result)
    return result
