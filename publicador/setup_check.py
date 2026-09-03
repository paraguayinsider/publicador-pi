"""Chequeo de configuración: correr una vez al terminar el setup y cada vez que algo falle.

    python -m publicador.setup_check

No publica nada. Verifica cada pieza y dice qué falta.
"""
from __future__ import annotations

import sys

import requests

from .config import ESTADOS, PROPS, load_settings

EXPECTED_TYPES = {
    PROPS["titulo"]: {"title"},
    PROPS["red"]: {"select", "multi_select"},
    PROPS["formato"]: {"select"},
    PROPS["texto"]: {"rich_text"},
    PROPS["imagen"]: {"files"},
    PROPS["fecha"]: {"date"},
    PROPS["estado"]: {"status", "select"},
    PROPS["url_publicada"]: {"url"},
    PROPS["error"]: {"rich_text"},
}


def ok(msg):
    print(f"  ✅ {msg}")


def bad(msg):
    print(f"  ❌ {msg}")


def warn(msg):
    print(f"  ⚠️  {msg}")


def check_notion(s) -> bool:
    print("\n[Notion]")
    if not s.notion_token or not s.notion_database_id:
        bad("Faltan NOTION_TOKEN o NOTION_DATABASE_ID")
        return False
    from .notion_db import NotionDB
    try:
        db = NotionDB(s)
        dsid = db.data_source_id
        ok(f"Base accesible (data source {dsid})")
    except Exception as exc:
        bad(f"No pude abrir la base: {exc}")
        bad("Revisá que la integración esté conectada a la base (••• → Conexiones) y que el id sea el correcto")
        return False
    schema = db.schema()
    good = True
    for name, types in EXPECTED_TYPES.items():
        t = schema.get(name)
        if t is None:
            bad(f"Falta la columna '{name}' (tipo {'/'.join(sorted(types))})")
            good = False
        elif t not in types:
            bad(f"La columna '{name}' es de tipo {t}; debería ser {'/'.join(sorted(types))}")
            good = False
        else:
            ok(f"Columna '{name}' ({t})")
    # opciones de Estado
    try:
        ds = db._req("GET", f"/data_sources/{dsid}")
        prop = ds["properties"].get(PROPS["estado"], {})
        opts = prop.get(prop.get("type"), {}).get("options", [])
        names = {o["name"] for o in opts}
        missing = set(ESTADOS.values()) - names
        if missing:
            bad(f"A 'Estado' le faltan las opciones: {', '.join(sorted(missing))}")
            good = False
        else:
            ok("Opciones de Estado completas")
    except Exception as exc:
        warn(f"No pude leer las opciones de Estado: {exc}")
    return good


def check_meta(s) -> bool:
    print("\n[Meta — Facebook / Instagram]")
    if not s.meta_enabled:
        bad("Faltan META_PAGE_ID o META_PAGE_TOKEN")
        return False
    from .meta import Graph
    g = Graph(s)
    try:
        page = g.call("GET", s.meta_page_id, fields="name,instagram_business_account")
        ok(f"Página de Facebook: {page.get('name')}")
    except Exception as exc:
        bad(f"Página: {exc}")
        return False
    good = True
    ig = (page.get("instagram_business_account") or {}).get("id")
    if ig:
        if s.ig_user_id and s.ig_user_id != ig:
            bad(f"IG_USER_ID configurado ({s.ig_user_id}) no coincide con el vinculado a la página ({ig})")
            good = False
        else:
            ok(f"Instagram vinculado: id {ig}" + ("" if s.ig_user_id else "  → poné IG_USER_ID=" + ig))
            if not s.ig_user_id:
                good = False
        try:
            q = g.ig_daily_quota()
            ok(f"Cuota IG: {q.get('quota_usage', '?')} usadas de {q.get('config', {}).get('quota_total', '?')} en 24 h")
        except Exception as exc:
            warn(f"No pude leer la cuota de Instagram: {exc}")
    else:
        bad("La página no tiene una cuenta de Instagram profesional vinculada")
        good = False
    try:
        exp = g.token_expiry()
        ttype = g.call("GET", "debug_token", input_token=g.token)["data"].get("type", "?")
        ok(f"Token válido (tipo {ttype}); " + ("no vence" if exp is None else f"vence el {exp.date()}"))
        try:
            g.page_token()
            ok("Token de página obtenido para publicar en Facebook")
        except Exception as exc:
            bad(f"No pude obtener el token de página: {exc}")
            good = False
        scopes = set(g.token_scopes())
        needed = {"pages_manage_posts", "pages_read_engagement", "instagram_basic", "instagram_content_publish"}
        missing = needed - scopes
        if missing:
            bad(f"Al token le faltan permisos: {', '.join(sorted(missing))}")
            good = False
        else:
            ok("Permisos del token completos")
    except Exception as exc:
        bad(f"Token: {exc}")
        good = False
    return good


