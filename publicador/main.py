"""Publicador PI — corrida horaria.

Lee la base de Notion, toma las filas Aprobadas con hora cumplida, publica en la
red indicada y escribe de vuelta Publicado + URL, o Error + motivo.

Uso:  python -m publicador.main            (producción / GitHub Actions)
      DRY_RUN=1 python -m publicador.main  (muestra qué haría, no publica ni escribe)
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone

from .config import ESTADOS, FORMATOS, REDES, Settings, load_settings
from .media import MediaRepo, download, media_path, to_jpeg
from .models import Post, PublishError
from .notion_db import NotionDB

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("publicador")


class Runner:
    def __init__(self, settings: Settings):
        self.s = settings
        self.db = NotionDB(settings)
        self._graph = None
        self._x = None
        self._linkedin = None
        self._media = None
        self.warnings: list[str] = []

    # conectores perezosos: solo se crean si hace falta
    @property
    def graph(self):
        if self._graph is None:
            from .meta import Graph
            self._graph = Graph(self.s)
        return self._graph

    @property
    def x(self):
        if self._x is None:
            from .x_api import XClient
            self._x = XClient(self.s)
        return self._x

    @property
    def media(self) -> MediaRepo:
        if self._media is None:
            self._media = MediaRepo(self.s)
        return self._media

    # ---------- corrida ----------
    def run(self) -> int:
        now = datetime.now(timezone.utc)
        if self.s.dry_run:
            log.info("=== DRY RUN: no se publica ni se escribe en Notion ===")

        # 1. filas que quedaron colgadas en Publicando (corrida anterior cortada)
        for p in self.db.stuck_publishing():
            log.warning("Fila '%s' quedó en Publicando; la marco Error para revisión manual", p.title)
            self.db.set_status(p.page_id, ESTADOS["error"],
                               error="La corrida anterior se cortó mientras publicaba. Revisá si salió en la red; "
                                     "si no, volvé a poner Aprobado.")

        # 2. filas aprobadas y vencidas
        posts = self.db.approved_due(now)
        log.info("%d publicación(es) para procesar", len(posts))
        ok = err = skipped = 0
        for post in posts:
            result = self.process(post, now)
            ok += result == "ok"
            err += result == "error"
            skipped += result == "skip"

        # 3. salud de los tokens (avisa por fallo del workflow → mail de GitHub)
        self.check_meta_token(now)
        self.check_linkedin_token(now)

        log.info("Listo: %d publicadas, %d con error, %d omitidas", ok, err, skipped)
        if self.warnings:
            for w in self.warnings:
                log.error("AVISO: %s", w)
            return 1  # hace fallar el job → GitHub manda mail
        return 0

    def process(self, post: Post, now: datetime) -> str:
        label = f"[{post.network}/{post.format}] {post.title!r}"
        # Redes sin credenciales (X sin API paga; LinkedIn sin token): se programan a mano.
        # La fila queda en Aprobado; quien la publica a mano la pasa a Publicado con el link.
        if post.network in ("linkedin", "x") and self.s.is_manual(post.network):
            log.info("%s: %s se programa a mano; la fila queda en Aprobado", label, post.network)
            return "skip"
        try:
            self.validate(post, now)
            self.db.set_status(post.page_id, ESTADOS["publicando"])
            url = self.publish(post)
            self.db.set_status(post.page_id, ESTADOS["publicado"], url=url, error="")
            log.info("%s: publicado -> %s", label, url)
            return "ok"
        except PublishError as exc:
            log.error("%s: %s", label, exc)
            self.db.set_status(post.page_id, ESTADOS["error"], error=str(exc))
            return "error"
        except Exception as exc:  # cualquier otra cosa también queda registrada en la fila
            log.exception("%s: error inesperado", label)
            self.db.set_status(post.page_id, ESTADOS["error"], error=f"Error inesperado: {exc}"[:1900])
            return "error"

    def validate(self, post: Post, now: datetime) -> None:
        if post.network not in REDES:
            raise PublishError(f"Red '{post.network or '(vacía)'}' no reconocida. Opciones: Instagram, Facebook, LinkedIn, X")
        if post.format not in FORMATOS:
            raise PublishError(f"Formato '{post.format or '(vacío)'}' no reconocido. Opciones: imagen, carrusel, reel, solo texto")
        if post.when is None:
            raise PublishError("La fila no tiene fecha y hora")
        delay = now - post.when
        if delay > timedelta(hours=self.s.max_delay_hours):
            raise PublishError(
                f"Publicación vencida: estaba programada hace {delay.total_seconds()/3600:.0f} h "
                f"(máximo {self.s.max_delay_hours:.0f} h). Cambiá la fecha y volvé a poner Aprobado.")
        n = len(post.files)
        if post.format == "imagen" and n != 1:
            raise PublishError(f"El formato 'imagen' necesita exactamente 1 archivo (hay {n})")
        if post.format == "carrusel" and not 2 <= n <= 10:
            raise PublishError(f"El formato 'carrusel' necesita entre 2 y 10 imágenes (hay {n})")
        if post.format == "reel":
            if n != 1 or not post.files[0].is_video:
                raise PublishError("El formato 'reel' necesita exactamente 1 video .mp4")
        if post.format == "solo texto" and not post.text.strip():
            raise PublishError("La fila no tiene texto")
        if post.network in ("facebook", "instagram") and not self.s.meta_enabled:
            raise PublishError("Meta no está configurado en el robot (faltan META_PAGE_ID / META_PAGE_TOKEN)")

    @property
    def linkedin(self):
        if self._linkedin is None:
            from .linkedin import LinkedInClient
            self._linkedin = LinkedInClient(self.s)
        return self._linkedin

    def publish(self, post: Post) -> str:
        if post.network in ("x", "linkedin"):
            images = []
            if post.format in ("imagen", "carrusel"):
                images = [to_jpeg(download(f)) for f in post.files]
            client = self.x if post.network == "x" else self.linkedin
            return client.publish(post.format, post.text, images)

        # Facebook / Instagram: los medios van a URL pública
        urls: list[str] = []
        if post.format != "solo texto":
            for i, f in enumerate(post.files, 1):
                raw = download(f)
                if post.format == "reel":
                    data, ext = raw, "mp4"
                else:
                    data, ext = to_jpeg(raw, check_ig_ratio=(post.network == "instagram")), "jpg"
                urls.append(self.media.upload(media_path(post.short_id, i, ext), data,
                                              message=f"publicador: {post.title[:50]}"))
        if post.network == "facebook":
            return self.graph.fb_publish(post.format, post.text, urls)
        return self.graph.ig_publish(post.format, post.text, urls)

    def check_meta_token(self, now: datetime) -> None:
        if not self.s.meta_enabled:
            return
        try:
            exp = self.graph.token_expiry()
        except PublishError as exc:
            self.warnings.append(f"Token de Meta: {exc}")
            return
        if exp is None:
            log.info("Token de Meta: sin vencimiento")
            return
        days = (exp - now).days
        log.info("Token de Meta vence en %d días (%s)", days, exp.date())
        if days <= self.s.token_warn_days:
            self.warnings.append(
                f"El token de Meta vence en {days} días ({exp.date()}). Renovalo y actualizá META_PAGE_TOKEN en GitHub Secrets.")


    def check_linkedin_token(self, now: datetime) -> None:
        if not self.s.linkedin_enabled:
            return
        try:
            exp = self.linkedin.token_expiry()
        except PublishError as exc:
            self.warnings.append(f"Token de LinkedIn: {exc}")
            return
        if exp is None:
            log.info("Token de LinkedIn: vencimiento no consultable (faltan LINKEDIN_CLIENT_ID/SECRET)")
            return
        days = (exp - now).days
        log.info("Token de LinkedIn vence en %d días (%s)", days, exp.date())
        if days <= self.s.token_warn_days:
            self.warnings.append(
                f"El token de LinkedIn vence en {days} días ({exp.date()}). Generá uno nuevo en el portal de "
                f"desarrolladores (OAuth 2.0 token generator) y actualizá LINKEDIN_ACCESS_TOKEN en GitHub Secrets.")


def main() -> int:
    settings = load_settings()
    return Runner(settings).run()


if __name__ == "__main__":
    sys.exit(main())
