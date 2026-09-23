"""
database.py — SQLite schema and query helpers for MyPlate.

Tables
------
  users          — goals and setup flag
  profile        — body stats for TDEE (age, weight, height, gender, activity, goal)
  daily_summary  — one row per day, tracks flex-day toggle
  meal_entries   — individual logged food items
                   (source: 'text' | 'photo' | 'search')
                   (photo_filename: optional saved image)
"""

import sqlite3
from datetime import date
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "myplate.db"

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL DEFAULT 'Friend',
    calorie_goal        REAL    NOT NULL DEFAULT 2000,
    protein_goal        REAL    NOT NULL DEFAULT 150,
    carb_goal           REAL    NOT NULL DEFAULT 250,
    fat_goal            REAL    NOT NULL DEFAULT 65,
    is_setup_complete   INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS profile (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1 REFERENCES users(id) ON DELETE CASCADE,
    age         INTEGER,
    weight_kg   REAL,
    height_cm   REAL,
    gender      TEXT,       -- 'male' | 'female'
    activity    TEXT,       -- 'sedentary' | 'light' | 'moderate' | 'active' | 'very_active'
    goal        TEXT,       -- 'lose' | 'maintain' | 'gain'
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_summary (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1 REFERENCES users(id) ON DELETE CASCADE,
    date        TEXT    NOT NULL,
    is_flex_day INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, date)
);

CREATE TABLE IF NOT EXISTS meal_entries (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    daily_summary_id INTEGER NOT NULL REFERENCES daily_summary(id) ON DELETE CASCADE,
    raw_text         TEXT    NOT NULL,
    parsed_cals      REAL    NOT NULL DEFAULT 0,
    parsed_protein   REAL    NOT NULL DEFAULT 0,
    parsed_carbs     REAL    NOT NULL DEFAULT 0,
    parsed_fats      REAL    NOT NULL DEFAULT 0,
    source           TEXT    NOT NULL DEFAULT 'text',
    photo_filename   TEXT,
    timestamp        TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""


def _migrate(conn):
    """Safely add new columns to existing tables (idempotent)."""
    # users — is_setup_complete
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "is_setup_complete" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN is_setup_complete INTEGER NOT NULL DEFAULT 0")
    if "name" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT 'Friend'")
    if "gemini_api_key" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN gemini_api_key TEXT DEFAULT ''")

    # profile — add missing columns from old schema
    prof_cols = {r[1] for r in conn.execute("PRAGMA table_info(profile)")}
    for col, defn in [
        ("age",       "INTEGER"),
        ("weight_kg", "REAL"),
        ("height_cm", "REAL"),
        ("gender",    "TEXT"),
        ("activity",  "TEXT"),
        ("goal",      "TEXT"),
        ("updated_at","TEXT NOT NULL DEFAULT (datetime('now'))"),
    ]:
        if col not in prof_cols:
            try:
                conn.execute(f"ALTER TABLE profile ADD COLUMN {col} {defn}")
            except Exception:
                pass   # column may already exist in some form

    # meal_entries — source, photo_filename
    cols = {r[1] for r in conn.execute("PRAGMA table_info(meal_entries)")}
    if "source" not in cols:
        conn.execute("ALTER TABLE meal_entries ADD COLUMN source TEXT NOT NULL DEFAULT 'text'")
    if "photo_filename" not in cols:
        conn.execute("ALTER TABLE meal_entries ADD COLUMN photo_filename TEXT")


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        # Seed default user
        if not conn.execute("SELECT id FROM users WHERE id = 1").fetchone():
            conn.execute("INSERT INTO users (id, name) VALUES (1, 'Friend')")


# ---------------------------------------------------------------------------
# User / Profile
# ---------------------------------------------------------------------------

def get_user(user_id: int = 1) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else {}


def get_profile(user_id: int = 1) -> dict:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM profile WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else {}


def save_profile(user_id: int, age: int, weight_kg: float, height_cm: float,
                 gender: str, activity: str, goal: str) -> None:
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM profile WHERE user_id = ?", (user_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE profile
                   SET age=?, weight_kg=?, height_cm=?, gender=?, activity=?, goal=?,
                       updated_at=datetime('now')
                   WHERE user_id=?""",
                (age, weight_kg, height_cm, gender, activity, goal, user_id),
            )
        else:
            conn.execute(
                """INSERT INTO profile (user_id, age, weight_kg, height_cm, gender, activity, goal)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, age, weight_kg, height_cm, gender, activity, goal),
            )


def complete_setup(user_id: int, name: str, calorie_goal: float,
                   protein_goal: float, carb_goal: float, fat_goal: float) -> None:
    with get_db() as conn:
        conn.execute(
            """UPDATE users
               SET name=?, calorie_goal=?, protein_goal=?, carb_goal=?, fat_goal=?,
                   is_setup_complete=1
               WHERE id=?""",
            (name, calorie_goal, protein_goal, carb_goal, fat_goal, user_id),
        )


