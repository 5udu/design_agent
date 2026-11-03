from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


ReasoningEffort = Literal['off', 'low', 'high', 'max']


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(default='新项目', min_length=1, max_length=120)
    brand: str = ''
    product: str = ''
    approved_copy: list[str] = Field(default_factory=list)


class RunCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=12000)
    target_asset_id: str | None = None
    reasoning_effort: ReasoningEffort | None = None


class ResumeInput(BaseModel):
    answer: str = Field(min_length=1, max_length=6000)
