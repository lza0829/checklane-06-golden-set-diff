from __future__ import annotations

import json
from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.parse_fixture import parse_jsonl
from app.engines.rule_engine import decide_disposition, evaluate_row
from app.repositories import batches as batch_repo
from app.repositories import curated as curated_repo
from app.repositories import datasets as dataset_repo
from app.repositories import golden as golden_repo
from app.repositories import quarantine as quarantine_repo
from app.repositories import rules as rules_repo
from app.seed import _now
from app.services import golden_service


def run_inspect(dataset_id: int) -> dict[str, Any]:
    conn = connect()
    try:
        ds = dataset_repo.get_dataset(conn, dataset_id)
        if not ds:
            raise ValueError(f"dataset {dataset_id} not found")
        if not ds["enabled"]:
            raise ValueError(f"dataset {dataset_id} disabled")

        path = FIXTURES_DIR / ds["fixture_path"]
        rows = parse_jsonl(path)
        rules = [r for r in rules_repo.list_rules(conn, dataset_id) if r.get("enabled")]
        gset = golden_repo.get_set(conn, dataset_id)

        scanned = len(rows)
        passed = failed = quarantined = released = 0
        row_results: list[dict[str, Any]] = []
        now = _now()

        for idx, payload in enumerate(rows, start=1):
            hits = evaluate_row(payload, rules)
            disposition = decide_disposition(hits)
            entity_key = str(payload.get("entity_key") or "") or None
            if disposition == "quarantine":
                quarantined += 1
                failed += 1
            else:
                passed += 1
            row_results.append(
                {
                    "row_no": idx,
                    "entity_key": entity_key,
                    "payload": payload,
                    "disposition": disposition,
                    "rule_hits": hits,
                }
            )

        checks = [
            {
                "name": "scan_breakdown",
                "ok": scanned == passed + quarantined,
                "detail": f"scanned({scanned}) = passed({passed}) + quarantined({quarantined})",
            }
        ]

        # insert batch first to get id, then rows / quarantine / curated
        report: dict[str, Any] = {
            "dataset_id": dataset_id,
            "dataset_name": ds["name"],
            "fixture_path": ds["fixture_path"],
            "rule_count": len(rules),
            "counts": {
                "scanned": scanned,
                "passed": passed,
                "failed": failed,
                "quarantined": quarantined,
                "released": 0,
            },
            "checks": checks,
            "golden": {"registered": bool(gset)},
        }
        batch_id = batch_repo.insert_batch(
            conn,
            dataset_id=dataset_id,
            status="inspected",
            scanned=scanned,
            passed=passed,
            failed=failed,
            quarantined=quarantined,
            released=0,
            report=report,
            created_at=now,
        )

        pass_candidates: list[dict[str, Any]] = []
        pass_keys = [
            (item["entity_key"] or f"row-{item['row_no']}")
            for item in row_results
            if item["disposition"] != "quarantine"
        ]
        if gset and gset["enabled"]:
            # 有金标：先记录将被覆盖的旧精选行，失败撤回时可精确还原。
            curated_repo.snapshot_prior(conn, batch_id, dataset_id, pass_keys)

        for item in row_results:
            batch_repo.insert_row(
                conn,
                batch_id=batch_id,
                row_no=item["row_no"],
                entity_key=item["entity_key"],
                payload=item["payload"],
                disposition=item["disposition"],
                rule_hits=item["rule_hits"],
            )
            if item["disposition"] == "quarantine":
                reasons = "; ".join(
                    f"{h['name']}:{h['detail']}" for h in item["rule_hits"] if h.get("severity") == "error"
                )
                quarantine_repo.insert(
                    conn,
                    batch_id=batch_id,
                    dataset_id=dataset_id,
                    row_no=item["row_no"],
                    entity_key=item["entity_key"],
                    reason=reasons or "rule hit",
                    payload=item["payload"],
                    created_at=now,
                )
            else:
                key = item["entity_key"] or f"row-{item['row_no']}"
                pass_candidates.append({"entity_key": key, "payload": item["payload"]})
                # 规则放行 → 写入精选区（签发动作）；金标差分失败时再整批撤回。
                curated_repo.upsert(
                    conn,
                    dataset_id=dataset_id,
                    batch_id=batch_id,
                    entity_key=key,
                    payload=item["payload"],
                    released_at=now,
                )
                released += 1

        report["counts"]["released"] = released
        report["checks"].append(
            {
                "name": "release_matches_passed",
                "ok": released == passed,
                "detail": f"released({released}) = passed({passed})",
            }
        )
        report["checks"].append(
            {
                "name": "quarantine_table_matches",
                "ok": quarantine_repo.count_all(conn) >= quarantined,
                "detail": "quarantine rows persisted for this and prior batches",
            }
        )

        status = "released" if released else "inspected"
        if gset and gset["enabled"]:
            gate = golden_service.apply_gate(
                conn,
                batch_id=batch_id,
                dataset_id=dataset_id,
                candidates=pass_candidates,
                report=report,
                now=now,
                gset=gset,
            )
            status = gate["status"]
            released = gate["released"]
            checks.append(
                {
                    "name": "golden_gate_required",
                    "ok": True,
                    "detail": (
                        f"已登记金标 {gset['fixture_path']} → 差分 {gate['diff']['result']}；"
                        f"发布状态 {status}"
                    ),
                }
            )
        else:
            if gset and not gset["enabled"]:
                report["golden"] = {"registered": True, "enabled": False}
                checks.append(
                    {
                        "name": "golden_gate_skipped",
                        "ok": True,
                        "detail": "金标已停用，按规则入检签发",
                    }
                )

        batch_repo.set_outcome(
            conn, batch_id, status=status, released=released, report=report
        )
        conn.commit()

        detail = batch_repo.get_batch(conn, batch_id)
        assert detail is not None
        detail["rows"] = batch_repo.list_batch_rows(conn, batch_id)
        detail["curated_total"] = curated_repo.count_all(conn, dataset_id)
        detail["curated_batch_count"] = curated_repo.count_by_batch(conn, batch_id)
        detail["quarantine_total"] = quarantine_repo.count_all(conn)
        detail["golden_diff"] = golden_repo.latest_for_batch(conn, batch_id)
        return detail
    finally:
        conn.close()


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        datasets = conn.execute("SELECT COUNT(*) AS c FROM datasets").fetchone()["c"]
        rules = conn.execute("SELECT COUNT(*) AS c FROM rules WHERE enabled=1").fetchone()["c"]
        batches = conn.execute("SELECT COUNT(*) AS c FROM inspect_batches").fetchone()["c"]
        golden_sets = conn.execute("SELECT COUNT(*) AS c FROM golden_sets").fetchone()["c"]
        failed_batches = conn.execute(
            "SELECT COUNT(*) AS c FROM inspect_batches WHERE status='golden_failed'"
        ).fetchone()["c"]
        return {
            "dataset_count": datasets,
            "enabled_rule_count": rules,
            "batch_count": batches,
            "curated_count": curated_repo.count_all(conn),
            "quarantine_count": quarantine_repo.count_all(conn),
            "golden_set_count": golden_sets,
            "golden_failed_batch_count": failed_batches,
        }
    finally:
        conn.close()
