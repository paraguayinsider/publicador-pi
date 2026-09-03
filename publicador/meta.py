"""Facebook (página) e Instagram (cuenta profesional) por Graph API."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import requests

from .config import Settings
from .models import PublishError

log = logging.getLogger("publicador.meta")

URL_RE = re.compile(r"https?://\S+")


class Graph:
    def __init__(self, settings: Settings):
        if not settings.meta_enabled:
            raise RuntimeError("Faltan META_PAGE_ID o META_PAGE_TOKEN")
        self.s = settings
        self.base = f"https://graph.facebook.com/{settings.graph_version}"
        self.token = settings.meta_page_token

    def call(self, method: str, path: str, **params) -> dict:
        params.setdefault("access_token", self.token)
        if method == "GET":
            r = requests.get(f"{self.base}/{path}", params=params, timeout=120)
        else:
            r = requests.post(f"{self.base}/{path}", data=params, timeout=300)
        try:
            data = r.json()
        except ValueError:
            raise PublishError(f"Meta devolvió una respuesta no JSON ({r.status_code})")
        if r.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            msg = err.get("error_user_msg") or err.get("message") or r.text[:300]
            raise PublishError(f"Meta ({path}): {msg} [code {err.get('code')}, sub {err.get('error_subcode')}]")
        return data

    # ---------- token ----------
    def token_expiry(self) -> datetime | None:
        """None = no vence. Lanza PublishError si el token es inválido."""
        data = self.call("GET", "debug_token", input_token=self.token)["data"]
        if not data.get("is_valid", False):
            raise PublishError(f"El token de Meta no es válido: {data.get('error', {}).get('message')}")
        exp = data.get("expires_at") or 0
        return datetime.fromtimestamp(exp, tz=timezone.utc) if exp else None

    def token_scopes(self) -> list[str]:
        return self.call("GET", "debug_token", input_token=self.token)["data"].get("scopes", [])

    # ---------- Facebook ----------
    def fb_publish(self, fmt: str, text: str, media_urls: list[str]) -> str:
        page = self.s.meta_page_id
        if self.s.dry_run:
            log.info("[DRY RUN] Facebook %s: %r + %s", fmt, text[:60], media_urls)
            return "https://www.facebook.com/dry-run"
        if fmt == "solo texto":
            params = {"message": text}
            links = URL_RE.findall(text)
            if links:
                params["link"] = links[0].rstrip(".,)")
            post_id = self.call("POST", f"{page}/feed", **params)["id"]
        elif fmt == "imagen":
            res = self.call("POST", f"{page}/photos", url=media_urls[0], message=text)
            post_id = res.get("post_id") or res["id"]
        elif fmt == "carrusel":
            ids = [self.call("POST", f"{page}/photos", url=u, published="false")["id"] for u in media_urls]
            import json
            post_id = self.call("POST", f"{page}/feed", message=text,
                                attached_media=json.dumps([{"media_fbid": i} for i in ids]))["id"]
        elif fmt == "reel":
            res = self.call("POST", f"{page}/videos", file_url=media_urls[0], description=text)
            vid = res["id"]
            return self._fb_video_permalink(vid)
        else:
            raise PublishError(f"Formato '{fmt}' no soportado en Facebook")
        return self.call("GET", post_id, fields="permalink_url").get("permalink_url") \
            or f"https://www.facebook.com/{post_id}"

    def _fb_video_permalink(self, video_id: str) -> str:
        for _ in range(10):
            data = self.call("GET", video_id, fields="permalink_url,status")
            url = data.get("permalink_url")
            if url:
                return url if url.startswith("http") else f"https://www.facebook.com{url}"
            time.sleep(15)
        return f"https://www.facebook.com/{self.s.meta_page_id}/videos/{video_id}"

    # ---------- Instagram ----------
    def ig_publish(self, fmt: str, text: str, media_urls: list[str]) -> str:
        ig = self.s.ig_user_id
        if not ig:
            raise PublishError("Falta IG_USER_ID (id de la cuenta profesional de Instagram)")
        if fmt == "solo texto":
            raise PublishError("Instagram no admite publicaciones sin imagen. Cambiá el formato o subí una imagen.")
        if fmt not in ("imagen", "carrusel", "reel"):
            raise PublishError(f"Formato '{fmt}' no soportado en Instagram")
        if self.s.dry_run:
            log.info("[DRY RUN] Instagram %s: %r + %s", fmt, text[:60], media_urls)
            return "https://www.instagram.com/p/dry-run/"
        if fmt == "imagen":
            cid = self.call("POST", f"{ig}/media", image_url=media_urls[0], caption=text)["id"]
        elif fmt == "carrusel":
            if not 2 <= len(media_urls) <= 10:
                raise PublishError("Un carrusel necesita entre 2 y 10 imágenes")
            children = [self.call("POST", f"{ig}/media", image_url=u, is_carousel_item="true")["id"]
                        for u in media_urls]
            cid = self.call("POST", f"{ig}/media", media_type="CAROUSEL",
                            children=",".join(children), caption=text)["id"]
        elif fmt == "reel":
            cid = self.call("POST", f"{ig}/media", media_type="REELS", video_url=media_urls[0],
                            caption=text, share_to_feed="true")["id"]
            self._ig_wait(cid)
        else:
            raise PublishError(f"Formato '{fmt}' no soportado en Instagram")
        media_id = self.call("POST", f"{ig}/media_publish", creation_id=cid)["id"]
        return self.call("GET", media_id, fields="permalink").get("permalink") \
            or f"https://www.instagram.com/{media_id}"

    def _ig_wait(self, container_id: str, max_wait: int = 420) -> None:
        """Los videos se procesan antes de poder publicarse."""
        waited = 0
        while waited < max_wait:
            data = self.call("GET", container_id, fields="status_code,status")
            code = data.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"Instagram no pudo procesar el video: {data.get('status')}")
            time.sleep(20)
            waited += 20
        raise PublishError("Instagram tardó más de 7 minutos en procesar el video")

    def ig_daily_quota(self) -> dict:
        ig = self.s.ig_user_id
        data = self.call("GET", f"{ig}/content_publishing_limit", fields="quota_usage,config")
        return (data.get("data") or [{}])[0]
