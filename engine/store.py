"""SQLite storage: users, sessions, flashcards, XP ledger.

One connection per operation. At hackathon scale that is simpler and safer than
a shared connection pool, and SQLite handles the concurrency fine. WAL mode so a
read during a write does not block.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data")
DB = os.environ.get("ML_DB") or os.path.join(DATA, "app.db")

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  pw_hash       TEXT NOT NULL,
  pw_salt       TEXT NOT NULL,
  key_enc       TEXT,              -- Fernet ciphertext, never plaintext
  key_provider  TEXT,
  key_hint      TEXT,              -- masked tail, safe to show
  created       REAL NOT NULL,
  streak        INTEGER NOT NULL DEFAULT 0,
  last_active   TEXT,              -- YYYY-MM-DD
  best_streak   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cards (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL,
  topic      TEXT NOT NULL,
  concept    TEXT NOT NULL,
  kind       TEXT NOT NULL,        -- recall | misconception | cloze
  front      TEXT NOT NULL,
  back       TEXT NOT NULL,
  source_mis TEXT,                 -- misconception id this card targets
  ease       REAL NOT NULL DEFAULT 2.5,
  interval   REAL NOT NULL DEFAULT 0,
  reps       INTEGER NOT NULL DEFAULT 0,
  lapses     INTEGER NOT NULL DEFAULT 0,
  due        REAL NOT NULL,
  created    REAL NOT NULL,
  UNIQUE(user_id, front)
);
CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(user_id, due);

CREATE TABLE IF NOT EXISTS xp_events (
  id        TEXT PRIMARY KEY,
  user_id   TEXT NOT NULL,
  kind      TEXT NOT NULL,         -- mastery | teachback | flashcard
  amount    INTEGER NOT NULL,
  detail    TEXT,
  ts        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_xp_user ON xp_events(user_id, ts);

CREATE TABLE IF NOT EXISTS topics (
  id        TEXT PRIMARY KEY,
  owner     TEXT,                  -- user id, or NULL for built-in
  subject   TEXT,
  title     TEXT,
  created   REAL NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    os.makedirs(DATA, exist_ok=True)
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init() -> None:
    os.makedirs(DATA, exist_ok=True)
    with _lock, connect() as con:
        con.executescript(SCHEMA)


def now() -> float:
    return time.time()


def today() -> str:
    return time.strftime("%Y-%m-%d")


def new_id(prefix: str = "") -> str:
    return (prefix + uuid.uuid4().hex[:16]) if prefix else uuid.uuid4().hex[:16]


# ------------------------------------------------------------------ users
def create_user(email: str, name: str, pw_hash: str, pw_salt: str) -> dict:
    uid = new_id("u_")
    with _lock, connect() as con:
        con.execute(
            "INSERT INTO users(id,email,name,pw_hash,pw_salt,created,last_active) "
            "VALUES(?,?,?,?,?,?,?)",
            (uid, email.lower().strip(), name.strip(), pw_hash, pw_salt, now(), None))
    return get_user(uid)


def get_user(uid: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM users WHERE email=?",
                          (email.lower().strip(),)).fetchone()
    return dict(row) if row else None


def set_user_key(uid: str, enc: str | None, provider: str | None, hint: str | None) -> None:
    with _lock, connect() as con:
        con.execute("UPDATE users SET key_enc=?, key_provider=?, key_hint=? WHERE id=?",
                    (enc, provider, hint, uid))


def touch_streak(uid: str) -> dict:
    """Increment the streak once per calendar day."""
    d = today()
    user = get_user(uid)
    if not user:
        return {}
    last = user.get("last_active")
    streak = user.get("streak") or 0
    best = user.get("best_streak") or 0
    if last == d:
        return {"streak": streak, "best_streak": best, "bumped": False}
    # yesterday?
    y = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    streak = streak + 1 if last == y else 1
    best = max(best, streak)
    with _lock, connect() as con:
        con.execute("UPDATE users SET streak=?, best_streak=?, last_active=? WHERE id=?",
                    (streak, best, d, uid))
    return {"streak": streak, "best_streak": best, "bumped": True}


# ------------------------------------------------------------------ topics
def register_topic(tid: str, owner: str | None, subject: str, title: str) -> None:
    with _lock, connect() as con:
        con.execute("INSERT OR REPLACE INTO topics(id,owner,subject,title,created) "
                    "VALUES(?,?,?,?,?)", (tid, owner, subject, title, now()))


def topics_for(owner: str | None) -> list[dict]:
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM topics WHERE owner IS NULL OR owner=? ORDER BY created DESC",
            (owner,)).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------- cards
def upsert_cards(user_id: str, cards: list[dict]) -> int:
    """Insert new cards; skip ones this user already has (same front)."""
    added = 0
    with _lock, connect() as con:
        for c in cards:
            try:
                con.execute(
                    "INSERT INTO cards(id,user_id,topic,concept,kind,front,back,source_mis,"
                    "ease,interval,reps,lapses,due,created) VALUES(?,?,?,?,?,?,?,?,2.5,0,0,0,?,?)",
                    (new_id("c_"), user_id, c["topic"], c["concept"], c["kind"],
                     c["front"], c["back"], c.get("source_mis"), now(), now()))
                added += 1
            except sqlite3.IntegrityError:
                continue
    return added


def due_cards(user_id: str, limit: int = 30) -> list[dict]:
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM cards WHERE user_id=? AND due<=? ORDER BY due ASC LIMIT ?",
            (user_id, now(), limit)).fetchall()
    return [dict(r) for r in rows]


def all_cards(user_id: str) -> list[dict]:
    with connect() as con:
        rows = con.execute("SELECT * FROM cards WHERE user_id=? ORDER BY due ASC",
                           (user_id,)).fetchall()
    return [dict(r) for r in rows]


def get_card(user_id: str, cid: str) -> dict | None:
    with connect() as con:
        row = con.execute("SELECT * FROM cards WHERE id=? AND user_id=?",
                          (cid, user_id)).fetchone()
    return dict(row) if row else None


def update_card(cid: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with _lock, connect() as con:
        con.execute(f"UPDATE cards SET {cols} WHERE id=?", (*fields.values(), cid))


def card_stats(user_id: str) -> dict:
    t = now()
    with connect() as con:
        row = con.execute(
            "SELECT COUNT(*) n, SUM(CASE WHEN due<=? THEN 1 ELSE 0 END) due, "
            "SUM(reps) reps FROM cards WHERE user_id=?", (t, user_id)).fetchone()
    return {"total": row["n"] or 0, "due": row["due"] or 0, "reviews": row["reps"] or 0}


# ---------------------------------------------------------------------- xp
def add_xp(user_id: str, kind: str, amount: int, detail: str = "") -> int:
    if amount == 0:
        return total_xp(user_id)
    with _lock, connect() as con:
        con.execute("INSERT INTO xp_events(id,user_id,kind,amount,detail,ts) "
                    "VALUES(?,?,?,?,?,?)",
                    (new_id("x_"), user_id, kind, amount, detail, now()))
    return total_xp(user_id)


def total_xp(user_id: str) -> int:
    with connect() as con:
        row = con.execute("SELECT COALESCE(SUM(amount),0) s FROM xp_events WHERE user_id=?",
                          (user_id,)).fetchone()
    return int(row["s"])


def xp_breakdown(user_id: str) -> list[dict]:
    with connect() as con:
        rows = con.execute(
            "SELECT kind, SUM(amount) amount, COUNT(*) n FROM xp_events "
            "WHERE user_id=? GROUP BY kind", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def recent_xp(user_id: str, limit: int = 12) -> list[dict]:
    with connect() as con:
        rows = con.execute(
            "SELECT kind, amount, detail, ts FROM xp_events WHERE user_id=? "
            "ORDER BY ts DESC LIMIT ?", (user_id, limit)).fetchall()
    return [dict(r) for r in rows]