def update_user_goals(user_id: int, calorie_goal: float, protein_goal: float,
                      carb_goal: float, fat_goal: float, gemini_api_key: str | None = None) -> None:
    with get_db() as conn:
        if gemini_api_key is not None:
            conn.execute(
                """UPDATE users
                   SET calorie_goal=?, protein_goal=?, carb_goal=?, fat_goal=?, gemini_api_key=?
                   WHERE id=?""",
                (calorie_goal, protein_goal, carb_goal, fat_goal, gemini_api_key.strip(), user_id),
            )
        else:
            conn.execute(
                """UPDATE users
                   SET calorie_goal=?, protein_goal=?, carb_goal=?, fat_goal=?
                   WHERE id=?""",
                (calorie_goal, protein_goal, carb_goal, fat_goal, user_id),
            )


def get_gemini_api_key(user_id: int = 1) -> str:
    with get_db() as conn:
        row = conn.execute("SELECT gemini_api_key FROM users WHERE id=?", (user_id,)).fetchone()
        if row and row["gemini_api_key"]:
            return row["gemini_api_key"].strip()
    return ""


def set_gemini_api_key(api_key: str, user_id: int = 1) -> None:
    with get_db() as conn:
        conn.execute("UPDATE users SET gemini_api_key=? WHERE id=?", (api_key.strip(), user_id))


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------

def get_or_create_daily_summary(user_id: int = 1, for_date: str | None = None) -> dict:
    for_date = for_date or date.today().isoformat()
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_summary WHERE user_id=? AND date=?",
            (user_id, for_date),
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO daily_summary (user_id, date) VALUES (?, ?)",
                (user_id, for_date),
            )
            row = conn.execute(
                "SELECT * FROM daily_summary WHERE user_id=? AND date=?",
                (user_id, for_date),
            ).fetchone()
    return dict(row)


def toggle_flex_day(summary_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute(
            "SELECT is_flex_day FROM daily_summary WHERE id=?", (summary_id,)
        ).fetchone()
        new_val = 0 if cur["is_flex_day"] else 1
        conn.execute(
            "UPDATE daily_summary SET is_flex_day=? WHERE id=?", (new_val, summary_id)
        )
    return bool(new_val)


def get_daily_totals(summary_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute(
            """SELECT
                 COALESCE(SUM(parsed_cals),    0) AS total_cals,
                 COALESCE(SUM(parsed_protein), 0) AS total_protein,
                 COALESCE(SUM(parsed_carbs),   0) AS total_carbs,
                 COALESCE(SUM(parsed_fats),    0) AS total_fats
               FROM meal_entries WHERE daily_summary_id=?""",
            (summary_id,),
        ).fetchone()
    return dict(row)


# ---------------------------------------------------------------------------
# Meal entries
# ---------------------------------------------------------------------------

def add_meal_entry(summary_id: int, raw_text: str, calories: float,
                   protein: float, carbs: float, fats: float,
                   source: str = "text", photo_filename: str | None = None) -> dict:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO meal_entries
                 (daily_summary_id, raw_text, parsed_cals, parsed_protein,
                  parsed_carbs, parsed_fats, source, photo_filename)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (summary_id, raw_text, calories, protein, carbs, fats,
             source, photo_filename),
        )
        row = conn.execute(
            "SELECT * FROM meal_entries WHERE id=?", (cur.lastrowid,)
        ).fetchone()
    return dict(row)


def get_meals_for_day(summary_id: int) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM meal_entries WHERE daily_summary_id=? ORDER BY timestamp ASC",
            (summary_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_meal_entry(meal_id: int, calories: float, protein: float,
                      carbs: float, fats: float) -> dict | None:
    with get_db() as conn:
        conn.execute(
            """UPDATE meal_entries
               SET parsed_cals=?, parsed_protein=?, parsed_carbs=?, parsed_fats=?
               WHERE id=?""",
            (calories, protein, carbs, fats, meal_id),
        )
        row = conn.execute(
            "SELECT * FROM meal_entries WHERE id=?", (meal_id,)
        ).fetchone()
    return dict(row) if row else None


def delete_meal_entry(meal_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM meal_entries WHERE id=?", (meal_id,))
    return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Trends
# ---------------------------------------------------------------------------

def get_weekly_trends(user_id: int = 1) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT
                 ds.date,
                 COALESCE(SUM(me.parsed_cals),    0) AS total_cals,
                 COALESCE(SUM(me.parsed_protein), 0) AS total_protein
               FROM daily_summary ds
               LEFT JOIN meal_entries me ON me.daily_summary_id = ds.id
               WHERE ds.user_id=? AND ds.date >= date('now', '-6 days')
               GROUP BY ds.date
               ORDER BY ds.date ASC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]
