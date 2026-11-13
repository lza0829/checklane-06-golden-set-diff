from __future__ import annotations

import json
import sqlite3
from typing import Any


def get_set(conn: sqlite3.Connection, dataset_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, dataset_id, fixture_path, row_count, registered_at, enabled
        FROM golden_sets WHERE dataset_id=?
        """,
        (dataset_id,),
    ).fetchone()
    return dict(row) if row else None


def list_sets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT g.id, g.dataset_id, d.name AS dataset_name, g.fixture_path,
               g.row_count, g.registered_at, g.enabled
        FROM golden_sets g
        LEFT JOIN datasets d ON d.id = g.dataset_id
        ORDER BY g.dataset_id
        """
    ).fetchall()
    return [dict(r) for r in rows]


def replace_set(
    conn: sqlite3.Connection,
    *,
    dataset_id: int,
    fixture_path: str,
    rows: list[dict[str, Any]],
    registered_at: str,
) -> int:
    """登记/覆盖数据集金标：先清旧行，再写新行；返回金标行数。"""
    conn.execute("DELETE FROM golden_rows WHERE dataset_id=?", (dataset_id,))
    for row in rows:
        conn.execute(
            "INSERT INTO golden_rows(dataset_id, entity_key, payload_json) VALUES (?,?,?)",
            (dataset_id, row["entity_key"], json.dumps(row["payload"], ensure_ascii=False)),
        )
    existing = conn.execute(
        "SELECT id FROM golden_sets WHERE dataset_id=?", (dataset_id,)
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE golden_sets
            SET fixture_path=?, row_count=?, registered_at=?, enabled=1
            WHERE dataset_id=?
            """,
            (fixture_path, len(rows), registered_at, dataset_id),
        )
        golden_id = int(existing["id"])
    else:
        cur = conn.execute(
            """
            INSERT INTO golden_sets(dataset_id, fixture_path, row_count, registered_at, enabled)
            VALUES (?,?,?,?,1)
            """,
            (dataset_id, fixture_path, len(rows), registered_at),
        )
        golden_id = int(cur.lastrowid)
    return golden_id


def delete_set(conn: sqlite3.Connection, dataset_id: int) -> None:
    conn.execute("DELETE FROM golden_rows WHERE dataset_id=?", (dataset_id,))
    conn.execute("DELETE FROM golden_sets WHERE dataset_id=?", (dataset_id,))


def list_rows(conn: sqlite3.Connection, dataset_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT entity_key, payload_json FROM golden_rows
        WHERE dataset_id=? ORDER BY id
        """,
        (dataset_id,),
    ).fetchall()
    return [
        {"entity_key": r["entity_key"], "payload": json.loads(r["payload_json"])}
        for r in rows
    ]


def count_rows(conn: sqlite3.Connection, dataset_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM golden_rows WHERE dataset_id=?", (dataset_id,)
        ).fetchone()["c"]
    )


def insert_diff(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    dataset_id: int,
    result: str,
    counts: dict[str, Any],
    rolled_back: int,
    report: dict[str, Any],
    created_at: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO golden_diffs(
          batch_id, dataset_id, result, golden_count, curated_count,
          golden_missing_count, curated_missing_count, field_mismatch_count,
          rolled_back_count, report_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            batch_id,
            dataset_id,
            result,
            counts.get("golden", 0),
            counts.get("curated", 0),
            counts.get("golden_missing", 0),
            counts.get("curated_missing", 0),
            counts.get("field_mismatch", 0),
            rolled_back,
            json.dumps(report, ensure_ascii=False),
            created_at,
        ),
    )
    return int(cur.lastrowid)


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["report"] = json.loads(d.pop("report_json"))
    return d


def get_diff(conn: sqlite3.Connection, diff_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, batch_id, dataset_id, result, golden_count, curated_count,
               golden_missing_count, curated_missing_count, field_mismatch_count,
               rolled_back_count, report_json, created_at
        FROM golden_diffs WHERE id=?
        """,
        (diff_id,),
    ).fetchone()
    return _decode(row) if row else None


def latest_for_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, batch_id, dataset_id, result, golden_count, curated_count,
               golden_missing_count, curated_missing_count, field_mismatch_count,
               rolled_back_count, report_json, created_at
        FROM golden_diffs WHERE batch_id=? ORDER BY id DESC LIMIT 1
        """,
        (batch_id,),
    ).fetchone()
    return _decode(row) if row else None


def list_diffs(
    conn: sqlite3.Connection, dataset_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    if dataset_id is None:
        rows = conn.execute(
            """
            SELECT id, batch_id, dataset_id, result, golden_count, curated_count,
                   golden_missing_count, curated_missing_count, field_mismatch_count,
                   rolled_back_count, report_json, created_at
            FROM golden_diffs ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, batch_id, dataset_id, result, golden_count, curated_count,
                   golden_missing_count, curated_missing_count, field_mismatch_count,
                   rolled_back_count, report_json, created_at
            FROM golden_diffs WHERE dataset_id=? ORDER BY id DESC LIMIT ?
            """,
            (dataset_id, limit),
        ).fetchall()
    return [_decode(r) for r in rows]
