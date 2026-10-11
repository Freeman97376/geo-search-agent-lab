import asyncio
import hashlib
import json
import os
import time
from collections import Counter
from itertools import combinations
from urllib.parse import quote, urlsplit

import httpx

from geo_search_lab.evaluation.metrics import normalize_url
from geo_search_lab.pilot_common import now
from .planning import plan
from .store import Store


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def redact(value, key):
    text = json.dumps(value, ensure_ascii=False)
    if key:
        text = text.replace(key, "[REDACTED]").replace(quote(key, safe=""), "[REDACTED]")
    return json.loads(text)


def fetch(params, key):
    # No redirect, environment proxy, retry or dynamic endpoint from provider data.
    with httpx.Client(timeout=45, trust_env=False, follow_redirects=False) as client:
        return client.get("https://serpapi.com/search.json", params=dict(params, api_key=key))


def planning_summary(trials):
    groups = {}
    for trial in trials:
        key = (trial["demand"]["id"], trial["temperature"])
        groups.setdefault(key, []).append(trial)
    output = []
    for (demand_id, temperature), values in groups.items():
        valid = [v for v in values if v["status"] == "ok"]
        sets = [set(v["queries"]) for v in valid]
        similarities = [len(a & b) / len(a | b) if a | b else 1 for a, b in combinations(sets, 2)]
        frequency = Counter(q for v in valid for q in set(v["queries"]))
        output.append(dict(demand_id=demand_id, temperature=temperature, attempted=len(values),
                           valid=len(valid), failed=len(values)-len(valid),
                           exact_output_variants=len({tuple(v["queries"]) for v in valid}),
                           mean_exact_query_jaccard=sum(similarities)/len(similarities) if similarities else None,
                           query_trial_frequency=dict(frequency), semantic_coverage=None))
    return output


def query_frequencies(trials):
    """Exact text counts within each demand; failures never inflate frequencies."""
    groups, denominators = {}, Counter()
    for trial in trials:
        if trial["status"] != "ok":
            continue
        demand = trial["demand"]
        denominators[demand["id"]] += 1
        for query, count in Counter(trial["queries"]).items():
            row = groups.setdefault((demand["id"], query), dict(demand_id=demand["id"], demand_text=demand["text"],
                query=query, occurrence_count=0, trial_count=0, temperature_counts={}, origins=[]))
            row["occurrence_count"] += count
            row["trial_count"] += 1
            temp = str(float(trial["temperature"]))
            arm = row["temperature_counts"].setdefault(temp, dict(occurrence_count=0, trial_count=0))
            arm["occurrence_count"] += count
            arm["trial_count"] += 1
            row["origins"].append(dict(trial_id=trial.get("trial_id"), repeat=trial["repeat"],
                                       temperature=trial["temperature"], occurrence_count=count))
    for row in groups.values():
        row["valid_trials"] = denominators[row["demand_id"]]
    return sorted(groups.values(), key=lambda r: (r["demand_id"], -r["occurrence_count"], r["query"]))


