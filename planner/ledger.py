"""Single-process SQLite budget ledger. No locations or response bodies persist."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from uuid import uuid4

KINDS = ("rail", "station", "walk", "rag")


class PlanError(Exception):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


class Stopped(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class Ledger:
    def __init__(self, path: str | Path, session_caps: dict | None = None):
        if str(path) == ":memory:":
            raise ValueError("재시작 보존을 위한 SQLite 파일 경로가 필요합니다.")
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.session_caps = dict(session_caps if session_caps is not None else {"rail": 120, "station": 96, "walk": 0, "rag": 12})
        if set(self.session_caps) != set(KINDS) or any(type(v) is not int or v < 0 for v in self.session_caps.values()):
            raise ValueError("모든 종류의 세션 상한을 명시해야 합니다.")
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS caps(session TEXT, kind TEXT, cap INTEGER, used INTEGER DEFAULT 0,
                    PRIMARY KEY(session,kind));
                CREATE TABLE IF NOT EXISTS operations(session TEXT, id TEXT, fingerprint TEXT, state TEXT,
                    PRIMARY KEY(session,id));
                CREATE TABLE IF NOT EXISTS operation_caps(session TEXT, operation TEXT, kind TEXT, cap INTEGER,
                    PRIMARY KEY(session,operation,kind));
                CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, session TEXT, operation TEXT,
                    kind TEXT, phase TEXT, outcome TEXT DEFAULT 'dispatched', error TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def create_session(self) -> dict:
        session = str(uuid4())
        with self.transaction() as db:
            db.execute("INSERT INTO sessions VALUES (?)", (session,))
            db.executemany("INSERT INTO caps(session,kind,cap) VALUES (?,?,?)", [(session, kind, self.session_caps[kind]) for kind in KINDS])
        return {"sessionId": session, "dataSource": "fixture", "limits": self.session_caps.copy()}

    def begin(self, session: str, operation: str, fingerprint: str, caps: dict):
        with self.transaction() as db:
            if not db.execute("SELECT 1 FROM sessions WHERE id=?", (session,)).fetchone():
                raise PlanError("UNKNOWN_SESSION")
            prior = db.execute("SELECT fingerprint,state FROM operations WHERE session=? AND id=?", (session, operation)).fetchone()
            if prior:
                code = "OPERATION_ID_CONFLICT" if prior["fingerprint"] != fingerprint else "ALREADY_RUNNING" if prior["state"] == "running" else "OPERATION_ALREADY_USED"
                raise PlanError(code, 409)
            db.execute("INSERT INTO operations VALUES (?,?,?,'running')", (session, operation, fingerprint))
            db.executemany("INSERT INTO operation_caps VALUES (?,?,?,?)", [(session, operation, kind, caps[kind]) for kind in KINDS])

    def reserve(self, session: str, operation: str, kind: str, phase: str) -> int:
        if kind not in KINDS or phase not in ("baseline", "repair", "pricing"):
            raise ValueError("알 수 없는 원장 분류입니다.")
        with self.transaction() as db:
            state = db.execute("SELECT state FROM operations WHERE session=? AND id=?", (session, operation)).fetchone()
            if not state or state["state"] != "running":
                raise Stopped("operation_not_running")
            session_row = db.execute("SELECT cap,used FROM caps WHERE session=? AND kind=?", (session, kind)).fetchone()
            op_cap = db.execute("SELECT cap FROM operation_caps WHERE session=? AND operation=? AND kind=?", (session, operation, kind)).fetchone()["cap"]
            used = db.execute("SELECT COUNT(*) FROM attempts WHERE session=? AND operation=? AND kind=?", (session, operation, kind)).fetchone()[0]
            if used >= op_cap or session_row["used"] >= session_row["cap"]:
                raise Stopped(f"{kind}_budget_exhausted")
            db.execute("UPDATE caps SET used=used+1 WHERE session=? AND kind=?", (session, kind))
            return db.execute("INSERT INTO attempts(session,operation,kind,phase) VALUES (?,?,?,?)", (session, operation, kind, phase)).lastrowid

    def record(self, attempt: int, outcome: str, error: str | None = None):
        with self.transaction() as db:
            db.execute("UPDATE attempts SET outcome=?,error=? WHERE id=?", (outcome, error, attempt))

    def finish(self, session: str, operation: str, state="finished"):
        with self.transaction() as db:
            db.execute("UPDATE operations SET state=? WHERE session=? AND id=? AND state='running'", (state, session, operation))

    def abandon_running(self):
        """Call on sole-server startup only, not while a different worker is active."""
        with self.transaction() as db:
            db.execute("UPDATE operations SET state='abandoned' WHERE state='running'")

    def snapshot(self, session: str, operation: str) -> dict:
        with self.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT id,kind,phase,outcome,error FROM attempts WHERE session=? AND operation=? ORDER BY id", (session, operation))]
            limits = {r["kind"]: r["cap"] for r in db.execute("SELECT kind,cap FROM operation_caps WHERE session=? AND operation=?", (session, operation))}
            cumulative = {r["kind"]: {"used": r["used"], "limit": r["cap"]} for r in db.execute("SELECT kind,used,cap FROM caps WHERE session=?", (session,))}
        counts = {kind: sum(r["kind"] == kind for r in rows) for kind in KINDS}
        return {"limits": limits, "railAttempts": counts["rail"], "logicalAttempts": counts["rail"],
                "stationAttempts": counts["station"], "walkAttempts": counts["walk"], "ragRequests": counts["rag"],
                "totalRoutingOperationAttempts": counts["rail"] + counts["station"] + counts["walk"],
                "baselineAttempts": sum(r["phase"] == "baseline" for r in rows),
                "repairAttempts": sum(r["phase"] == "repair" for r in rows),
                "failedAttempts": sum(r["outcome"] == "failed" for r in rows),
                "providerCalls": 0, "httpRequestsObserved": 0, "billingUnitsObserved": 0,
                "measurementBasis": "fixture-no-network", "sessionUsage": cumulative, "attempts": rows}

    def remaining(self, session: str, operation: str, kind: str) -> int:
        view = self.snapshot(session, operation)
        used = {"rail": "railAttempts", "station": "stationAttempts", "walk": "walkAttempts", "rag": "ragRequests"}[kind]
        return min(view["limits"][kind] - view[used], view["sessionUsage"][kind]["limit"] - view["sessionUsage"][kind]["used"])
