from fastapi import APIRouter, HTTPException

from app.schemas.entities import GoldenRegisterRequest
from app.services import golden_service

router = APIRouter(tags=["golden"])


@router.get("/golden")
def list_golden():
    return golden_service.list_golden()


@router.get("/golden/{dataset_id}")
def get_golden(dataset_id: int):
    overview = golden_service.get_golden(dataset_id)
    if not overview:
        raise HTTPException(404, "golden set not registered for this dataset")
    return overview


@router.put("/golden/{dataset_id}")
def register_golden(dataset_id: int, body: GoldenRegisterRequest):
    try:
        return golden_service.register_golden(dataset_id, body.fixture_path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/golden-diffs")
def list_diffs(dataset_id: int | None = None):
    return golden_service.list_diffs(dataset_id)


@router.get("/batches/{batch_id}/golden-diff")
def latest_diff(batch_id: int):
    diff = golden_service.latest_diff(batch_id)
    if not diff:
        raise HTTPException(404, "no golden diff for this batch")
    return diff


@router.post("/batches/{batch_id}/golden-diff")
def run_diff(batch_id: int):
    try:
        return golden_service.run_golden_diff(batch_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
