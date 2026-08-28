import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import database, main, services


@pytest.fixture()
def isolated_data(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(database, "DATA_DIR", tmp_path)
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "marketing-agent.db")
    monkeypatch.setattr(database, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(main, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            production=False,
            dashboard_password="",
            deepseek_api_key="",
            facebook_ready=False,
            linkedin_ready=False,
        ),
    )
    database.initialise()
    return tmp_path


def insert_asset(asset_id="asset-1", product="LabLink", mime_type="image/png"):
    with database.connect() as db:
        db.execute(
            "INSERT INTO assets (id, original_name, mime_type, path, label, description, product, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, "shot.png", mime_type, "unused-path", f"{product} — Shot", "A visible workflow", product, services.now()),
        )
    return asset_id


def insert_draft(
    caption="A useful, reviewable Facebook caption for a diagnostic center team.",
    asset_ids=(),
    channel="facebook",
):
    post_id = "post-1"
    with database.connect() as db:
        db.execute(
            """INSERT INTO posts (
                id, status, caption, headline, cta, hashtags_json, image_notes,
                confidence, fact_ids_json, asset_ids_json, created_at, channel
            ) VALUES (?, 'draft', ?, '', '', '[]', '', 'high', '[]', ?, ?, ?)""",
            (post_id, caption, json.dumps(list(asset_ids)), services.now(), channel),
        )
    return post_id


def test_settings_reject_invalid_timezone_and_time(isolated_data):
    with TestClient(main.app) as client:
        payload = {
            "postsPerDay": 1,
            "timezone": "Not/AZone",
            "mode": "approval",
            "enabled": True,
            "postingTimes": ["10:00"],
            "contactCta": "",
            "writingExamples": "",
        }
        assert client.post("/api/settings", json=payload).status_code == 422

        payload["timezone"] = "Asia/Dhaka"
        payload["postingTimes"] = ["29:00"]
        response = client.post("/api/settings", json=payload)
        assert response.status_code == 400
        assert "24-hour" in response.json()["detail"]


def test_settings_persist_scheduled_post_language(isolated_data):
    with TestClient(main.app) as client:
        response = client.post(
            "/api/settings",
            json={
                "postsPerDay": 1,
                "timezone": "Asia/Dhaka",
                "scheduleLanguage": "en",
                "mode": "approval",
                "enabled": True,
                "postingTimes": ["10:00"],
            },
        )

    assert response.status_code == 200
    assert response.json()["scheduleLanguage"] == "en"
    assert services.settings_dict()["scheduleLanguage"] == "en"


def test_scheduler_runs_a_slot_crossed_between_ten_minute_checks():
    config = {"timezone": "Asia/Dhaka", "postingTimes": ["10:00"]}
    checked_after = datetime(2026, 8, 8, 3, 59, tzinfo=timezone.utc)
    checked_at = datetime(2026, 8, 8, 4, 5, tzinfo=timezone.utc)

    assert main.due_schedule_slots(config, checked_after, checked_at) == ["2026-08-08-10:00"]


def test_scheduler_uses_configured_language_for_generated_post(isolated_data, monkeypatch):
    insert_asset()
    captured = {}

    async def fake_generation(*args, **kwargs):
        captured.update(kwargs)
        return {"id": "generated-post"}

    monkeypatch.setattr(main, "generate_post", fake_generation)
    asyncio.run(
        main.run_schedule_slot(
            {"mode": "approval", "scheduleLanguage": "en"},
            "2026-08-08-10:00",
        )
    )

    assert captured["language"] == "en"
    assert captured["channel"] == "facebook"


def test_scheduler_creates_a_linkedin_companion_when_configured(isolated_data, monkeypatch):
    insert_asset()
    captured = []

    async def fake_generation(*args, **kwargs):
        captured.append(kwargs)
        post_id = f"generated-{len(captured)}"
        with database.connect() as db:
            db.execute(
                "INSERT INTO posts (id, status, caption, headline, cta, hashtags_json, image_notes, confidence, fact_ids_json, asset_ids_json, created_at, channel) VALUES (?, 'draft', 'caption', '', '', '[]', '', 'high', '[]', '[]', ?, ?)",
                (post_id, services.now(), kwargs.get("channel", "facebook")),
            )
        return {"id": post_id}

    monkeypatch.setattr(
        main,
        "settings",
        SimpleNamespace(
            production=False,
            dashboard_password="",
            facebook_ready=True,
            linkedin_ready=True,
        ),
    )
    monkeypatch.setattr(main, "generate_post", fake_generation)
    asyncio.run(
        main.run_schedule_slot(
            {"mode": "approval", "scheduleLanguage": "bn"},
            "2026-08-08-10:00",
        )
    )

    assert [call["channel"] for call in captured] == ["facebook", "linkedin"]
    assert captured[1]["language"] == "en"
    scheduled = [post["channel"] for post in services.list_posts() if post["scheduledFor"]]
    assert sorted(scheduled) == ["facebook", "linkedin"]


