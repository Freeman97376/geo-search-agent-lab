"""Transactional, append-only research records. Audit outputs have no access route."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from geo_search_lab.pilot_common import now, safe_id
from geo_search_lab.research.contracts import OutputInput, Review, StudyInput, validate_grade
from geo_search_lab.research.corpus import manifest
from geo_search_lab.workbench.contracts import Conflict
from geo_search_lab.workbench.store import encoded


class ResearchStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "research.sqlite3"
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS studies(id TEXT PRIMARY KEY, created_at TEXT, document TEXT);
            CREATE TABLE IF NOT EXISTS outputs(study TEXT, slot TEXT, at TEXT, document TEXT, digest TEXT,
                PRIMARY KEY(study,slot));
            CREATE TABLE IF NOT EXISTS research_reviews(study TEXT, slot TEXT, revision INTEGER, at TEXT, document TEXT,
                PRIMARY KEY(study,slot,revision));
            CREATE TABLE IF NOT EXISTS critical_errors(id INTEGER PRIMARY KEY AUTOINCREMENT, study TEXT, slot TEXT,
                at TEXT, document TEXT);
            CREATE TABLE IF NOT EXISTS budget(id INTEGER PRIMARY KEY CHECK(id=1), ceiling INTEGER, paused INTEGER, reason TEXT);
            INSERT OR IGNORE INTO budget VALUES(1,10000000,0,NULL);
            CREATE TABLE IF NOT EXISTS grades(id TEXT PRIMARY KEY, study TEXT, slot TEXT, ordinal INTEGER,
                status TEXT, started_at TEXT, finished_at TEXT, reserved INTEGER, charged INTEGER,
                request TEXT, response TEXT, document TEXT, UNIQUE(study,slot,ordinal));
            CREATE TABLE IF NOT EXISTS reconciliations(id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt TEXT, at TEXT, actual INTEGER, evidence TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, value: StudyInput):
        if value.data_kind == "imported" and any(c.review_status != "single_reviewed" or c.source_status != "verified" or not c.source_urls or not c.captured_at for c in value.cases):
            raise ValueError("真实数据研究需要先完成人工审题、公开依据和采集时间记录；草案请使用模拟研究")
        doc = manifest(value)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT document FROM studies WHERE id=?", (value.study_id,)).fetchone()
            if old:
                if json.loads(old[0])["manifest_sha256"] != doc["manifest_sha256"]:
                    raise Conflict("研究已冻结；修改语料、规则或分组需要新的研究 ID 和版本")
            else:
                db.execute("INSERT INTO studies VALUES(?,?,?)", (value.study_id, now(), encoded(doc)))
        return self.overview(value.study_id)

    def study(self, study_id):
        safe_id(study_id)
        with self.connect() as db:
            row = db.execute("SELECT document FROM studies WHERE id=?", (study_id,)).fetchone()
        if not row:
            raise KeyError(study_id)
        doc = json.loads(row[0])
        digest = doc.pop("manifest_sha256")
        if hashlib.sha256(encoded(doc).encode()).hexdigest() != digest:
            raise Conflict("研究清单完整性校验失败")
        doc["manifest_sha256"] = digest
        return doc

    def list_studies(self):
        with self.connect() as db:
            ids = [r[0] for r in db.execute("SELECT id FROM studies ORDER BY created_at,id")]
        return [self.overview(i) for i in ids]

    def slot(self, study_id, slot_id):
        safe_id(slot_id)
        study = self.study(study_id)
        slot = next((s for s in study["slots"] if s["slot_id"] == slot_id), None)
        if not slot:
            raise KeyError(slot_id)
        if slot["partition"] != "development" or not slot["reference"]:
            raise Conflict("此阶段仅开放开发集参考样本；审计与其他槽位保持封存")
        return study, slot

    def output(self, study_id, slot_id):
        self.slot(study_id, slot_id)
        with self.connect() as db:
            row = db.execute("SELECT document,digest FROM outputs WHERE study=? AND slot=?", (study_id, slot_id)).fetchone()
        if not row:
            return None
        if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
            raise Conflict("输出完整性校验失败")
        return json.loads(row[0])

    def import_output(self, study_id, slot_id, value: OutputInput):
        self.slot(study_id, slot_id)
        if hashlib.sha256(value.raw_output.encode()).hexdigest() != value.source_sha256:
            raise ValueError("source_sha256 必须匹配 raw_output 的 UTF-8 字节")
        doc = value.model_dump()
        digest = hashlib.sha256(encoded(doc).encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT digest FROM outputs WHERE study=? AND slot=?", (study_id, slot_id)).fetchone()
            if row:
                if row[0] != digest:
                    raise Conflict("已保存输出不可覆盖；失败样本不能替换")
            else:
                # One captured run cannot masquerade as independent planned samples.
                for prior in db.execute("SELECT document FROM outputs WHERE study=?", (study_id,)):
                    if json.loads(prior[0])["source_run_id"] == value.source_run_id:
                        raise Conflict("同一次源运行已绑定其他槽位，不能重复计作独立样本")
                db.execute("INSERT INTO outputs VALUES(?,?,?,?,?)", (study_id, slot_id, now(), encoded(doc), digest))
        return {"slot_id": slot_id, "status": value.status, "output_sha256": digest}

    def reviews(self, study_id, slot_id):
        self.slot(study_id, slot_id)
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute(
                "SELECT document FROM research_reviews WHERE study=? AND slot=? ORDER BY revision", (study_id, slot_id))]

    def save_review(self, study_id, slot_id, value: Review):
        _, slot = self.slot(study_id, slot_id)
        output = self.output(study_id, slot_id)
        if not output or output["status"] != "succeeded":
            raise Conflict("尚无可评阅的模型输出；失败不计零分")
        grade = validate_grade({"labels": [l.model_dump() for l in value.labels]}, slot["case"], output)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT COUNT(*) FROM research_reviews WHERE study=? AND slot=?", (study_id, slot_id)).fetchone()[0]
            if count != value.expected_revision:
                raise Conflict("评分已更新，请重新载入，历史记录不能覆盖")
            first = db.execute("SELECT document FROM research_reviews WHERE study=? LIMIT 1", (study_id,)).fetchone()
            if first and json.loads(first[0])["reviewer_id"] != value.reviewer_id:
                raise Conflict("本研究处于单人调试阶段；第二位评审需要独立验证流程")
            doc = {**grade, "revision": count + 1, "at": now(), "reviewer_id": value.reviewer_id,
                   "reference_status": "single_reviewed", "initial_review": count == 0}
            db.execute("INSERT INTO research_reviews VALUES(?,?,?,?,?)", (study_id, slot_id, count + 1, doc["at"], encoded(doc)))
        return doc

    def overview(self, study_id):
        study = self.study(study_id)
        slots = [s for s in study["slots"] if s["partition"] == "development" and s["reference"]]
        counts = {"pending_output": 0, "pending_review": 0, "reviewed": 0, "failed": 0, "unassessable": 0}
        rows = []
        for s in slots:
            output = self.output(study_id, s["slot_id"])
            history = self.reviews(study_id, s["slot_id"])
            status = ("pending_output" if not output else output["status"] if output["status"] != "succeeded"
                      else "reviewed" if history else "pending_review")
            counts[status] += 1
            rows.append({"slot_id": s["slot_id"], "status": status, "revision": len(history)})
        return {"schema_version": "research-1", "study_id": study_id, "version": study["version"],
                "manifest_sha256": study["manifest_sha256"], "data_kind": study["data_kind"],
                "planned_trials": 300, "development_reference_slots": 36, "sealed_audit_slots": 24,
                "counts": counts, "slots": rows,
                "analysis_available": counts["pending_output"] == counts["pending_review"] == 0,
                "reference_status": "single_reviewer_development", "live_enabled": False}

    def blind(self, study_id, slot_id):
        study, slot = self.slot(study_id, slot_id)
        output = self.output(study_id, slot_id)
        return {"schema_version": "research-1", "study_id": study_id, "slot_id": slot_id,
                "data_kind": study["data_kind"], "prompt": slot["case"]["prompt"], "rubric": slot["case"]["rubric"],
                "rubric_version": study["version"], "reference_at": slot["case"]["reference_at"],
                "status": output["status"] if output else "pending_output", "queries": output["queries"] if output else None,
                "reviews": self.reviews(study_id, slot_id)}

    def require_analysis(self, study_id):
        if not self.overview(study_id)["analysis_available"]:
            raise Conflict("先完成全部开发集初评或记录不可评状态，再开放配置、模型评分和导出，避免影响后续盲评")

    def analysis_records(self, study_id):
        self.require_analysis(study_id)
        study = self.study(study_id)
        records = []
        for slot in study["slots"]:
            if slot["partition"] != "development" or not slot["reference"]:
                continue
            sid = slot["slot_id"]
            with self.connect() as db:
                attempts = [dict(r) for r in db.execute("SELECT * FROM grades WHERE study=? AND slot=? ORDER BY ordinal", (study_id, sid))]
                errors = [json.loads(r[0]) for r in db.execute("SELECT document FROM critical_errors WHERE study=? AND slot=? ORDER BY id", (study_id, sid))]
            for a in attempts:
                for name in ("request", "response", "document"):
                    a[name] = json.loads(a[name]) if a[name] else None
            records.append({"study_id": study_id, "version": study["version"], "data_kind": study["data_kind"],
                            "manifest_sha256": study["manifest_sha256"], **slot,
                            "output": self.output(study_id, sid), "reviews": self.reviews(study_id, sid),
                            "grader_attempts": attempts, "critical_errors": errors})
        return records

    def add_error(self, study_id, slot_id, value):
        self.require_analysis(study_id)
        _, slot = self.slot(study_id, slot_id)
        if value.item_id not in {r["item_id"] for r in slot["case"]["rubric"]}:
            raise ValueError("未知评分项")
        with self.connect() as db:
            attempt = db.execute("SELECT study,slot FROM grades WHERE id=?", (value.attempt_id,)).fetchone()
            if not attempt or tuple(attempt) != (study_id, slot_id):
                raise ValueError("错误注释必须关联此样本的裁判尝试")
            doc = {**value.model_dump(), "at": now(), "human_review_revision": len(self.reviews(study_id, slot_id)),
                   "reference_status": "single_reviewer_annotation"}
            db.execute("INSERT INTO critical_errors(study,slot,at,document) VALUES(?,?,?,?)", (study_id, slot_id, doc["at"], encoded(doc)))
        return doc
