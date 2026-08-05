"""Import a public website as a reviewable knowledge item.

Run: python scripts/import_site.py https://inarisoftlabs.com
Review this import in the dashboard before scheduling or publishing.
"""
import re
import sys
import urllib.request
from datetime import datetime
from uuid import uuid4

from app.database import connect, initialise

url = sys.argv[1] if len(sys.argv) > 1 else "https://inarisoftlabs.com"
page = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "InariSoftLabs-Marketing-Agent/0.1"}), timeout=30)
html = page.read().decode("utf-8", errors="ignore")
text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=re.I)
text = re.sub(r"<[^>]+>", " ", text)
text = re.sub(r"\s+", " ", text).strip()
initialise()
with connect() as db:
    db.execute("INSERT INTO knowledge VALUES (?, ?, ?, ?, ?, ?)", (str(uuid4()), f"Website import — {url}", text[:18000], "website", url, datetime.utcnow().isoformat()))
print(f"Saved {len(text)} characters. Review the imported facts before enabling automatic publishing.")
