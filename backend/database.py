"""
database.py — SQLite schema initialization and helper functions.
"""
import sqlite3
import json
import logging
from datetime import datetime
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_connection():
    """Return a SQLite connection with row_factory set to dict-like rows."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # allows row["column"] access
    conn.execute("PRAGMA journal_mode=WAL")  # better concurrent read performance
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""
    logger.info("Initializing database at %s", DB_PATH)
    conn = get_connection()
    try:
        c = conn.cursor()

        # --- Users table ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # --- Imou API token cache ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS imou_token (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                access_token TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            )
        """)

        # --- Notification/alarm log ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alarm_id TEXT UNIQUE,
                device_id TEXT NOT NULL,
                device_name TEXT,
                channel_id TEXT DEFAULT '0',
                event_type TEXT NOT NULL,
                alarm_time TEXT,
                image_url TEXT,
                raw_data TEXT,
                is_read INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # --- Manually registered devices (for Device Access Service mode) ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS manual_devices (
                device_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                channel_count INTEGER DEFAULT 1,
                sort_order INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # --- Device info cache (refreshed periodically) ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS device_cache (
                device_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # --- Generic key-value settings ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # --- Notification preferences per event type ---
        c.execute("""
            CREATE TABLE IF NOT EXISTS alert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                enabled INTEGER DEFAULT 1,
                sound INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(device_id, event_type)
            )
        """)

        conn.commit()

        # Migrations — add columns that didn't exist in older schema versions
        _migrate(c)
        conn.commit()

        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error("Database init failed: %s", e)
        raise
    finally:
        conn.close()


def _migrate(c):
    """Apply incremental schema migrations safely (idempotent)."""
    existing = {row[1] for row in c.execute("PRAGMA table_info(notifications)")}
    if "alarm_id" not in existing:
        c.execute("ALTER TABLE notifications ADD COLUMN alarm_id TEXT")
        logger.info("Migration: added alarm_id column to notifications")
    # Add a unique index on alarm_id (partial — only where alarm_id IS NOT NULL)
    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_alarm_id
        ON notifications (alarm_id) WHERE alarm_id IS NOT NULL
    """)


# ─────────────────────────────── Token helpers ───────────────────────────────

def save_token(access_token: str, expires_at: int):
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO imou_token (id, access_token, expires_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET access_token=excluded.access_token, expires_at=excluded.expires_at
        """, (access_token, expires_at))
        conn.commit()
    finally:
        conn.close()


def load_token():
    """Returns (access_token, expires_at) or (None, 0)."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT access_token, expires_at FROM imou_token WHERE id=1").fetchone()
        if row:
            return row["access_token"], row["expires_at"]
        return None, 0
    finally:
        conn.close()


# ─────────────────────────────── Device cache ────────────────────────────────

def save_devices(device_list: list):
    """Cache device data from Imou API."""
    conn = get_connection()
    try:
        for device in device_list:
            conn.execute("""
                INSERT INTO device_cache (device_id, data, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(device_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at
            """, (device["deviceId"], json.dumps(device)))
        conn.commit()
    finally:
        conn.close()


def load_devices():
    """Return cached device list."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT data FROM device_cache ORDER BY rowid").fetchall()
        return [json.loads(r["data"]) for r in rows]
    finally:
        conn.close()


def get_device(device_id: str):
    """Return single cached device or None."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT data FROM device_cache WHERE device_id=?", (device_id,)).fetchone()
        return json.loads(row["data"]) if row else None
    finally:
        conn.close()


# ─────────────────────────────── Notifications ───────────────────────────────

def save_notification(device_id, device_name, channel_id, event_type, alarm_time, image_url, raw_data, alarm_id=None):
    """Insert notification. Returns (row_id, is_new). Skips duplicates by alarm_id."""
    conn = get_connection()
    try:
        if alarm_id:
            # Check if already stored
            existing = conn.execute("SELECT id FROM notifications WHERE alarm_id=?", (alarm_id,)).fetchone()
            if existing:
                return existing["id"], False
        cur = conn.execute("""
            INSERT INTO notifications (alarm_id, device_id, device_name, channel_id, event_type, alarm_time, image_url, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (alarm_id, device_id, device_name, channel_id, event_type, alarm_time, image_url, json.dumps(raw_data)))
        conn.commit()
        return cur.lastrowid, True
    finally:
        conn.close()


def get_notifications(limit=50, offset=0, unread_only=False, device_id=None):
    conn = get_connection()
    try:
        conditions = []
        params = []
        if unread_only:
            conditions.append("is_read = 0")
        if device_id:
            conditions.append("device_id = ?")
            params.append(device_id)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(
            f"SELECT * FROM notifications {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_unread_count():
    conn = get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) as cnt FROM notifications WHERE is_read=0").fetchone()
        return row["cnt"]
    finally:
        conn.close()


def mark_notifications_read(ids=None):
    """Mark specific IDs or all as read."""
    conn = get_connection()
    try:
        if ids:
            placeholders = ",".join("?" * len(ids))
            conn.execute(f"UPDATE notifications SET is_read=1 WHERE id IN ({placeholders})", ids)
        else:
            conn.execute("UPDATE notifications SET is_read=1")
        conn.commit()
    finally:
        conn.close()


def update_notification_image(row_id: int, image_url: str):
    """Update the image_url for a saved notification after the snapshot is ready."""
    conn = get_connection()
    try:
        conn.execute("UPDATE notifications SET image_url=? WHERE id=?", (image_url, row_id))
        conn.commit()
    finally:
        conn.close()


def delete_notifications(older_than_days=30):
    """Delete notifications older than N days."""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM notifications WHERE created_at < datetime('now', ?)",
            (f"-{older_than_days} days",)
        )
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────── Settings ────────────────────────────────────

def get_setting(key: str, default=None):
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────── Users ───────────────────────────────────────

def get_user(username: str):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_users():
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, username, is_admin, created_at FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_user(username: str, password_hash: str, is_admin: bool = False):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?,?,?)",
            (username, password_hash, 1 if is_admin else 0)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_user_password(user_id: int, password_hash: str):
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user_id))
        conn.commit()
    finally:
        conn.close()


def delete_user(user_id: int):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────── Manual devices ──────────────────────────────

def get_manual_devices() -> list:
    """Return all manually-registered devices (Device Access Service mode)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM manual_devices ORDER BY sort_order, created_at"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_manual_device(device_id: str, name: str, channel_count: int = 1):
    conn = get_connection()
    try:
        conn.execute("""
            INSERT INTO manual_devices (device_id, name, channel_count)
            VALUES (?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET name=excluded.name, channel_count=excluded.channel_count
        """, (device_id.strip().upper(), name.strip(), channel_count))
        conn.commit()
    finally:
        conn.close()


def delete_manual_device(device_id: str):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM manual_devices WHERE device_id=?", (device_id,))
        conn.commit()
    finally:
        conn.close()


def update_manual_device_order(device_ids: list):
    """Set sort order by position in list."""
    conn = get_connection()
    try:
        for i, did in enumerate(device_ids):
            conn.execute("UPDATE manual_devices SET sort_order=? WHERE device_id=?", (i, did))
        conn.commit()
    finally:
        conn.close()
