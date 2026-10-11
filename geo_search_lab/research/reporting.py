"""Descriptive, equal-family summaries; unresolved labels are bounds, not CIs."""
import csv
import io
from collections import Counter, defaultdict
from statistics import mean

from geo_search_lab.pilot_common import now


def coverage(labels, rubric, kind="need"):
    expected = {r["item_id"] for r in rubric if r["kind"] == kind}
    rows = [r for r in labels if r["item_id"] in expected]
    if not expected or len(rows) != len(expected):
        return None
    counts = Counter(r["coverage"] for r in rows)
    return {"lower": counts["complete"] / len(rows),
            "upper": (counts["complete"] + counts["uncertain"]) / len(rows),
            "items": len(rows), "counts": dict(counts), "conflicts": dict(Counter(r["conflict"] for r in rows))}


def error_bounds(auto, human):
    lo, hi = auto["lower"] - human["upper"], auto["upper"] - human["lower"]
    return {"signed_lower": lo, "signed_upper": hi,
            "absolute_lower": 0.0 if lo <= 0 <= hi else min(abs(lo), abs(hi)),
            "absolute_upper": max(abs(lo), abs(hi))}


def summarize(records):
    groups = defaultdict(list)
    failures, critical, repeated = Counter(), Counter(), []
    for r in records:
        output = r["output"]
        failures[output["status"]] += 1
        for ann in r["critical_errors"]:
            critical.update(ann["categories"])
        key = (r["arm"], output["profile_id"], output["model_digest"])
        groups[key].append(r)
        if r["diagnostic"]:
            attempts = r["grader_attempts"]
            grades = [a["document"]["grade"] for a in attempts if a["status"] == "succeeded" and a["document"].get("grade")]
            flips = []
            for item in r["case"]["rubric"]:
                labels = [next(v for v in g["labels"] if v["item_id"] == item["item_id"]) for g in grades]
                flips.append({"item_id": item["item_id"], "valid_attempts": len(labels),
                              "coverage_changed": len({v["coverage"] for v in labels}) > 1 if len(labels) > 1 else None,
                              "conflict_changed": len({v["conflict"] for v in labels}) > 1 if len(labels) > 1 else None})
            repeated.append({"slot_id": r["slot_id"], "planned_attempts": 3, "observed_attempts": len(attempts),
                             "status_counts": dict(Counter(a["status"] for a in attempts)), "items": flips,
                             "need_coverage": [coverage(g["labels"], r["case"]["rubric"]) for g in grades]})
    cohorts = []
    paired_errors = defaultdict(dict)
    for (arm, profile, digest), rows in groups.items():
        categories = {}
        for kind in ("need", "constraint"):
            points = []
            confusion, conflict_confusion, agreement = Counter(), Counter(), []
            for r in rows:
                if not r["reviews"]:
                    continue
                # Initial human review is fixed; amendments stay separate in the export.
                human = coverage(r["reviews"][0]["labels"], r["case"]["rubric"], kind)
                primary = next((a for a in r["grader_attempts"] if a["ordinal"] == 1), None)
                auto = coverage(primary["document"]["grade"]["labels"], r["case"]["rubric"], kind) if primary and primary["status"] == "succeeded" and primary["document"].get("grade") else None
                if auto and human:
                    target_ids = {i["item_id"] for i in r["case"]["rubric"] if i["kind"] == kind}
                    h = {i["item_id"]: i for i in r["reviews"][0]["labels"]}
                    a = {i["item_id"]: i for i in primary["document"]["grade"]["labels"]}
                    for item in target_ids:
                        confusion[(h[item]["coverage"], a[item]["coverage"])] += 1
                        conflict_confusion[(h[item]["conflict"], a[item]["conflict"])] += 1
                    agreement.append(sum(h[i]["coverage"] == a[i]["coverage"] for i in target_ids) / len(target_ids))
                    if kind == "need":
                        paired_errors[(r["family_id"], profile, digest)][arm] = error_bounds(auto, human)
                points.append({"family_id": r["family_id"], "slot_id": r["slot_id"], "human": human, "automatic": auto,
                               "error": error_bounds(auto, human) if auto and human else None})
            # Reference selection gives one output per family per arm. Never weight by rubric size.
            humans = [p["human"] for p in points if p["human"]]
            errors = [p["error"] for p in points if p["error"]]
            categories[kind] = {"per_family": points, "human_families": len(humans), "paired_grade_families": len(errors),
                                "macro_label_agreement": mean(agreement) if agreement else None,
                                "label_confusion": [{"human": h, "automatic": a, "count": n} for (h,a),n in sorted(confusion.items())],
                                "conflict_confusion": [{"human": h, "automatic": a, "count": n} for (h,a),n in sorted(conflict_confusion.items())],
                                "human_mean_bounds": {k: mean(v[k] for v in humans) for k in ("lower", "upper")} if humans else None,
                                "mean_error_bounds": {k: mean(v[k] for v in errors) for k in errors[0]} if errors else None}
        cohorts.append({"arm": arm, "profile_id": profile, "model_digest": digest, "planned_reference_slots": len(rows), "scores": categories})
    discrepancies = defaultdict(list)
    for (family, profile, digest), arms in paired_errors.items():
        if "A" in arms and "B" in arms:
            discrepancies[(profile, digest)].append({"family_id": family,
                "lower": arms["B"]["signed_lower"] - arms["A"]["signed_upper"],
                "upper": arms["B"]["signed_upper"] - arms["A"]["signed_lower"]})
    return {"schema_version": "research-1", "metric_version": "single-reviewer-descriptive-v1", "created_at": now(),
            "reference_slots": len(records), "output_status_counts": dict(failures), "cohorts": cohorts,
            "grader_status_counts": dict(Counter(a["status"] for r in records for a in r["grader_attempts"])),
            "critical_error_annotation_counts": dict(critical), "critical_error_denominator": "human annotations, not all possible errors",
            "repeatability": repeated, "formal_pass": None, "temperature_winner": None, "human_inter_rater_agreement": None,
            "temperature_effect_grader_discrepancy": [{"profile_id": p, "model_digest": d, "paired_families": len(v),
                "mean_bounds": {k: mean(x[k] for x in v) for k in ("lower", "upper")}, "per_family": v}
                for (p,d),v in discrepancies.items()],
            "limitations": ["单人参考，未完成独立验证", "区间是未决标签的上下界，不是置信区间", "初次人工评分与首次裁判评分用于主分析",
                            "评分失败和未评分不计零分", "仅开发集参考子集；不代表全部300次试验或真实客户分布",
                            "模拟数据不代表模型能力；置信区间与正式统计检验尚未启用"]}


