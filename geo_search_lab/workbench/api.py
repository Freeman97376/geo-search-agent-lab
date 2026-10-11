"""Local-only API and static workbench. Run with one worker via scripts/lab.py serve."""
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from geo_search_lab.experiments.planner import PROFILE
from geo_search_lab.pilot_common import load_benchmark, read_json
from geo_search_lab.workbench.contracts import Conflict, EvaluationRequest, ResultDocument, ReviewRequest, RunRequest
from geo_search_lab.workbench.evaluation import evaluate
from geo_search_lab.workbench.prompt_library import load_prompt_library
from geo_search_lab.workbench.service import DEFAULT_STORE, LabService
from geo_search_lab.workbench.store import Store


@asynccontextmanager
async def instance_lock(path):
    """Keep restart recovery from interrupting another service on the same result store."""
    handle = path.open("a+b")
    if path.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        import os
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError("此结果目录已有运行中的服务；不能启动第二个worker")
    try:
        yield
    finally:
        handle.close()


def create_app(root=DEFAULT_STORE, *, service=None, start_worker=True, offline_mode=False, workspace_mode=False, workspace=None):
    service = service or LabService(Store(Path(root)))
    from geo_search_lab.research.store import ResearchStore
    from geo_search_lab.research.grading import Grader
    from geo_search_lab.research.api import router as research_router
    research = ResearchStore(service.store.root / "research")
    from geo_search_lab.workspace.service import Workspace
    from geo_search_lab.workspace.api import router as workspace_router
    workspace = workspace or Workspace(service.store.root / "workspace")
    workspace_queue = asyncio.Queue()
    if offline_mode and workspace_mode:
        raise ValueError("--offline 与 --workspace 不能同时使用")
    if offline_mode or workspace_mode:
        start_worker = False
    queue = asyncio.Queue()

    async def worker():
        while True:
            identifier = await queue.get()
            try:
                if identifier is None:
                    return
                # Native upstream uses temporary module/environment patches. One worker executes at a time.
                await asyncio.to_thread(lambda: asyncio.run(service.execute(identifier)))
            finally:
                queue.task_done()

    async def workspace_worker():
        while True:
            identifier = await workspace_queue.get()
            try:
                if identifier is None:
                    return
                await asyncio.to_thread(lambda: asyncio.run(workspace.execute(identifier)))
            finally:
                workspace_queue.task_done()

    @asynccontextmanager
    async def lifespan(app):
        async with instance_lock(service.store.root / "service.lock"):
            service.store.interrupt_unfinished()
            Grader(research).recover_interrupted()
            workspace.store.recover()
            task = asyncio.create_task(worker()) if start_worker else None
            workspace_task = asyncio.create_task(workspace_worker()) if workspace_mode else None
            try:
                yield
            finally:
                if task:
                    await queue.put(None)
                    await task
                if workspace_task:
                    for batch in workspace.store.list():
                        if batch.get("status") in ("queued", "running"):
                            workspace.store.update(batch["id"], cancel_requested=True)
                    await workspace_queue.put(None)
                    await workspace_task

    app = FastAPI(title="GEO Search Lab", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.lab_service = service
    app.state.research_store = research
    app.state.workspace = workspace
    app.include_router(research_router(research))
    app.include_router(workspace_router(workspace, workspace_queue.put, workspace_mode))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        if request.url.path.startswith("/api/v1/workspace"):
            # Pydantic normally echoes invalid input; never echo a credential field.
            return JSONResponse({"detail": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
                                            for e in exc.errors()]}, 422)
        return await request_validation_exception_handler(request, exc)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.url.netloc}"
            if (origin and origin != expected) or request.headers.get("x-lab-request") != "1":
                return JSONResponse({"error": {"code": "invalid_origin", "message": "仅接受本地同源界面或带 X-Lab-Request: 1 的API调用"}}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse({"error": {"code": "conflict", "message": str(exc)}}, 409)

    @app.exception_handler(ValueError)
    async def invalid_handler(request, exc):
        return JSONResponse({"error": {"code": "invalid_request", "message": str(exc)}}, 422)

    @app.exception_handler(KeyError)
    async def missing_handler(request, exc):
        return JSONResponse({"error": {"code": "not_found", "message": "运行或原始记录不存在"}}, 404)

    @app.get("/api/v1/health")
    def health():
        return {"schema_version": "1.0", "status": "ok", "scope": "workspace" if workspace_mode else "planner_only", "paid_search_enabled": workspace_mode,
                "workspace_mode": workspace_mode,
                "offline_mode": offline_mode, "research_live_grader_enabled": False}

    @app.get("/api/v1/catalog")
    def catalog():
        return {"schema_version": "1.0", "profiles": [read_json(PROFILE)], "benchmark": load_benchmark(),
                "snapshots": [{k: v for k, v in value.items() if k != "path"} for value in service.catalog().values()],
                "capabilities": {"custom_prompt": True, "snapshot_planner": True, "replay": True,
                                 "live_search": False, "full_agent": False, "platform_comparison": False}}

    @app.get("/api/v1/prompt-library")
    def prompt_library():
        return load_prompt_library()

    @app.get("/api/v1/runs")
    def runs(experiment_id: str | None = None, status: str | None = None,
             limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        return service.store.listing(experiment_id=experiment_id, status=status, limit=limit, offset=offset)

    @app.post("/api/v1/runs", status_code=202)
    async def submit(value: RunRequest):
        if offline_mode or workspace_mode:
            raise Conflict("当前为离线评审服务，禁止启动模型运行")
        run, created = service.submit(value)
        if created and start_worker:
            await queue.put(run["id"])
        return {"schema_version": "1.0", "created": created, "run": run, "links": {
            "status": f"/api/v1/runs/{run['id']}", "result": f"/api/v1/runs/{run['id']}/result",
            "events": f"/api/v1/runs/{run['id']}/events", "view": f"/#run={run['id']}"}}

    @app.get("/api/v1/runs/{run_id}")
    def run(run_id: str):
        return service.store.get(run_id)

    @app.get("/api/v1/runs/{run_id}/result", response_model=ResultDocument)
    def result(run_id: str):
        return service.store.result(run_id)

    @app.get("/api/v1/schemas/result")
    def result_schema():
        return ResultDocument.model_json_schema()

    @app.get("/api/v1/runs/{run_id}/events")
    def events(run_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)):
        return service.store.events(run_id, after, limit)

    @app.get("/api/v1/runs/{run_id}/review")
    def review(run_id: str):
        return {"schema_version": "1.0", "review": service.store.latest_review(run_id)}

    @app.get("/api/v1/runs/{run_id}/reviews")
    def reviews(run_id: str):
        return {"schema_version": "1.0", "items": service.store.review_history(run_id)}

    @app.post("/api/v1/runs/{run_id}/review")
    def save_review(run_id: str, value: ReviewRequest):
        return {"schema_version": "1.0", "review": service.store.add_review(run_id, value.model_dump())}

    @app.post("/api/v1/runs/{run_id}/replay", status_code=202)
    async def replay(run_id: str, client_request_id: str | None = Query(None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")):
        if offline_mode or workspace_mode:
            raise Conflict("离线评审服务不调度旧版运行或重放")
        value, created = service.replay(run_id, client_request_id)
        if created and start_worker:
            await queue.put(value["id"])
        return {"schema_version": "1.0", "run": value, "created": created}

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel(run_id: str):
        return service.store.cancel(run_id)

    @app.get("/api/v1/runs/{run_id}/artifacts/{name}")
    def artifact(run_id: str, name: str):
        return FileResponse(service.store.artifact(run_id, name), media_type="application/octet-stream", filename=name)

    @app.get("/api/v1/runs/{run_id}/export")
    def export(run_id: str):
        content = json.dumps(service.export(run_id), ensure_ascii=False, indent=2)
        return Response(content, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'})

    @app.post("/api/v1/evaluations")
    def evaluation(value: EvaluationRequest):
        return evaluate(service.store, value.run_ids)

    @app.post("/api/v1/exports")
    def batch_export(value: EvaluationRequest):
        content = "".join(json.dumps(service.export(identifier), ensure_ascii=False) + "\n" for identifier in value.run_ids)
        return Response(content, media_type="application/x-ndjson", headers={"Content-Disposition": 'attachment; filename="lab-results.jsonl"'})

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / ("workspace.html" if workspace_mode else "research.html" if offline_mode else "index.html"))

    @app.get("/research", include_in_schema=False)
    def research_index():
        return FileResponse(static / "research.html")

    return app
