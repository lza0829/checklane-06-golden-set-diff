import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.config
    import app.db
    import app.main
    import app.seed

    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.seed)
    importlib.reload(app.main)
    app.seed.init_db()
    with TestClient(app.main.app) as c:
        yield c


def test_api_golden_register_and_diff(client):
    # 登记金标 → 查询回显条数
    resp = client.put("/api/golden/3", json={"fixture_path": "refunds_golden.jsonl"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 3

    # 入检 → 自动跑差分，成功发布
    batch = client.post("/api/inspect", json={"dataset_id": 3}).json()
    assert batch["status"] == "released"
    assert batch["golden_diff"]["result"] == "pass"

    diff = client.get(f"/api/batches/{batch['id']}/golden-diff").json()
    assert diff["result"] == "pass"
    assert diff["report"]["counts"]["diff_total"] == 0

    # 故意不一致 → 失败并撤回
    bad = client.post("/api/inspect", json={"dataset_id": 4}).json()
    assert bad["status"] == "golden_failed"
    assert bad["released_count"] == 0
    assert bad["golden_diff"]["report"]["counts"]["curated_missing"] == 1
    assert bad["golden_diff"]["rolled_back_count"] == 3

    curated = client.get("/api/curated", params={"dataset_id": 4}).json()
    assert all(r["batch_id"] != bad["id"] for r in curated)

    diffs = client.get("/api/golden-diffs", params={"dataset_id": 4}).json()
    assert diffs[0]["batch_id"] == bad["id"]
    assert diffs[0]["result"] == "fail"


def test_api_register_rejects_bad_fixture(client):
    resp = client.put("/api/golden/1", json={"fixture_path": "no_such_file.jsonl"})
    assert resp.status_code == 400
