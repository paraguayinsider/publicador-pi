"""Lectura y escritura de la base "Calendario editorial" en Notion.

API 2025-09-03: una base (database) contiene una o más fuentes de datos
(data sources). Las filas se consultan en /v1/data_sources/{id}/query.
Aceptamos en NOTION_DATABASE_ID tanto el id de la base como el de la fuente.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from .config import ESTADOS, PROPS, Settings
from .models import MediaFile, Post

log = logging.getLogger("publicador.notion")

API = "https://api.notion.com/v1"


class NotionDB:
    def __init__(self, settings: Settings):
        if not settings.notion_token or not settings.notion_database_id:
            raise RuntimeError("Faltan NOTION_TOKEN o NOTION_DATABASE_ID")
        self.s = settings
        self.http = requests.Session()
        self.http.headers.update({
            "Authorization": f"Bearer {settings.notion_token}",
            "Notion-Version": settings.notion_version,
            "Content-Type": "application/json",
        })
        self.tz = ZoneInfo(settings.timezone)
        self._data_source_id: str | None = None

    # ---------- infraestructura ----------
    def _req(self, method: str, path: str, **kw) -> dict:
        r = self.http.request(method, f"{API}{path}", timeout=60, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Notion {method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    @property
    def data_source_id(self) -> str:
        if self._data_source_id:
            return self._data_source_id
        raw = self.s.notion_database_id
        # ¿Es un id de base? Entonces tiene data_sources.
        try:
            db = self._req("GET", f"/databases/{raw}")
            sources = db.get("data_sources") or []
            if sources:
                self._data_source_id = sources[0]["id"]
                if len(sources) > 1:
                    log.warning("La base tiene %d fuentes de datos; uso la primera (%s)",
                                len(sources), sources[0].get("name"))
                return self._data_source_id
        except RuntimeError as exc:
            log.debug("No es id de base (%s); pruebo como data source", exc)
        # ¿Es un id de fuente de datos?
        self._req("GET", f"/data_sources/{raw}")
        self._data_source_id = raw
        return raw

    def schema(self) -> dict[str, str]:
        """Nombre de propiedad -> tipo. Para setup_check."""
        ds = self._req("GET", f"/data_sources/{self.data_source_id}")
        return {name: prop.get("type") for name, prop in ds.get("properties", {}).items()}

    # ---------- lectura ----------
    def approved_due(self, now: datetime | None = None) -> list[Post]:
        """Filas en estado Aprobado con fecha/hora cumplida."""
        now = now or datetime.now(timezone.utc)
        estado_prop = self._estado_prop_type()
        filt = {
            "and": [
                {"property": PROPS["estado"], estado_prop: {"equals": ESTADOS["aprobado"]}},
                {"property": PROPS["fecha"], "date": {"on_or_before": now.isoformat()}},
            ]
        }
        posts: list[Post] = []
        cursor = None
        while True:
            body = {"filter": filt, "page_size": 100,
                    "sorts": [{"property": PROPS["fecha"], "direction": "ascending"}]}
            if cursor:
                body["start_cursor"] = cursor
            data = self._req("POST", f"/data_sources/{self.data_source_id}/query", json=body)
            for page in data.get("results", []):
                try:
                    posts.append(self.parse_page(page))
                except Exception as exc:  # fila mal cargada: no frena el resto
                    log.error("No pude leer la fila %s: %s", page.get("id"), exc)
            cursor = data.get("next_cursor") if data.get("has_more") else None
            if not cursor:
                break
        return posts

    def stuck_publishing(self) -> list[Post]:
        """Filas que quedaron en Publicando (una corrida anterior se cortó)."""
        estado_prop = self._estado_prop_type()
        body = {"filter": {"property": PROPS["estado"], estado_prop: {"equals": ESTADOS["publicando"]}},
                "page_size": 100}
        data = self._req("POST", f"/data_sources/{self.data_source_id}/query", json=body)
        return [self.parse_page(p) for p in data.get("results", [])]

    def _estado_prop_type(self) -> str:
        if not hasattr(self, "_estado_type"):
            sch = self.schema()
            t = sch.get(PROPS["estado"], "status")
            self._estado_type = "status" if t == "status" else "select"
        return self._estado_type

    def parse_page(self, page: dict) -> Post:
        props = page["properties"]
        return Post(
            page_id=page["id"],
            title=_plain(props.get(PROPS["titulo"], {}).get("title", [])),
            network=(_choice(props.get(PROPS["red"])) or "").strip().lower(),
            format=(_choice(props.get(PROPS["formato"])) or "").strip().lower(),
            text=_plain(props.get(PROPS["texto"], {}).get("rich_text", [])),
            when=self._parse_date(props.get(PROPS["fecha"])),
            status=_choice(props.get(PROPS["estado"])) or "",
            files=[MediaFile(name=f.get("name", ""), url=(f.get("file") or f.get("external") or {}).get("url", ""))
                   for f in props.get(PROPS["imagen"], {}).get("files", [])],
        )

    def _parse_date(self, prop: dict | None) -> datetime | None:
        if not prop or not prop.get("date") or not prop["date"].get("start"):
            return None
        raw = prop["date"]["start"]
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # Notion devuelve sin zona cuando la fila no tiene hora o no fijaron zona:
            # asumimos la zona local del proyecto.
            dt = dt.replace(tzinfo=self.tz)
        return dt

    # ---------- escritura ----------
    def set_status(self, page_id: str, estado: str, *, url: str | None = None,
                   error: str | None = None) -> None:
        if self.s.dry_run:
            log.info("[DRY RUN] Notion %s -> %s url=%s error=%s", page_id, estado, url, error)
            return
        estado_prop = self._estado_prop_type()
        props: dict = {PROPS["estado"]: {estado_prop: {"name": estado}}}
        if url is not None:
            props[PROPS["url_publicada"]] = {"url": url or None}
        if error is not None:
            props[PROPS["error"]] = {"rich_text": [{"text": {"content": error[:1900]}}] if error else []}
        self._req("PATCH", f"/pages/{page_id}", json={"properties": props})


def _plain(rich: list[dict]) -> str:
    return "".join(r.get("plain_text", "") for r in rich)


def _choice(prop: dict | None) -> str | None:
    """Valor de una propiedad select / status / multi_select (primer valor)."""
    if not prop:
        return None
    t = prop.get("type")
    if t in ("select", "status"):
        v = prop.get(t)
        return v.get("name") if v else None
    if t == "multi_select":
        v = prop.get("multi_select") or []
        return v[0]["name"] if v else None
    return None