def safe_cell(value):
    value = "" if value is None else str(value)
    # Protect spreadsheet consumers from formula injection, including leading whitespace.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")) else value


def csv_export(records):
    stream = io.StringIO(newline="")
    columns = ["study_id", "version", "data_kind", "manifest_sha256", "slot_id", "family_id", "arm", "repeat",
               "source_run_id", "profile_id", "model_digest", "output_status", "reference_status", "review_revision", "review_at",
               "reviewer_id", "item_id", "kind", "coverage", "conflict", "evidence_json", "note"]
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    import json
    for r in records:
        base = {k: r[k] for k in columns if k in r}
        base.update({k: r["output"][k] for k in ("source_run_id", "profile_id", "model_digest")})
        base.update(output_status=r["output"]["status"], reference_status="single_reviewed" if r["reviews"] else "unassessable")
        for review in r["reviews"] or [None]:
            if review is None:
                writer.writerow({k: safe_cell(v) for k, v in base.items()})
                continue
            for label in review["labels"]:
                row = {**base, "review_revision": review["revision"], "review_at": review["at"], "reviewer_id": review["reviewer_id"],
                       "item_id": label["item_id"], "kind": next(i["kind"] for i in r["case"]["rubric"] if i["item_id"] == label["item_id"]),
                       "coverage": label["coverage"], "conflict": label["conflict"], "evidence_json": json.dumps(label["evidence"], ensure_ascii=False), "note": label["note"]}
                writer.writerow({k: safe_cell(v) for k, v in row.items()})
    return "\ufeff" + stream.getvalue()
