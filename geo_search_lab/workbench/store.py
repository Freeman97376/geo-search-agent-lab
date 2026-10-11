"""SQLite index with immutable result artifacts, cursor events and versioned reviews."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from geo_search_lab.pilot_common import artifact_path, now, read_json, safe_id, sha256, write_json
from geo_search_lab.workbench.contracts import Conflict


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "index.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                  id TEXT PRIMARY KEY, status TEXT NOT NULL, mode TEXT NOT NULL,
                  experiment_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  request_json TEXT NOT NULL, request_hash TEXT NOT NULL, client_key TEXT UNIQUE,
                  result_hash TEXT, error_json TEXT, parent_run_id TEXT);
                CREATE INDEX IF NOT EXISTS runs_listing ON runs(experiment_id, created_at DESC, id);
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                  at TEXT NOT NULL, type TEXT NOT NULL, data_json TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_by_run ON events(run_id, seq);
                CREATE TABLE IF NOT EXISTS reviews (
                  run_id TEXT NOT NULL, revision INTEGER NOT NULL, at TEXT NOT NULL,
                  data_json TEXT NOT NULL, PRIMARY KEY(run_id, revision));
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def directory(self, run_id):
        return self.root / "runs" / safe_id(run_id)

    def create(self, request, *, mode="live_local_llm", parent=None):
        serialized = encoded(request)
        digest = sha256(serialized.encode())
        key = request.get("client_request_id")
        identifier = "run_" + uuid.uuid4().hex
        timestamp = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if key:
                old = db.execute("SELECT id,request_hash FROM runs WHERE client_key=?", (key,)).fetchone()
                if old:
                    if old["request_hash"] != digest:
                        raise Conflict("client_request_id 已用于不同的请求")
                    return self.get(old["id"]), False
            if db.execute("SELECT count(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0] >= 24:
                raise Conflict("待处理运行已达24条，请等待当前任务完成")
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (identifier, "queued", mode, request["experiment_id"], timestamp, timestamp,
                        serialized, digest, key, None, None, parent))
            self._event(db, identifier, "run.queued", {"mode": mode})
        self.directory(identifier).mkdir(parents=True, exist_ok=False)
        return self.get(identifier), True

    def get(self, run_id):
        safe_id(run_id)
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise KeyError(run_id)
        value = dict(row)
        value["request"] = json.loads(value.pop("request_json"))
        value["error"] = json.loads(value.pop("error_json")) if value["error_json"] else None
        value.pop("error_json", None)
        value.pop("client_key")
        value["schema_version"] = "1.0"
        return value

    def listing(self, *, experiment_id=None, status=None, limit=50, offset=0):
        clauses, params = [], []
        if experiment_id:
            clauses.append("experiment_id=?")
            params.append(experiment_id)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as db:
            total = db.execute("SELECT count(*) FROM runs" + where, params).fetchone()[0]
            rows = db.execute("SELECT id FROM runs" + where + " ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                              [*params, limit, offset]).fetchall()
        return {"schema_version": "1.0", "total": total, "limit": limit, "offset": offset,
                "items": [self.get(r["id"]) for r in rows]}

    def _event(self, db, run_id, kind, data):
        db.execute("INSERT INTO events(run_id,at,type,data_json) VALUES(?,?,?,?)", (run_id, now(), kind, encoded(data)))

    def event(self, run_id, kind, data):
        with self.connect() as db:
            self._event(db, run_id, kind, data)

    def events(self, run_id, after=0, limit=200):
        self.get(run_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?",
                              (run_id, after, limit)).fetchall()
        values = [{"seq": r["seq"], "at": r["at"], "type": r["type"], "data": json.loads(r["data_json"])} for r in rows]
        return {"schema_version": "1.0", "items": values, "next_after": values[-1]["seq"] if values else after}

    def start(self, run_id):
        with self.connect() as db:
            changed = db.execute("UPDATE runs SET status='running',updated_at=? WHERE id=? AND status='queued'", (now(), run_id)).rowcount
            if changed:
                self._event(db, run_id, "run.started", {})
            return bool(changed)

    def finish(self, run_id, result):
        path = self.directory(run_id) / "result.json"
        write_json(path, result)
        digest = sha256(path.read_bytes())
        with self.connect() as db:
            changed = db.execute("UPDATE runs SET status=?,updated_at=?,result_hash=?,error_json=? WHERE id=? AND status='running'",
                       (result["status"], now(), digest, encoded(result.get("error")), run_id)).rowcount
            if changed != 1:
                raise Conflict("运行已结束，不能覆盖结果")
            self._event(db, run_id, "run." + result["status"], {"result_sha256": digest})

    def result(self, run_id):
        row = self.get(run_id)
        if not row["result_hash"]:
            raise Conflict("运行尚无最终结果，请先查询状态")
        path = self.directory(run_id) / "result.json"
        if sha256(path.read_bytes()) != row["result_hash"]:
            raise Conflict("结果文件校验失败")
        return read_json(path)

    def cancel(self, run_id):
        self.get(run_id)
        with self.connect() as db:
            changed = db.execute("UPDATE runs SET status='cancelled',updated_at=? WHERE id=? AND status='queued'", (now(), run_id)).rowcount
            if not changed:
                raise Conflict("只能取消尚未开始的运行")
            self._event(db, run_id, "run.cancelled", {})
        return self.get(run_id)

    def interrupt_unfinished(self):
        with self.connect() as db:
            rows = db.execute("SELECT id FROM runs WHERE status IN ('queued','running')").fetchall()
            for row in rows:
                db.execute("UPDATE runs SET status='interrupted',updated_at=?,error_json=? WHERE id=?",
                           (now(), encoded({"code": "service_restart", "message": "服务中断；保留记录，不自动重试"}), row["id"]))
                self._event(db, row["id"], "run.interrupted", {})
        return len(rows)

    def latest_review(self, run_id):
        self.get(run_id)
        with self.connect() as db:
            row = db.execute("SELECT * FROM reviews WHERE run_id=? ORDER BY revision DESC LIMIT 1", (run_id,)).fetchone()
        return {"revision": row["revision"], "at": row["at"], **json.loads(row["data_json"])} if row else None

    def review_history(self, run_id):
        self.get(run_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM reviews WHERE run_id=? ORDER BY revision", (run_id,)).fetchall()
        return [{"revision": row["revision"], "at": row["at"], **json.loads(row["data_json"])} for row in rows]

    def add_review(self, run_id, review):
        result = self.result(run_id)
        if result["status"] != "succeeded":
            raise Conflict("失败运行没有可评分的 Query 输出")
        for category in ("facets", "constraints"):
            expected = result["input"][category]
            if set(review[category]) != set(expected):
                raise ValueError("评分项必须与运行前声明的评价维度完全一致")
            for item in review[category].values():
                if any(i > len(result["output"]["sub_queries"]) for i in item["query_indices"]):
                    raise ValueError("引用的 Query 序号超出范围")
                if item["covered"] is True and not item["query_indices"]:
                    raise ValueError("判定覆盖时需要指出对应 Query")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            revision = db.execute("SELECT coalesce(max(revision),0) FROM reviews WHERE run_id=?", (run_id,)).fetchone()[0]
            if review["expected_revision"] != revision:
                raise Conflict("评分已经更新，请刷新后再保存")
            db.execute("INSERT INTO reviews VALUES(?,?,?,?)", (run_id, revision + 1, now(), encoded(review)))
            self._event(db, run_id, "review.saved", {"revision": revision + 1})
        return self.latest_review(run_id)

    def artifact(self, run_id, name):
        result = self.result(run_id)
        entry = next((a for a in result["artifacts"] if a["name"] == name), None)
        if entry is None:
            raise KeyError(name)
        path = artifact_path(self.directory(run_id), entry["relative_path"])
        if sha256(path.read_bytes()) != entry["sha256"]:
            raise Conflict("原始记录校验失败")
        return path
