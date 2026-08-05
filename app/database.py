import sqlite3
from pathlib import Path

DATA_DIR = Path("data")
DATABASE_PATH = DATA_DIR / "marketing-agent.db"
UPLOAD_DIR = DATA_DIR / "uploads"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialise() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
              kind TEXT NOT NULL, source_url TEXT, created_at TEXT NOT NULL
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
              description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS posts (
              id TEXT PRIMARY KEY, status TEXT NOT NULL, caption TEXT NOT NULL, headline TEXT,
              cta TEXT, hashtags_json TEXT NOT NULL, image_notes TEXT, confidence TEXT,
              fact_ids_json TEXT NOT NULL, asset_ids_json TEXT NOT NULL, created_at TEXT NOT NULL,
              scheduled_for TEXT, published_at TEXT, facebook_post_id TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS schedule_runs (slot TEXT PRIMARY KEY, run_at TEXT NOT NULL);
            """
        )
        if not db.execute("SELECT 1 FROM knowledge LIMIT 1").fetchone():
            db.execute(
                "INSERT INTO knowledge VALUES (?, ?, ?, ?, ?, datetime('now'))",
                ("brand-basics", "InariSoftLabs brand basics", "Company name: InariSoftLabs. Website: https://inarisoftlabs.com. Only make claims supported by the knowledge library. Add verified products, audiences, differentiators, case studies, and calls to action before enabling automatic publishing.", "brand", "https://inarisoftlabs.com"),
            )
        defaults = {"posts_per_day": "1", "timezone": "Asia/Dhaka", "mode": "approval", "enabled": "false", "posting_times": '["10:00"]'}
        for key, value in defaults.items():
            db.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, value))
        asset_columns = {row['name'] for row in db.execute('PRAGMA table_info(assets)')}
        if 'label' not in asset_columns:
            db.execute("ALTER TABLE assets ADD COLUMN label TEXT NOT NULL DEFAULT ''")
        if 'description' not in asset_columns:
            db.execute("ALTER TABLE assets ADD COLUMN description TEXT NOT NULL DEFAULT ''")


def rows(query: str, parameters: tuple = ()) -> list[dict]:
    with connect() as db:
        return [dict(item) for item in db.execute(query, parameters).fetchall()]
