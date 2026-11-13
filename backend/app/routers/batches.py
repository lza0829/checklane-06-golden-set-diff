from fastapi import APIRouter, HTTPException

from app.db import connect
from app.repositories import batches as batch_repo
from app.repositories import curated as curated_repo
from app.repositories import golden as golden_repo

router = APIRouter(tags=["batches"])


@router.get("/batches")
def list_batches():
    conn = connect()
    try:
        return batch_repo.list_batches(conn)
    finally:
        conn.close()


@router.get("/batches/{batch_id}")
def get_batch(batch_id: int):
    conn = connect()
    try:
        detail = batch_repo.get_batch(conn, batch_id)
        if not detail:
            raise HTTPException(404, "batch not found")
        detail["rows"] = batch_repo.list_batch_rows(conn, batch_id)
        detail["curated_batch_count"] = curated_repo.count_by_batch(conn, batch_id)
        detail["golden_diff"] = golden_repo.latest_for_batch(conn, batch_id)
        return detail
    finally:
        conn.close()
