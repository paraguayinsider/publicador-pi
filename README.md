# Publicador PI

El calendario vive en Notion. Un robot gratuito de GitHub lo lee cada hora y publica por las APIs oficiales de Facebook, Instagram y (opcional) X. **Solo se publica lo que está en Aprobado.** Costo: US$ 0.

```
Notion (Calendario editorial)  ←→  GitHub Actions (cada hora)  →  Facebook / Instagram / X
                                          ↓
                          este repo (público): copia JPEG de cada imagen, URL para Instagram
```

## Qué hay en este repo

| Archivo | Qué hace |
|---|---|
| `publicador/main.py` | La corrida: lee Notion, valida, publica, escribe de vuelta |
| `publicador/notion_db.py` | Lectura/escritura de la base de Notion |
| `publicador/media.py` | Baja la imagen de Notion, la convierte a JPEG y la guarda en el repo público |
| `publicador/meta.py` | Facebook (página) e Instagram (Content Publishing API) |
| `publicador/x_api.py` | X, solo si se cargan las claves |
| `publicador/setup_check.py` | Chequeo de configuración: dice qué falta |
| `publicador/config.py` | Nombres de columnas, estados y variables de entorno |
| `.github/workflows/publicar.yml` | El cron horario |
| `.github/workflows/chequeo.yml` | El chequeo, para correr a mano desde GitHub |
| `tests/` | Pruebas sin red (`pytest`) |

## Cómo funciona una publicación

1. La fila está en **Aprobado** y su **Fecha y hora** ya pasó.
2. El robot la pasa a **Publicando** (así nunca se publica dos veces).
3. Si hay imagen: la baja de Notion, la convierte a JPEG (máx. 1440 px, corrige orientación, fondo blanco si tenía transparencia) y la guarda en este repo en `AAAA/MM/<id>-1.jpg`. Instagram exige JPEG en URL pública; por eso el repo es público.
4. Publica por la API de la red.
5. Escribe **Publicado** + **URL publicada**. Si algo falla: **Error** + motivo legible en la columna **Error**. Se corrige la fila, se vuelve a poner Aprobado y el robot reintenta en la próxima hora.

Reglas que aplica el robot:

- **Formato `imagen`**: exactamente 1 archivo. **`carrusel`**: 2 a 10 imágenes. **`reel`**: 1 video `.mp4`. **`solo texto`**: sin archivo (Instagram no lo admite → Error).
- **Instagram** rechaza imágenes fuera de la relación 4:5 a 1.91:1. El robot avisa con Error antes de intentar; Fabri recorta y vuelve a subir.
- **Vencidas**: si la fecha pasó hace más de 48 h (por ejemplo, el robot estuvo apagado), no publica: marca Error "vencida" para que Augusto decida. Se cambia con `MAX_DELAY_HOURS`.
- **LinkedIn**: el robot la ignora (queda en Aprobado). Se programa a mano en LinkedIn una vez al mes hasta que LinkedIn apruebe el acceso a su API.
- **X**: si no están las claves, las filas con Red = X dan Error "X no está configurado". Texto máximo 280 caracteres (cada link cuenta 23). Video en X no soportado.
- **Token de Meta**: si vence en ≤ 7 días, el job termina en fallo y GitHub manda un mail al dueño del repo. Un token de página derivado de un token de usuario de larga duración **no vence**; el aviso existe por si se generó de otra forma.
- **Cuota de Instagram**: 50 publicaciones por API cada 24 h (documentación de Meta, 2026).

## Base de Notion: "Calendario editorial"

Crear una base con estas columnas, **con estos nombres exactos** (o cambiar los nombres en `publicador/config.py`):

