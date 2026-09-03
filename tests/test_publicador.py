"""Pruebas sin red: parsing de Notion, conversión de imágenes, validaciones y una corrida DRY RUN."""
from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest
import responses
from PIL import Image

from publicador.config import ESTADOS, PROPS, Settings
from publicador.main import Runner
from publicador.media import to_jpeg
from publicador.models import PublishError
from publicador.notion_db import NotionDB
from publicador.x_api import weighted_length

DB_ID = "db000000-0000-0000-0000-000000000001"
DS_ID = "ds000000-0000-0000-0000-000000000002"


def settings(**over) -> Settings:
    base = dict(notion_token="secret", notion_database_id=DB_ID, dry_run=True,
                meta_page_id="1", meta_page_token="tok", ig_user_id="17841",
                media_repo="augusto/pi-media", media_repo_token="ghp",
                timezone="America/Asuncion")
    base.update(over)
    return Settings(**base)


def page(pid, *, red="Instagram", formato="imagen", texto="Hola #Paraguay", when="2026-09-02T10:00:00.000-03:00",
         estado="Aprobado", files=(("foto.png", "https://files.notion.so/foto.png"),)):
    return {
        "id": pid,
        "properties": {
            PROPS["titulo"]: {"type": "title", "title": [{"plain_text": f"Post {pid[-1]}"}]},
            PROPS["red"]: {"type": "select", "select": {"name": red}},
            PROPS["formato"]: {"type": "select", "select": {"name": formato}},
            PROPS["texto"]: {"type": "rich_text", "rich_text": [{"plain_text": texto}]},
            PROPS["fecha"]: {"type": "date", "date": {"start": when} if when else None},
            PROPS["estado"]: {"type": "status", "status": {"name": estado}},
            PROPS["imagen"]: {"type": "files", "files": [
                {"name": n, "type": "file", "file": {"url": u}} for n, u in files]},
        },
    }


def png_bytes(w=1080, h=1350, mode="RGBA"):
    img = Image.new(mode, (w, h), (10, 20, 30, 255) if mode == "RGBA" else (10, 20, 30))
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def mock_notion(rsps, pages, stuck=()):
    rsps.get(f"https://api.notion.com/v1/databases/{DB_ID}",
             json={"object": "database", "data_sources": [{"id": DS_ID, "name": "Calendario"}]})
    rsps.get(f"https://api.notion.com/v1/data_sources/{DS_ID}",
             json={"properties": {PROPS["estado"]: {"type": "status", "status": {"options": [
                 {"name": v} for v in ESTADOS.values()]}}}})

    def query(request):
        body = json.loads(request.body)
        f = body["filter"]
        if "and" in f:  # aprobadas
            return 200, {}, json.dumps({"results": pages, "has_more": False})
        return 200, {}, json.dumps({"results": list(stuck), "has_more": False})

    rsps.add_callback(responses.POST, f"https://api.notion.com/v1/data_sources/{DS_ID}/query",
                      callback=query, content_type="application/json")


# ---------------- parsing ----------------
@responses.activate
def test_parse_page_and_timezone():
    mock_notion(responses, [])
    db = NotionDB(settings())
    p = db.parse_page(page("p1", when="2026-09-02T10:00:00"))  # sin zona → Asunción
    assert p.network == "instagram" and p.format == "imagen"
    assert p.when.utcoffset() == timedelta(hours=-3)
    assert p.files[0].name == "foto.png" and not p.files[0].is_video
    p2 = db.parse_page(page("p2", when="2026-09-02T13:00:00.000Z"))
    assert p2.when == datetime(2026, 9, 2, 13, tzinfo=timezone.utc)
    assert db.data_source_id == DS_ID


# ---------------- imágenes ----------------
def test_to_jpeg_converts_png_with_alpha_and_resizes():
    out = to_jpeg(png_bytes(2000, 2500), check_ig_ratio=True)
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG" and img.mode == "RGB"
    assert max(img.size) == 1440


def test_to_jpeg_rejects_bad_ig_ratio():
    with pytest.raises(PublishError, match="Relación de aspecto"):
        to_jpeg(png_bytes(1000, 2000), check_ig_ratio=True)  # 1:2, demasiado vertical
    # sin chequeo (Facebook) pasa
    assert to_jpeg(png_bytes(1000, 2000))


def test_to_jpeg_rejects_non_image():
    with pytest.raises(PublishError, match="no es una imagen"):
        to_jpeg(b"esto no es una imagen")


# ---------------- X ----------------
def test_weighted_length_counts_urls_as_23():
    assert weighted_length("hola") == 4
    assert weighted_length("hola https://paraguayinsider.com/una/url/muy/larga/larguisima") == 5 + 23


