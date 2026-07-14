"""
License Server — Database Models
Developer: Muhammad Saim - Software Engineer
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
import logging
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DB_FILE = Path(__file__).parent / "data" / "license_server.db"
DB_FILE.parent.mkdir(exist_ok=True)

DAILY_ACCOUNT_LIMIT = int(os.environ.get("DAILY_ACCOUNT_LIMIT", 6600))
LIMIT_50_PERCENT    = DAILY_ACCOUNT_LIMIT // 2


# ─── Helpers (defined before Database so they can be called in _create_tables) ─
def _hash_password(password: str) -> str:
    salt = "pixverse_sell_salt_2024"
    return hashlib.sha256((password + salt).encode()).hexdigest()


def _ts() -> str:
    return datetime.now().isoformat()


def _today() -> str:
    return date.today().isoformat()


# ─── Database ─────────────────────────────────────────────────────────────────
class Database:
    _instance = None
    _lock      = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.db_path = str(DB_FILE)
        self._conn_lock = threading.Lock()
        self._create_tables()

    @contextmanager
    def get_connection(self):
        with self._conn_lock:
            conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                yield conn
                conn.commit()
            except Exception as e:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _create_tables(self):
        with self.get_connection() as conn:
            c = conn.cursor()

            # ── Admin Users ────────────────────────────────────────────────────
            c.execute('''
                CREATE TABLE IF NOT EXISTS admin_users (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    username     TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at   TEXT NOT NULL,
                    last_login   TEXT
                )
            ''')

            # ── License Keys ───────────────────────────────────────────────────
            c.execute('''
                CREATE TABLE IF NOT EXISTS license_keys (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    license_key     TEXT NOT NULL UNIQUE,
                    customer_name   TEXT,
                    customer_email  TEXT,
                    machine_id      TEXT,
                    is_active       INTEGER DEFAULT 1,
                    daily_limit     INTEGER DEFAULT 6600,
                    created_at      TEXT NOT NULL,
                    activated_at    TEXT,
                    expires_at      TEXT,
                    last_verified   TEXT,
                    notes           TEXT
                )
            ''')

            # ── Daily Usage per License ────────────────────────────────────────
            c.execute('''
                CREATE TABLE IF NOT EXISTS daily_usage (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    license_key         TEXT NOT NULL,
                    usage_date          TEXT NOT NULL,
                    accounts_created    INTEGER DEFAULT 0,
                    milestone_50_sent   INTEGER DEFAULT 0,
                    milestone_100_sent  INTEGER DEFAULT 0,
                    updated_at          TEXT,
                    UNIQUE(license_key, usage_date),
                    FOREIGN KEY (license_key) REFERENCES license_keys(license_key) ON DELETE CASCADE
                )
            ''')

            # ── Audit Log ──────────────────────────────────────────────────────
            c.execute('''
                CREATE TABLE IF NOT EXISTS audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    license_key TEXT,
                    event_type  TEXT NOT NULL,
                    machine_id  TEXT,
                    details     TEXT,
                    ip_address  TEXT,
                    created_at  TEXT NOT NULL
                )
            ''')

            # ── Script Storage ─────────────────────────────────────────────────
            c.execute('''
                CREATE TABLE IF NOT EXISTS script_files (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename      TEXT NOT NULL,
                    file_data     BLOB NOT NULL,
                    file_size     INTEGER NOT NULL,
                    sha256        TEXT NOT NULL,
                    uploaded_at   TEXT NOT NULL,
                    uploaded_by   TEXT NOT NULL,
                    is_active     INTEGER DEFAULT 1,
                    version_label TEXT DEFAULT ''
                )
            ''')

            # Create default admin if none exists
            row = c.execute("SELECT COUNT(*) as n FROM admin_users").fetchone()
            if row["n"] == 0:
                pw_hash = _hash_password("admin123")
                c.execute(
                    "INSERT INTO admin_users (username, password_hash, created_at) VALUES (?,?,?)",
                    ("admin", pw_hash, _ts())
                )
                logger.info("Default admin created: admin / admin123 — CHANGE THIS PASSWORD!")

            conn.commit()


db = Database()


# ─── Admin User Model ──────────────────────────────────────────────────────────
class AdminUser:
    @staticmethod
    def get(username: str) -> Optional[Dict]:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM admin_users WHERE username=?", (username,)
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def check_password(username: str, password: str) -> bool:
        user = AdminUser.get(username)
        if not user:
            return False
        return user["password_hash"] == _hash_password(password)

    @staticmethod
    def update_last_login(username: str) -> None:
        with db.get_connection() as conn:
            conn.execute("UPDATE admin_users SET last_login=? WHERE username=?",
                         (_ts(), username))

    @staticmethod
    def change_password(username: str, new_password: str) -> None:
        with db.get_connection() as conn:
            conn.execute("UPDATE admin_users SET password_hash=? WHERE username=?",
                         (_hash_password(new_password), username))

    @staticmethod
    def create(username: str, password: str) -> None:
        with db.get_connection() as conn:
            conn.execute(
                "INSERT INTO admin_users (username, password_hash, created_at) VALUES (?,?,?)",
                (username, _hash_password(password), _ts())
            )


# ─── License Key Model ────────────────────────────────────────────────────────
class LicenseKey:
    @staticmethod
    def generate() -> str:
        """Generate a unique license key (format: XXXX-XXXX-XXXX-XXXX)."""
        raw = uuid.uuid4().hex.upper()
        return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}-{raw[12:16]}"

    @staticmethod
    def create(customer_name: str = "", customer_email: str = "",
               daily_limit: int = DAILY_ACCOUNT_LIMIT,
               expires_at: str = None, notes: str = "") -> str:
        key = LicenseKey.generate()
        with db.get_connection() as conn:
            conn.execute('''
                INSERT INTO license_keys
                (license_key, customer_name, customer_email, is_active,
                 daily_limit, created_at, expires_at, notes)
                VALUES (?,?,?,1,?,?,?,?)
            ''', (key, customer_name, customer_email, daily_limit,
                  _ts(), expires_at, notes))
        return key

    @staticmethod
    def get(license_key: str) -> Optional[Dict]:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM license_keys WHERE license_key=?", (license_key,)
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_all() -> List[Dict]:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM license_keys ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def activate(license_key: str, machine_id: str) -> None:
        with db.get_connection() as conn:
            conn.execute('''
                UPDATE license_keys
                SET machine_id=?, activated_at=?, last_verified=?
                WHERE license_key=?
            ''', (machine_id, _ts(), _ts(), license_key))

    @staticmethod
    def touch(license_key: str) -> None:
        """Update last_verified timestamp."""
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE license_keys SET last_verified=? WHERE license_key=?",
                (_ts(), license_key)
            )

    @staticmethod
    def deactivate(license_key: str) -> None:
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE license_keys SET is_active=0 WHERE license_key=?",
                (license_key,)
            )

    @staticmethod
    def reactivate(license_key: str) -> None:
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE license_keys SET is_active=1 WHERE license_key=?",
                (license_key,)
            )

    @staticmethod
    def delete(license_key: str) -> None:
        with db.get_connection() as conn:
            conn.execute(
                "DELETE FROM license_keys WHERE license_key=?", (license_key,)
            )

    @staticmethod
    def is_expired(lic: Dict) -> bool:
        if not lic.get("expires_at"):
            return False
        try:
            exp = datetime.fromisoformat(lic["expires_at"])
            return datetime.now() > exp
        except Exception:
            return False

    @staticmethod
    def update(license_key: str, **kwargs) -> None:
        allowed = {"customer_name", "customer_email", "daily_limit",
                   "expires_at", "notes", "is_active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [license_key]
        with db.get_connection() as conn:
            conn.execute(f"UPDATE license_keys SET {sets} WHERE license_key=?", vals)


# ─── Daily Usage Model ────────────────────────────────────────────────────────
class DailyUsage:
    @staticmethod
    def _ensure_row(conn, license_key: str, today: str) -> None:
        conn.execute('''
            INSERT OR IGNORE INTO daily_usage
            (license_key, usage_date, accounts_created, updated_at)
            VALUES (?,?,0,?)
        ''', (license_key, today, _ts()))

    @staticmethod
    def get_today(license_key: str) -> Dict:
        today = _today()
        with db.get_connection() as conn:
            DailyUsage._ensure_row(conn, license_key, today)
            row = conn.execute(
                "SELECT * FROM daily_usage WHERE license_key=? AND usage_date=?",
                (license_key, today)
            ).fetchone()
            return dict(row) if row else {}

    @staticmethod
    def increment(license_key: str, count: int) -> Dict:
        """
        Increment today's usage by `count`.
        Returns updated row dict.
        Raises ValueError if license daily_limit would be exceeded.
        """
        today = _today()
        lic = LicenseKey.get(license_key)
        limit = lic["daily_limit"] if lic else DAILY_ACCOUNT_LIMIT

        with db.get_connection() as conn:
            DailyUsage._ensure_row(conn, license_key, today)
            row = conn.execute(
                "SELECT accounts_created FROM daily_usage WHERE license_key=? AND usage_date=?",
                (license_key, today)
            ).fetchone()
            current = row["accounts_created"] if row else 0

            if current + count > limit:
                raise ValueError(
                    f"Daily limit {limit} exceeded. Created: {current}, Requested: {count}"
                )

            new_total = current + count
            conn.execute('''
                UPDATE daily_usage
                SET accounts_created=?, updated_at=?
                WHERE license_key=? AND usage_date=?
            ''', (new_total, _ts(), license_key, today))

            updated = conn.execute(
                "SELECT * FROM daily_usage WHERE license_key=? AND usage_date=?",
                (license_key, today)
            ).fetchone()
            return dict(updated) if updated else {}

    @staticmethod
    def mark_milestone(license_key: str, milestone: str) -> None:
        today = _today()
        col   = "milestone_50_sent" if milestone == "50_percent" else "milestone_100_sent"
        with db.get_connection() as conn:
            DailyUsage._ensure_row(conn, license_key, today)
            conn.execute(
                f"UPDATE daily_usage SET {col}=1, updated_at=? WHERE license_key=? AND usage_date=?",
                (_ts(), license_key, today)
            )

    @staticmethod
    def sync_count(license_key: str, accounts_created: int) -> None:
        """
        Sync the server's daily counter to the client-reported value.
        Uses MAX(current, reported) so it never decrements and never double-counts
        even if request_creation was also called.
        """
        today = _today()
        with db.get_connection() as conn:
            DailyUsage._ensure_row(conn, license_key, today)
            row = conn.execute(
                "SELECT accounts_created FROM daily_usage WHERE license_key=? AND usage_date=?",
                (license_key, today)
            ).fetchone()
            current = row["accounts_created"] if row else 0
            new_val = max(current, accounts_created)
            if new_val != current:
                conn.execute(
                    "UPDATE daily_usage SET accounts_created=?, updated_at=? WHERE license_key=? AND usage_date=?",
                    (new_val, _ts(), license_key, today)
                )

    @staticmethod
    def get_history(license_key: str, limit: int = 30) -> List[Dict]:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM daily_usage WHERE license_key=? ORDER BY usage_date DESC LIMIT ?",
                (license_key, limit)
            ).fetchall()
            return [dict(r) for r in rows]


# ─── Audit Log Model ──────────────────────────────────────────────────────────
class AuditLog:
    @staticmethod
    def log(license_key: str, event_type: str, machine_id: str = "",
            details: str = "", ip_address: str = "") -> None:
        with db.get_connection() as conn:
            conn.execute('''
                INSERT INTO audit_log
                (license_key, event_type, machine_id, details, ip_address, created_at)
                VALUES (?,?,?,?,?,?)
            ''', (license_key, event_type, machine_id,
                  details[:2000], ip_address, _ts()))

    @staticmethod
    def get_recent(limit: int = 100) -> List[Dict]:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def get_for_license(license_key: str, limit: int = 50) -> List[Dict]:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE license_key=? ORDER BY created_at DESC LIMIT ?",
                (license_key, limit)
            ).fetchall()
            return [dict(r) for r in rows]


# ─── Script Storage ───────────────────────────────────────────────────────────
class ScriptStorage:
    """Store and retrieve the uploaded .pyc script file."""

    @staticmethod
    def save(filename: str, file_data: bytes, uploaded_by: str,
             version_label: str = "") -> int:
        sha256 = hashlib.sha256(file_data).hexdigest()
        with db.get_connection() as conn:
            conn.execute("UPDATE script_files SET is_active=0")
            cur = conn.execute('''
                INSERT INTO script_files
                (filename, file_data, file_size, sha256, uploaded_at, uploaded_by, is_active, version_label)
                VALUES (?,?,?,?,?,?,1,?)
            ''', (filename, file_data, len(file_data), sha256, _ts(), uploaded_by, version_label))
            return cur.lastrowid

    @staticmethod
    def get_active() -> Optional[Dict]:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT id, filename, file_size, sha256, uploaded_at, uploaded_by, version_label, file_data "
                "FROM script_files WHERE is_active=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_all() -> List[Dict]:
        with db.get_connection() as conn:
            rows = conn.execute(
                "SELECT id, filename, file_size, sha256, uploaded_at, uploaded_by, is_active, version_label "
                "FROM script_files ORDER BY uploaded_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    def set_active(script_id: int) -> None:
        with db.get_connection() as conn:
            conn.execute("UPDATE script_files SET is_active=0")
            conn.execute("UPDATE script_files SET is_active=1 WHERE id=?", (script_id,))

    @staticmethod
    def delete(script_id: int) -> None:
        with db.get_connection() as conn:
            conn.execute("DELETE FROM script_files WHERE id=?", (script_id,))


# ─── Dashboard Stats ──────────────────────────────────────────────────────────
def get_dashboard_stats() -> Dict[str, Any]:
    with db.get_connection() as conn:
        total_licenses  = conn.execute("SELECT COUNT(*) as n FROM license_keys").fetchone()["n"]
        active_licenses = conn.execute("SELECT COUNT(*) as n FROM license_keys WHERE is_active=1").fetchone()["n"]

        today   = _today()
        today_row = conn.execute(
            "SELECT SUM(accounts_created) as total FROM daily_usage WHERE usage_date=?", (today,)
        ).fetchone()
        today_created = today_row["total"] or 0

        recent_logs = conn.execute(
            "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 10"
        ).fetchall()

        all_keys = conn.execute(
            "SELECT lk.*, du.accounts_created "
            "FROM license_keys lk "
            "LEFT JOIN daily_usage du ON lk.license_key=du.license_key AND du.usage_date=? "
            "ORDER BY lk.created_at DESC",
            (today,)
        ).fetchall()

    return {
        "total_licenses":  total_licenses,
        "active_licenses": active_licenses,
        "today_created":   today_created,
        "recent_logs":     [dict(r) for r in recent_logs],
        "all_keys":        [dict(r) for r in all_keys],
        "today":           today,
        "daily_limit":     DAILY_ACCOUNT_LIMIT,
    }
