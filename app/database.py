import os
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("MARKETING_AGENT_DATA_DIR", PROJECT_ROOT / "data")).resolve()
DATABASE_PATH = DATA_DIR / "marketing-agent.db"
UPLOAD_DIR = DATA_DIR / "uploads"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DATABASE_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 10000")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA recursive_triggers = ON")
    return db


def initialise() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
              kind TEXT NOT NULL, source_url TEXT, created_at TEXT NOT NULL,
              reviewed INTEGER NOT NULL DEFAULT 1, product TEXT NOT NULL DEFAULT ''
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_search USING fts5(
              title, body, content='knowledge', content_rowid='rowid'
            );
            CREATE TRIGGER IF NOT EXISTS knowledge_insert AFTER INSERT ON knowledge BEGIN
              INSERT INTO knowledge_search(rowid, title, body) VALUES (new.rowid, new.title, new.body);
            END;
            CREATE TRIGGER IF NOT EXISTS knowledge_delete AFTER DELETE ON knowledge BEGIN
              INSERT INTO knowledge_search(knowledge_search, rowid, title, body) VALUES('delete', old.rowid, old.title, old.body);
            END;
            CREATE TRIGGER IF NOT EXISTS knowledge_update AFTER UPDATE ON knowledge BEGIN
              INSERT INTO knowledge_search(knowledge_search, rowid, title, body) VALUES('delete', old.rowid, old.title, old.body);
              INSERT INTO knowledge_search(rowid, title, body) VALUES (new.rowid, new.title, new.body);
            END;
            CREATE TABLE IF NOT EXISTS assets (
              id TEXT PRIMARY KEY, original_name TEXT NOT NULL, mime_type TEXT NOT NULL,
              path TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',
              description TEXT NOT NULL DEFAULT '', product TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS posts (
              id TEXT PRIMARY KEY, status TEXT NOT NULL, caption TEXT NOT NULL, headline TEXT,
              cta TEXT, hashtags_json TEXT NOT NULL, image_notes TEXT, confidence TEXT,
              fact_ids_json TEXT NOT NULL, asset_ids_json TEXT NOT NULL, created_at TEXT NOT NULL,
              scheduled_for TEXT, published_at TEXT, facebook_post_id TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS post_events (
              id TEXT PRIMARY KEY, post_id TEXT NOT NULL, created_at TEXT NOT NULL,
              level TEXT NOT NULL, event_type TEXT NOT NULL, message TEXT NOT NULL,
              details TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schedule_runs (slot TEXT PRIMARY KEY, run_at TEXT NOT NULL);
            """
        )
        knowledge_columns = {row["name"] for row in db.execute("PRAGMA table_info(knowledge)")}
        if "reviewed" not in knowledge_columns:
            db.execute("ALTER TABLE knowledge ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 1")
        if "product" not in knowledge_columns:
            db.execute("ALTER TABLE knowledge ADD COLUMN product TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE knowledge SET product='LabLink' WHERE product='' AND lower(title || ' ' || body) LIKE '%lablink%'")
        db.execute("UPDATE knowledge SET product='KarbarPro' WHERE product='' AND lower(title || ' ' || body) LIKE '%karbarpro%'")
        if not db.execute("SELECT 1 FROM knowledge LIMIT 1").fetchone():
            db.execute(
                "INSERT INTO knowledge(id, title, body, kind, source_url, created_at, reviewed) VALUES (?, ?, ?, ?, ?, datetime('now'), 1)",
                (
                    "brand-basics",
                    "InariSoftLabs brand basics",
                    "Company name: InariSoftLabs. Website: https://inarisoftlabs.com. Only make claims supported by the knowledge library. Add verified products, audiences, differentiators, case studies, and calls to action before enabling automatic publishing.",
                    "brand",
                    "https://inarisoftlabs.com",
                ),
            )
        defaults = {
            "posts_per_day": "1",
            "timezone": "Asia/Dhaka",
            "mode": "approval",
            "enabled": "false",
            "posting_times": '["10:00"]',
            "writing_examples": "",
        }
        for key, value in defaults.items():
            db.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, value))
        asset_columns = {row["name"] for row in db.execute("PRAGMA table_info(assets)")}
        if "label" not in asset_columns:
            db.execute("ALTER TABLE assets ADD COLUMN label TEXT NOT NULL DEFAULT ''")
        if "description" not in asset_columns:
            db.execute("ALTER TABLE assets ADD COLUMN description TEXT NOT NULL DEFAULT ''")
        if "product" not in asset_columns:
            db.execute("ALTER TABLE assets ADD COLUMN product TEXT NOT NULL DEFAULT ''")
        # Backfill the product relationship for assets imported before this field
        # existed. New uploads must provide an explicit product in the UI/API.
        db.execute(
            """UPDATE assets SET product=trim(substr(label, 1, instr(label, '—') - 1))
               WHERE product='' AND instr(label, '—') > 0"""
        )
        post_columns = {row["name"] for row in db.execute("PRAGMA table_info(posts)")}
        if "updated_at" not in post_columns:
            db.execute("ALTER TABLE posts ADD COLUMN updated_at TEXT")
        schedule_columns = {row["name"] for row in db.execute("PRAGMA table_info(schedule_runs)")}
        if "status" not in schedule_columns:
            db.execute("ALTER TABLE schedule_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'succeeded'")
        if "error" not in schedule_columns:
            db.execute("ALTER TABLE schedule_runs ADD COLUMN error TEXT")
        db.execute("CREATE INDEX IF NOT EXISTS posts_status_created ON posts(status, created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS posts_scheduled_for ON posts(scheduled_for)")
        db.execute("CREATE INDEX IF NOT EXISTS post_events_post_created ON post_events(post_id, created_at DESC)")


def rows(query: str, parameters: tuple = ()) -> list[dict]:
    with connect() as db:
        return [dict(item) for item in db.execute(query, parameters).fetchall()]