class Workspace:
    def __init__(self, root, *, planner=plan, fetcher=fetch):
        self.store = Store(root)
        self.planner, self.fetcher = planner, fetcher
        self.session_key = None

    def key(self):
        return self.session_key if self.session_key is not None else os.environ.get("SERPAPI_API_KEY", "")

    def key_status(self):
        return dict(configured=bool(self.key()), source="session_memory" if self.session_key is not None else "environment",
                    saved_to_disk=False)

    def planning(self, value):
        project = self.store.get(value.project_id, "project")
        body = value.model_dump()
        body.update(project=project, status="queued", prompt_time=now(), trials=[],
                    total=len(value.demands)*len(value.temperatures)*value.repeats,
                    semantic_coverage=None, context_mode="empty_ablation")
        return self.store.create("planning", body)

    def preview(self, value):
        project = self.store.get(value.project_id, "project")
        entries = []
        demands = {}
        for query in value.manual:
            if query.demand_id in demands and demands[query.demand_id] != query.demand_text:
                raise ValueError("同一需求 ID 对应不同原始文本")
            demands[query.demand_id] = query.demand_text
            entries.append((query.text, dict(source="manual", query_id=query.id, demand_id=query.demand_id,
                                            demand_text=query.demand_text)))
        if value.planning_batch_id:
            batch = self.store.get(value.planning_batch_id, "planning")
            if batch["project_id"] != value.project_id or batch["status"] in ("queued", "running"):
                raise ValueError("只能导入本项目已结束的拆分批次")
            for trial in batch["trials"]:
                if trial["status"] != "ok":
                    continue
                demand = trial["demand"]
                if demand["id"] in demands and demands[demand["id"]] != demand["text"]:
                    raise ValueError("手动输入与拆分批次的需求 ID 冲突")
                demands[demand["id"]] = demand["text"]
                for index, query in enumerate(trial["queries"]):
                    entries.append((query, dict(source="planning", batch_id=batch["id"], trial_id=trial["trial_id"],
                                               demand_id=demand["id"], demand_text=demand["text"], query_index=index,
                                               temperature=trial["temperature"], repeat=trial["repeat"],
                                               profile_id=trial.get("profile", {}).get("profile_id"),
                                               model_digest=trial.get("profile", {}).get("model_digest"))))
        if not entries:
            raise ValueError("请填写 query 或选择含成功结果的拆分批次")
        settings = value.settings.model_dump()
        unique = {}
        for query, origin in entries:
            identifier = digest(dict(query=query, settings=settings))
            unique.setdefault(identifier, dict(id=identifier, query=query, origins=[]))["origins"].append(origin)
        queries = list(unique.values())
        estimate = len(queries)*2
        return dict(project_id=value.project_id, project=project, queries=queries, settings=settings,
                    cohort_id=digest(settings), request_cap=value.request_cap,
                    estimated_requests=estimate, can_start=estimate <= value.request_cap,
                    original_occurrences=len(entries), unique_queries=len(queries), page_offsets=[0, 10],
                    rank_limit=20, dedup_policy="exact_text_and_settings_within_batch",
                    semantic_coverage=None)

    def search(self, value):
        body = self.preview(value)
        if not body["can_start"]:
            raise ValueError(f"预估 {body['estimated_requests']} 次请求，超过上限 {body['request_cap']}；请减少 query 或调整上限")
        if not self.key():
            raise ValueError("请先配置 SerpAPI 密钥；保存密钥不会调用搜索")
        body.update(status="queued", results=[])
        return self.store.create("search", body)

    async def execute(self, identifier):
        batch = self.store.claim(identifier)
        if not batch:
            return
        try:
            if batch["kind"] == "planning":
                await self.execute_planning(batch)
            elif batch["kind"] == "search":
                await asyncio.to_thread(self.execute_search, batch)
        except Exception as exc:
            self.store.update(identifier, status="failed", error_type=type(exc).__name__, finished_at=now())

    async def execute_planning(self, batch):
        trials = []
        for demand in batch["demands"]:
            for repeat in range(1, batch["repeats"]+1):
                # Interleave arms so one temperature is not always run hours later.
                temps = batch["temperatures"] if repeat % 2 else list(reversed(batch["temperatures"]))
                for temperature in temps:
                    if self.store.get(batch["id"]).get("cancel_requested"):
                        self.store.update(batch["id"], status="cancelled", finished_at=now())
                        return
                    trial_id = f"trial-{len(trials)+1:04d}"
                    destination = self.store.root / "artifacts" / batch["id"] / trial_id
                    result = await self.planner(demand, temperature, repeat, destination, batch["prompt_time"])
                    result["trial_id"] = trial_id
                    trials.append(result)
                    self.store.update(batch["id"], trials=trials, summary=planning_summary(trials))
                    if result["status"] == "failed":
                        self.store.update(batch["id"], status="partial", finished_at=now(),
                                          stop_reason="local_model_failure_no_automatic_retry")
                        return
        self.store.update(batch["id"], status="completed", finished_at=now())

    def execute_search(self, batch):
        results, key = [], self.key()
        if not key:
            raise ValueError("Missing key")
        for query in batch["queries"]:
            if self.store.get(batch["id"]).get("cancel_requested"):
                self.store.update(batch["id"], status="cancelled", finished_at=now())
                return
            result = dict(query_id=query["id"], query=query["query"], origins=query["origins"],
                          organic=[], pages=[], status="running", collected_at=now())
            results.append(result)
            stop = False
            for offset in (0, 10):
                if self.store.get(batch["id"]).get("cancel_requested"):
                    stop = True
                    break
                params = dict(engine="google", q=query["query"], start=offset, **batch["settings"])
                if not params["location"]:
                    del params["location"]
                ordinal = self.store.reserve(batch["id"], query["id"], offset)
                started = time.perf_counter()
                page = dict(start=offset, request_ordinal=ordinal, request=params, started_at=now())
                directory = self.store.root / "artifacts" / batch["id"]
                directory.mkdir(parents=True, exist_ok=True)
                try:
                    response = self.fetcher(params, key)
                    raw = response.content
                    page.update(http_status=response.status_code, raw_sha256=hashlib.sha256(raw).hexdigest())
                    # Exact bytes when no secret is present; keep original hash if redaction is needed.
                    safe = raw.replace(key.encode(), b"[REDACTED]").replace(quote(key, safe="").encode(), b"[REDACTED]")
                    name = f"search-{ordinal:04d}.response.bin"
                    (directory / name).write_bytes(safe)
                    page.update(raw_file=name, saved_sha256=hashlib.sha256(safe).hexdigest(), raw_redacted=safe != raw)
                    payload = redact(response.json(), key)
                    page.update(payload=payload)
                    if (response.status_code != 200 or payload.get("error")
                            or payload.get("search_metadata", {}).get("status") != "Success"):
                        raise ValueError("Provider response failed")
                    echoed = payload.get("search_parameters", {})
                    mismatches = {k: echoed[k] for k in params if k in echoed and str(echoed[k]) != str(params[k])}
                    page.update(effective_settings_mismatch=mismatches, settings_echo_available=bool(echoed))
                    if mismatches:
                        raise ValueError("Provider echoed different search settings")
                    organic = payload.get("organic_results", [])
                    if not isinstance(organic, list) or any(not isinstance(item, dict) for item in organic):
                        raise ValueError("Invalid organic result shape")
                    for index, item in enumerate(organic[:10], 1):
                        result["organic"].append(dict(item, observed_rank=offset+index,
                                                      provider_position=item.get("position"), page_start=offset,
                                                      result_type="organic"))
                    page.update(status="ok", organic_count=len(organic), elapsed_ms=round((time.perf_counter()-started)*1000, 2))
                    self.store.complete_request(batch["id"], ordinal, "response_ok", {k:v for k,v in page.items() if k != "payload"})
                except Exception as exc:
                    page.update(status="failed", error_type=type(exc).__name__,
                                elapsed_ms=round((time.perf_counter()-started)*1000, 2))
                    self.store.complete_request(batch["id"], ordinal, "failed_counted", {k:v for k,v in page.items() if k != "payload"})
                    stop = True
                result["pages"].append(page)
                self.store.update(batch["id"], results=results)
                if stop:
                    break
            result.update(collected_count=len(result["organic"]),
                          complete_top20=all(p["status"] == "ok" and p["organic_count"] >= 10 for p in result["pages"]) and len(result["pages"]) == 2,
                          status="partial" if stop or len(result["organic"]) < 20 else "completed")
            self.store.update(batch["id"], results=results)
            if stop:
                status = "cancelled" if self.store.get(batch["id"]).get("cancel_requested") else "partial"
                self.store.update(batch["id"], status=status, finished_at=now(), stop_reason="stopped_without_retry")
                return
        self.store.update(batch["id"], status="completed", finished_at=now())

    def result(self, identifier):
        from .overview import planning_overview, search_overview
        value = self.store.get(identifier)
        if value["kind"] == "planning":
            value["query_frequencies"] = query_frequencies(value["trials"])
            value["overview"] = planning_overview(value)
        if value["kind"] == "search":
            value["request_ledger"] = self.store.ledger(identifier)
            value["counted_requests"] = len(value["request_ledger"])
            # Re-evaluate saved SERPs against current targets; never dispatch search here.
            project = self.store.get(value["project_id"], "project")
            value["analysis_project"] = project
            value["views"] = views(value, project)
            value["overview"] = search_overview(value)
        return value


