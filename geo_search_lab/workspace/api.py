import csv
import io
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .contracts import Key, Planning, Project, Search


def router(workspace, enqueue, enabled):
    api = APIRouter(prefix="/api/v1/workspace")

    def require_live():
        if not enabled:
            raise HTTPException(409, "请使用 --workspace 启动独立拆分与搜索工作台")

    @api.get("/status")
    def status():
        return dict(enabled=enabled, serpapi=workspace.key_status(), answer_generation=False,
                    default_request_cap=10, estimated_requests_per_query=2, rank_limit=20)

    @api.put("/serpapi-key")
    def key(value: Key):
        require_live()
        workspace.session_key = value.key
        return workspace.key_status()

    @api.get("/projects")
    def projects():
        return workspace.store.list("project")

    @api.post("/projects")
    def project(value: Project):
        return workspace.store.create("project", value.model_dump())

    @api.put("/projects/{identifier}")
    def update_project(identifier: str, value: Project):
        workspace.store.get(identifier, "project")
        return workspace.store.update(identifier, **value.model_dump())

    @api.get("/batches")
    def batches():
        return [{k:v for k,v in item.items() if k not in ("trials", "results", "queries", "project")}
                for item in workspace.store.list() if item["kind"] in ("planning", "search")]

    @api.post("/planning", status_code=202)
    async def planning(value: Planning):
        require_live()
        batch = workspace.planning(value)
        await enqueue(batch["id"])
        return batch

    @api.post("/search/preview")
    def preview(value: Search):
        return workspace.preview(value)

    @api.post("/search", status_code=202)
    async def search(value: Search):
        require_live()
        batch = workspace.search(value)
        await enqueue(batch["id"])
        return batch

    @api.get("/batches/{identifier}")
    def batch(identifier: str):
        return workspace.result(identifier)

    @api.post("/batches/{identifier}/cancel")
    def cancel(identifier: str):
        value = workspace.store.get(identifier)
        if value.get("status") not in ("queued", "running"):
            raise HTTPException(409, "批次已结束")
        return workspace.store.update(identifier, cancel_requested=True)

    @api.get("/batches/{identifier}/export")
    def export(identifier: str, format: str = "json"):
        value = workspace.result(identifier)
        if format == "json":
            content, media = json.dumps(value, ensure_ascii=False, indent=2), "application/json"
        elif format in ("csv", "query_csv"):
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            if format == "query_csv":
                if value["kind"] != "planning":
                    raise HTTPException(422, "query_csv 仅适用于拆分批次")
                fields = ["demand_id", "demand_text", "query", "occurrence_count", "trial_count", "valid_trials", "temperature_counts"]
                rows = [dict(r, temperature_counts=json.dumps(r["temperature_counts"], ensure_ascii=False)) for r in value["query_frequencies"]]
            elif value["kind"] == "search":
                fields = ["query", "target", "role", "best_rank", "exact_domain_best_rank", "subdomain_best_rank", "exact_page_best_rank", "collected_count", "complete_top20", "observation"]
                rows = value["views"]["query_website_rows"]
            else:
                fields = ["demand_id", "demand_text", "temperature", "repeat", "status", "queries", "trial_id"]
                rows = [dict(t, demand_id=t["demand"]["id"], demand_text=t["demand"]["text"],
                             queries=json.dumps(t["queries"], ensure_ascii=False)) for t in value["trials"]]
            writer.writerow(fields)
            for row in rows:
                cells = [row.get(f, "") for f in fields]
                writer.writerow(["'"+v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for v in cells])
            content, media = "\ufeff"+output.getvalue(), "text/csv; charset=utf-8"
        else:
            raise HTTPException(422, "仅支持 json、csv 或 query_csv")
        filename = value["id"] + ("-query-frequency.csv" if format == "query_csv" else "." + format)
        return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    return api