| Columna | Tipo | Valores |
|---|---|---|
| Título | Título | — |
| Red | Selección | Instagram · Facebook · LinkedIn · X |
| Formato | Selección | imagen · carrusel · reel · solo texto |
| Texto | Texto | el copy completo, con hashtags |
| Imagen | Archivos y multimedia | 1 imagen (o 2–10 para carrusel, o 1 .mp4 para reel) |
| Fecha y hora | Fecha | **con hora** (activar "Incluir hora") |
| Estado | Estado | Borrador · Aprobado · Publicando · Publicado · Error |
| Fuente | URL | de dónde sale el dato (no lo usa el robot) |
| URL publicada | URL | la escribe el robot |
| Error | Texto | lo escribe el robot |

Vista recomendada: Calendario por "Fecha y hora", con Estado como color. Una fila por publicación y por red (el mismo post en Instagram y Facebook son dos filas).

## Setup, paso a paso (1–2 h, una sola vez)

### 1. Notion

1. Crear la base con las columnas de arriba.
2. Ir a https://www.notion.so/profile/integrations → **New integration** → tipo *Internal*, nombre "Publicador PI", permisos: leer y actualizar contenido. Copiar el **Internal Integration Secret** → será `NOTION_TOKEN`.
3. En la base: menú `•••` (arriba a la derecha) → **Conexiones** → agregar "Publicador PI".
4. El id de la base: abrir la base como página completa; en la URL, los 32 caracteres antes de `?v=` → `NOTION_DATABASE_ID` (sirve con o sin guiones).

### 2. Meta (Facebook + Instagram)

Requisitos previos: Instagram en **cuenta profesional** (Configuración → Tipo de cuenta) y **vinculada a la página de Facebook** (en la página: Configuración → Cuentas vinculadas → Instagram). Augusto debe ser **administrador de la página**.

1. https://developers.facebook.com → **My Apps → Create App**. Caso de uso: *Other* → tipo *Business*. Nombre: "Publicador PI". Augusto queda como administrador de la app. **La app se queda en modo Desarrollo: no hace falta App Review** para publicar en páginas y cuentas administradas por quien tiene rol en la app (confianza media-alta; se confirma en este paso).
2. En la app: **Add product → Instagram** (Instagram API con Facebook Login) y **Facebook Login for Business**. Solo hace falta agregarlos; no hay que configurar el login.
3. Token de usuario: https://developers.facebook.com/tools/explorer → elegir la app → *User or Page: Get User Access Token* → marcar permisos `pages_show_list`, `pages_manage_posts`, `pages_read_engagement`, `instagram_basic`, `instagram_content_publish`, `business_management` → *Generate Access Token* → aceptar dando acceso a la página y a la cuenta de Instagram.
4. Convertirlo en token de larga duración. En el navegador (reemplazar valores; el *App Secret* está en la app → App settings → Basic):
   ```
   https://graph.facebook.com/v24.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=TOKEN_CORTO
   ```
   Copiar el `access_token` que devuelve (token de usuario, 60 días).
5. Token de página (este es el que usa el robot y **no vence**):
   ```
   https://graph.facebook.com/v24.0/me/accounts?access_token=TOKEN_LARGO
   ```
   Copiar `id` → `META_PAGE_ID` y `access_token` → `META_PAGE_TOKEN` de la página de Paraguay Insider.
6. Id de Instagram:
   ```
   https://graph.facebook.com/v24.0/META_PAGE_ID?fields=instagram_business_account&access_token=META_PAGE_TOKEN
   ```
   → `IG_USER_ID`. (El chequeo del paso 4 también lo muestra.)

### 3. GitHub

1. Crear repo **público** `publicador-pi` y subir todos estos archivos. Público porque las imágenes ya publicadas quedan en `AAAA/MM/` dentro del mismo repo y Instagram tiene que poder leerlas; el código no tiene nada secreto (todo va en Secrets). En repos públicos los minutos de Actions son ilimitados.
2. En `publicador-pi`: **Settings → Secrets and variables → Actions → New repository secret**, uno por uno:

   | Secret | Valor |
   |---|---|
   | `NOTION_TOKEN` | paso 1.2 |
   | `NOTION_DATABASE_ID` | paso 1.4 |
   | `META_PAGE_ID` | paso 2.5 |
   | `META_PAGE_TOKEN` | paso 2.5 |
   | `IG_USER_ID` | paso 2.6 |
   | `X_CONSUMER_KEY` … `X_ACCESS_TOKEN_SECRET` | solo si se decide X (ver abajo) |

