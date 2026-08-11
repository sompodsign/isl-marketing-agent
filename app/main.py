import asyncio
import json
import logging
import mimetypes
import random
import re
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.database import PROJECT_ROOT, UPLOAD_DIR, connect, initialise
from app.services import (
    asset_product,
    create_and_publish_bangla_post,
    generate_post,
    image_candidates,
    list_assets,
    list_knowledge,
    list_post_events,
    log_post_event,
    list_posts,
    publish_post,
    save_settings,
    settings_dict,
)

logger = logging.getLogger(__name__)
security = HTTPBasic(auto_error=False)
PUBLIC_DIR = PROJECT_ROOT / "public"
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "video/mp4", "video/quicktime"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
APPLICATIONS = {"LabLink", "KarbarPro", "Shikha"}
SCHEDULER_POLL_SECONDS = 10 * 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.production and not settings.dashboard_password:
        raise RuntimeError("DASHBOARD_PASSWORD is required when APP_ENV=production.")
    initialise()
    stale_before = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    with connect() as db:
        db.execute(
            "UPDATE posts SET status='failed', error='Publishing was interrupted; review before retrying.' "
            "WHERE status='publishing' AND (updated_at IS NULL OR updated_at < ?)",
            (stale_before,),
        )
    scheduler_task = asyncio.create_task(scheduler_loop()) if getattr(settings, "run_scheduler", True) else None
    app.state.scheduler_task = scheduler_task
    try:
        yield
    finally:
        if scheduler_task:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="InariSoftLabs Marketing Agent", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-origin request blocked."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; script-src 'self'; frame-ancestors 'none'"
    )
    return response


def authenticate(credentials: HTTPBasicCredentials | None = Depends(security)):
    if not settings.dashboard_password:
        return
    valid_username = bool(credentials) and secrets.compare_digest(credentials.username, "admin")
    valid_password = bool(credentials) and secrets.compare_digest(credentials.password, settings.dashboard_password)
    if not valid_username or not valid_password:
        raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Basic"})


class KnowledgeInput(BaseModel):
    title: str = Field(min_length=2, max_length=160)
    text: str = Field(min_length=20, max_length=20000)
    type: str = Field(default="product", max_length=40)
    sourceUrl: str | None = Field(default=None, max_length=1000)
    product: str = Field(min_length=2, max_length=80)


class SettingsInput(BaseModel):
    postsPerDay: int = Field(ge=1, le=3)
    timezone: str = Field(default="Asia/Dhaka", max_length=80)
    mode: str = Field(default="approval")
    scheduleLanguage: str = Field(default="bn", max_length=5)
    enabled: bool = False
    postingTimes: list[str] = Field(default_factory=list, max_length=3)
    contactCta: str = Field(default="", max_length=300)
    writingExamples: str = Field(default="", max_length=8000)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as error:
            raise ValueError("Use a valid IANA timezone such as Asia/Dhaka.") from error
        return value


class DraftInput(BaseModel):
    assetIds: list[str] = Field(default_factory=list, max_length=10)
    angle: str = Field(default="", max_length=500)
    visualContext: str = Field(default="", max_length=2000)
    language: str = Field(default="bn", max_length=5)


class AssetMetadataInput(BaseModel):
    label: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)
    product: str = Field(min_length=2, max_length=80)


class PostUpdateInput(BaseModel):
    caption: str = Field(min_length=20, max_length=10000)
    headline: str = Field(default="", max_length=200)
    assetIds: list[str] = Field(default_factory=list, max_length=10)


async def scheduler_loop():
    last_checked_at = datetime.now(timezone.utc) - timedelta(seconds=SCHEDULER_POLL_SECONDS)
    while True:
        checked_at = datetime.now(timezone.utc)
        try:
            await run_schedule(last_checked_at, checked_at)
        except Exception:
            logger.exception("Scheduled marketing run failed")
        last_checked_at = checked_at
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)


