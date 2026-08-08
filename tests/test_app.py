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
        ),
    )
    database.initialise()
    return tmp_path


def insert_draft(caption="A useful, reviewable Facebook caption for a diagnostic center team."):
    post_id = "post-1"
    with database.connect() as db:
        db.execute(
            """INSERT INTO posts (
                id, status, caption, headline, cta, hashtags_json, image_notes,
                confidence, fact_ids_json, asset_ids_json, created_at
            ) VALUES (?, 'draft', ?, '', '', '[]', '', 'high', '[]', '[]', ?)""",
            (post_id, caption, services.now()),
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
    post_id = insert_draft()
    fake_settings = SimpleNamespace(
        facebook_ready=True,
        facebook_version="v1.0",
        facebook_page_id="page",
        facebook_token="token",
    )
    monkeypatch.setattr(services, "settings", fake_settings)
    calls = []

    def fake_request(url, data):
        calls.append((url, data))
        return {"id": "facebook-1"}

    monkeypatch.setattr(services, "_facebook_request", fake_request)
    first = services.publish_post(post_id)
    second = services.publish_post(post_id)

    assert first["status"] == "published"
    assert second["facebookPostId"] == "facebook-1"
    assert len(calls) == 1


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
        async def post(self, url, **kwargs): captured.update(kwargs["json"]); return FakeResponse()

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
