import asyncio
import json
import logging
import mimetypes
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
    create_and_publish_bangla_post,
    generate_post,
    list_assets,
    list_knowledge,
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


class SettingsInput(BaseModel):
    postsPerDay: int = Field(ge=1, le=3)
    timezone: str = Field(default="Asia/Dhaka", max_length=80)
    mode: str = Field(default="approval")
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


class AssetMetadataInput(BaseModel):
    label: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)


class PostUpdateInput(BaseModel):
    caption: str = Field(min_length=20, max_length=10000)
    headline: str = Field(default="", max_length=200)
    assetIds: list[str] = Field(default_factory=list, max_length=10)


async def scheduler_loop():
    while True:
        try:
            await run_schedule()
        except Exception:
            logger.exception("Scheduled marketing run failed")
        await asyncio.sleep(60)


async def run_schedule():
    config = settings_dict()
    if not config["enabled"]:
        return
    try:
        local = datetime.now(ZoneInfo(config["timezone"]))
    except Exception:
        return
    slot = f"{local.date().isoformat()}-{local.strftime('%H:%M')}"
    if local.strftime("%H:%M") not in config["postingTimes"]:
        return
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
        post = candidates[-1] if candidates else await generate_post([], "", "")
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
        "integrations": {"deepseek": bool(settings.deepseek_api_key), "facebook": settings.facebook_ready},
    }


@app.post("/api/settings", dependencies=[Depends(authenticate)])
def update_settings(input: SettingsInput):
    if input.mode not in {"approval", "auto_publish"}:
        raise HTTPException(400, "Mode must be approval or auto_publish.")
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
    record = {
        "id": str(uuid4()),
        "title": input.title.strip(),
        "body": input.text.strip(),
        "kind": input.type.strip(),
        "source_url": input.sourceUrl,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "reviewed": 1,
    }
    with connect() as db:
        db.execute(
            "INSERT INTO knowledge(id, title, body, kind, source_url, created_at, reviewed) VALUES (:id, :title, :body, :kind, :source_url, :created_at, :reviewed)",
            record,
        )
    return {
        "id": record["id"],
        "title": record["title"],
        "text": record["body"],
        "type": record["kind"],
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
    label: str = Form(default="", max_length=120),
    description: str = Form(default="", max_length=1000),
):
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
                "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?)",
                (asset_id, safe_name, file.content_type, str(file_path), label.strip(), description.strip(), created),
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
    with connect() as db:
        updated = db.execute(
            "UPDATE assets SET label=?, description=? WHERE id=?",
            (input.label.strip(), input.description.strip(), asset_id),
        ).rowcount
    if not updated:
        raise HTTPException(404, "Asset not found.")
    return {"id": asset_id, "label": input.label.strip(), "description": input.description.strip()}


@app.post("/api/posts/generate", dependencies=[Depends(authenticate)], status_code=201)
async def make_draft(input: DraftInput):
    existing_ids = {asset["id"] for asset in list_assets()}
    if not set(input.assetIds) <= existing_ids:
        raise HTTPException(400, "One or more selected assets no longer exist.")
    try:
        return await generate_post(input.assetIds, input.angle, input.visualContext)
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
    return next(post for post in list_posts() if post["id"] == post_id)


@app.post("/api/posts/publish-now", dependencies=[Depends(authenticate)])
async def publish_now(request: Request):
    try:
        post = await create_and_publish_bangla_post()
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse(url="/", status_code=303)
        return post
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
