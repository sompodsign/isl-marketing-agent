import asyncio
import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

from app.config import settings
from app.database import UPLOAD_DIR, connect, initialise
from app.services import (create_and_publish_bangla_post, generate_post, list_assets,
                          list_knowledge, list_posts, publish_post, save_settings,
                          settings_dict)

app = FastAPI(title="InariSoftLabs Marketing Agent", docs_url=None, redoc_url=None)
security = HTTPBasic(auto_error=False)
PUBLIC_DIR = Path("public")
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "video/mp4", "video/quicktime"}


def authenticate(credentials: HTTPBasicCredentials | None = Depends(security)):
    if not settings.dashboard_password:
        return
    if not credentials or credentials.username != "admin" or credentials.password != settings.dashboard_password:
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
    postingTimes: list[str] = []
    contactCta: str = Field(default='', max_length=300)


class DraftInput(BaseModel):
    assetIds: list[str] = []
    angle: str = Field(default="", max_length=500)
    visualContext: str = Field(default="", max_length=2000)


class AssetMetadataInput(BaseModel):
    label: str = Field(default='', max_length=120)
    description: str = Field(default='', max_length=1000)


@app.on_event("startup")
async def startup():
    initialise()
    asyncio.create_task(scheduler_loop())


async def scheduler_loop():
    while True:
        try:
            await run_schedule()
        except Exception as error:
            print(f"Scheduler: {error}")
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
        if db.execute("SELECT 1 FROM schedule_runs WHERE slot=?", (slot,)).fetchone():
            return
        db.execute("INSERT INTO schedule_runs VALUES (?, ?)", (slot, datetime.utcnow().isoformat()))
    drafts = [post for post in list_posts() if post["status"] == "draft" and not post["scheduledFor"]]
    post = drafts[0] if drafts else await generate_post([], "", "")
    if config["mode"] == "auto_publish":
        publish_post(post["id"])
    else:
        with connect() as db: db.execute("UPDATE posts SET scheduled_for=? WHERE id=?", (datetime.utcnow().isoformat(), post["id"]))


@app.get("/api/dashboard", dependencies=[Depends(authenticate)])
def dashboard():
    return {"settings": settings_dict(), "knowledge": list_knowledge(), "assets": list_assets(), "posts": list_posts(), "integrations": {"deepseek": bool(settings.deepseek_api_key), "facebook": settings.facebook_ready}}


@app.post("/api/settings", dependencies=[Depends(authenticate)])
def update_settings(input: SettingsInput):
    if input.mode not in {"approval", "auto_publish"}:
        raise HTTPException(400, "Mode must be approval or auto_publish.")
    times = input.postingTimes
    defaults = {1: ["10:00"], 2: ["10:00", "18:00"], 3: ["09:00", "14:00", "20:00"]}
    if len(times) != input.postsPerDay or any(not re.fullmatch(r"[0-2][0-9]:[0-5][0-9]", time) for time in times):
        times = defaults[input.postsPerDay]
    return save_settings({**input.model_dump(), "postingTimes": times})


@app.post("/api/knowledge", dependencies=[Depends(authenticate)], status_code=201)
def add_knowledge(input: KnowledgeInput):
    record = {"id": str(uuid4()), "title": input.title.strip(), "body": input.text.strip(), "kind": input.type.strip(), "source_url": input.sourceUrl, "created_at": datetime.utcnow().isoformat()}
    with connect() as db:
        db.execute("INSERT INTO knowledge VALUES (:id, :title, :body, :kind, :source_url, :created_at)", record)
    return {"id": record["id"], "title": record["title"], "text": record["body"], "type": record["kind"]}


@app.post("/api/assets", dependencies=[Depends(authenticate)], status_code=201)
async def add_asset(
    file: UploadFile = File(...),
    label: str = Form(default=''),
    description: str = Form(default=''),
):
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(400, "Upload a JPG, PNG, WebP, GIF, MP4, or MOV file.")
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(400, "Each upload must be 8 MB or less.")
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "-", file.filename or "asset")
    asset_id = str(uuid4()); file_path = UPLOAD_DIR / f"{asset_id}-{safe_name}"; file_path.write_bytes(content)
    created = datetime.utcnow().isoformat()
    with connect() as db:
        db.execute(
            "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?, ?)",
            (asset_id, safe_name, file.content_type, str(file_path), label.strip(), description.strip(), created),
        )
    return {"id": asset_id, "originalName": safe_name, "mimeType": file.content_type, "label": label.strip(), "description": description.strip(), "createdAt": created}


@app.patch("/api/assets/{asset_id}", dependencies=[Depends(authenticate)])
def update_asset(asset_id: str, input: AssetMetadataInput):
    with connect() as db:
        updated = db.execute(
            "UPDATE assets SET label=?, description=? WHERE id=?",
            (input.label.strip(), input.description.strip(), asset_id),
        ).rowcount
    if not updated:
        raise HTTPException(404, 'Asset not found.')
    return {"id": asset_id, "label": input.label.strip(), "description": input.description.strip()}


@app.post("/api/posts/generate", dependencies=[Depends(authenticate)], status_code=201)
async def make_draft(input: DraftInput):
    try: return await generate_post(input.assetIds, input.angle, input.visualContext)
    except ValueError as error: raise HTTPException(400, str(error)) from error


@app.post("/api/posts/{post_id}/publish", dependencies=[Depends(authenticate)])
def send_post(post_id: str):
    try: return publish_post(post_id)
    except ValueError as error: raise HTTPException(400, str(error)) from error


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
    await run_schedule(); return {"ok": True}


@app.get("/", dependencies=[Depends(authenticate)])
def home(): return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/{file_name}", dependencies=[Depends(authenticate)])
def static(file_name: str):
    candidate = PUBLIC_DIR / file_name
    if candidate.name != file_name or not candidate.exists(): raise HTTPException(404)
    return FileResponse(
        candidate,
        media_type=mimetypes.guess_type(candidate.name)[0],
        headers={"Cache-Control": "no-store"},
    )
