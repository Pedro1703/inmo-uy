"""Adaptador de InfoCasas.

El sitio es Next.js: cada página de listado trae los avisos ya serializados en
el <script id="__NEXT_DATA__">, así que no hace falta parsear el DOM ni ejecutar
JavaScript. La ruta útil es:

    props.pageProps.fetchResult.searchFast.data        -> 21 avisos
    props.pageProps.fetchResult.searchFast.paginatorInfo -> total, lastPage
"""

import json
import re
import time

import requests

import config

_NEXT = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S
)


def _sesion():
    s = requests.Session()
    s.headers.update({
        "User-Agent": config.USER_AGENT,
        # gzip no es opcional: baja cada página de ~738 KB a ~85 KB.
        "Accept-Encoding": "gzip, deflate, br",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-UY,es;q=0.9,en;q=0.8",
    })
    return s


def url_listado(operacion, tipo, departamento, pagina=1):
    u = f"{config.BASE}/{operacion}/{tipo}/{departamento}"
    return u if pagina == 1 else f"{u}/pagina{pagina}"


def _get(sesion, url):
    """GET con backoff exponencial. Devuelve el HTML o None."""
    espera = 2.0
    for intento in range(config.REINTENTOS):
        try:
            r = sesion.get(url, timeout=config.TIMEOUT)
            if r.status_code == 200:
                return r.text
            # 404 en página fuera de rango es una respuesta legítima, no un fallo.
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        if intento < config.REINTENTOS - 1:
            time.sleep(espera)
            espera *= 2
    return None


def parsear(html):
    """Extrae (avisos, info_paginacion) del __NEXT_DATA__ de un listado."""
    if not html:
        return [], {}
    m = _NEXT.search(html)
    if not m:
        return [], {}
    try:
        datos = json.loads(m.group(1))
        fast = datos["props"]["pageProps"]["fetchResult"]["searchFast"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return [], {}
    return fast.get("data") or [], fast.get("paginatorInfo") or {}


def paginas_totales(sesion, operacion, tipo, departamento):
    """Cantidad de páginas y total de avisos declarados para una búsqueda."""
    html = _get(sesion, url_listado(operacion, tipo, departamento))
    _, info = parsear(html)
    return info.get("lastPage", 0), info.get("total", 0)


def recorrer(sesion, operacion, tipo, departamento, max_paginas=None):
    """Itera los avisos de una búsqueda, página por página.

    Emite (aviso, pagina_actual, paginas_totales). No deduplica: eso queda a
    cargo del llamador, que es quien conoce los ids ya persistidos.
    """
    ultima, _ = paginas_totales(sesion, operacion, tipo, departamento)
    if not ultima:
        return
    limite = min(ultima, max_paginas) if max_paginas else ultima

    for pagina in range(1, limite + 1):
        time.sleep(config.DELAY)
        html = _get(sesion, url_listado(operacion, tipo, departamento, pagina))
        avisos, _ = parsear(html)
        if not avisos:
            continue
        for aviso in avisos:
            yield aviso, pagina, limite
