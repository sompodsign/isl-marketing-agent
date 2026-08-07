"""Import a version-controlled knowledge pack into the marketing-agent SQLite database.

Usage: python3 scripts/import_knowledge_pack.py knowledge/lablink.json
The stable IDs make the operation safe to repeat: source-backed records are updated,
while manually created dashboard records remain untouched.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import connect, initialise


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/import_knowledge_pack.py <pack.json>")
    pack_path = Path(sys.argv[1])
    records = json.loads(pack_path.read_text())
    if not isinstance(records, list) or not records:
        raise SystemExit("Knowledge pack must be a non-empty JSON list.")

    required = {"id", "title", "type", "text", "sourceUrl"}
    missing = [record.get("id", "<unknown>") for record in records if required - record.keys()]
    if missing:
        raise SystemExit(f"Records missing required fields: {', '.join(missing)}")

    initialise()
    timestamp = datetime.now(timezone.utc).isoformat()
    pack_products = {"lablink": "LabLink", "karbarpro": "KarbarPro", "shikha": "Shikha"}
    pack_product = pack_products.get(pack_path.stem.lower(), "")
    with connect() as db:
        for record in records:
            db.execute(
                """
                INSERT OR REPLACE INTO knowledge
                (id, title, body, kind, source_url, created_at, reviewed, product)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    record["id"],
                    record["title"],
                    record["text"],
                    record["type"],
                    record["sourceUrl"],
                    timestamp,
                    record.get("product", pack_product),
                ),
            )
    print(f"Imported {len(records)} knowledge records from {pack_path}.")


if __name__ == "__main__":
    main()