def test_upload_checks_file_signature(isolated_data):
    with TestClient(main.app) as client:
        response = client.post(
            "/api/assets",
            data={"product": "LabLink"},
            files={"file": ("fake.png", b"not a png", "image/png")},
        )
    assert response.status_code == 400
    assert not list((isolated_data / "uploads").iterdir())


def test_publish_is_idempotent(isolated_data, monkeypatch):
    insert_asset()
    post_id = insert_draft(asset_ids=("asset-1",))
    fake_settings = SimpleNamespace(
        facebook_ready=True,
        facebook_version="v1.0",
        facebook_page_id="page",
        facebook_token="token",
    )
    monkeypatch.setattr(services, "settings", fake_settings)
    calls = []

    def fake_multipart(url, fields, file_path, mime_type, field_name="source"):
        return {"id": "photo-1"}

    def fake_request(url, data):
        calls.append((url, data))
        return {"id": "facebook-1"}

    monkeypatch.setattr(services, "_facebook_multipart", fake_multipart)
    monkeypatch.setattr(services, "_facebook_request", fake_request)
    first = services.publish_post(post_id)
    second = services.publish_post(post_id)

    assert first["status"] == "published"
    assert second["facebookPostId"] == "facebook-1"
    assert len(calls) == 1


def test_linkedin_publish_uploads_image_and_creates_post(isolated_data, monkeypatch):
    insert_asset()
    post_id = insert_draft(asset_ids=("asset-1",), channel="linkedin")
    fake_settings = SimpleNamespace(
        linkedin_ready=True,
        linkedin_token="token",
        linkedin_author="urn:li:organization:123",
        linkedin_api_version="202506",
    )
    monkeypatch.setattr(services, "settings", fake_settings)
    registrations, uploads, created = [], [], []

    def fake_register(author):
        registrations.append(author)
        return {"uploadUrl": "https://upload.example.com/asset", "asset": "urn:li:digitalmediaAsset:555"}

    def fake_upload(url, path, mime_type):
        uploads.append((url, path, mime_type))

    def fake_create(author, commentary, media_urn):
        created.append((author, commentary, media_urn))
        return "urn:li:share:999"

    monkeypatch.setattr(services, "_linkedin_register_image_upload", fake_register)
    monkeypatch.setattr(services, "_linkedin_upload_image", fake_upload)
    monkeypatch.setattr(services, "_linkedin_create_post", fake_create)

    first = services.publish_post(post_id)
    second = services.publish_post(post_id)

    assert registrations == ["urn:li:organization:123"]
    assert uploads and uploads[0][2] == "image/png"
    assert created and created[0][2] == "urn:li:digitalmediaAsset:555"
    assert first["status"] == "published"
    assert first["linkedinPostId"] == "urn:li:share:999"
    assert second["linkedinPostId"] == "urn:li:share:999"
    assert len(created) == 1, "publishing twice must not create a second LinkedIn post"


def test_linkedin_publish_requires_configuration(isolated_data, monkeypatch):
    insert_asset()
    post_id = insert_draft(asset_ids=("asset-1",), channel="linkedin")
    monkeypatch.setattr(services, "settings", SimpleNamespace(linkedin_ready=False))
    with pytest.raises(ValueError, match="LinkedIn is not configured"):
        services.publish_post(post_id)


def test_linkedin_publish_rejects_video_assets(isolated_data, monkeypatch):
    insert_asset(mime_type="video/mp4")
    post_id = insert_draft(asset_ids=("asset-1",), channel="linkedin")
    monkeypatch.setattr(
        services,
        "settings",
        SimpleNamespace(linkedin_ready=True, linkedin_token="token", linkedin_author="urn:li:organization:1", linkedin_api_version="202506"),
    )
    with pytest.raises(ValueError, match="image posts only"):
        services.publish_post(post_id)
    statuses = {post["id"]: post["status"] for post in services.list_posts()}
    assert statuses[post_id] == "failed"


