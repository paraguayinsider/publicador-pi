"""Configuración del Publicador PI.

Todo lo secreto viene de variables de entorno (GitHub Secrets en producción,
archivo .env en local). Nada se escribe en Notion ni en el repo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


# --- Nombres de las propiedades en la base "Calendario editorial" de Notion ---
# Si cambiás el nombre de una columna en Notion, cambialo acá.
PROPS = {
    "titulo": "Título",
    "red": "Red",
    "formato": "Formato",
    "texto": "Texto",
    "imagen": "Imagen",
    "fecha": "Fecha y hora",
    "estado": "Estado",
    "fuente": "Fuente",
    "url_publicada": "URL publicada",
    "error": "Error",
}

# Valores esperados
ESTADOS = {
    "borrador": "Borrador",
    "aprobado": "Aprobado",
    "publicando": "Publicando",
    "publicado": "Publicado",
    "error": "Error",
}
REDES = {"instagram", "facebook", "linkedin", "x"}
FORMATOS = {"imagen", "carrusel", "reel", "solo texto"}


@dataclass
class Settings:
    # Notion
    notion_token: str | None = field(default_factory=lambda: _env("NOTION_TOKEN"))
    notion_database_id: str | None = field(default_factory=lambda: _env("NOTION_DATABASE_ID"))
    notion_version: str = field(default_factory=lambda: _env("NOTION_VERSION", "2025-09-03"))

    # Meta (Facebook + Instagram)
    meta_page_id: str | None = field(default_factory=lambda: _env("META_PAGE_ID"))
    meta_page_token: str | None = field(default_factory=lambda: _env("META_PAGE_TOKEN"))
    ig_user_id: str | None = field(default_factory=lambda: _env("IG_USER_ID"))
    graph_version: str = field(default_factory=lambda: _env("GRAPH_API_VERSION", "v24.0"))

    # Repo público de medios (URL pública obligatoria para Instagram)
    media_repo: str | None = field(default_factory=lambda: _env("MEDIA_REPO"))  # "usuario/pi-media"
    media_repo_token: str | None = field(default_factory=lambda: _env("MEDIA_REPO_TOKEN"))
    media_branch: str = field(default_factory=lambda: _env("MEDIA_BRANCH", "main"))
    # Base pública desde donde se sirven los archivos. Por defecto raw.githubusercontent.com.
    # Alternativa (GitHub Pages): https://<usuario>.github.io/pi-media
    media_url_base: str | None = field(default_factory=lambda: _env("MEDIA_URL_BASE"))

    # X (opcional)
    x_consumer_key: str | None = field(default_factory=lambda: _env("X_CONSUMER_KEY"))
    x_consumer_secret: str | None = field(default_factory=lambda: _env("X_CONSUMER_SECRET"))
    x_access_token: str | None = field(default_factory=lambda: _env("X_ACCESS_TOKEN"))
    x_access_token_secret: str | None = field(default_factory=lambda: _env("X_ACCESS_TOKEN_SECRET"))

    # LinkedIn (opcional). Token de 60 días generado en el portal de desarrolladores.
    linkedin_access_token: str | None = field(default_factory=lambda: _env("LINKEDIN_ACCESS_TOKEN"))
    linkedin_client_id: str | None = field(default_factory=lambda: _env("LINKEDIN_CLIENT_ID"))
    linkedin_client_secret: str | None = field(default_factory=lambda: _env("LINKEDIN_CLIENT_SECRET"))
    # urn:li:person:XXXX (perfil) o urn:li:organization:XXXX (página). Vacío = el perfil del token.
    linkedin_author_urn: str | None = field(default_factory=lambda: _env("LINKEDIN_AUTHOR_URN"))
    linkedin_version: str = field(default_factory=lambda: _env("LINKEDIN_VERSION", "202608"))

    # Comportamiento
    timezone: str = field(default_factory=lambda: _env("TIMEZONE", "America/Asuncion"))
    max_delay_hours: float = field(default_factory=lambda: float(_env("MAX_DELAY_HOURS", "48")))
    token_warn_days: int = field(default_factory=lambda: int(_env("TOKEN_WARN_DAYS", "7")))
    dry_run: bool = field(default_factory=lambda: _env("DRY_RUN", "0") in ("1", "true", "yes"))

    @property
    def x_enabled(self) -> bool:
        return all([self.x_consumer_key, self.x_consumer_secret,
                    self.x_access_token, self.x_access_token_secret])

    @property
    def meta_enabled(self) -> bool:
        return bool(self.meta_page_id and self.meta_page_token)

    @property
    def linkedin_enabled(self) -> bool:
        return bool(self.linkedin_access_token)

    def is_manual(self, network: str) -> bool:
        """Redes sin credenciales: se programan a mano y el robot no las toca."""
        return (network == "x" and not self.x_enabled) or (network == "linkedin" and not self.linkedin_enabled)

    @property
    def media_enabled(self) -> bool:
        return bool(self.media_repo and self.media_repo_token)

    def public_media_url(self, path: str) -> str:
        if self.media_url_base:
            return f"{self.media_url_base.rstrip('/')}/{path}"
        return f"https://raw.githubusercontent.com/{self.media_repo}/{self.media_branch}/{path}"


def load_settings() -> Settings:
    # .env local, si existe (no se usa en GitHub Actions)
    env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return Settings()
