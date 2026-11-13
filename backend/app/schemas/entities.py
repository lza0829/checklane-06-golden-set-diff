from pydantic import BaseModel


class InspectRequest(BaseModel):
    dataset_id: int


class RuleEnablePatch(BaseModel):
    enabled: bool


class GoldenRegisterRequest(BaseModel):
    fixture_path: str