# ---------------- validaciones ----------------
@responses.activate
def test_validate_rules():
    mock_notion(responses, [])
    r = Runner(settings())
    now = datetime(2026, 9, 2, 14, tzinfo=timezone.utc)
    db = r.db
    with pytest.raises(PublishError, match="vencida"):
        r.validate(db.parse_page(page("p1", when="2026-08-20T10:00:00-03:00")), now)
    with pytest.raises(PublishError, match="exactamente 1 archivo"):
        r.validate(db.parse_page(page("p2", files=())), now)
    with pytest.raises(PublishError, match="entre 2 y 10"):
        r.validate(db.parse_page(page("p3", formato="carrusel")), now)
    with pytest.raises(PublishError, match="1 video"):
        r.validate(db.parse_page(page("p4", formato="reel")), now)
    with pytest.raises(PublishError, match="X no está configurado"):
        r.validate(db.parse_page(page("p5", red="X", formato="solo texto")), now)
    with pytest.raises(PublishError, match="no reconocida"):
        r.validate(db.parse_page(page("p6", red="TikTok")), now)
    r.validate(db.parse_page(page("ok", red="Facebook", formato="solo texto", files=())), now)


# ---------------- corrida completa en DRY RUN ----------------
@responses.activate
def test_dry_run_end_to_end(caplog):
    pages = [
        page("p1"),                                              # IG imagen → ok
        page("p2", red="Facebook", formato="solo texto", files=()),  # FB texto → ok
        page("p3", red="LinkedIn"),                              # LinkedIn → omitida
        page("p4", red="Instagram", formato="solo texto", files=()),  # IG sin imagen → error
    ]
    stuck = [page("p9", estado="Publicando")]
    mock_notion(responses, pages, stuck)
    responses.get("https://files.notion.so/foto.png", body=png_bytes(1080, 1350), content_type="image/png")
    responses.get("https://graph.facebook.com/v24.0/debug_token",
                  json={"data": {"is_valid": True, "expires_at": 0}})

    caplog.set_level("INFO")
    code = Runner(settings()).run()
    assert code == 0
    log = caplog.text
    assert "Listo: 2 publicadas, 1 con error, 1 omitidas" in log
    assert "LinkedIn se programa a mano" in log
    assert "Instagram no admite publicaciones sin imagen" in log
    assert "quedó en Publicando" in log
    # en dry run no se escribió nada en Notion
    assert not [c for c in responses.calls if c.request.method == "PATCH"]


@responses.activate
def test_token_expiry_warning_fails_job():
    mock_notion(responses, [])
    soon = int((datetime.now(timezone.utc) + timedelta(days=3)).timestamp())
    responses.get("https://graph.facebook.com/v24.0/debug_token",
                  json={"data": {"is_valid": True, "expires_at": soon}})
    assert Runner(settings()).run() == 1


# ---------------- corrida REAL simulada (todas las APIs mockeadas) ----------------
@responses.activate
def test_real_run_instagram_image_writes_back(monkeypatch):
    import publicador.media as media_mod
    monkeypatch.setattr(media_mod.MediaRepo, "_wait_public", lambda self, url, tries=6: None)
    mock_notion(responses, [page("p1")])
    responses.get("https://files.notion.so/foto.png", body=png_bytes(1080, 1350), content_type="image/png")
    # GitHub: archivo no existe todavía → PUT crea
    import re as _re
    responses.get(_re.compile(r"https://api\.github\.com/repos/augusto/pi-media/contents/.*"), status=404)
    responses.put(_re.compile(r"https://api\.github\.com/repos/augusto/pi-media/contents/.*"),
                  json={"content": {"path": "x"}}, status=201)
    g = "https://graph.facebook.com/v24.0"
    responses.post(f"{g}/17841/media", json={"id": "cont1"})
    responses.post(f"{g}/17841/media_publish", json={"id": "media1"})
    responses.get(f"{g}/media1", json={"permalink": "https://www.instagram.com/p/ABC/"})
    responses.get(f"{g}/debug_token", json={"data": {"is_valid": True, "expires_at": 0}})
    patched = []
    responses.add_callback(responses.PATCH, "https://api.notion.com/v1/pages/p1",
                           callback=lambda r: (patched.append(json.loads(r.body)) or (200, {}, "{}")),
                           content_type="application/json")

    assert Runner(settings(dry_run=False)).run() == 0

    # 1) Publicando  2) Publicado + URL
    assert patched[0]["properties"][PROPS["estado"]]["status"]["name"] == "Publicando"
    final = patched[1]["properties"]
    assert final[PROPS["estado"]]["status"]["name"] == "Publicado"
    assert final[PROPS["url_publicada"]]["url"] == "https://www.instagram.com/p/ABC/"
    assert final[PROPS["error"]]["rich_text"] == []
    # la imagen se subió al repo público y se pasó a IG como URL raw
    ig_call = [c for c in responses.calls if c.request.url.endswith("/17841/media")][0]
    assert "image_url=https%3A%2F%2Fraw.githubusercontent.com%2Faugusto%2Fpi-media%2Fmain%2F" in ig_call.request.body
    assert "caption=Hola" in ig_call.request.body
