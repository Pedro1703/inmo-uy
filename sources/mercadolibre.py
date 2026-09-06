"""Adaptador de MercadoLibre — NO VIABLE. Ver el diagnóstico abajo.

PROBADO CON CREDENCIALES REALES (5/9/2026). No es un problema de setup:

    POST /oauth/token  (client_credentials)     -> 200  token OK, scope "read"
    GET  /users/me                              -> 200  identifica al usuario
    GET  /sites/MLU/search?category=MLU1459     -> 403  forbidden
    GET  /sites/MLU/search?q=apartamento        -> 403  forbidden
    GET  /users/{ajeno}/items/search            -> 403  "Searching another
                                                         user items is restricted."
    GET  /users/{propio}/items/search           -> 200  sólo los items propios

Es decir: el token es válido y funciona, pero MercadoLibre cerró la búsqueda
del catálogo. Sólo se pueden listar los items de la propia cuenta, lo que es
inútil para relevar el mercado. El flujo authorization_code NO lo resuelve: el
token de client_credentials ya autentica como el usuario y con scope read, y
aun así el endpoint responde 403.

Lo que SÍ responde con este token (por si sirve para otra cosa):
    /categories/{id}, /sites/MLU/categories, /sites/MLU, /items?ids=...

El scraping web tampoco: listado.mercadolibre.com.uy redirige a
/gz/account-verification (anti-bot).

Conclusión: mientras MercadoLibre no reabra /sites/{site}/search, la única
fuente viable del proyecto es InfoCasas. El código de abajo queda porque
funciona en cuanto el endpoint se reabra —basta con que buscar() deje de
recibir 403—, no porque hoy sirva.

CÓMO ACTIVARLO, si algún día se reabre
--------------------------------------
Las credenciales se leen de un .env local (no versionado):

    MELI_CLIENT_ID=...
    MELI_CLIENT_SECRET=...

Registro de la app en https://developers.mercadolibre.com.uy/ — en
"Redirect URI" hay que poner una URL HTTPS con dominio real (rechaza
http:// y rechaza localhost); con client_credentials nunca se usa.
"""

import time

import requests

import config

API = "https://api.mercadolibre.com"
SITIO = "MLU"          # Uruguay
CATEGORIA = "MLU1459"  # Inmuebles
# La API corta la paginación en offset 1000; para superarlo hay que particionar
# la búsqueda por filtros (barrio, rango de precio) igual que en InfoCasas.
MAX_OFFSET = 1000


def disponible():
    return bool(config.MELI_ACTIVO and config.MELI_CLIENT_ID
                and config.MELI_CLIENT_SECRET)


def token():
    """Token de aplicación (client_credentials)."""
    if not disponible():
        raise RuntimeError(
            "MercadoLibre inactivo: completá MELI_CLIENT_ID / MELI_CLIENT_SECRET "
            "en config.py y poné MELI_ACTIVO = True. Ver el encabezado de este "
            "archivo para los pasos."
        )
    r = requests.post(f"{API}/oauth/token", timeout=30, data={
        "grant_type": "client_credentials",
        "client_id": config.MELI_CLIENT_ID,
        "client_secret": config.MELI_CLIENT_SECRET,
    }, headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()["access_token"]


def _atributo(item, *ids):
    """Primer atributo presente entre los ids dados."""
    for a in item.get("attributes") or []:
        if a.get("id") in ids:
            return a.get("value_name") or a.get("value_struct", {}).get("number")
    return None


def normalizar(item):
    """Traduce un item de MELI al esquema de InfoCasas que espera clean.py."""
    loc = item.get("location") or {}
    barrio = (loc.get("neighborhood") or {}).get("name")
    estado = (loc.get("state") or {}).get("name")

    def _n(v):
        try:
            return float(str(v).split()[0])
        except (TypeError, ValueError, IndexError):
            return None

    precio = item.get("price")
    if item.get("currency_id") != "USD":
        precio = None  # clean.py descarta lo que no tenga precio en USD

    return {
        "id": f"ML{item.get('id')}",
        "title": item.get("title"),
        "link": item.get("permalink"),
        "price_amount_usd": precio,
        "m2": _n(_atributo(item, "TOTAL_AREA", "COVERED_AREA")),
        "m2Built": _n(_atributo(item, "COVERED_AREA")),
        "bedrooms": _n(_atributo(item, "BEDROOMS", "ROOMS")),
        "bathrooms": _n(_atributo(item, "FULL_BATHROOMS", "BATHROOMS")),
        "garage": _n(_atributo(item, "PARKING_LOTS")),
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "locations": {
            "neighbourhood": [{"name": barrio}] if barrio else [],
            "state": [{"name": estado}] if estado else [],
        },
        "property_type": {"name": _atributo(item, "PROPERTY_TYPE")},
        "commonExpenses": {},
        "isProject": False,
        "hidePrice": False,
        "_fuente": "mercadolibre",
    }


def buscar(operacion="Venta", limite=1000):
    """Itera items normalizados. Requiere credenciales activas."""
    cabeceras = {"Authorization": f"Bearer {token()}"}
    offset = 0
    while offset < min(limite, MAX_OFFSET):
        r = requests.get(f"{API}/sites/{SITIO}/search", timeout=30,
                         headers=cabeceras,
                         params={"category": CATEGORIA, "offset": offset,
                                 "limit": 50, "OPERATION": operacion})
        if r.status_code != 200:
            raise RuntimeError(
                f"MercadoLibre respondió {r.status_code}: {r.text[:200]}")
        resultados = r.json().get("results") or []
        if not resultados:
            return
        for item in resultados:
            yield normalizar(item)
        offset += 50
        time.sleep(0.5)