def domain_match_type(link, target):
    """Host relationship, never a substring or brand-name match. www is a subdomain."""
    try:
        parsed = urlsplit(link)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            return None
        host = (parsed.hostname or "").lower().encode("idna").decode().rstrip(".")
        target = target.lower().encode("idna").decode().rstrip(".")
        if host == target:
            return "exact_domain"
        if host.endswith("." + target):
            return "subdomain"
    except (ValueError, TypeError, UnicodeError):
        pass
    return None


def matches(link, target, page=False):
    try:
        if page:
            return bool(normalize_url(link)) and normalize_url(link) == normalize_url(target)
        return domain_match_type(link, target) is not None
    except (ValueError, TypeError, UnicodeError):
        return False


def views(batch, project):
    targets = [(project["website"], "main")] + [(v, "competitor") for v in project["competitors"]] + [(v, "page") for v in project["pages"]]
    rows = []
    by_id = {r["query_id"]: r for r in batch["results"]}
    for query in batch["queries"]:
        result = by_id.get(query["id"], {})
        organic = result.get("organic", [])
        for target, role in targets:
            hits = [item for item in organic if matches(item.get("link", ""), target, role == "page")]
            typed_hits = [dict(url=item.get("link"), rank=item["observed_rank"],
                               match_type="exact_page" if role == "page" else domain_match_type(item.get("link", ""), target))
                          for item in hits]
            rows.append(dict(query_id=query["id"], query=query["query"], target=target, role=role,
                             best_rank=min((r["observed_rank"] for r in hits), default=None),
                             exact_domain_best_rank=min((r["rank"] for r in typed_hits if r["match_type"] == "exact_domain"), default=None),
                             subdomain_best_rank=min((r["rank"] for r in typed_hits if r["match_type"] == "subdomain"), default=None),
                             exact_page_best_rank=min((r["rank"] for r in typed_hits if r["match_type"] == "exact_page"), default=None),
                             matches=typed_hits,
                             matching_urls=[r.get("link") for r in hits], collected_count=len(organic),
                             complete_top20=result.get("complete_top20", False),
                             observation="found" if hits else "not_found_in_collected_range" if organic else "unobserved",
                             origins=query["origins"]))
    demands = {}
    for row in rows:
        for demand_id in dict.fromkeys(o["demand_id"] for o in row["origins"]):
            origin = next(o for o in row["origins"] if o["demand_id"] == demand_id)
            group = demands.setdefault((demand_id, row["target"]), dict(demand_id=demand_id, demand_text=origin["demand_text"],
                                      target=row["target"], role=row["role"], queries=[], semantic_coverage=None))
            group["queries"].append({k:v for k,v in row.items() if k != "origins"})
    demand_rows = []
    for group in demands.values():
        observed = [r for r in group["queries"] if r["collected_count"]]
        group.update(total_unique_queries=len(group["queries"]), observed_queries=len(observed),
                     detected_queries=sum(r["best_rank"] is not None for r in observed),
                     exact_domain_detected_queries=sum(r["exact_domain_best_rank"] is not None for r in observed),
                     subdomain_detected_queries=sum(r["subdomain_best_rank"] is not None for r in observed),
                     exact_page_detected_queries=sum(r["exact_page_best_rank"] is not None for r in observed),
                     complete_top20_queries=sum(r["complete_top20"] for r in group["queries"]))
        group["website_detection_rate_observed"] = group["detected_queries"]/len(observed) if observed else None
        demand_rows.append(group)
    return dict(query_website_rows=rows, demand_website_rows=demand_rows, matching_policy="host_relationship_v2_www_is_subdomain",
                unit="demand_x_website", semantic_coverage=None,
                warning="检出比例只描述本批已观测 query；缺失排名不是 0；不同搜索设置/批次不自动合并。")
