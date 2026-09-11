import time

import pytest

from conftest import seed_product

H = {"Host": "127.0.0.1:8686"}


@pytest.fixture
def client(fake_ai, store):
    from web import create_app

    app = create_app(start_worker=True)
    app.config["TESTING"] = True
    return app.test_client()


def wait_for(client, job_id, statuses, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}?draft=1", headers=H).get_json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job stuck in {job['status']}: {job.get('error')}")


def test_full_review_flow(client, store):
    a = seed_product("dji-pocket-4-creator-combo", "DJI Pocket 4 Creator Combo", 14740000)
    b = seed_product("dji-pocket-4-standard-combo", "DJI Pocket 4 Standard Combo", 11635000)

    preview = client.post("/api/preview", json={"urls": [a, b]}, headers=H).get_json()
    assert preview["plan"]["kind"] == "variable" and preview["plan"]["labels"] == ["Creator Combo", "Standard Combo"]

    res = client.post("/api/jobs", json={"urls": [a, b], "options": {"discount_vnd": 100000, "status": "draft"}}, headers=H)
    assert res.status_code == 201
    job_id = res.get_json()["id"]

    dup = client.post("/api/jobs", json={"urls": [a], "options": {}}, headers=H)
    assert dup.status_code == 409 and dup.get_json()["job_id"] == job_id

    job = wait_for(client, job_id, {"review", "failed"})
    assert job["status"] == "review", job.get("error")
    assert job["stages"]["content"]["status"] == "done"
    assert job["logs"], "logs are recorded"
    draft = job["draft"]
    assert draft["variants"][1]["sale_price"] == 11535000

    draft["variants"][0]["label"] = "Combo Sáng Tạo"
    draft["images"] = list(reversed(draft["images"]))
    saved = client.put(f"/api/jobs/{job_id}/draft", json={"draft": draft}, headers=H)
    assert saved.status_code == 200

    bad = client.put(f"/api/jobs/{job_id}/draft", json={"draft": dict(draft, title="")}, headers=H)
    assert bad.status_code == 422

    client.post(f"/api/jobs/{job_id}/publish", json={"draft": draft}, headers=H)
    job = wait_for(client, job_id, {"done", "failed"})
    assert job["status"] == "done", job.get("error")
    pid = job["result"]["product_id"]
    assert store.products[pid]["attributes"][0]["options"][0] == "Combo Sáng Tạo"

    products = client.get("/api/products", headers=H).get_json()["items"]
    assert [p["product_id"] for p in products] == [pid]

    synced = client.post("/api/products/sync", json={}, headers=H).get_json()["items"]
    assert synced[0]["status"] == "draft"

    overview = client.get("/api/overview", headers=H).get_json()
    assert overview["stats"]["products"] == 1


def test_failed_job_retry_resumes(client, fake_ai, monkeypatch):
    url = seed_product("mini-5", "DJI Mini 5", 20000000)
    calls = {"n": 0}
    original = fake_ai.rewrite

    def flaky(*args, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            from lib.rewriter import RewriteError

            raise RewriteError("Anthropic API đang giới hạn tốc độ (rate limit)")
        return original(*args, **kw)

    from lib import pipeline

    monkeypatch.setattr(pipeline, "rewrite_content", flaky)
    job_id = client.post("/api/jobs", json={"urls": [url], "options": {"review": False}}, headers=H).get_json()["id"]
    job = wait_for(client, job_id, {"failed", "done"})
    assert job["status"] == "failed" and "rate limit" in job["error"]
    assert job["stages"]["content"]["status"] == "error"

    assert client.post(f"/api/jobs/{job_id}/retry", json={}, headers=H).status_code == 200
    job = wait_for(client, job_id, {"done", "failed"})
    assert job["status"] == "done", job.get("error")
    assert job["stages"]["review"]["status"] == "skipped"


def test_security_guards(client):
    assert client.post("/api/jobs", data="urls=x", headers=H).status_code == 415
    assert client.post("/api/jobs", json={"urls": []}, headers={**H, "Origin": "https://evil.example"}).status_code == 403
    assert client.get("/media/..%2F..%2Fetc/passwd", headers=H).status_code == 404
    settings = client.get("/api/settings", headers=H).get_json()["values"]
    assert "sk-ant-test" not in settings["ANTHROPIC_API_KEY"]["value"]


def test_settings_save_keeps_secrets_when_blank(client, isolated):
    from lib import settings

    res = client.put("/api/settings", json={"values": {"ANTHROPIC_API_KEY": "", "PRICE_DISCOUNT_VND": "60000", "ANTHROPIC_MODEL": "claude-sonnet-5"}}, headers=H)
    assert res.status_code == 200
    assert settings.get("ANTHROPIC_API_KEY") == "sk-ant-test"
    assert settings.default_discount() == 60000
    env = (isolated / ".env").read_text()
    assert "PRICE_DISCOUNT_VND" in env and "ANTHROPIC_API_KEY" not in env
