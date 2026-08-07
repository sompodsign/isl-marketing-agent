"""Import a directory of product images and videos into the asset library.

Usage: python3 scripts/import_product_assets.py ../inarisoftlabs/assets
The deterministic IDs make this safe to run again without duplicating assets or
overwriting dashboard-edited labels and descriptions.
"""

import mimetypes
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import UPLOAD_DIR, connect, initialise


ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "video/mp4",
    "video/quicktime",
}

LOGO_FILE_NAMES = {
    "logo.jpg",
    "logo.png",
    "logo-with-text.png",
    "logo_name_with_white_bg.png",
    "logo_with_name.png",
    "logo_with_text_on_right_side.png",
}


def asset_label(relative_path: Path) -> str:
    product = relative_path.parts[0].replace("_", " ").replace("-", " ").title()
    name = relative_path.stem.replace("_", " ").replace("-", " ").title()
    return f"{product} — {name}"


def asset_product(relative_path: Path) -> str:
    return relative_path.parts[0].replace("_", " ").replace("-", " ").title()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python3 scripts/import_product_assets.py <asset-directory>")

    source_root = Path(sys.argv[1]).resolve()
    if not source_root.is_dir():
        raise SystemExit(f"Asset directory does not exist: {source_root}")

    initialise()
    imported = 0
    skipped = 0
    for source_path in sorted(path for path in source_root.rglob("*") if path.is_file()):
        if source_path.name == ".DS_Store":
            continue
        if source_path.name in LOGO_FILE_NAMES:
            print(f"Skipping logo asset: {source_path}")
            skipped += 1
            continue
        mime_type, _ = mimetypes.guess_type(source_path.name)
        if mime_type not in ALLOWED_MIME_TYPES:
            print(f"Skipping unsupported asset: {source_path}")
            skipped += 1
            continue

        relative_path = source_path.relative_to(source_root)
        asset_id = str(uuid5(NAMESPACE_URL, f"inarisoftlabs-product-asset/{relative_path.as_posix()}"))
        destination = UPLOAD_DIR / f"{asset_id}-{source_path.name}"
        shutil.copy2(source_path, destination)
        with connect() as db:
            inserted = db.execute(
                """
                INSERT OR IGNORE INTO assets
                (id, original_name, mime_type, path, label, description, product, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    source_path.name,
                    mime_type,
                    str(destination),
                    asset_label(relative_path),
                    f"Imported product asset from InariSoftLabs: {relative_path.as_posix()}",
                    asset_product(relative_path),
                    datetime.now(timezone.utc).isoformat(),
                ),
            ).rowcount
        imported += inserted

    print(f"Imported {imported} product assets; skipped {skipped} unsupported files.")


if __name__ == "__main__":
    main()
