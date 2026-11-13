from __future__ import annotations

import json
import sqlite3
from typing import Any


def upsert(
    conn: sqlite3.Connection,
    *,
    dataset_id: int,
    batch_id: int,
    entity_key: str,
    payload: dict[str, Any],
    released_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO curated(dataset_id, batch_id, entity_key, payload_json, released_at)
        VALUES (?,?,?,?,?)
        ON CONFLICT(dataset_id, entity_key) DO UPDATE SET
          batch_id=excluded.batch_id,
          payload_json=excluded.payload_json,
          released_at=excluded.released_at
        """,
        (dataset_id, batch_id, entity_key, json.dumps(payload, ensure_ascii=False), released_at),
    )


def list_all(conn: sqlite3.Connection, dataset_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
    if dataset_id is None:
        rows = conn.execute(
            "SELECT id, dataset_id, batch_id, entity_key, payload_json, released_at FROM curated ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, dataset_id, batch_id, entity_key, payload_json, released_at FROM curated WHERE dataset_id=? ORDER BY id DESC LIMIT ?",
            (dataset_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json"))
        out.append(d)
    return out


def count_all(conn: sqlite3.Connection, dataset_id: int | None = None) -> int:
    if dataset_id is None:
        return int(conn.execute("SELECT COUNT(*) AS c FROM curated").fetchone()["c"])
    return int(
        conn.execute("SELECT COUNT(*) AS c FROM curated WHERE dataset_id=?", (dataset_id,)).fetchone()["c"]
    )


def count_by_batch(conn: sqlite3.Connection, batch_id: int) -> int:
    return int(
        conn.execute("SELECT COUNT(*) AS c FROM curated WHERE batch_id=?", (batch_id,)).fetchone()["c"]
    )


def snapshot_prior(conn: sqlite3.Connection, batch_id: int, dataset_id: int, keys: list[str]) -> None:
    """在发布该批各行之前，记录这些键在本批之前已有的精选区内容。

    精选区按 (dataset_id, entity_key) 覆盖，因此失败撤回时不能盲删：若该键
    在本批之前已由更早批次签发，应还原旧值，而不是把旧行一并抹掉。
    同一批次的快照只记一次（重跑差分不覆盖最初的快照）。
    """
    for key in keys:
        exists = conn.execute(
            "SELECT 1 FROM curated_prior WHERE batch_id=? AND entity_key=?",
            (batch_id, key),
        ).fetchone()
        if exists:
            continue
        prior = conn.execute(
            "SELECT batch_id, payload_json, released_at FROM curated WHERE dataset_id=? AND entity_key=?",
            (dataset_id, key),
        ).fetchone()
        prior_batch_id = None
        if prior is not None and int(prior["batch_id"]) != batch_id:
            prior_batch_id = int(prior["batch_id"])
        conn.execute(
            """
            INSERT OR IGNORE INTO curated_prior(
              batch_id, dataset_id, entity_key, prior_batch_id, prior_payload_json, prior_released_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                batch_id,
                dataset_id,
                key,
                prior_batch_id,
                None if prior is None else prior["payload_json"],
                None if prior is None else prior["released_at"],
            ),
        )


def rollback_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, int]:
    """撤回本批签发：删除本批写入的行，并按快照还原此前被覆盖的旧行。

    - ``removed``：本批写入精选区、随后被撤回的行数。
    - ``restored``：因本批覆盖而还原为更早批次内容的行数。
    """
    rows = conn.execute(
        "SELECT dataset_id, entity_key FROM curated WHERE batch_id=?", (batch_id,)
    ).fetchall()
    priors = conn.execute(
        """
        SELECT dataset_id, entity_key, prior_batch_id, prior_payload_json, prior_released_at
        FROM curated_prior WHERE batch_id=?
        """,
        (batch_id,),
    ).fetchall()
    prior_map = {(p["dataset_id"], p["entity_key"]): p for p in priors}

    restored = 0
    for row in rows:
        ident = (row["dataset_id"], row["entity_key"])
        prior = prior_map.get(ident)
        conn.execute(
            "DELETE FROM curated WHERE dataset_id=? AND entity_key=?",
            (row["dataset_id"], row["entity_key"]),
        )
        if prior is not None and prior["prior_batch_id"] is not None:
            conn.execute(
                """
                INSERT INTO curated(dataset_id, batch_id, entity_key, payload_json, released_at)
                VALUES (?,?,?,?,?)
                """,
                (
                    prior["dataset_id"],
                    int(prior["prior_batch_id"]),
                    prior["entity_key"],
                    prior["prior_payload_json"],
                    prior["prior_released_at"],
                ),
            )
            restored += 1
    return {"removed": len(rows), "restored": restored}