def due_schedule_slots(config: dict, checked_after: datetime, checked_at: datetime) -> list[str]:
    """Return configured local-time slots crossed since the previous scheduler check."""
    try:
        zone = ZoneInfo(config["timezone"])
    except Exception:
        return []
    if checked_after.tzinfo is None:
        checked_after = checked_after.replace(tzinfo=timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    first_local = checked_after.astimezone(zone)
    last_local = checked_at.astimezone(zone)
    slots: list[tuple[datetime, str]] = []
    for date in {first_local.date(), last_local.date()}:
        for time_value in config["postingTimes"]:
            hour, minute = map(int, time_value.split(":"))
            slot_local = datetime(date.year, date.month, date.day, hour, minute, tzinfo=zone)
            slot_at = slot_local.astimezone(timezone.utc)
            if checked_after < slot_at <= checked_at:
                slots.append((slot_at, f"{date.isoformat()}-{time_value}"))
    return [slot for _, slot in sorted(slots)]


async def run_schedule(checked_after: datetime | None = None, checked_at: datetime | None = None):
    config = settings_dict()
    if not config["enabled"]:
        return
    checked_at = checked_at or datetime.now(timezone.utc)
    checked_after = checked_after or checked_at - timedelta(seconds=SCHEDULER_POLL_SECONDS)
    for slot in due_schedule_slots(config, checked_after, checked_at):
        await run_schedule_slot(config, slot)


async def run_schedule_slot(config: dict, slot: str):
    with connect() as db:
        claimed = db.execute(
            "INSERT OR IGNORE INTO schedule_runs(slot, run_at, status, error) VALUES (?, ?, 'running', NULL)",
            (slot, datetime.now(timezone.utc).isoformat()),
        ).rowcount
        if not claimed:
            return
    try:
        candidates = [
            post for post in list_posts() if post["status"] in {"draft", "failed"} and not post["scheduledFor"]
        ]
        if candidates:
            post = candidates[-1]
        else:
            images = image_candidates()
            products: dict[str, list[dict]] = {}
            for candidate in images:
                products.setdefault(asset_product(candidate) or "InariSoftLabs", []).append(candidate)
            product, product_images = random.choice(list(products.items()))
            post = await generate_post(
                [], "", "", language=config.get("scheduleLanguage", "bn"), image_options=product_images
            )
        if config["mode"] == "auto_publish":
            await asyncio.to_thread(publish_post, post["id"])
        else:
            with connect() as db:
                db.execute(
                    "UPDATE posts SET scheduled_for=?, updated_at=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(), post["id"]),
                )
        with connect() as db:
            db.execute("UPDATE schedule_runs SET status='succeeded', error=NULL WHERE slot=?", (slot,))
    except Exception as error:
        with connect() as db:
            db.execute("DELETE FROM schedule_runs WHERE slot=?", (slot,))
        raise error


@app.get("/api/dashboard", dependencies=[Depends(authenticate)])
def dashboard():
    return {
        "settings": settings_dict(),
        "knowledge": list_knowledge(),
        "assets": list_assets(),
        "posts": list_posts(),
        "postEvents": list_post_events(),
        "integrations": {
            "writer": "openai" if settings.openai_api_key else ("gemini" if settings.gemini_api_key else ("deepseek" if settings.deepseek_api_key else "")),
            "facebook": settings.facebook_ready,
        },
    }


@app.post("/api/settings", dependencies=[Depends(authenticate)])
def update_settings(input: SettingsInput):
    if input.mode not in {"approval", "auto_publish"}:
        raise HTTPException(400, "Mode must be approval or auto_publish.")
    if input.scheduleLanguage not in {"bn", "en"}:
        raise HTTPException(400, "Choose Bengali or English for scheduled posts.")
    times = input.postingTimes
    defaults = {1: ["10:00"], 2: ["10:00", "18:00"], 3: ["09:00", "14:00", "20:00"]}
    if len(times) != input.postsPerDay:
        times = defaults[input.postsPerDay]
    if any(not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", time) for time in times):
        raise HTTPException(400, "Posting times must use 24-hour HH:MM format.")
    if len(set(times)) != len(times):
        raise HTTPException(400, "Posting times must be unique.")
    return save_settings({**input.model_dump(), "postingTimes": times})


@app.post("/api/knowledge", dependencies=[Depends(authenticate)], status_code=201)
def add_knowledge(input: KnowledgeInput):
    if input.product not in APPLICATIONS:
        raise HTTPException(400, "Choose LabLink, KarbarPro, or Shikha for this knowledge.")
    record = {
        "id": str(uuid4()),
        "title": input.title.strip(),
        "body": input.text.strip(),
        "kind": input.type.strip(),
        "source_url": input.sourceUrl,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reviewed": 1,
        "product": input.product,
    }
    with connect() as db:
        db.execute(
            "INSERT INTO knowledge(id, title, body, kind, source_url, created_at, reviewed, product) VALUES (:id, :title, :body, :kind, :source_url, :created_at, :reviewed, :product)",
            record,
        )
    return {
        "id": record["id"],
        "title": record["title"],
        "text": record["body"],
        "type": record["kind"],
        "product": record["product"],
        "reviewed": True,
    }


@app.post("/api/knowledge/{knowledge_id}/approve", dependencies=[Depends(authenticate)])
def approve_knowledge(knowledge_id: str):
    with connect() as db:
        updated = db.execute("UPDATE knowledge SET reviewed=1 WHERE id=?", (knowledge_id,)).rowcount
    if not updated:
        raise HTTPException(404, "Knowledge item not found.")
    return {"id": knowledge_id, "reviewed": True}


@app.post("/api/assets", dependencies=[Depends(authenticate)], status_code=201)
async def add_asset(
    file: UploadFile = File(...),
    product: str = Form(..., min_length=2, max_length=80),
    label: str = Form(default="", max_length=120),
    description: str = Form(default="", max_length=1000),
):
    product = product.strip()
    if product not in APPLICATIONS:
        raise HTTPException(400, "Choose LabLink, KarbarPro, or Shikha for this visual.")
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Upload a JPG, PNG, WebP, GIF, MP4, or MOV file.")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", file.filename or "asset")
    asset_id = str(uuid4())
    file_path = UPLOAD_DIR / f"{asset_id}-{safe_name}"
    size = 0
    signature = b""
    try:
        with file_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(400, "Each upload must be 8 MB or less.")
                if not signature:
                    signature = chunk[:16]
                destination.write(chunk)
        if not valid_media_signature(file.content_type, signature):
            raise HTTPException(400, "The file content does not match its declared media type.")
        created = datetime.now(timezone.utc).isoformat()
        with connect() as db:
            db.execute(
                "INSERT INTO assets (id, original_name, mime_type, path, label, description, product, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (asset_id, safe_name, file.content_type, str(file_path), label.strip(), description.strip(), product, created),
            )
    except Exception:
        file_path.unlink(missing_ok=True)
        raise
    return {
        "id": asset_id,
        "originalName": safe_name,
        "mimeType": file.content_type,
        "label": label.strip(),
        "description": description.strip(),
        "product": product,
        "createdAt": created,
    }


def valid_media_signature(mime_type: str, signature: bytes) -> bool:
    checks = {
        "image/jpeg": signature.startswith(b"\xff\xd8\xff"),
        "image/png": signature.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": signature.startswith(b"RIFF") and signature[8:12] == b"WEBP",
        "image/gif": signature.startswith((b"GIF87a", b"GIF89a")),
        "video/mp4": signature[4:8] == b"ftyp",
        "video/quicktime": signature[4:8] == b"ftyp",
    }
    return checks.get(mime_type, False)


@app.patch("/api/assets/{asset_id}", dependencies=[Depends(authenticate)])
def update_asset(asset_id: str, input: AssetMetadataInput):
    if input.product.strip() not in APPLICATIONS:
        raise HTTPException(400, "Choose LabLink, KarbarPro, or Shikha for this visual.")
    with connect() as db:
        updated = db.execute(
            "UPDATE assets SET label=?, description=?, product=? WHERE id=?",
            (input.label.strip(), input.description.strip(), input.product.strip(), asset_id),
        ).rowcount
    if not updated:
        raise HTTPException(404, "Asset not found.")
    return {"id": asset_id, "label": input.label.strip(), "description": input.description.strip(), "product": input.product.strip()}


@app.post("/api/posts/generate", dependencies=[Depends(authenticate)], status_code=201)
async def make_draft(input: DraftInput):
    if input.language not in {"bn", "en"}:
        raise HTTPException(400, "Choose Bengali or English for the post language.")
    existing_ids = {asset["id"] for asset in list_assets()}
    if not set(input.assetIds) <= existing_ids:
        raise HTTPException(400, "One or more selected assets no longer exist.")
    try:
        return await generate_post(input.assetIds, input.angle, input.visualContext, language=input.language)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/posts/{post_id}/publish", dependencies=[Depends(authenticate)])
def send_post(post_id: str):
    try:
        return publish_post(post_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.patch("/api/posts/{post_id}", dependencies=[Depends(authenticate)])
def update_post(post_id: str, input: PostUpdateInput):
    existing_ids = {asset["id"] for asset in list_assets()}
    if not set(input.assetIds) <= existing_ids:
        raise HTTPException(400, "One or more selected assets no longer exist.")
    with connect() as db:
        updated = db.execute(
            """UPDATE posts SET caption=?, headline=?, asset_ids_json=?, updated_at=?, error=NULL
               WHERE id=? AND status IN ('draft', 'failed')""",
            (
                input.caption.strip(),
                input.headline.strip(),
                json.dumps(input.assetIds),
                datetime.now(timezone.utc).isoformat(),
                post_id,
            ),
        ).rowcount
    if not updated:
        raise HTTPException(409, "Only draft or failed posts can be edited.")
    log_post_event(post_id, "draft_edited", "Draft caption or selected visuals were updated.")
    return next(post for post in list_posts() if post["id"] == post_id)


@app.post("/api/posts/publish-now", dependencies=[Depends(authenticate)])
async def publish_now(input: DraftInput = DraftInput()):
    try:
        if input.language not in {"bn", "en"}:
            raise HTTPException(400, "Choose Bengali or English for the post language.")
        if input.assetIds:
            existing_ids = {asset["id"] for asset in list_assets()}
            if not set(input.assetIds) <= existing_ids:
                raise HTTPException(400, "One or more selected assets no longer exist.")
            post = await generate_post(input.assetIds, input.angle, input.visualContext, language=input.language)
            return await asyncio.to_thread(publish_post, post["id"])
        if input.language == "bn":
            return await create_and_publish_bangla_post()
        post = await generate_post([], input.angle, input.visualContext, language="en")
        return await asyncio.to_thread(publish_post, post["id"])
    except ValueError as error:
        raise HTTPException(400, str(error)) from error


@app.post("/api/scheduler/run", dependencies=[Depends(authenticate)])
async def run_now():
    await run_schedule()
    return {"ok": True}


@app.get("/healthz")
def healthcheck():
    try:
        with connect() as db:
            db.execute("SELECT 1").fetchone()
    except Exception as error:
        raise HTTPException(503, "Database unavailable") from error
    return {"status": "ok"}


@app.get("/api/assets/{asset_id}/content", dependencies=[Depends(authenticate)])
def asset_content(asset_id: str):
    asset = next((item for item in list_assets() if item["id"] == asset_id), None)
    if not asset:
        raise HTTPException(404, "Asset not found.")
    with connect() as db:
        record = db.execute("SELECT path, mime_type FROM assets WHERE id=?", (asset_id,)).fetchone()
    return FileResponse(
        record["path"], media_type=record["mime_type"], headers={"Cache-Control": "private, max-age=300"}
    )


@app.get("/", dependencies=[Depends(authenticate)])
def home():
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/{file_name}", dependencies=[Depends(authenticate)])
def static(file_name: str):
    candidate = PUBLIC_DIR / file_name
    if candidate.name != file_name or not candidate.exists():
        raise HTTPException(404)
    return FileResponse(
        candidate,
        media_type=mimetypes.guess_type(candidate.name)[0],
        headers={"Cache-Control": "no-store"},
    )
