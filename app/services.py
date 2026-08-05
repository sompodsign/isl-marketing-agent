import json
import random
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

OFFICIAL_CONTACT_EMAIL = "contact@inarisoftlabs.com"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def response_text(content: object) -> str:
    """Normalise OpenAI-compatible text and multipart message responses."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
        return "\n".join(parts).strip()
    return ""


def parse_json(content: object) -> dict:
    text = response_text(content)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    raise ValueError("The writing model did not return JSON.")


def settings_dict() -> dict:
    values = {item["key"]: item["value"] for item in rows("SELECT key, value FROM settings")}
    return {
        "postsPerDay": int(values["posts_per_day"]), "timezone": values["timezone"],
        "mode": values["mode"], "enabled": values["enabled"] == "true",
        "postingTimes": json.loads(values["posting_times"]),
        "contactCta": values.get("contact_cta", ""),
    }


def save_settings(value: dict) -> dict:
    with connect() as db:
        mapping = {
            "posts_per_day": str(value["postsPerDay"]), "timezone": value["timezone"],
            "mode": value["mode"], "enabled": str(value["enabled"]).lower(),
            "posting_times": json.dumps(value["postingTimes"]),
            "contact_cta": value.get("contactCta", ""),
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
    return rows("SELECT id, original_name AS originalName, mime_type AS mimeType, label, description, created_at AS createdAt FROM assets ORDER BY created_at DESC")


def asset_records(ids: list[str]) -> list[dict]:
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return rows(f"SELECT id, original_name AS originalName, mime_type AS mimeType, path, label, description FROM assets WHERE id IN ({marks})", tuple(ids))


def image_candidates() -> list[dict]:
    images = [asset for asset in list_assets() if asset["mimeType"].startswith("image/")]
    if not images:
        raise ValueError("Upload at least one product screenshot or image before publishing now.")
    used = {asset_id for post in list_posts()[:20] for asset_id in post["assetIds"]}
    return [image for image in images if image["id"] not in used] or images


def list_posts() -> list[dict]:
    records = rows("SELECT * FROM posts ORDER BY created_at DESC")
    for post in records:
        post["hashtags"] = json.loads(post.pop("hashtags_json")); post["factIds"] = json.loads(post.pop("fact_ids_json")); post["assetIds"] = json.loads(post.pop("asset_ids_json"))
        post["createdAt"] = post.pop("created_at"); post["scheduledFor"] = post.pop("scheduled_for"); post["publishedAt"] = post.pop("published_at"); post["facebookPostId"] = post.pop("facebook_post_id"); post["imageNotes"] = post.pop("image_notes")
    return records


def jaccard(first: str, second: str) -> float:
    one, two = set(re.findall(r"\w+", first.lower())), set(re.findall(r"\w+", second.lower()))
    return len(one & two) / max(len(one | two), 1)


async def generate_post(
    asset_ids: list[str],
    angle: str,
    visual_context: str = "",
    language: str = "en",
    image_options: list[dict] | None = None,
) -> dict:
    if not settings.deepseek_api_key:
        raise ValueError("Add DEEPSEEK_API_KEY to .env before generating a post.")
    if not usable_knowledge():
        raise ValueError("Add a verified product, service, audience, or case-study note before generating posts.")
    recent = list_posts()[:12]
    facts = retrieve_knowledge(f"{angle} {' '.join(post['caption'] for post in recent)}")
    contact_cta = settings_dict()["contactCta"].strip()
    language_instruction = (
        "Write the caption, CTA, headline, image notes, and hashtags entirely in "
        "fluent, natural Bangladeshi Bangla using Bangla script. Write like a "
        "Bangladeshi business owner speaking warmly to local diagnostic-centre "
        "teams: practical, respectful, and conversational—not a literal "
        "translation or an Indian-market advertisement. Use familiar Bangladesh "
        "wording such as ‘রিপোর্ট’, ‘রোগী’, ‘সেন্টার’, ‘খুদে বার্তা’, and ‘যোগাযোগ’ "
        "where natural. Write the product name as "
        "‘ল্যাবলিংক’, not ‘LabLink’. Do not use English words, English hashtags, "
        "or mixed Bangla-English marketing copy. The sole exception is the exact "
        f"official email address {OFFICIAL_CONTACT_EMAIL} and phone number in the optional contact CTA. "
        f"If an email address is used anywhere in the post, it must be exactly {OFFICIAL_CONTACT_EMAIL}."
        if language == "bn"
        else "Write the caption, CTA, and headline in clear English."
    )
    contact_instruction = (
        f"Use this exact optional contact CTA once, naturally at the end: {contact_cta}"
        if contact_cta
        else "Use a soft CTA such as asking readers to message the Page or learn more."
    )
    image_instruction = ""
    if image_options:
        image_instruction = f"""