3. Pestaña **Actions** → si pide habilitar workflows, habilitarlos.

*Opcional, si se prefiere el código en privado:* crear `publicador-pi` privado y un segundo repo público `pi-media`; token fine-grained en https://github.com/settings/personal-access-tokens con *Only select repositories: pi-media* y *Contents: Read and write*; cargar los secrets `MEDIA_REPO` (`usuario/pi-media`) y `MEDIA_REPO_TOKEN`. El robot los usa si existen.

### 4. Chequeo y primera prueba

1. **Actions → Chequeo de configuración → Run workflow.** Abrir el log: cada ítem sale con ✅ o ❌ y qué corregir.
2. Cargar una fila de prueba en Notion: Red = Facebook, Formato = imagen, una imagen, Fecha y hora = hace 5 minutos, Estado = Aprobado.
3. **Actions → Publicar → Run workflow** con *dry_run* marcado. El log muestra qué haría, sin publicar.
4. Repetir sin *dry_run*. La fila debe pasar a Publicado con la URL. Borrar la publicación de prueba en Facebook si se quiere.
5. Lo mismo con Instagram. Desde ahí, el cron corre solo cada hora (minuto 7, hora UTC; en Asunción es la misma hora de reloj, cada hora).

### 5. X (pendiente de decisión)

El nivel gratuito de la API de X no existe para cuentas nuevas; se paga por uso. **Verificar precios en https://developer.x.com antes de crear la cuenta.** Si se decide X:

1. https://developer.x.com → crear proyecto y app → *User authentication settings*: permisos **Read and write**, tipo *Web App*, cualquier URL de callback.
2. *Keys and tokens* → **API Key and Secret** (`X_CONSUMER_KEY`, `X_CONSUMER_SECRET`) y **Access Token and Secret** (`X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`; generarlos *después* de poner Read and write, si no quedan de solo lectura).
3. Cargar los 4 secrets y correr el chequeo.

## Operación mensual

1. ChatGPT escribe el lote del mes → se carga al calendario en **Borrador** (una fila por red).
2. Fabri sube la imagen a cada fila.
3. Augusto revisa y pasa a **Aprobado**.
4. El robot publica a la hora indicada. Las filas quedan en **Publicado** con el link.
5. Si una fila queda en **Error**, la columna Error dice qué pasó. Corregir → volver a **Aprobado**.
6. LinkedIn: programar a mano en LinkedIn (~12 posts, una vez al mes).

Mails automáticos de GitHub: solo cuando el job falla (token por vencer, Notion inaccesible). Un Error en una fila **no** es fallo del job: se ve en Notion.

## Mantenimiento

- **Token de Meta**: si el chequeo dice que vence, repetir los pasos 2.3–2.5 y actualizar `META_PAGE_TOKEN`.
- **Versión de Graph API**: `v24.0` en `GRAPH_API_VERSION`. Meta mantiene cada versión ~2 años; cambiar el valor cuando avise.
- **Si Instagram dice que no puede leer la imagen** ("Media not fetchable" o similar): activar GitHub Pages en el repo (Settings → Pages → rama main) y crear la variable `MEDIA_URL_BASE = https://usuario.github.io/publicador-pi` en *Settings → Secrets and variables → Actions → Variables*.
- **Videos (reels)**: el archivo va también al repo; máximo práctico 95 MB por archivo.
- **Cron apagado**: GitHub desactiva los cron de repos sin commits por 60 días; el workflow hace un commit de "latido" por semana para evitarlo. Si igual aparece "This scheduled workflow is disabled", botón *Enable workflow*.

## Probar en una computadora (opcional)

```
pip install -r requirements-dev.txt
pytest                                  # pruebas sin red
cp .env.example .env                    # completar
python -m publicador.setup_check
DRY_RUN=1 python -m publicador.main
```
