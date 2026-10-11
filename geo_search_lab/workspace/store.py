"""Durable job state and a conservative, pre-dispatch request ledger."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from geo_search_lab.pilot_common import now


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "workspace.sqlite3"
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,kind TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(batch_id TEXT,ordinal INTEGER,query_id TEXT,start INTEGER,
                    status TEXT,started_at TEXT,finished_at TEXT,detail TEXT,
                    PRIMARY KEY(batch_id,ordinal));
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, kind, value):
        value = dict(value, id=uuid4().hex, kind=kind, created_at=now(), schema_version="1.0")
        with self.db() as db:
            db.execute("INSERT INTO documents VALUES(?,?,?)", (value["id"], kind, json.dumps(value, ensure_ascii=False)))
        return value

    def get(self, identifier, kind=None):
        with self.db() as db:
            row = db.execute("SELECT kind,body FROM documents WHERE id=?", (identifier,)).fetchone()
        if not row or kind and row["kind"] != kind:
            raise KeyError(identifier)
        return json.loads(row["body"])

    def update(self, identifier, **changes):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM documents WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise KeyError(identifier)
            value = json.loads(row[0])
            value.update(changes)
            db.execute("UPDATE documents SET body=? WHERE id=?", (json.dumps(value, ensure_ascii=False), identifier))
        return value

    def list(self, kind=None):
        with self.db() as db:
            rows = db.execute("SELECT body FROM documents WHERE (? IS NULL OR kind=?) ORDER BY rowid DESC", (kind, kind)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def claim(self, identifier):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            value = json.loads(db.execute("SELECT body FROM documents WHERE id=?", (identifier,)).fetchone()[0])
            if value.get("status") != "queued":
                return None
            value.update(status="running", started_at=now())
            db.execute("UPDATE documents SET body=? WHERE id=?", (json.dumps(value, ensure_ascii=False), identifier))
        return value

    def reserve(self, batch_id, query_id, start):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            batch = json.loads(db.execute("SELECT body FROM documents WHERE id=?", (batch_id,)).fetchone()[0])
            count = db.execute("SELECT COUNT(*) FROM requests WHERE batch_id=?", (batch_id,)).fetchone()[0]
            if batch["status"] != "running" or batch.get("cancel_requested") or count >= batch["request_cap"]:
                raise ValueError("请求上限或批次停止边界已触发")
            ordinal = count + 1
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?,?,NULL,NULL)",
                       (batch_id, ordinal, query_id, start, "dispatch_reserved", now()))
        return ordinal

    def complete_request(self, batch_id, ordinal, status, detail):
        with self.db() as db:
            db.execute("UPDATE requests SET status=?,finished_at=?,detail=? WHERE batch_id=? AND ordinal=?",
                       (status, now(), json.dumps(detail, ensure_ascii=False), batch_id, ordinal))

    def ledger(self, batch_id):
        with self.db() as db:
            rows = db.execute("SELECT * FROM requests WHERE batch_id=? ORDER BY ordinal", (batch_id,)).fetchall()
        return [dict(row, detail=json.loads(row["detail"]) if row["detail"] else None) for row in rows]

    def recover(self):
        for value in self.list():
            if value.get("status") in ("queued", "running"):
                self.update(value["id"], status="interrupted", finished_at=now())
        with self.db() as db:
            db.execute("UPDATE requests SET status='unknown_after_restart' WHERE status='dispatch_reserved'")
