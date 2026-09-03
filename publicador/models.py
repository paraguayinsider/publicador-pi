from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


class PublishError(Exception):
    """Error que se escribe en la columna Error de Notion (mensaje legible)."""


@dataclass
class MediaFile:
    name: str
    url: str  # URL temporal de Notion (vence en ~1 h) o URL externa

    @property
    def is_video(self) -> bool:
        return self.name.lower().rsplit(".", 1)[-1] in {"mp4", "mov", "m4v"}


@dataclass
class Post:
    page_id: str
    title: str
    network: str  # instagram | facebook | linkedin | x (en minúsculas)
    format: str  # imagen | carrusel | reel | solo texto
    text: str
    when: datetime | None  # con zona horaria
    status: str
    files: list[MediaFile] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return self.page_id.replace("-", "")[:8]