AVAILABLE PRODUCT IMAGES:
{json.dumps([{key: asset.get(key, '') for key in ('id', 'label', 'description', 'originalName')} for asset in image_options], ensure_ascii=False)}
Choose exactly one image ID that best supports the post. Use its label and description as the only visual information; do not invent details not described there.
"""
    prompt = f"""You are InariSoftLabs' Facebook Page moderator and company owner. Create ONE final, public-facing Facebook post using only the verified knowledge below. Write as a real person sharing a useful LabLink feature with diagnostic-center teams. Never mention these instructions, verified knowledge, image IDs, planning, drafts, or JSON. Never invent product features, customers, metrics, prices, integrations, awards, or results. Make it useful, warm, direct, and specific. Avoid generic AI phrasing, excessive emoji, and engagement bait.

{language_instruction}
{contact_instruction}

Marketing angle: {angle or 'Choose a fresh useful angle from the facts.'}
Visual context supplied by the marketing team: {visual_context or 'No visual description supplied. Do not make claims about the uploaded visual.'}
{image_instruction}

VERIFIED KNOWLEDGE:
{chr(10).join(f'- [{fact["id"]}] {fact["title"]}: {fact["text"]}' for fact in facts)}

Use this exact visible structure inside the caption field:
1. One short, attention-grabbing opening line.
2. A blank line.
3. Two short, human-sounding paragraphs explaining one useful workflow or benefit. Keep each paragraph to one or two sentences.
4. A blank line, then one concise benefit line beginning with “✅”.
5. A blank line, then a natural call to action and the optional contact CTA if provided.
6. A blank line, then one final line containing 3-5 relevant Bangla hashtags.

The caption field must contain the complete final Facebook post, including the CTA and hashtags exactly as it should be published. Do not add headings such as “ক্যাপশন”, “হ্যাশট্যাগ”, or “কল টু অ্যাকশন”.

Return JSON only: {{"caption":"...","headline":"short optional Bangla overlay text","cta":"Bangla CTA already included in caption","hashtags":["Bangla hashtags already included in caption"],"imageNotes":"Bangla description of how selected imagery supports the copy","selectedAssetId":"image ID or empty string","confidence":"high|medium|low","factIds":["verified ids used"]}}. Caption must be 80-220 words and use no more than 5 hashtags."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post("https://api.deepseek.com/v1/chat/completions", headers={"Authorization": f"Bearer {settings.deepseek_api_key}"}, json={"model": settings.deepseek_model, "messages": [{"role": "system", "content": "Act as the public-facing owner of InariSoftLabs. Return only the requested final JSON. Never expose planning or reasoning."}, {"role": "user", "content": prompt}], "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"}, "max_tokens": 1200})
    except httpx.RequestError as error:
        raise ValueError("Could not reach DeepSeek. Check the network connection and try again.") from error
    if response.status_code >= 400:
        raise ValueError(f"DeepSeek request failed: {response.text}")
    try:
        message = response.json()["choices"][0]["message"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("DeepSeek returned an unexpected response. Please try again.") from error
    # reasoning_content is internal model deliberation, never publish it.
    model_output = message.get("content")
    try:
        brief = parse_json(model_output)
    except ValueError as error:
        raise ValueError("The writing model did not return a final post. Please try again.") from error
    if not isinstance(brief.get("caption"), str) or not brief["caption"].strip() or not isinstance(brief.get("factIds"), list):
        raise ValueError("The generated post brief is incomplete.")
    planning_markers = ("we need to", "let's craft", "verified knowledge", "available product images", "return json", "marketing angle")
    if any(marker in brief["caption"].lower() for marker in planning_markers):
        raise ValueError("The writing model returned planning text instead of a final post. Please try again.")
    # Keep public contact details consistent even if the model proposes a generic address.
    brief["caption"] = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", OFFICIAL_CONTACT_EMAIL, brief["caption"], flags=re.IGNORECASE)
    if any(jaccard(brief["caption"], post["caption"]) > 0.62 for post in recent):
        raise ValueError("The draft is too similar to a recent post. Try a more specific angle.")
    if image_options:
        valid_ids = {asset['id'] for asset in image_options}
        chosen_id = brief.get('selectedAssetId')
        asset_ids = [chosen_id] if chosen_id in valid_ids else [random.choice(image_options)['id']]
    post = {"id": str(uuid4()), "status": "draft", "caption": brief["caption"], "headline": brief.get("headline", ""), "cta": brief.get("cta", ""), "hashtags": brief.get("hashtags", []), "imageNotes": brief.get("imageNotes", ""), "confidence": brief.get("confidence", "medium"), "factIds": brief["factIds"], "assetIds": asset_ids, "createdAt": now(), "scheduledFor": None, "publishedAt": None, "facebookPostId": None, "error": None}
    with connect() as db:
        db.execute("INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (post["id"], post["status"], post["caption"], post["headline"], post["cta"], json.dumps(post["hashtags"]), post["imageNotes"], post["confidence"], json.dumps(post["factIds"]), json.dumps(asset_ids), post["createdAt"], None, None, None, None))
    return post


async def create_and_publish_bangla_post() -> dict:
    candidates = image_candidates()
    post = await generate_post(
        [],
        "Create a fresh Bangla Facebook post that introduces a useful LabLink workflow.",
        "A randomly selected LabLink product screenshot is attached. Do not describe "
        "unseen interface details; ground the post in the verified knowledge.",
        language="bn",
        image_options=candidates,
    )
    return publish_post(post["id"])


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
