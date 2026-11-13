import importlib
import json

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.config
    import app.db
    import app.seed
    import app.engines.parse_fixture
    import app.repositories.batches
    import app.repositories.curated
    import app.repositories.datasets
    import app.repositories.golden
    import app.repositories.quarantine
    import app.repositories.rules
    import app.modules.golden_set_diff
    import app.services.golden_service
    import app.services.inspect_service as svc

    for mod in (
        app.config,
        app.db,
        app.engines.parse_fixture,
        app.repositories.batches,
        app.repositories.curated,
        app.repositories.datasets,
        app.repositories.golden,
        app.repositories.quarantine,
        app.repositories.rules,
        app.modules.golden_set_diff,
        app.services.golden_service,
        svc,
    ):
        importlib.reload(mod)
    app.seed.init_db()
    return svc


def _open():
    from app.db import connect

    return connect()


def test_orders_inspect_quarantine_and_release(env):
    detail = env.run_inspect(1)
    assert detail["scanned_count"] == 6
    # ORD-3 missing sku, ORD-4 bad price → quarantine; ORD-5 warn-only still pass
    assert detail["quarantined_count"] == 2
    assert detail["passed_count"] == 4
    assert detail["released_count"] == 4
    assert all(c["ok"] for c in detail["report"]["checks"] if c["name"] == "scan_breakdown")
    assert all(c["ok"] for c in detail["report"]["checks"] if c["name"] == "release_matches_passed")


def test_golden_consistent_publishes_successfully(env):
    detail = env.run_inspect(3)
    assert detail["status"] == "released"
    assert detail["released_count"] == 3
    diff = detail["golden_diff"]
    assert diff["result"] == "pass"
    report = diff["report"]
    assert report["counts"] == {
        "golden": 3,
        "curated": 3,
        "common": 3,
        "equal": 3,
        "union": 3,
        "golden_missing": 0,
        "curated_missing": 0,
        "field_mismatch": 0,
        "field_diffs": 0,
        "diff_total": 0,
    }
    assert all(c["ok"] for c in report["checks"])
    # 金标条数 / 精选区 / 批次签发 三处互证
    conn = _open()
    try:
        golden_rows = conn.execute(
            "SELECT COUNT(*) c FROM golden_rows WHERE dataset_id=3"
        ).fetchone()["c"]
        curated_batch = conn.execute(
            "SELECT COUNT(*) c FROM curated WHERE batch_id=?", (detail["id"],)
        ).fetchone()["c"]
        batch = conn.execute(
            "SELECT status, released_count FROM inspect_batches WHERE id=?",
            (detail["id"],),
        ).fetchone()
    finally:
        conn.close()
    assert golden_rows == 3
    assert curated_batch == 3
    assert (batch["status"], batch["released_count"]) == ("released", 3)


def test_golden_mismatch_fails_and_rolls_back(env):
    detail = env.run_inspect(4)
    assert detail["status"] == "golden_failed"
    assert detail["released_count"] == 0
    assert detail["curated_batch_count"] == 0
    assert detail["curated_total"] == 0

    diff = detail["golden_diff"]
    assert diff["result"] == "fail"
    report = diff["report"]
    counts = report["counts"]
    # 精选缺 1（SHP-4）+ 同键字段不一致 1（SHP-3.carrier）
    assert counts["curated_missing"] == 1
    assert counts["field_mismatch"] == 1
    assert counts["golden_missing"] == 0
    assert counts["diff_total"] == 2
    assert counts["union"] == counts["equal"] + counts["diff_total"]

    curated_missing = report["categories"]["curated_missing"]["items"]
    assert [i["entity_key"] for i in curated_missing] == ["SHP-4"]
    field_mismatch = report["categories"]["field_mismatch"]["items"]
    assert len(field_mismatch) == 1
    assert field_mismatch[0]["entity_key"] == "SHP-3"
    assert field_mismatch[0]["fields"][0]["field"] == "carrier"
    assert field_mismatch[0]["fields"][0]["golden_value"] == "SF"
    assert field_mismatch[0]["fields"][0]["curated_value"] == "YTO"

    assert report["checks"] and all(c["ok"] for c in report["checks"])
    assert diff["rolled_back_count"] == 3

    # 撤回后精选区确实不再有该批的行，批次不是成功发布态
    conn = _open()
    try:
        curated = conn.execute(
            "SELECT COUNT(*) c FROM curated WHERE batch_id=?", (detail["id"],)
        ).fetchone()["c"]
        batch = conn.execute(
            "SELECT status, released_count FROM inspect_batches WHERE id=?",
            (detail["id"],),
        ).fetchone()
        golden = conn.execute(
            "SELECT COUNT(*) c FROM golden_rows WHERE dataset_id=4"
        ).fetchone()["c"]
    finally:
        conn.close()
    assert curated == 0
    assert (batch["status"], batch["released_count"]) == ("golden_failed", 0)
    assert golden == 4


