"""Medios: bajar de Notion, normalizar a JPEG y publicar en el repo público pi-media.

Instagram exige una URL pública y JPEG. El archivo de Notion tiene una URL
temporal (vence en ~1 h), así que lo copiamos a un repositorio público de
GitHub y usamos esa URL.
"""
from __future__ import annotations

import base64
import io
import logging
from datetime import datetime, timezone

import requests
from PIL import Image, ImageOps

from .config import Settings
from .models import MediaFile, PublishError

log = logging.getLogger("publicador.media")

MAX_SIDE = 1440          # px; Instagram recomienda ancho 1440
JPEG_QUALITY = 90
IG_MIN_RATIO = 0.8       # 4:5 (vertical)
IG_MAX_RATIO = 1.91      # 1.91:1 (horizontal)
MAX_UPLOAD_MB = 95       # límite práctico de la API de contenidos de GitHub (100 MB)


def download(mf: MediaFile) -> bytes:
    if not mf.url:
        raise PublishError(f"El archivo '{mf.name}' no tiene URL en Notion")
    r = requests.get(mf.url, timeout=120)
    if r.status_code != 200:
        raise PublishError(f"No pude bajar '{mf.name}' de Notion (HTTP {r.status_code})")
    return r.content


def to_jpeg(data: bytes, *, check_ig_ratio: bool = False) -> bytes:
    """Convierte cualquier imagen a JPEG RGB, corrige orientación EXIF y limita el tamaño."""
    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:
        raise PublishError(f"El archivo no es una imagen válida ({exc})") from exc
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        # fondo blanco para transparencias
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if check_ig_ratio:
        ratio = w / h
        if not (IG_MIN_RATIO - 0.005 <= ratio <= IG_MAX_RATIO + 0.005):
            raise PublishError(
                f"Relación de aspecto {w}x{h} ({ratio:.2f}) fuera del rango de Instagram "
                f"(entre 4:5 = 0.80 y 1.91:1 = 1.91). Recortá la imagen y volvé a subirla.")
    if max(w, h) > MAX_SIDE:
        scale = MAX_SIDE / max(w, h)
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, progressive=False)
    return out.getvalue()


class MediaRepo:
    """Sube archivos al repo público (GitHub Contents API) y devuelve la URL pública."""

    def __init__(self, settings: Settings):
        if not settings.media_enabled:
            raise RuntimeError("Faltan MEDIA_REPO o MEDIA_REPO_TOKEN")
        self.s = settings
        self.http = requests.Session()
        self.http.headers.update({
            "Authorization": f"Bearer {settings.media_repo_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _url(self, path: str) -> str:
        return f"https://api.github.com/repos/{self.s.media_repo}/contents/{path}"

    def upload(self, path: str, data: bytes, message: str = "publicador: media") -> str:
        if self.s.dry_run:
            log.info("[DRY RUN] subiría %s (%d KB) a %s", path, len(data) // 1024, self.s.media_repo)
            return self.s.public_media_url(path)
        if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
            raise PublishError(f"El archivo pesa {len(data)/1e6:.0f} MB; el máximo es {MAX_UPLOAD_MB} MB")
        body = {"message": message, "content": base64.b64encode(data).decode(),
                "branch": self.s.media_branch}
        # si ya existe (reintento), hace falta el sha para reemplazarlo
        r = self.http.get(self._url(path), params={"ref": self.s.media_branch}, timeout=60)
        if r.status_code == 200:
            body["sha"] = r.json()["sha"]
        r = self.http.put(self._url(path), json=body, timeout=180)
        if r.status_code not in (200, 201):
            raise PublishError(f"GitHub no aceptó el archivo {path}: {r.status_code} {r.text[:300]}")
        url = self.s.public_media_url(path)
        self._wait_public(url)
        return url

    def _wait_public(self, url: str, tries: int = 6) -> None:
        """raw.githubusercontent tarda unos segundos en servir un archivo nuevo."""
        import time
        for i in range(tries):
            r = requests.head(url, timeout=30, allow_redirects=True)
            if r.status_code == 200:
                return
            time.sleep(5 * (i + 1))
        raise PublishError(f"La URL pública todavía no responde: {url}")


def media_path(short_id: str, index: int, ext: str) -> str:
    now = datetime.now(timezone.utc)
    return f"{now:%Y/%m}/{short_id}-{index}.{ext}"
