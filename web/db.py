"""SQLite storage for import jobs, their logs, and published products."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    status      TEXT NOT NULL,           -- queued | running | review | done | failed | cancelled
    phase       TEXT NOT NULL,           -- prepare | publish
    kind        TEXT,                    -- simple | variable
    title       TEXT,
    thumb       TEXT,
    urls        TEXT NOT NULL,           -- JSON list
    options     TEXT NOT NULL,           -- JSON
    stages      TEXT NOT NULL DEFAULT '{}',
    draft       TEXT,
    result      TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);

CREATE TABLE IF NOT EXISTS job_logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id   TEXT NOT NULL,
    ts       REAL NOT NULL,
    stage    TEXT,
    level    TEXT,
    message  TEXT
);
CREATE INDEX IF NOT EXISTS idx_logs_job ON job_logs(job_id, id);

CREATE TABLE IF NOT EXISTS products (
    site         TEXT NOT NULL,
    product_id   INTEGER NOT NULL,
    sku          TEXT,
    kind         TEXT,
    name         TEXT,
    status       TEXT,
    permalink    TEXT,
    edit_link    TEXT,
    thumb_url    TEXT,
    price_min    INTEGER,
    price_max    INTEGER,
    sale_price   INTEGER,
    variations   TEXT,
    source_urls  TEXT,
    job_id       TEXT,
    remote_state TEXT DEFAULT 'ok',      -- ok | missing
    created_at   REAL,
    updated_at   REAL,
    synced_at    REAL,
    PRIMARY KEY (site, product_id)
);
"""

JSON_JOB_FIELDS = ("urls", "options", "stages", "draft", "result")


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params=()):
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params=()) -> list:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params=()) -> Optional[dict]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # --- jobs ---------------------------------------------------------------
    @staticmethod
    def _decode_job(row: Optional[dict]) -> Optional[dict]:
        if row is None:
            return None
        for key in JSON_JOB_FIELDS:
            if row.get(key):
                row[key] = json.loads(row[key])
        return row

    def create_job(self, job_id: str, urls: list, options: dict) -> None:
        now = time.time()
        self.execute(
            "INSERT INTO jobs (id, created_at, updated_at, status, phase, urls, options, stages) VALUES (?,?,?,?,?,?,?,?)",
            (job_id, now, now, "queued", "prepare", json.dumps(urls), json.dumps(options), "{}"),
        )

    def update_job(self, job_id: str, **fields) -> None:
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols, values = [], []
        for key, value in fields.items():
            if key in JSON_JOB_FIELDS and value is not None:
                value = json.dumps(value, ensure_ascii=False)
            cols.append(f"{key} = ?")
            values.append(value)
        values.append(job_id)
        self.execute(f"UPDATE jobs SET {', '.join(cols)} WHERE id = ?", values)

    def transition(self, job_id: str, from_statuses: tuple, phase: Optional[str] = None, **fields) -> bool:
        """Atomically update a job only if it is still in one of from_statuses
        (and phase, when given). Returns False when another request/the worker
        changed it first - callers must not assume their write happened."""
        fields["updated_at"] = time.time()
        cols, values = [], []
        for key, value in fields.items():
            if key in JSON_JOB_FIELDS and value is not None:
                value = json.dumps(value, ensure_ascii=False)
            cols.append(f"{key} = ?")
            values.append(value)
        sql = f"UPDATE jobs SET {', '.join(cols)} WHERE id = ? AND status IN ({','.join('?' * len(from_statuses))})"
        values += [job_id, *from_statuses]
        if phase is not None:
            sql += " AND phase = ?"
            values.append(phase)
        with self._lock:
            return self._conn.execute(sql, values).rowcount == 1

    def get_job(self, job_id: str) -> Optional[dict]:
        return self._decode_job(self.one("SELECT * FROM jobs WHERE id = ?", (job_id,)))

    def list_jobs(self, status: str = "", limit: int = 50, light: bool = True) -> list:
        cols = "id, created_at, updated_at, status, phase, kind, title, thumb, urls, stages, result, error" if light else "*"
        sql = f"SELECT {cols} FROM jobs"
        params: list = []
        if status:
            statuses = status.split(",")
            sql += f" WHERE status IN ({','.join('?' * len(statuses))})"
            params.extend(statuses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [self._decode_job(r) for r in self.query(sql, params)]

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self.execute("DELETE FROM job_logs WHERE job_id = ?", (job_id,))
            self.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def job_counts(self) -> dict:
        rows = self.query("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def add_log(self, job_id: str, stage: str, level: str, message: str) -> None:
        self.execute(
            "INSERT INTO job_logs (job_id, ts, stage, level, message) VALUES (?,?,?,?,?)",
            (job_id, time.time(), stage, level, message),
        )

    def logs_after(self, job_id: str, after: int = 0, limit: int = 2000) -> list:
        return self.query(
            "SELECT id, ts, stage, level, message FROM job_logs WHERE job_id = ? AND id > ? ORDER BY id LIMIT ?",
            (job_id, after, limit),
        )

    # --- products -----------------------------------------------------------
    def upsert_product(self, site: str, data: dict, job_id: str = "") -> None:
        now = time.time()
        existing = self.one("SELECT created_at FROM products WHERE site = ? AND product_id = ?", (site, data["product_id"]))
        self.execute(
            """INSERT OR REPLACE INTO products
               (site, product_id, sku, kind, name, status, permalink, edit_link, thumb_url, price_min, price_max,
                sale_price, variations, source_urls, job_id, remote_state, created_at, updated_at, synced_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                site,
                data["product_id"],
                data.get("sku"),
                data.get("kind"),
                data.get("name"),
                data.get("status"),
                data.get("permalink"),
                data.get("edit_link"),
                data.get("thumb_url"),
                data.get("price_min"),
                data.get("price_max"),
                data.get("sale_price"),
                json.dumps(data.get("variations", []), ensure_ascii=False),
                json.dumps(data.get("source_urls", []), ensure_ascii=False),
                job_id or data.get("job_id"),
                data.get("remote_state", "ok"),
                existing["created_at"] if existing else data.get("created_at", now),
                now,
                data.get("synced_at"),
            ),
        )

    def list_products(self, site: str) -> list:
        rows = self.query("SELECT * FROM products WHERE site = ? ORDER BY updated_at DESC", (site,))
        for r in rows:
            r["variations"] = json.loads(r["variations"] or "[]")
            r["source_urls"] = json.loads(r["source_urls"] or "[]")
        return rows

    def update_product_remote(self, site: str, product_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.execute(
            f"UPDATE products SET {cols} WHERE site = ? AND product_id = ?", (*fields.values(), site, product_id)
        )
