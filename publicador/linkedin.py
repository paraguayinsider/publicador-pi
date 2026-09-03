"""LinkedIn por la Posts API (versionada). Publica como miembro (perfil) o como
organización (página), según LINKEDIN_AUTHOR_URN.

Token: LINKEDIN_ACCESS_TOKEN (60 días; se renueva a mano en el portal de desarrolladores,
herramienta "OAuth 2.0 token generator"). Con LINKEDIN_CLIENT_ID/SECRET el robot consulta
cuánto falta para que venza y avisa 7 días antes.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import requests

from .config import Settings
from .models import PublishError

log = logging.getLogger("publicador.linkedin")

API = "https://api.linkedin.com"
MAX_CHARS = 3000
# Caracteres reservados del "little text format" del campo commentary
_RESERVED = re.compile(r"([\\|{}@\[\]()<>#*_~])")
_HASHTAG = re.compile(r"(?<!\w)#([^\W_][\w]*)", re.UNICODE)


def ltf_escape(text: str) -> str:
    """Escapa los caracteres reservados y convierte #tag en {hashtag|\\#|tag}."""
    tags: list[str] = []

    def keep(m):
        tags.append(m.group(1))
        return f"\x00{len(tags) - 1}\x00"

    tmp = _HASHTAG.sub(keep, text)
    tmp = _RESERVED.sub(r"\\\1", tmp)
    for i, tag in enumerate(tags):
        tmp = tmp.replace(f"\x00{i}\x00", "{hashtag|\\#|" + tag + "}")
    return tmp


class LinkedInClient:
    def __init__(self, settings: Settings):
        if not settings.linkedin_enabled:
            raise RuntimeError("LinkedIn no está configurado (falta LINKEDIN_ACCESS_TOKEN)")
        self.s = settings
        self.headers = {
            "Authorization": f"Bearer {settings.linkedin_access_token}",
            "LinkedIn-Version": settings.linkedin_version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }
        self._author: str | None = settings.linkedin_author_urn

    # ---------- identidad ----------
    def me(self) -> dict:
        r = requests.get(f"{API}/v2/userinfo", headers=self.headers, timeout=60)
        if r.status_code != 200:
            raise PublishError(f"LinkedIn /userinfo -> {r.status_code}: {r.text[:300]}")
        return r.json()

    @property
    def author(self) -> str:
        if not self._author:
            self._author = f"urn:li:person:{self.me()['sub']}"
        return self._author

    # ---------- token ----------
    def token_expiry(self) -> datetime | None:
        """Fecha de vencimiento del token (None si no se puede consultar)."""
        if not (self.s.linkedin_client_id and self.s.linkedin_client_secret):
            return None
        r = requests.post("https://www.linkedin.com/oauth/v2/introspectToken", timeout=60,
                          data={"client_id": self.s.linkedin_client_id,
                                "client_secret": self.s.linkedin_client_secret,
                                "token": self.s.linkedin_access_token})
        if r.status_code != 200:
            raise PublishError(f"LinkedIn introspectToken -> {r.status_code}: {r.text[:200]}")
        data = r.json()
        if not data.get("active"):
            raise PublishError("El token de LinkedIn no está activo (vencido o revocado)")
        exp = data.get("expires_at")
        return datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None

    # ---------- medios ----------
    def upload_image(self, data: bytes) -> str:
        r = requests.post(f"{API}/rest/images?action=initializeUpload", headers=self.headers, timeout=60,
                          json={"initializeUploadRequest": {"owner": self.author}})
        if r.status_code != 200:
            raise PublishError(f"LinkedIn initializeUpload -> {r.status_code}: {r.text[:300]}")
        val = r.json()["value"]
        up = requests.put(val["uploadUrl"], data=data, timeout=180,
                          headers={"Authorization": self.headers["Authorization"], "Content-Type": "image/jpeg"})
        if up.status_code not in (200, 201):
            raise PublishError(f"LinkedIn no aceptó la imagen: {up.status_code} {up.text[:200]}")
        urn = val["image"]
        self._wait_available(urn)
        return urn

    def _wait_available(self, urn: str, max_wait: int = 90) -> None:
        waited = 0
        while waited < max_wait:
            r = requests.get(f"{API}/rest/images/{urn}", headers=self.headers, timeout=60)
            status = r.json().get("status") if r.status_code == 200 else None
            if status == "AVAILABLE":
                return
            if status == "PROCESSING_FAILED":
                raise PublishError("LinkedIn no pudo procesar la imagen")
            time.sleep(5)
            waited += 5
        log.warning("LinkedIn: la imagen %s sigue en proceso; intento publicar igual", urn)

    # ---------- publicar ----------
    def publish(self, fmt: str, text: str, images: list[bytes]) -> str:
        if len(text) > MAX_CHARS:
            raise PublishError(f"El texto tiene {len(text)} caracteres (máximo {MAX_CHARS} en LinkedIn)")
        if fmt == "reel":
            raise PublishError("Video en LinkedIn no está soportado en esta versión del robot")
        if self.s.dry_run:
            log.info("[DRY RUN] LinkedIn %s: %r + %d imágenes", fmt, text[:60], len(images))
            return "https://www.linkedin.com/feed/update/dry-run/"
        body: dict = {
            "author": self.author,
            "commentary": ltf_escape(text),
            "visibility": "PUBLIC",
            "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [],
                             "thirdPartyDistributionChannels": []},
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        if fmt == "imagen" and images:
            body["content"] = {"media": {"id": self.upload_image(images[0])}}
        elif fmt == "carrusel" and images:
            ids = [self.upload_image(b) for b in images[:20]]
            body["content"] = {"multiImage": {"images": [{"id": i} for i in ids]}}
        r = requests.post(f"{API}/rest/posts", headers=self.headers, json=body, timeout=120)
        if r.status_code != 201:
            raise PublishError(f"LinkedIn rechazó el post: {r.status_code} {r.text[:300]}")
        urn = r.headers.get("x-restli-id") or r.headers.get("X-RestLi-Id") or ""
        return f"https://www.linkedin.com/feed/update/{urn}/" if urn else "https://www.linkedin.com/feed/"
