from datetime import datetime, timezone

from app.config import DATA_DIR, FIXTURES_DIR
from app.db import connect
from app.engines.parse_fixture import parse_jsonl


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS datasets(
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          fixture_path TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          note TEXT
        );
        CREATE TABLE IF NOT EXISTS rules(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          field TEXT NOT NULL,
          rule_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          params_json TEXT NOT NULL DEFAULT '{}',
          enabled INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        CREATE TABLE IF NOT EXISTS inspect_batches(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          scanned_count INTEGER NOT NULL DEFAULT 0,
          passed_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          quarantined_count INTEGER NOT NULL DEFAULT 0,
          released_count INTEGER NOT NULL DEFAULT 0,
          report_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        CREATE TABLE IF NOT EXISTS inspect_rows(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL,
          entity_key TEXT,
          payload_json TEXT NOT NULL,
          disposition TEXT NOT NULL,
          rule_hits_json TEXT NOT NULL,
          FOREIGN KEY(batch_id) REFERENCES inspect_batches(id)
        );
        CREATE TABLE IF NOT EXISTS quarantine(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          dataset_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL,
          entity_key TEXT,
          reason TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS curated(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          batch_id INTEGER NOT NULL,
          entity_key TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          released_at TEXT NOT NULL,
          UNIQUE(dataset_id, entity_key)
        );
        CREATE TABLE IF NOT EXISTS curated_prior(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          dataset_id INTEGER NOT NULL,
          entity_key TEXT NOT NULL,
          prior_batch_id INTEGER,
          prior_payload_json TEXT,
          prior_released_at TEXT,
          UNIQUE(batch_id, entity_key)
        );
        CREATE TABLE IF NOT EXISTS golden_sets(          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL UNIQUE,
          fixture_path TEXT NOT NULL,
          row_count INTEGER NOT NULL DEFAULT 0,
          registered_at TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        CREATE TABLE IF NOT EXISTS golden_rows(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          entity_key TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          UNIQUE(dataset_id, entity_key),
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        CREATE TABLE IF NOT EXISTS golden_diffs(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          dataset_id INTEGER NOT NULL,
          result TEXT NOT NULL,
          golden_count INTEGER NOT NULL DEFAULT 0,
          curated_count INTEGER NOT NULL DEFAULT 0,
          golden_missing_count INTEGER NOT NULL DEFAULT 0,
          curated_missing_count INTEGER NOT NULL DEFAULT 0,
          field_mismatch_count INTEGER NOT NULL DEFAULT 0,
          rolled_back_count INTEGER NOT NULL DEFAULT 0,
          report_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(batch_id) REFERENCES inspect_batches(id)
        );
        """
    )
    n = conn.execute("SELECT COUNT(*) AS c FROM datasets").fetchone()["c"]
    if n == 0:
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            ("Orders Snapshot", "orders_snapshot.jsonl", "order", 1, "含缺失与类型错误行，演示入检"),
        )
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            ("Inventory Snapshot", "inventory_snapshot.jsonl", "sku", 1, "库存快照第二路"),
        )
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            (
                "Refunds Snapshot",
                "refunds_snapshot.jsonl",
                "refund",
                1,
                "与金标一致：演示金标差分通过后成功发布",
            ),
        )
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            (
                "Shipments Snapshot",
                "shipments_snapshot.jsonl",
                "shipment",
                1,
                "与金标故意不一致：演示差分失败并撤回精选区",
            ),
        )
        conn.executemany(
            "INSERT INTO rules(dataset_id, name, field, rule_type, severity, params_json, enabled) VALUES (?,?,?,?,?,?,?)",
            [
                (1, "sku 必填", "sku", "required", "error", "{}", 1),
                (1, "price 须为数字", "price", "type_number", "error", "{}", 1),
                (1, "qty 须为整数", "qty", "type_int", "warn", "{}", 1),
                (2, "sku 必填", "sku", "required", "error", "{}", 1),
                (2, "on_hand 须为整数", "on_hand", "type_int", "error", "{}", 1),
            ],
        )
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?)",
            ("release_mode", "pass_only"),
        )
        # 两个演示数据集的规则：全部能通过，让金标差分成为唯一发布闸门。
        conn.executemany(
            "INSERT INTO rules(dataset_id, name, field, rule_type, severity, params_json, enabled) VALUES (?,?,?,?,?,?,?)",
            [
                (3, "refund_id 必填", "refund_id", "required", "error", "{}", 1),
                (3, "amount 须为数字", "amount", "type_number", "error", "{}", 1),
                (4, "shipment_id 必填", "shipment_id", "required", "error", "{}", 1),
                (4, "amount 须为数字", "amount", "type_number", "error", "{}", 1),
            ],
        )
    _seed_golden_demo(conn)
    conn.commit()
    conn.close()


def _seed_golden_demo(conn) -> None:
    """幂等补齐演示用金标登记；仅 seed 的四个内置数据集参与。"""
    from app.repositories import golden as golden_repo

    demo = [
        (3, "refunds_golden.jsonl", "refunds_snapshot.jsonl"),
        (4, "shipments_golden.jsonl", "shipments_snapshot.jsonl"),
    ]
    for dataset_id, golden_path, fixture_path in demo:
        ds = conn.execute(
            "SELECT id FROM datasets WHERE id=? AND fixture_path=?",
            (dataset_id, fixture_path),
        ).fetchone()
        if not ds:
            continue
        if golden_repo.get_set(conn, dataset_id):
            continue
        path = FIXTURES_DIR / golden_path
        if not path.exists():
            continue
        rows = [
            {"entity_key": str(r.get("entity_key")), "payload": r}
            for r in parse_jsonl(path)
            if str(r.get("entity_key") or "").strip()
        ]
        if not rows:
            continue
        golden_repo.replace_set(
            conn,
            dataset_id=dataset_id,
            fixture_path=golden_path,
            rows=rows,
            registered_at=_now(),
        )