def test_draft_can_be_edited_before_publish(isolated_data):
    post_id = insert_draft()
    with TestClient(main.app) as client:
        response = client.patch(
            f"/api/posts/{post_id}",
            json={
                "caption": "A more human caption with a concrete daily workflow.",
                "headline": "Daily work",
                "assetIds": [],
            },
        )
    assert response.status_code == 200
    assert response.json()["caption"].startswith("A more human")


def test_generation_uses_human_example_and_validates_facts(isolated_data, monkeypatch):
    with database.connect() as db:
        db.execute(
            "INSERT INTO knowledge(id, title, body, kind, source_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "fact-1",
                "Report workflow",
                "LabLink keeps report work in draft, verified, and delivered stages for center teams.",
                "workflow",
                None,
                services.now(),
            ),
        )
        db.execute("INSERT OR REPLACE INTO settings VALUES ('writing_examples', 'A calm owner-style example.')")

    words = ["practical"] * 74
    caption = "A clear opening\n\n" + " ".join(words) + "\n\n#One #Two #Three"
    response_payload = {
        "caption": caption,
        "headline": "Clear report work",
        "cta": "Message us",
        "hashtags": ["#One", "#Two", "#Three"],
        "imageNotes": "",
        "selectedAssetId": "",
        "confidence": "high",
        "factIds": ["fact-1"],
    }
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(response_payload)}}]}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured.update(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr(services.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        services,
        "settings",
        SimpleNamespace(deepseek_api_key="key", deepseek_model="model"),
    )

    post = asyncio.run(services.generate_post([], "report workflow"))
    prompt = captured["messages"][1]["content"]
    assert "HUMAN WRITING RULES" in prompt
    assert "A calm owner-style example." in prompt
    assert "everyday, educated voice commonly used by Bangladesh-based businesses" in prompt
    assert "silently read the Bangla" in prompt
    assert "Never invent a time of day" in prompt
    assert post["factIds"] == ["fact-1"]


def test_linkedin_generation_uses_professional_english_prompt(isolated_data, monkeypatch):
    with database.connect() as db:
        db.execute(
            "INSERT INTO knowledge(id, title, body, kind, source_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "fact-1",
                "Report workflow",
                "LabLink keeps report work in draft, verified, and delivered stages for center teams.",
                "workflow",
                None,
                services.now(),
            ),
        )
    words = ["reliable"] * 90
    caption = "Clinics lose hours chasing report status\n\n" + " ".join(words) + "\n\n#SoftwareDevelopment #CustomSoftware #LabLink"
    response_payload = {
        "caption": caption,
        "headline": "Report workflow",
        "cta": "Message us",
        "hashtags": ["#SoftwareDevelopment", "#CustomSoftware", "#LabLink"],
        "imageNotes": "",
        "selectedAssetId": "",
        "confidence": "high",
        "factIds": ["fact-1"],
    }
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(response_payload)}}]}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured.update(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr(services.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(services, "settings", SimpleNamespace(deepseek_api_key="key", deepseek_model="model"))

    post = asyncio.run(services.generate_post([], "report workflow", channel="linkedin"))
    prompt = captured["messages"][1]["content"]
    assert "LINKEDIN WRITING RULES" in prompt
    assert "software development partner" in prompt
    assert "HUMAN WRITING RULES" not in prompt
    assert "silently read the Bangla" not in prompt
    assert "complete final LinkedIn post" in prompt
    assert "brand-services" in prompt, "company service facts must ground LinkedIn copy"
    assert post["channel"] == "linkedin"


def test_generation_rejects_unknown_channel(isolated_data, monkeypatch):
    monkeypatch.setattr(services, "settings", SimpleNamespace(deepseek_api_key="key", deepseek_model="model"))
    with pytest.raises(ValueError, match="Choose Facebook or LinkedIn"):
        asyncio.run(services.generate_post([], "angle", channel="twitter"))


def test_api_rejects_unknown_channel(isolated_data):
    with TestClient(main.app) as client:
        response = client.post("/api/posts/generate", json={"assetIds": ["asset-1"], "channel": "twitter"})
    assert response.status_code == 400
    assert "Facebook or LinkedIn" in response.json()["detail"]


def test_generate_post_retries_when_draft_is_too_similar(isolated_data, monkeypatch):
    with database.connect() as db:
        db.execute(
            "INSERT INTO knowledge(id, title, body, kind, source_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                "fact-1",
                "Report workflow",
                "LabLink keeps report work in draft, verified, and delivered stages for center teams.",
                "workflow",
                None,
                services.now(),
            ),
        )
    filler_a = ["report"] * 74
    similar_caption = "Report opening here\n\n" + " ".join(filler_a) + "\n\n#One #Two #Three"
    filler_b = ["invoice"] * 74
    divergent_caption = "Invoice opening here\n\n" + " ".join(filler_b) + "\n\n#Alpha #Beta #Gamma"
    # Seed a recent post identical to the first draft so Jaccard = 1.0 > 0.62.
    insert_draft(caption=similar_caption)

    def payload_for(caption):
        return {
            "caption": caption,
            "headline": "Headline",
            "cta": "Message us",
            "hashtags": ["#One", "#Two", "#Three"] if "report" in caption else ["#Alpha", "#Beta", "#Gamma"],
            "imageNotes": "",
            "selectedAssetId": "",
            "confidence": "high",
            "factIds": ["fact-1"],
        }

    call_log = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            # First call returns the too-similar draft; later calls return the divergent one.
            caption = similar_caption if len(call_log) == 1 else divergent_caption
            return {"choices": [{"message": {"content": json.dumps(payload_for(caption))}}]}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            call_log.append(url)
            return FakeResponse()

    monkeypatch.setattr(services.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        services,
        "settings",
        SimpleNamespace(deepseek_api_key="key", deepseek_model="model"),
    )

    post = asyncio.run(services.generate_post([], "report workflow"))
    assert len(call_log) == 2, "first draft should be rejected as too similar and retried once"
    assert "invoice" in post["caption"]


def test_bangla_terms_are_retrievable(isolated_data):
    with database.connect() as db:
        db.execute(
            "INSERT INTO knowledge(id, title, body, kind, source_url, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("bangla", "রিপোর্ট যাচাই", "রিপোর্ট যাচাই করে সেন্টারের কাজ গুছিয়ে রাখা যায়।", "workflow", None, services.now()),
        )
    results = services.retrieve_knowledge("রিপোর্ট যাচাই")
    assert results[0]["id"] == "bangla"


def test_selected_product_is_read_from_imported_asset_label():
    assert services.selected_product([{"label": "KarbarPro — Sales History"}]) == "KarbarPro"


def test_selected_product_uses_explicit_asset_assignment():
    assert services.selected_product([{"product": "KarbarPro", "label": "Any screenshot"}]) == "KarbarPro"


def test_product_page_links_are_added_before_hashtags():
    caption = "Opening line\n\nA useful workflow for the team.\n\n#One #Two #Three"

    assert services.add_product_page_link(caption, "LabLink") == (
        "Opening line\n\nA useful workflow for the team.\n\n"
        "www.inarisoftlabs.com\n#One #Two #Three"
    )
    assert services.add_product_page_link(caption, "KarbarPro").count("www.inarisoftlabs.com") == 1
    assert services.add_product_page_link(caption, "Shikha").count("www.inarisoftlabs.com") == 1
    assert services.add_product_page_link(
        "Learn more at https://inarisoftlabs.com/products/lablink\n\n#One",
        "LabLink",
    ) == "Learn more at www.inarisoftlabs.com\n\n#One"


def test_missing_configured_contact_cta_is_added_before_hashtags():
    assert services.ensure_contact_cta("Useful details\n\n#One #Two #Three", "Call: 01705569764") == (
        "Useful details\n\nCall: 01705569764\n\n#One #Two #Three"
    )


def test_website_link_normalization_does_not_change_contact_email():
    caption = "Email contact@inarisoftlabs.com\n\nhttps://inarisoftlabs.com/products/lablink\n\n#One"
    assert services.add_product_page_link(caption, "LabLink") == (
        "Email contact@inarisoftlabs.com\n\nwww.inarisoftlabs.com\n\n#One"
    )


def test_publish_rejects_post_without_an_image(isolated_data, monkeypatch):
    post_id = insert_draft()
    monkeypatch.setattr(services, "settings", SimpleNamespace(facebook_ready=True, linkedin_ready=False))
    with pytest.raises(ValueError, match="Select one product image"):
        services.publish_post(post_id)


def test_publish_now_without_a_selected_asset_uses_an_available_image(monkeypatch):
    captured = {}

    async def fake_create(language, angle, visual_context, channel="facebook"):
        captured.update(language=language, angle=angle, visual_context=visual_context, channel=channel)
        return {"id": "published-post", "assetIds": ["image-1"]}

    monkeypatch.setattr(main, "create_and_publish_bangla_post", fake_create)

    result = asyncio.run(main.publish_now(main.DraftInput(language="en", angle="A useful angle")))

    assert result["assetIds"] == ["image-1"]
    assert captured == {"language": "en", "angle": "A useful angle", "visual_context": "", "channel": "facebook"}


def test_karbarpro_has_its_own_writing_example(isolated_data, monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": '{"caption":"একটি স্বাভাবিক ক্যাপশন।", "headline":"", "cta":"", "hashtags":[], "imageNotes":"", "selectedAssetId":"", "confidence":"high", "factIds":[]}'}}]}

    class FakeClient:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def post(self, url, **kwargs):
            captured.update(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr(services.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(services, "settings", SimpleNamespace(deepseek_api_key="key", deepseek_model="model"))
    monkeypatch.setattr(services, "usable_knowledge", lambda: True)
    monkeypatch.setattr(services, "product_knowledge", lambda product, angle: [{"id":"karbarpro-test", "title":"KarbarPro fact", "text":"A verified KarbarPro fact.", "type":"product"}])
    with pytest.raises(ValueError, match="between 80 and 220 words"):
        asyncio.run(services.generate_post([], "", image_options=[{"product":"KarbarPro", "id":"image-1", "label":"KarbarPro — Customer Dues", "description":"Dues screen"}]))
    assert "বিকেলের ভিড়ের আগে কার কাছে কত বাকি আছে" in captured["messages"][1]["content"]


def test_selected_product_rejects_mixed_product_assets():
    with pytest.raises(ValueError, match="one product only"):
        services.selected_product([{"label": "KarbarPro — Sales History"}, {"label": "LabLink — Reports"}])


def test_unreviewed_imports_are_not_retrieved(isolated_data):
    with database.connect() as db:
        db.execute(
            "INSERT INTO knowledge(id, title, body, kind, source_url, created_at, reviewed) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (
                "pending",
                "Unreviewed claim",
                "A uniqueunreviewedterm that must not reach the writing model.",
                "website",
                None,
                services.now(),
            ),
        )
    assert all(item["id"] != "pending" for item in services.retrieve_knowledge("uniqueunreviewedterm"))


def test_failed_schedule_can_be_retried(isolated_data, monkeypatch):
    insert_asset()
    posting_time = datetime.now(ZoneInfo("Asia/Dhaka")).strftime("%H:%M")
    with database.connect() as db:
        db.execute("INSERT OR REPLACE INTO settings VALUES ('enabled', 'true')")
        db.execute("INSERT OR REPLACE INTO settings VALUES ('timezone', 'Asia/Dhaka')")
        db.execute("INSERT OR REPLACE INTO settings VALUES ('posting_times', ?)", (json.dumps([posting_time]),))

    async def fail_generation(*args, **kwargs):
        raise ValueError("temporary failure")

    monkeypatch.setattr(main, "generate_post", fail_generation)
    with pytest.raises(ValueError, match="temporary failure"):
        asyncio.run(main.run_schedule())
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM schedule_runs").fetchone()[0] == 0


def make_image_file(tmp_path, name="shot.png", size=(1440, 900)):
    from PIL import Image

    path = tmp_path / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (240, 244, 248)).save(path)
    return path


def insert_real_image_asset(asset_id="asset-1", product="LabLink"):
    path = make_image_file(database.DATA_DIR)
    with database.connect() as db:
        db.execute(
            "INSERT INTO assets (id, original_name, mime_type, path, label, description, product, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (asset_id, "shot.png", "image/png", str(path), f"{product} — Shot", "A visible workflow", product, services.now()),
        )
    return asset_id


def test_facebook_publish_uploads_composed_social_card(isolated_data, monkeypatch):
    insert_real_image_asset()
    post_id = insert_draft(asset_ids=("asset-1",))
    with database.connect() as db:
        db.execute("UPDATE posts SET headline=? WHERE id=?", ("রোগী ও ডাক্তার — সব তথ্য এক জায়গায়", post_id))
    monkeypatch.setattr(
        services,
        "settings",
        SimpleNamespace(facebook_ready=True, facebook_version="v1.0", facebook_page_id="page", facebook_token="token", social_cards=True),
    )
    uploads = []

    def fake_multipart(url, fields, file_path, mime_type, field_name="source"):
        uploads.append((file_path, mime_type))
        return {"id": "photo-1"}

    monkeypatch.setattr(services, "_facebook_multipart", fake_multipart)
    monkeypatch.setattr(services, "_facebook_request", lambda url, data: {"id": "facebook-1"})

    services.publish_post(post_id)

    assert uploads, "the photo endpoint must receive an upload"
    uploaded_path, uploaded_mime = uploads[0]
    assert uploaded_mime == "image/png"
    from pathlib import Path

    card = Path(uploaded_path)
    assert card.name.startswith(post_id), "the lead upload must be the composed card, not the raw screenshot"
    from PIL import Image

    with Image.open(card) as composed:
        assert composed.size == (1200, 675)


def test_publish_falls_back_to_raw_screenshot_when_composition_fails(isolated_data, monkeypatch):
    insert_asset()  # path "unused-path" does not exist, so composition must fall back
    post_id = insert_draft(asset_ids=("asset-1",))
    monkeypatch.setattr(
        services,
        "settings",
        SimpleNamespace(facebook_ready=True, facebook_version="v1.0", facebook_page_id="page", facebook_token="token", social_cards=True),
    )
    uploads = []

    def fake_multipart(url, fields, file_path, mime_type, field_name="source"):
        uploads.append((file_path, mime_type))
        return {"id": "photo-1"}

    monkeypatch.setattr(services, "_facebook_multipart", fake_multipart)
    monkeypatch.setattr(services, "_facebook_request", lambda url, data: {"id": "facebook-1"})

    services.publish_post(post_id)

    assert uploads == [("unused-path", "image/png")]


def test_visual_endpoint_serves_composed_card(isolated_data):
    insert_real_image_asset()
    post_id = insert_draft(asset_ids=("asset-1",))
    response = TestClient(main.app).get(f"/api/posts/{post_id}/visual")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    from PIL import Image
    import io

    with Image.open(io.BytesIO(response.content)) as composed:
        assert composed.size == (1200, 675)


def test_visual_endpoint_returns_404_for_video_posts(isolated_data):
    insert_asset(mime_type="video/mp4")
    post_id = insert_draft(asset_ids=("asset-1",))
    response = TestClient(main.app).get(f"/api/posts/{post_id}/visual")
    assert response.status_code == 404


def test_social_card_renders_bangla_headline(tmp_path):
    from app.visuals import compose_card

    headline = "রোগী ও ডাক্তার — সব তথ্য এক জায়গায়"
    from PIL import Image

    # Compact header layout (wide dashboard screenshot).
    wide_shot = make_image_file(tmp_path, name="wide.png", size=(1440, 900))
    wide_card = tmp_path / "card-wide.png"
    compose_card(str(wide_shot), headline, "LabLink", wide_card)
    with Image.open(wide_card).convert("RGB") as card:
        histogram = card.crop((56, 58, 1144, 140)).histogram()
        white_ink = min(histogram[255], histogram[256 + 255], histogram[512 + 255])
    assert white_ink > 3000, "the Bengali headline must be rendered in the compact header band"

    # Tall header layout (squarish screenshot).
    square_shot = make_image_file(tmp_path, name="square.png", size=(900, 900))
    square_card = tmp_path / "card-square.png"
    compose_card(str(square_shot), headline, "LabLink", square_card)
    with Image.open(square_card).convert("RGB") as card:
        histogram = card.crop((56, 100, 1144, 250)).histogram()
        white_ink = min(histogram[255], histogram[256 + 255], histogram[512 + 255])
    assert white_ink > 3000, "the Bengali headline must be rendered in the tall header band"


def test_wide_screenshots_get_a_compact_header_and_larger_screenshot(tmp_path):
    from PIL import Image

    from app.visuals import compose_card

    wide_shot = make_image_file(tmp_path, name="wide.png", size=(1440, 900))
    card_path = compose_card(str(wide_shot), "কোন রিপোর্ট বাকি — এক নজরে", "LabLink", tmp_path / "card.png")
    with Image.open(card_path).convert("RGB") as card:
        # Compact header: at y=200 the card must already be past the dark
        # brand gradient (the tall layout would still show header colours).
        pixel = card.getpixel((600, 200))
        assert min(pixel) > 200, f"y=200 must be past the compact header, got {pixel}"

        # The fitted screenshot must dominate the card: sample the centre
        # column across the body and count non-background samples.
        body_background = (238, 242, 247)
        samples = [card.getpixel((600, row)) for row in range(170, 660, 6)]
        non_background = sum(1 for pixel in samples if pixel != body_background)
        assert non_background > 60, "the screenshot must fill the enlarged body area"
