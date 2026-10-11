"""Versioned descriptive aggregation; no fabricated universal score or inferred platform gap."""
import json
from collections import Counter, defaultdict
from statistics import mean, median

from geo_search_lab.pilot_common import now, sha256
from geo_search_lab.workbench.store import encoded


def complete_score(review, category):
    if not review or not review[category] or any(v["covered"] is None for v in review[category].values()):
        return None
    return mean(v["covered"] for v in review[category].values())


def evaluate(store, run_ids):
    runs = [store.get(identifier) for identifier in run_ids]
    groups = defaultdict(list)
    replay_count = 0
    for run in runs:
        if run["mode"] == "replay":
            replay_count += 1
            continue
        if not run["result_hash"] or run["status"] != "succeeded":
            continue
        result = store.result(run["id"])
        config = result["configuration"]
        source = config["upstream"] or {}
        cohort = {"experiment_id": result["experiment_id"], "profile_sha256": config["profile_sha256"], "model": config["profile"]["model"],
                  "model_digest": config["profile"]["model_digest"], "upstream_commit": source.get("commit"),
                  "dependency_sha256": source.get("dependencies_sha256"), "seed_mode": config["seed_mode"],
                  "provider": config["provider"], "prompt_day": (config["prompt_time"] or "unknown")[:10]}
        key = encoded(cohort)
        groups[key].append((result, store.latest_review(run["id"])))
    summaries = []
    for key, items in groups.items():
        per_question = defaultdict(list)
        for result, review in items:
            per_question[(result["input"]["prompt_sha256"], result["input"]["snapshot_id"])].append((result, review))
        question_rows = []
        for (digest, snapshot_id), pairs in per_question.items():
            strict = [r["metrics"]["strict_json"] for r, _ in pairs if r["metrics"]["strict_json"] is not None]
            scores = {}
            for category in ("facets", "constraints"):
                # Different predeclared rubrics form different score groups, even for the same prompt.
                rubrics = {tuple(r["input"][category]) for r, _ in pairs}
                values = [complete_score(review, category) for _, review in pairs]
                observed = [v for v in values if v is not None]
                scores[category] = {"score": mean(observed) if observed and len(rubrics) == 1 else None,
                                    "fully_reviewed_runs": len(observed), "total_runs": len(pairs),
                                    "rubric_mismatch": len(rubrics) > 1}
            question_rows.append({"prompt_sha256": digest, "snapshot_id": snapshot_id, "runs": len(pairs),
                "strict_json_rate": mean(strict) if strict else None,
                "strict_json_denominator": len(strict), "review_scores": scores})
        metrics = [r["metrics"] for r, _ in items]
        prompt_rates = defaultdict(list)
        for question in question_rows:
            if question["strict_json_rate"] is not None:
                prompt_rates[question["prompt_sha256"]].append(question["strict_json_rate"])
        times = [m["model_http_seconds"] for m in metrics if m["model_http_seconds"] is not None]
        token_values = [m["model_tokens"] for m in metrics if m["model_tokens"] is not None]
        summaries.append({"cohort_id": sha256(key.encode())[:16], "configuration": json.loads(key),
                          "successful_live_runs": len(items), "unique_questions": len({key[0] for key in per_question}),
                          "strict_json_macro_rate": mean(mean(values) for values in prompt_rates.values()) if prompt_rates else None,
                          "median_model_seconds": median(times) if times else None, "timing_denominator": len(times),
                          "reported_tokens": sum(token_values) if token_values else None, "token_denominator": len(token_values),
                          "per_question": question_rows})
    return {"schema_version": "1.0", "metric_version": "planner-descriptive-v1", "created_at": now(),
            "status_counts": dict(Counter(r["status"] for r in runs)), "selected_runs": len(runs),
            "excluded_replays": replay_count, "cohorts": summaries,
            "overall_quality_score": None, "platform_gap": None,
            "limitations": ["质量评价只来自显式人工标注；未标注保持未知", "只对成功Live运行计算输出指标，失败数单列",
                            "不同模型、配置、初搜模式和日期分组报告", "格式成功率不代表研究质量；当前没有整体等效性检验"]}
