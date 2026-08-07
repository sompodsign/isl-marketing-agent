"""Remove imported logos and icons, leaving only product screenshots and videos."""

import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import connect, initialise


LOGO_SOURCE_PATHS = {
    "inarisoftlabs/logo.png",
    "inarisoftlabs/logo_with_text_on_right_side.png",
    "jamuna_model_diagnostic/logo.jpg",
    "karbarpro/logo.png",
    "karbarpro/logo_name_with_white_bg.png",
    "karbarpro/logo_with_name.png",
    "lablink/logo.png",
    "lablink/logo-with-text.png",
    "shikha/logo.png",
}


def main() -> None:
    initialise()
    asset_ids = [
        str(uuid5(NAMESPACE_URL, f"inarisoftlabs-product-asset/{path}"))
        for path in sorted(LOGO_SOURCE_PATHS)
    ]
    marks = ",".join("?" for _ in asset_ids)
    with connect() as db:
        records = db.execute(f"SELECT id, path FROM assets WHERE id IN ({marks})", asset_ids).fetchall()
        db.execute(f"DELETE FROM assets WHERE id IN ({marks})", asset_ids)
    for record in records:
        Path(record["path"]).unlink(missing_ok=True)
    print(f"Removed {len(records)} logo and icon assets.")


if __name__ == "__main__":
    main()
