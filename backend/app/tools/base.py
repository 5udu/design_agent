"""The small contract between tool implementations and the execution runtime."""
from dataclasses import dataclass
from typing import Awaitable, Callable
from app.persistence.assets import Assets
from app.persistence.repositories import Store
from app.providers.ocr import OCR


SubmitImage = Callable[[str, str | None, str], Awaitable[tuple[bytes, dict]]]


@dataclass(frozen=True)
class ToolContext:
    store: Store
    assets: Assets
    ocr: OCR
    submit_image: SubmitImage


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    properties: dict
    required: list[str]
    handler: Callable[[ToolContext, dict, dict], Awaitable[dict]]
    waits_for_user: bool = False

    def schema(self):
        return {'type': 'function', 'function': {
            'name': self.name, 'description': self.description,
            'parameters': {'type': 'object', 'properties': self.properties,
                           'required': self.required, 'additionalProperties': False}}}
