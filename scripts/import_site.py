"""Import a public website as an unreviewed knowledge item.

Run: python scripts/import_site.py https://inarisoftlabs.com
Approve the import in the dashboard before it can be used for generation.
"""

import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import connect, initialise  # noqa: E402

MAX_PAGE_BYTES = 2 * 1024 * 1024


class VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "template"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "template"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth and data.strip():
            self.parts.append(data.strip())

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def fetch_visible_text(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Use an absolute http:// or https:// URL.")
    request = urllib.request.Request(url, headers={"User-Agent": "InariSoftLabs-Marketing-Agent/0.2"})
    with urllib.request.urlopen(request, timeout=30) as page:
        content = page.read(MAX_PAGE_BYTES + 1)
        if len(content) > MAX_PAGE_BYTES:
            raise ValueError("The page is larger than the 2 MB import limit.")
        charset = page.headers.get_content_charset() or "utf-8"
    parser = VisibleTextParser()
    parser.feed(content.decode(charset, errors="replace"))
    return parser.text()


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "https://inarisoftlabs.com"
    text = fetch_visible_text(url)
    if len(text) < 20:
        raise SystemExit("The page did not contain enough visible text to import.")
    initialise()
    with connect() as db:
        db.execute(
            "INSERT INTO knowledge(id, title, body, kind, source_url, created_at, reviewed) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (
                str(uuid4()),
                f"Website import — {url}",
                text[:18000],
                "website",
                url,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    print(f"Saved {len(text)} characters as unreviewed knowledge. Approve it in the dashboard before use.")


if __name__ == "__main__":
    main()
