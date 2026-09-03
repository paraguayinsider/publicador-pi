"""X (Twitter) por API v2 con OAuth 1.0a de usuario. Opcional: solo si hay claves."""
from __future__ import annotations

import logging
import re

import requests
from requests_oauthlib import OAuth1

from .config import Settings
from .models import PublishError

log = logging.getLogger("publicador.x")

URL_RE = re.compile(r"https?://\S+")
MAX_CHARS = 280
URL_WEIGHT = 23  # toda URL cuenta 23 caracteres


def weighted_length(text: str) -> int:
    n = len(URL_RE.sub("", text))
    return n + URL_WEIGHT * len(URL_RE.findall(text))


class XClient:
    def __init__(self, settings: Settings):
        if not settings.x_enabled:
            raise RuntimeError("X no está configurado (faltan las 4 claves X_*)")
        self.s = settings
        self.auth = OAuth1(settings.x_consumer_key, settings.x_consumer_secret,
                           settings.x_access_token, settings.x_access_token_secret)

    def me(self) -> dict:
        r = requests.get("https://api.x.com/2/users/me", auth=self.auth, timeout=60)
        if r.status_code != 200:
            raise PublishError(f"X /users/me -> {r.status_code}: {r.text[:300]}")
        return r.json()["data"]

    def upload_image(self, data: bytes) -> str:
        r = requests.post("https://api.x.com/2/media/upload", auth=self.auth, timeout=180,
                          files={"media": ("image.jpg", data, "image/jpeg")},
                          data={"media_category": "tweet_image"})
        if r.status_code not in (200, 201):
            raise PublishError(f"X no aceptó la imagen: {r.status_code} {r.text[:300]}")
        body = r.json()
        return str(body.get("data", body).get("id") or body.get("data", body).get("media_key"))

    def publish(self, fmt: str, text: str, images: list[bytes]) -> str:
        if weighted_length(text) > MAX_CHARS:
            raise PublishError(f"El texto tiene {weighted_length(text)} caracteres (máximo {MAX_CHARS}; cada link cuenta 23)")
        if fmt == "reel":
            raise PublishError("Video en X no está soportado en esta versión del robot")
        if self.s.dry_run:
            log.info("[DRY RUN] X %s: %r + %d imágenes", fmt, text[:60], len(images))
            return "https://x.com/i/web/status/dry-run"
        payload: dict = {"text": text}
        if fmt in ("imagen", "carrusel") and images:
            ids = [self.upload_image(b) for b in images[:4]]
            payload["media"] = {"media_ids": ids}
        r = requests.post("https://api.x.com/2/tweets", json=payload, auth=self.auth, timeout=60)
        if r.status_code not in (200, 201):
            raise PublishError(f"X rechazó el post: {r.status_code} {r.text[:300]}")
        tweet_id = r.json()["data"]["id"]
        return f"https://x.com/i/web/status/{tweet_id}"
