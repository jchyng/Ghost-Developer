import sqlite3
import time
import uuid
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS chats (
                id          TEXT PRIMARY KEY,
                cwd         TEXT NOT NULL,
                title       TEXT NOT NULL,
                session_id  TEXT,
                created_at  REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          TEXT PRIMARY KEY,
                chat_id     TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS terminals (
                id          TEXT PRIMARY KEY,
                chat_id     TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                cwd         TEXT NOT NULL,
                created_at  REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schedules (
                id          TEXT PRIMARY KEY,
                chat_id     TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                resume_at   REAL NOT NULL,
                created_at  REAL NOT NULL
            );
        """)


# ── Chats ──────────────────────────────────────────────────────────────────

def create_chat(cwd: str, title: str) -> dict:
    chat = {
        "id": str(uuid.uuid4())[:8],
        "cwd": cwd,
        "title": title,
        "session_id": None,
        "created_at": time.time(),
    }
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chats (id, cwd, title, session_id, created_at) VALUES (?,?,?,?,?)",
            (chat["id"], chat["cwd"], chat["title"], chat["session_id"], chat["created_at"]),
        )
    return chat


def list_chats() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chats ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_chat(chat_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
    return dict(row) if row else None


def update_chat_session_id(chat_id: str, session_id: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE chats SET session_id=? WHERE id=?",
            (session_id, chat_id),
        )


def update_chat_title(chat_id: str, title: str) -> dict | None:
    with get_conn() as conn:
        conn.execute("UPDATE chats SET title=? WHERE id=?", (title, chat_id))
    return get_chat(chat_id)


def delete_chat(chat_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))


# ── Messages ───────────────────────────────────────────────────────────────

def add_message(chat_id: str, role: str, content: str) -> dict:
    msg = {
        "id": str(uuid.uuid4())[:8],
        "chat_id": chat_id,
        "role": role,
        "content": content,
        "created_at": time.time(),
    }
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO messages (id, chat_id, role, content, created_at) VALUES (?,?,?,?,?)",
            (msg["id"], msg["chat_id"], msg["role"], msg["content"], msg["created_at"]),
        )
    return msg


def list_messages(chat_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE chat_id=? ORDER BY created_at ASC",
            (chat_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Schedules (rate limit) ─────────────────────────────────────────────────

def upsert_schedule(chat_id: str, resume_at: float):
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM schedules WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE schedules SET resume_at=? WHERE chat_id=?",
                (resume_at, chat_id),
            )
        else:
            conn.execute(
                "INSERT INTO schedules (id, chat_id, resume_at, created_at) VALUES (?,?,?,?)",
                (str(uuid.uuid4())[:8], chat_id, resume_at, time.time()),
            )


def get_pending_schedules() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM schedules WHERE resume_at > ?", (time.time(),)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_schedule(chat_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM schedules WHERE chat_id=?", (chat_id,))