def test_golden_missing_category(env):
    import tempfile
    from pathlib import Path

    from app.services import golden_service

    legacy = [
        {"entity_key": "ORD-1", "sku": "SKU-A", "price": 12.5, "qty": 2, "title": "Alpha"},
        {"entity_key": "ORD-2", "sku": "SKU-B", "price": 9.0, "qty": 1, "title": "Beta"},
        {"entity_key": "ORD-6", "sku": "SKU-E", "price": 20, "qty": 4, "title": "Clean"},
    ]
    path = Path(tempfile.mkdtemp()) / "orders_golden_min.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in legacy), encoding="utf-8")
    golden_service.register_golden(1, str(path))

    detail = env.run_inspect(1)
    diff = detail["golden_diff"]
    assert detail["status"] == "golden_failed"
    assert diff["result"] == "fail"
    assert diff["report"]["counts"]["golden_missing"] == 1
    assert [i["entity_key"] for i in diff["report"]["categories"]["golden_missing"]["items"]] == ["ORD-5"]
    assert detail["curated_batch_count"] == 0


def test_manual_rerun_diff_can_recover(env):
    from app.services import golden_service

    detail = env.run_inspect(4)
    assert detail["status"] == "golden_failed"

    # 修正金标使其与本批一致，独立重跑差分 → 补签发、批次回到成功发布态
    golden_service.register_golden(4, "shipments_golden_fixed.jsonl")
    rerun = golden_service.run_golden_diff(detail["id"])
    assert rerun["result"] == "pass"
    assert rerun["batch_status"] == "released"
    assert rerun["batch_released_count"] == 3

    conn = _open()
    try:
        curated = conn.execute(
            "SELECT COUNT(*) c FROM curated WHERE batch_id=?", (detail["id"],)
        ).fetchone()["c"]
    finally:
        conn.close()
    assert curated == 3


def test_registration_persists_and_survives_reload(env):
    from app.config import FIXTURES_DIR
    from app.services import golden_service

    fixture = FIXTURES_DIR / "shipments_golden_fixed.jsonl"
    golden_service.register_golden(4, "shipments_golden_fixed.jsonl")
    overview = golden_service.get_golden(4)
    assert overview["row_count"] == 3
    assert overview["row_count_live"] == 3
    assert overview["fixture_path"] == "shipments_golden_fixed.jsonl"
    assert len(overview["rows"]) == 3
    _ = fixture  # fixture path resolves from FIXTURES_DIR

    # 重新打开数据库（模拟刷新）后，金标与差分报告仍在
    conn = _open()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM golden_rows WHERE dataset_id=4"
        ).fetchone()["c"] == 3
        assert conn.execute("SELECT COUNT(*) c FROM golden_sets WHERE dataset_id=4").fetchone()["c"] == 1
    finally:
        conn.close()

    detail = env.run_inspect(4)
    diff_id = detail["golden_diff"]["id"]
    conn = _open()
    try:
        persisted = conn.execute(
            "SELECT result, report_json FROM golden_diffs WHERE id=?", (diff_id,)
        ).fetchone()
    finally:
        conn.close()
    assert persisted["result"] == "pass"
    assert json.loads(persisted["report_json"])["counts"]["diff_total"] == 0


def test_failed_batch_does_not_erase_earlier_published_rows(env):
    """先由一致批次成功发布，再由故意不一致批次失败：只撤回后批写入，保留前批。"""
    from app.services import golden_service

    # 批次 A：金标与本批一致 → 成功发布 3 行
    golden_service.register_golden(4, "shipments_snapshot.jsonl")
    batch_a = env.run_inspect(4)
    assert batch_a["status"] == "released"
    assert batch_a["curated_batch_count"] == 3

    # 批次 B：同一夹具，金标改为故意不一致 → 失败；精选区按 (dataset,key) 覆盖了 A 的行
    golden_service.register_golden(4, "shipments_golden.jsonl")
    batch_b = env.run_inspect(4)
    assert batch_b["status"] == "golden_failed"
    assert batch_b["curated_batch_count"] == 0
    assert batch_b["golden_diff"]["rolled_back_count"] == 3  # 本批 3 行已签发，失败后撤回
    assert batch_b["golden_diff"]["report"]["restored"] == 3  # 其中 3 行还原为批次 A 的内容

    conn = _open()
    try:
        curated = conn.execute(
            "SELECT batch_id, entity_key FROM curated WHERE dataset_id=4 ORDER BY entity_key"
        ).fetchall()
    finally:
        conn.close()
    assert [(r["batch_id"], r["entity_key"]) for r in curated] == [
        (batch_a["id"], "SHP-1"),
        (batch_a["id"], "SHP-2"),
        (batch_a["id"], "SHP-3"),
    ]