def check_media(s) -> bool:
    print("\n[Repo público de medios]")
    if not s.media_enabled:
        bad("Faltan MEDIA_REPO o MEDIA_REPO_TOKEN")
        return False
    r = requests.get(f"https://api.github.com/repos/{s.media_repo}", timeout=30,
                     headers={"Authorization": f"Bearer {s.media_repo_token}"})
    if r.status_code != 200:
        bad(f"No pude abrir {s.media_repo}: {r.status_code} {r.text[:200]}")
        return False
    data = r.json()
    if data.get("private"):
        bad(f"{s.media_repo} es privado: Instagram no podrá leer las imágenes. Tiene que ser público.")
        return False
    ok(f"{s.media_repo} es público (rama {s.media_branch})")
    # Prueba real de escritura: crea/actualiza un archivo chico en state/ (el token del job
    # de Actions no informa 'permissions', así que la única prueba fiable es escribir).
    import base64
    from datetime import datetime, timezone
    path = "state/media_check.txt"
    url = f"https://api.github.com/repos/{s.media_repo}/contents/{path}"
    headers = {"Authorization": f"Bearer {s.media_repo_token}", "Accept": "application/vnd.github+json"}
    body = {"message": "chequeo: prueba de escritura", "branch": s.media_branch,
            "content": base64.b64encode(datetime.now(timezone.utc).isoformat().encode()).decode()}
    prev = requests.get(url, headers=headers, params={"ref": s.media_branch}, timeout=30)
    if prev.status_code == 200:
        body["sha"] = prev.json()["sha"]
    w = requests.put(url, headers=headers, json=body, timeout=60)
    if w.status_code in (200, 201):
        ok("Escritura en el repo verificada (state/media_check.txt)")
        return True
    bad(f"No pude escribir en el repo ({w.status_code}): {w.text[:200]}. "
        "Revisá 'permissions: contents: write' en el workflow o el token MEDIA_REPO_TOKEN.")
    return False


def check_linkedin(s) -> bool:
    print("\n[LinkedIn]")
    if not s.linkedin_enabled:
        warn("LinkedIn no configurado (opcional). Las filas con Red = LinkedIn quedan para publicar a mano.")
        return True
    from .linkedin import LinkedInClient
    li = LinkedInClient(s)
    try:
        me = li.me()
        ok(f"Token válido; perfil: {me.get('name')} (urn:li:person:{me.get('sub')})")
    except Exception as exc:
        bad(f"LinkedIn: {exc}")
        bad("Generá un token nuevo en el portal de desarrolladores (OAuth 2.0 token generator) con los "
            "permisos openid, profile y w_member_social")
        return False
    ok(f"Publica como: {li.author}" + ("" if s.linkedin_author_urn else " (perfil del token)"))
    try:
        exp = li.token_expiry()
        if exp is None:
            warn("Sin LINKEDIN_CLIENT_ID/SECRET no puedo consultar el vencimiento (el token dura 60 días)")
        else:
            ok(f"Token vence el {exp.date()}")
    except Exception as exc:
        bad(f"Vencimiento del token: {exc}")
        return False
    return True


def check_x(s) -> bool:
    print("\n[X]")
    if not s.x_enabled:
        warn("X no configurado (opcional). Las filas con Red = X quedan para publicar a mano.")
        return True
    from .x_api import XClient
    try:
        me = XClient(s).me()
        ok(f"Cuenta: @{me.get('username')}")
        return True
    except Exception as exc:
        bad(f"X: {exc}")
        return False


def main() -> int:
    s = load_settings()
    print(f"Publicador PI — chequeo de configuración (zona {s.timezone}, Graph {s.graph_version}, Notion {s.notion_version})")
    results = [check_notion(s), check_meta(s), check_media(s), check_linkedin(s), check_x(s)]
    print()
    if all(results):
        print("Todo listo. Probá con: DRY_RUN=1 python -m publicador.main")
        return 0
    print("Hay cosas que corregir (ver ❌ arriba).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
