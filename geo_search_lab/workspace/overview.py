"""Read-only, deterministic summaries of recorded observations, not LLM judgments."""
from collections import Counter


def planning_overview(batch):
    trials = batch.get("trials", [])
    valid = [t for t in trials if t["status"] == "ok"]
    return dict(version="research-overview-v1", kind="planning", status=batch["status"],
                demands=batch.get("demands", []), planned_trials=batch.get("total", 0),
                recorded_trials=len(trials), valid_trials=len(valid),
                unsuccessful_trials=len(trials)-len(valid),
                unique_demand_queries=len(batch.get("query_frequencies", [])),
                successful_query_occurrences=sum(len(t["queries"]) for t in valid),
                semantic_coverage=None,
                interpretation="频次反映文字输出的重复程度，不代表需求覆盖率或搜索效果。")


def search_overview(batch):
    results = {r["query_id"]: r for r in batch.get("results", [])}
    ledger = batch.get("request_ledger", [])
    active = batch["status"] in ("queued", "running")
    demands, rows, failures = {}, [], []
    successful_pages = 0
    ads_count = 0
    main_target = batch.get("analysis_project", batch.get("project", {})).get("website")
    main_rows = {r["query_id"]: r for r in batch.get("views", {}).get("query_website_rows", [])
                 if r["role"] == "main"}
    for ordinal, query in enumerate(batch["queries"], 1):
        origins = query["origins"]
        for origin in origins:
            demands.setdefault(origin["demand_id"], dict(id=origin["demand_id"], text=origin["demand_text"]))
        result = results.get(query["id"], {})
        pages = result.get("pages", [])
        good = [p for p in pages if p.get("status") == "ok"]
        bad = [p for p in pages if p.get("status") == "failed"]
        organic = result.get("organic", [])
        requests = [r for r in ledger if r["query_id"] == query["id"]]
        both_pages = {0, 10}.issubset({p["start"] for p in good})
        query_failures = []
        for page in bad:
            error = page.get("error_type", "UnknownError")
            seconds = round(page.get("elapsed_ms", 0)/1000, 1)
            page_number = page["start"]//10+1
            if error == "ReadTimeout":
                message = f"第 {page_number} 页等待 {seconds:g} 秒后读取超时；未取得该页搜索响应"
            elif page.get("http_status"):
                message = f"第 {page_number} 页响应校验失败（HTTP {page['http_status']}，{error}）"
            else:
                message = f"第 {page_number} 页请求失败（{error}），未取得有效搜索响应"
            failure = dict(query_id=query["id"], query_ordinal=ordinal, page=page_number,
                           request_ordinal=page.get("request_ordinal"), error_type=error, message=message)
            failures.append(failure)
            query_failures.append(failure)
        if bad:
            state = "partial_failure" if organic else "failed_unobserved"
            explanation = query_failures[-1]["message"]
        elif both_pages:
            state = "two_pages_complete" if result.get("complete_top20") else "two_pages_short"
            explanation = f"两页均已返回，共 {len(organic)} 条自然结果"
            if not result.get("complete_top20"):
                explanation += "；不计为完整 Top 20"
        elif good:
            state = "partial_running" if active else "partial_stopped"
            explanation = f"已返回 {len(good)} 页、{len(organic)} 条自然结果；其余分页" + ("尚未完成" if active else "未取得")
        elif requests:
            state = "requesting" if active else "unobserved"
            explanation = "请求进行中，尚无有效响应" if active else "请求已预留或发出，但无有效响应；不能视为搜索无结果"
        else:
            state = "not_started"
            explanation = "尚未执行" + ("，等待队列调度" if active else "，不能视为搜索无结果")
        # Missing ads is evidence of non-reporting, not evidence that no ad exists.
        count = sum(len(p.get("payload", {}).get("ads", [])) for p in good
                    if isinstance(p.get("payload", {}).get("ads", []), list))
        ads_count += count
        successful_pages += len(good)
        main = main_rows.get(query["id"], {})
        temperature_counts = Counter(str(float(o["temperature"])) for o in origins
                                     if o.get("source") == "planning" and "temperature" in o)
        rows.append(dict(ordinal=ordinal, query_id=query["id"], query=query["query"],
                         demand_ids=list(dict.fromkeys(o["demand_id"] for o in origins)),
                         source_occurrences=len(origins),
                         generated_occurrences=sum(o.get("source") == "planning" for o in origins),
                         temperature_counts=dict(temperature_counts), state=state, explanation=explanation,
                         collected_count=len(organic), has_valid_response=bool(good), successful_pages=len(good),
                         pagination_completed=both_pages, complete_top20=result.get("complete_top20", False),
                         top_results=[{k:r.get(k) for k in ("observed_rank", "title", "link", "snippet")} for r in organic[:5]],
                         main_target=main_target, exact_domain_best_rank=main.get("exact_domain_best_rank"),
                         subdomain_best_rank=main.get("subdomain_best_rank"), main_matches=main.get("matches", []),
                         sponsored_records=count, sponsored_observation="reported" if count else "not_reported" if good else "unobserved",
                         response_provenance=[dict(page=p["start"]//10+1, received_request_at=p.get("started_at"),
                             search_id=p.get("payload", {}).get("search_metadata", {}).get("id"),
                             provider_created_at=p.get("payload", {}).get("search_metadata", {}).get("created_at")) for p in good]))
    statuses = Counter(r["status"] for r in ledger)
    return dict(version="research-overview-v1", kind="search", status=batch["status"],
                demands=list(demands.values()), query_count=len(rows),
                queries_with_response=sum(r["has_valid_response"] for r in rows),
                queries_with_results=sum(r["collected_count"] > 0 for r in rows),
                queries_not_started=sum(r["state"] == "not_started" for r in rows),
                queries_with_failure=sum(r["state"] in ("partial_failure", "failed_unobserved") for r in rows),
                complete_pagination_queries=sum(r["pagination_completed"] for r in rows),
                complete_top20_queries=sum(r["complete_top20"] for r in rows),
                organic_records=sum(r["collected_count"] for r in rows), successful_pages=successful_pages,
                request_counts=dict(counted=len(ledger), succeeded=statuses["response_ok"],
                                    failed=statuses["failed_counted"], pending=statuses["dispatch_reserved"],
                                    unknown=statuses["unknown_after_restart"]),
                sponsored_records=ads_count,
                sponsored_observation="reported" if ads_count else "not_reported" if successful_pages else "unobserved",
                settings=batch["settings"], main_target=main_target, queries=rows, failures=failures,
                semantic_coverage=None,
                limitations=["分页位置是观测位置，不保证连续自然排名；短页与请求失败分别记录。",
                             "结果可能包含服务端缓存；响应生成时间与本批请求时间分别保留，不视为独立新样本。",
                             "官网命中仅按指定域名及其子域名统计，不代表品牌在其他网站上的全部曝光。",
                             "本概览不评定语义覆盖、内容质量、转化效果或 AI 引用相关性。"])
