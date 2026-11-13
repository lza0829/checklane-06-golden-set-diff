"""金标差分模块：按实体键比对「本批签发内容」与「数据集金标」。

三类差异（互相排斥，键级别）：

- ``golden_missing``  金标缺：本批精选有该键，金标夹具中没有。
- ``curated_missing`` 精选缺：金标有该键，本批精选没有（例如被隔离/未放行）。
- ``field_mismatch``  同键字段不一致：两处都有该键，但存在字段值（或字段有无）不同。

``entity_key`` 是唯一比对键，不参与字段比对。
"""

from __future__ import annotations

from typing import Any, Iterable

KEY_FIELD = "entity_key"
CATEGORIES = ("golden_missing", "curated_missing", "field_mismatch")


def _same_value(left: Any, right: Any) -> bool:
    """严格到「类型不同即不同」的轻量比较。

    JSON 中 1 与 1.0 视为同一数值；True/False 不与 1/0 混同（否则
    bool 会因 Python 相等规则被误判为一致）。
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    return left == right


def _payload_map(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get(KEY_FIELD) or "").strip()
        if not key:
            continue
        payload = row.get("payload")
        out[key] = payload if isinstance(payload, dict) else {}
    return out


def _field_diffs(golden: dict[str, Any], curated: dict[str, Any]) -> list[dict[str, Any]]:
    fields = sorted((set(golden) | set(curated)) - {KEY_FIELD})
    diffs: list[dict[str, Any]] = []
    for field in fields:
        g_present = field in golden
        c_present = field in curated
        g_value = golden.get(field)
        c_value = curated.get(field)
        if g_present and c_present and _same_value(g_value, c_value):
            continue
        diffs.append(
            {
                "field": field,
                "golden_value": g_value,
                "curated_value": c_value,
                "golden_present": g_present,
                "curated_present": c_present,
            }
        )
    return diffs


def build_diff(
    golden_rows: Iterable[dict[str, Any]],
    curated_rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """按实体键比对，返回分类明细与结果。

    ``golden_rows`` / ``curated_rows`` 每项需含 ``entity_key`` 与 ``payload``。
    重复键按「后者覆盖」处理（与精选区 upsert 语义一致）。
    """
    golden = _payload_map(golden_rows)
    curated = _payload_map(curated_rows)

    golden_keys = set(golden)
    curated_keys = set(curated)
    common = golden_keys & curated_keys

    golden_missing: list[dict[str, Any]] = []
    for key in sorted(curated_keys - golden_keys):
        payload = curated[key]
        golden_missing.append(
            {
                "entity_key": key,
                "fields": sorted(set(payload) - {KEY_FIELD}),
                "curated_payload": payload,
            }
        )

    curated_missing: list[dict[str, Any]] = []
    for key in sorted(golden_keys - curated_keys):
        payload = golden[key]
        curated_missing.append(
            {
                "entity_key": key,
                "fields": sorted(set(payload) - {KEY_FIELD}),
                "golden_payload": payload,
            }
        )

    field_mismatch: list[dict[str, Any]] = []
    field_diff_total = 0
    equal_keys = 0
    for key in sorted(common):
        diffs = _field_diffs(golden[key], curated[key])
        if diffs:
            field_diff_total += len(diffs)
            field_mismatch.append(
                {
                    "entity_key": key,
                    "fields": diffs,
                    "golden_payload": golden[key],
                    "curated_payload": curated[key],
                }
            )
        else:
            equal_keys += 1

    counts = {
        "golden": len(golden_keys),
        "curated": len(curated_keys),
        "common": len(common),
        "equal": equal_keys,
        "union": len(golden_keys | curated_keys),
        "golden_missing": len(golden_missing),
        "curated_missing": len(curated_missing),
        "field_mismatch": len(field_mismatch),
        "field_diffs": field_diff_total,
    }
    counts["diff_total"] = (
        counts["golden_missing"] + counts["curated_missing"] + counts["field_mismatch"]
    )
    return {
        "result": "pass" if counts["diff_total"] == 0 else "fail",
        "counts": counts,
        "categories": {
            "golden_missing": {"count": len(golden_missing), "items": golden_missing},
            "curated_missing": {"count": len(curated_missing), "items": curated_missing},
            "field_mismatch": {"count": len(field_mismatch), "items": field_mismatch},
        },
    }


def category_partition_check(diff: dict[str, Any]) -> dict[str, Any]:
    """键集合互证：并集键数 = 一致键 + 三类差异键数。"""
    c = diff["counts"]
    accounted = c["equal"] + c["diff_total"]
    return {
        "name": "category_partition",
        "ok": accounted == c["union"],
        "detail": (
            f"并集键({c['union']}) = 一致({c['equal']}) + "
            f"金标缺({c['golden_missing']}) + 精选缺({c['curated_missing']}) + "
            f"字段不一致({c['field_mismatch']})"
        ),
    }


def details_match_check(diff: dict[str, Any]) -> dict[str, Any]:
    """各类 count 与明细条数互证。"""
    parts = []
    ok = True
    for name in CATEGORIES:
        cat = diff["categories"][name]
        match = cat["count"] == len(cat["items"])
        ok = ok and match
        parts.append(f"{name}: count={cat['count']} / items={len(cat['items'])}")
    return {
        "name": "details_match_counts",
        "ok": ok,
        "detail": "；".join(parts),
    }
