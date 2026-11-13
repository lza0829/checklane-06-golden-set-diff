"""金标差分服务：登记金标夹具、执行差分闸门、失败撤回精选区。

发布语义：

- 数据集登记了启用中的金标 → 规则入检签发后自动跑差分；只有 ``result=pass``
  才能让批次停在 ``released``（成功发布态）。
- 差分失败 → 批次置为 ``golden_failed``，``released_count`` 归零，且该批已写入
  精选区的行全部撤回（``rolled_back_count`` 记录撤回条数）；若某键在更早批次已
  签发过，撤回时还原为旧值（``restored`` 记录还原条数），不误删历史发布。
- 未登记金标的旧数据集保持原有入检链路（报告标注 ``golden.registered=false``）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.parse_fixture import parse_jsonl
from app.modules.golden_set_diff import (
    KEY_FIELD,
    build_diff,
    category_partition_check,
    details_match_check,
)
from app.repositories import batches as batch_repo
from app.repositories import curated as curated_repo
from app.repositories import datasets as dataset_repo
from app.repositories import golden as golden_repo


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolve_fixture_path(raw: str) -> Path:
    path = Path(raw.strip())
    if not raw.strip():
        raise ValueError("fixture_path 不能为空")
    if not path.is_absolute():
        path = FIXTURES_DIR / path
    if not path.exists():
        raise ValueError(f"金标夹具不存在：{path}")
    return path


def _validated_golden_rows(path: Path) -> list[dict[str, Any]]:
    rows = parse_jsonl(path)
    if not rows:
        raise ValueError("金标夹具为空，至少需要一行")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for idx, payload in enumerate(rows, start=1):
        key = str(payload.get(KEY_FIELD) or "").strip()
        if not key:
            raise ValueError(f"金标第 {idx} 行缺少 {KEY_FIELD}")
        if key in seen:
            raise ValueError(f"金标实体键重复：{key}")
        seen.add(key)
        out.append({KEY_FIELD: key, "payload": payload})
    return out


def register_golden(dataset_id: int, fixture_path: str) -> dict[str, Any]:
    """登记/覆盖数据集金标夹具，落库 golden_sets + golden_rows。"""
    conn = connect()
    try:
        ds = dataset_repo.get_dataset(conn, dataset_id)
        if not ds:
            raise ValueError(f"dataset {dataset_id} not found")
        path = resolve_fixture_path(fixture_path)
        rows = _validated_golden_rows(path)
        golden_id = golden_repo.replace_set(
            conn,
            dataset_id=dataset_id,
            fixture_path=fixture_path.strip(),
            rows=rows,
            registered_at=_now(),
        )
        conn.commit()
        overview = golden_overview(conn, dataset_id)
        assert overview is not None
        overview["id"] = golden_id
        overview["preview"] = rows[:20]
        return overview
    finally:
        conn.close()


def golden_overview(conn, dataset_id: int) -> dict[str, Any] | None:
    gset = golden_repo.get_set(conn, dataset_id)
    if not gset:
        return None
    rows = golden_repo.list_rows(conn, dataset_id)
    return {
        "dataset_id": gset["dataset_id"],
        "fixture_path": gset["fixture_path"],
        "row_count": gset["row_count"],
        "registered_at": gset["registered_at"],
        "enabled": gset["enabled"],
        "rows": rows,
        "row_count_live": len(rows),
    }


def get_golden(dataset_id: int) -> dict[str, Any] | None:
    conn = connect()
    try:
        return golden_overview(conn, dataset_id)
    finally:
        conn.close()


def list_golden() -> list[dict[str, Any]]:
    conn = connect()
    try:
        out = []
        for gset in golden_repo.list_sets(conn):
            d = dict(gset)
            d["row_count_live"] = golden_repo.count_rows(conn, d["dataset_id"])
            out.append(d)
        return out
    finally:
        conn.close()


def list_diffs(dataset_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return golden_repo.list_diffs(conn, dataset_id)
    finally:
        conn.close()


def latest_diff(batch_id: int) -> dict[str, Any] | None:
    conn = connect()
    try:
        return golden_repo.latest_for_batch(conn, batch_id)
    finally:
        conn.close()


def _candidate_rows(conn, batch_id: int) -> list[dict[str, Any]]:
    """本批「规则放行」候选行；键缺失时按行号补 ``row-N``，与签发写库一致。"""
    out: list[dict[str, Any]] = []
    for row in batch_repo.list_batch_rows(conn, batch_id):
        if row["disposition"] != "pass":
            continue
        key = row["entity_key"] or f"row-{row['row_no']}"
        out.append({KEY_FIELD: key, "payload": row["payload"], "row_no": row["row_no"]})
    return out


def _dedupe(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """与精选区 upsert 一致：同键后者覆盖，返回 key→payload。"""
    out: dict[str, dict[str, Any]] = {}
    for item in candidates:
        out[str(item[KEY_FIELD])] = item["payload"]
    return out


def apply_gate(
    conn,
    *,
    batch_id: int,
    dataset_id: int,
    candidates: list[dict[str, Any]],
    report: dict[str, Any],
    now: str,
    gset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """在一个事务内执行差分闸门并落差分报告；成功补签发 / 失败撤回。

    返回 ``{"status", "released", "golden", "diff"}``，由调用方写回批次。
    """
    gset = gset or golden_repo.get_set(conn, dataset_id)
    assert gset is not None
    golden_rows = golden_repo.list_rows(conn, dataset_id)
    curated_keys = _dedupe(candidates)
    curated_rows = [
        {KEY_FIELD: key, "payload": payload} for key, payload in curated_keys.items()
    ]

    diff = build_diff(golden_rows, curated_rows)
    counts = diff["counts"]
    checks: list[dict[str, Any]] = [category_partition_check(diff), details_match_check(diff)]

    gs_count = golden_repo.count_rows(conn, dataset_id)
    checks.append(
        {
            "name": "golden_row_count_matches",
            "ok": gs_count == gset["row_count"] == counts["golden"],
            "detail": (
                f"金标表({gs_count}) = 登记({gset['row_count']}) = 差分金标键({counts['golden']})"
            ),
        }
    )

    fail = diff["result"] == "fail"
    release_ready = len(curated_keys)
    rolled_back = 0
    restored = 0
    if fail:
        # 撤回本批写入精选区的行；若某键在此前批次已签发过，还原旧值而非误删。
        outcome = curated_repo.rollback_batch(conn, batch_id)
        rolled_back = outcome["removed"]
        restored = outcome["restored"]
        released = 0
        status = "golden_failed"
    else:
        # 先为将被覆盖的键留快照（已存在则保留首次快照），再幂等补签发。
        curated_repo.snapshot_prior(conn, batch_id, dataset_id, list(curated_keys))
        for key, payload in curated_keys.items():
            curated_repo.upsert(
                conn,
                dataset_id=dataset_id,
                batch_id=batch_id,
                entity_key=key,
                payload=payload,
                released_at=now,
            )
        released = release_ready
        status = "released" if released else "inspected"

    curated_live = curated_repo.count_by_batch(conn, batch_id)
    checks.append(
        {
            "name": "curated_matches_release",
            "ok": curated_live == released == (0 if fail else release_ready),
            "detail": (
                f"精选区本批({curated_live}) = 批次签发({released})；"
                f"规则放行候选({release_ready})，撤回({rolled_back})"
            ),
        }
    )
    expected_status = "golden_failed" if fail else ("released" if released else "inspected")
    checks.append(
        {
            "name": "batch_state_matches_result",
            "ok": status == expected_status and (released == 0 if fail else True),
            "detail": (
                f"差分={diff['result']} → 批次状态应为 {expected_status}，实际 {status}；"
                f"签发 {released}，撤回 {rolled_back}"
            ),
        }
    )

    report["counts"]["release_ready"] = release_ready
    report["counts"]["released"] = released
    report["counts"]["rolled_back"] = rolled_back
    report["counts"]["restored"] = restored

    diff_id = golden_repo.insert_diff(
        conn,
        batch_id=batch_id,
        dataset_id=dataset_id,
        result=diff["result"],
        counts=counts,
        rolled_back=rolled_back,
        report={
            "dataset_id": dataset_id,
            "batch_id": batch_id,
            "fixture_path": gset["fixture_path"],
            "golden_registered_at": gset["registered_at"],
            "result": diff["result"],
            "counts": counts,
            "categories": diff["categories"],
            "checks": checks,
            "rolled_back": rolled_back,
            "restored": restored,
        },
        created_at=now,
    )

    golden_report = {
        "registered": True,
        "fixture_path": gset["fixture_path"],
        "registered_at": gset["registered_at"],
        "golden_row_count": gs_count,
        "result": diff["result"],
        "diff_id": diff_id,
        "counts": counts,
        "rolled_back": rolled_back,
        "restored": restored,
        "checks": checks,
    }
    report["golden"] = golden_report

    diff_record = {
        "id": diff_id,
        "batch_id": batch_id,
        "dataset_id": dataset_id,
        "result": diff["result"],
        "fixture_path": gset["fixture_path"],
        "counts": counts,
        "categories": diff["categories"],
        "checks": checks,
        "rolled_back": rolled_back,
        "restored": restored,
        "created_at": now,
    }
    return {"status": status, "released": released, "golden": golden_report, "diff": diff_record}


def run_golden_diff(batch_id: int) -> dict[str, Any]:
    """独立触发：对已存在批次重跑差分并执行同一发布闸门。"""
    conn = connect()
    try:
        batch = batch_repo.get_batch(conn, batch_id)
        if not batch:
            raise ValueError(f"batch {batch_id} not found")
        gset = golden_repo.get_set(conn, batch["dataset_id"])
        if not gset:
            raise ValueError(f"dataset {batch['dataset_id']} 未登记金标夹具")
        if not gset["enabled"]:
            raise ValueError(f"dataset {batch['dataset_id']} 金标已停用")

        now = _now()
        report = batch["report"]
        result = apply_gate(
            conn,
            batch_id=batch_id,
            dataset_id=batch["dataset_id"],
            candidates=_candidate_rows(conn, batch_id),
            report=report,
            now=now,
            gset=gset,
        )
        batch_repo.set_outcome(
            conn,
            batch_id,
            status=result["status"],
            released=result["released"],
            report=report,
        )
        conn.commit()
        return {
            **result["diff"],
            "batch_status": result["status"],
            "batch_released_count": result["released"],
            "batch_report": report,
            "restored": result["diff"]["restored"],
            "category_counts": {
                "golden_missing": result["diff"]["counts"]["golden_missing"],
                "curated_missing": result["diff"]["counts"]["curated_missing"],
                "field_mismatch": result["diff"]["counts"]["field_mismatch"],
            },
        }
    finally:
        conn.close()
