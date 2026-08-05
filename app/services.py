import json
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from app.config import settings
from app.database import UPLOAD_DIR, connect, rows


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json(text: str) -> dict:
    found = re.search(r"\{[\s\S]*\}", text)
    if not found:
        raise ValueError("The writing model did not return a usable post brief.")
    return json.loads(found.group())


def settings_dict() -> dict:
    values = {item["key"]: item["value"] for item in rows("SELECT key, value FROM settings")}
    return {
        "postsPerDay": int(values["posts_per_day"]), "timezone": values["timezone"],
        "mode": values["mode"], "enabled": values["enabled"] == "true",
        "postingTimes": json.loads(values["posting_times"]),
    }


def save_settings(value: dict) -> dict:
    with connect() as db:
        mapping = {
            "posts_per_day": str(value["postsPerDay"]), "timezone": value["timezone"],
            "mode": value["mode"], "enabled": str(value["enabled"]).lower(),
            "posting_times": json.dumps(value["postingTimes"]),
        }
        db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", mapping.items())
    return settings_dict()


def list_knowledge() -> list[dict]:
    return rows("SELECT id, title, body AS text, kind AS type, source_url AS sourceUrl, created_at AS createdAt FROM knowledge ORDER BY created_at DESC")


def retrieve_knowledge(query: str, limit: int = 7) -> list[dict]:
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]{2,}", query)
    # FTS5's AND query produces precise, explainable retrieval. Fall back to newest facts for a fresh library.
    if terms:
        fts_query = " OR ".join(f'"{term}"' for term in terms[:25])
        try:
            result = rows(
                "SELECT k.id, k.title, k.body AS text, k.kind AS type FROM knowledge_search s JOIN knowledge k ON k.rowid=s.rowid WHERE knowledge_search MATCH ? ORDER BY bm25(knowledge_search) LIMIT ?",
                (fts_query, limit),
            )
            if result:
                return result
        except sqlite3.OperationalError:
            pass
    return rows("SELECT id, title, body AS text, kind AS type FROM knowledge ORDER BY created_at DESC LIMIT ?", (limit,))


def usable_knowledge() -> bool:
    return bool(rows("SELECT 1 FROM knowledge WHERE kind != 'brand' AND length(body) > 40 LIMIT 1"))


def list_assets() -> list[dict]:
    return rows("SELECT id, original_name AS originalName, mime_type AS mimeType, created_at AS createdAt FROM assets ORDER BY created_at DESC")


def asset_records(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return rows(f"SELECT id, original_name AS originalName, mime_type AS mimeType, path FROM assets WHERE id IN ({marks})", tuple(ids))


def list_posts() -> list[dict]:
    records = rows("SELECT * FROM posts ORDER BY created_at DESC")
    for post in records:
        post["hashtags"] = json.loads(post.pop("hashtags_json")); post["factIds"] = json.loads(post.pop("fact_ids_json")); post["assetIds"] = json.loads(post.pop("asset_ids_json"))
        post["createdAt"] = post.pop("created_at"); post["scheduledFor"] = post.pop("scheduled_for"); post["publishedAt"] = post.pop("published_at"); post["facebookPostId"] = post.pop("facebook_post_id"); post["imageNotes"] = post.pop("image_notes")
    return records


def jaccard(first: str, second: str) -> float:
    one, two = set(re.findall(r"\w+", first.lower())), set(re.findall(r"\w+", second.lower()))
    return len(one & two) / max(len(one | two), 1)


async def generate_post(asset_ids: list[str], angle: str, visual_context: str = "") -> dict:
    if not settings.deepseek_api_key:
        raise ValueError("Add DEEPSEEK_API_KEY to .env before generating a post.")
    if not usable_knowledge():
        raise ValueError("Add a verified product, service, audience, or case-study note before generating posts.")
    recent = list_posts()[:12]
    facts = retrieve_knowledge(f"{angle} {' '.join(post['caption'] for post in recent)}")
    prompt = f"""You are the careful social media editor for InariSoftLabs. Create ONE engaging Facebook post using only the verified knowledge below. Never invent product features, customers, metrics, prices, integrations, awards, or results. Make it useful, warm, direct, and specific. Avoid generic AI phrasing, excessive emoji, and engagement bait.\n\nMarketing angle: {angle or 'Choose a fresh useful angle from the facts.'}\nVisual context supplied by the marketing team: {visual_context or 'No visual description supplied. Do not make claims about the uploaded visual.'}\n\nVERIFIED KNOWLEDGE:\n{chr(10).join(f'- [{fact["id"]}] {fact["title"]}: {fact["text"]}' for fact in facts)}\n\nReturn JSON only: {{\"caption\":\"...\",\"headline\":\"short optional overlay text\",\"cta\":\"...\",\"hashtags\":[\"#...\"],\"imageNotes\":\"how selected imagery supports the copy\",\"confidence\":\"high|medium|low\",\"factIds\":[\"verified ids used\"]}}. Caption must be 80-220 words, include a CTA, and use no more than 5 hashtags."""
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post("https://api.deepseek.com/v1/chat/completions", headers={"Authorization": f"Bearer {settings.deepseek_api_key}"}, json={"model": settings.deepseek_model, "messages": [{"role": "system", "content": "You write grounded social marketing copy and always follow the user's required JSON format."}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}, "max_tokens": 1000})
    if response.status_code >= 400:
        raise ValueError(f"DeepSeek request failed: {response.text}")
    brief = parse_json(response.json()["choices"][0]["message"]["content"])
    if not brief.get("caption") or not isinstance(brief.get("factIds"), list):
        raise ValueError("The generated post brief is incomplete.")
    if any(jaccard(brief["caption"], post["caption"]) > 0.62 for post in recent):
        raise ValueError("The draft is too similar to a recent post. Try a more specific angle.")
    post = {"id": str(uuid4()), "status": "draft", "caption": brief["caption"], "headline": brief.get("headline", ""), "cta": brief.get("cta", ""), "hashtags": brief.get("hashtags", []), "imageNotes": brief.get("imageNotes", ""), "confidence": brief.get("confidence", "medium"), "factIds": brief["factIds"], "assetIds": asset_ids, "createdAt": now(), "scheduledFor": None, "publishedAt": None, "facebookPostId": None, "error": None}
    with connect() as db:
        db.execute("INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (post["id"], post["status"], post["caption"], post["headline"], post["cta"], json.dumps(post["hashtags"]), post["imageNotes"], post["confidence"], json.dumps(post["factIds"]), json.dumps(asset_ids), post["createdAt"], None, None, None, None))
    return post


def _facebook_request(url: str, data: dict) -> dict:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def _facebook_multipart(url: str, fields: dict, file_path: str, mime_type: str, field_name: str = "source") -> dict:
    """Upload a Page photo/video without adding another HTTP dependency."""
    boundary = f"----InariSoftLabs{uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode(), str(value).encode(), b"\r\n"])
    filename = Path(file_path).name
    parts.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(), f"Content-Type: {mime_type}\r\n\r\n".encode(), Path(file_path).read_bytes(), b"\r\n", f"--{boundary}--\r\n".encode()])
    request = urllib.request.Request(url, data=b"".join(parts), method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read())


def publish_post(post_id: str) -> dict:
    if not settings.facebook_ready:
        raise ValueError("Facebook is not configured. Set Page ID, Page token, and Graph API version in .env.")
    post = next((item for item in list_posts() if item["id"] == post_id), None)
    if not post:
        raise ValueError("Post not found.")
    try:
        endpoint = f"https://graph.facebook.com/{settings.facebook_version}/{settings.facebook_page_id}/feed"
        assets = asset_records(post["assetIds"])
        images = [asset for asset in assets if asset["mimeType"].startswith("image/")]
        videos = [asset for asset in assets if asset["mimeType"].startswith("video/")]
        if videos:
            # Facebook video posts accept a description and a binary source in one request.
            result = _facebook_multipart(f"https://graph.facebook.com/{settings.facebook_version}/{settings.facebook_page_id}/videos", {"access_token": settings.facebook_token, "description": post["caption"]}, videos[0]["path"], videos[0]["mimeType"])
        elif images:
            uploaded = []
            for asset in images[:10]:
                photo = _facebook_multipart(f"https://graph.facebook.com/{settings.facebook_version}/{settings.facebook_page_id}/photos", {"access_token": settings.facebook_token, "published": "false"}, asset["path"], asset["mimeType"])
                uploaded.append(photo["id"])
            payload = {"message": post["caption"], "access_token": settings.facebook_token}
            for index, image_id in enumerate(uploaded):
                payload[f"attached_media[{index}]"] = json.dumps({"media_fbid": image_id})
            result = _facebook_request(endpoint, payload)
        else:
            result = _facebook_request(endpoint, {"message": post["caption"], "access_token": settings.facebook_token})
        with connect() as db:
            db.execute("UPDATE posts SET status='published', published_at=?, facebook_post_id=?, error=NULL WHERE id=?", (now(), result.get("id"), post_id))
    except Exception as error:
        with connect() as db: db.execute("UPDATE posts SET status='failed', error=? WHERE id=?", (str(error), post_id))
        raise ValueError(f"Facebook publishing failed: {error}") from error
    return next(item for item in list_posts() if item["id"] == post_id)
