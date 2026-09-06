"""Lectura del listado web de MercadoLibre.

MELI bloquea el listado a los clientes automáticos (redirige a
/gz/account-verification), así que hace falta una sesión validada a mano una
vez: la crea meli_sesion.py y queda en data/meli/perfil/.

Dos límites del sitio que condicionan todo:
  - La paginación profunda está capada: _Desde_49 devuelve los mismos 48
    resultados que la página 1. La variedad se consigue con muchas búsquedas
    distintas (barrio, rango de precio), no paginando.
  - La API oficial no sirve de complemento: /items de un vendedor ajeno
    responde 403 access_denied, igual que /sites/MLU/search.
"""

import json
import os
import re
import time

from playwright.sync_api import sync_playwright

AQUI = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERFIL = os.path.join(AQUI, "data", "meli", "perfil")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


# La paginación profunda está capada (devuelve siempre los mismos 48), así que
# la variedad se consigue con MUCHAS búsquedas distintas en vez de con páginas.
# Filtrar por barrio además mejora la representatividad geográfica.
BARRIOS_MVD = ["pocitos", "cordon", "centro", "punta-carretas", "malvin", "buceo",
               "carrasco", "parque-batlle", "la-blanqueada", "prado", "aguada",
               "tres-cruces", "union", "colon", "penarol", "ciudad-vieja",
               "parque-rodo", "palermo", "villa-biarritz", "punta-gorda"]
BARRIOS_MDO = ["punta-del-este", "maldonado", "piriapolis", "san-carlos",
               "la-barra", "manantiales", "punta-ballena"]
PRECIOS = ["_PriceRange_0USD-80000USD", "_PriceRange_80000USD-150000USD",
           "_PriceRange_150000USD-300000USD", "_PriceRange_300000USD-0USD"]

BUSQUEDAS = []
for b in BARRIOS_MVD:
    BUSQUEDAS.append(f"https://inmuebles.mercadolibre.com.uy/apartamentos/venta/montevideo/{b}/")
for b in BARRIOS_MDO:
    BUSQUEDAS.append(f"https://inmuebles.mercadolibre.com.uy/apartamentos/venta/maldonado/{b}/")
for tipo in ["casas", "apartamentos"]:
    for dep in ["montevideo", "maldonado", "canelones"]:
        for pr in PRECIOS:
            BUSQUEDAS.append(
                f"https://inmuebles.mercadolibre.com.uy/{tipo}/venta/{dep}/{pr}")


def parsear_tarjeta(txt):
    """Extrae precio, superficie y ambientes del texto de una tarjeta.

    El formato es una lista de líneas, p. ej.:
        Apartamento en venta / US$ / 131.000 / 2 dormitorios / 1 baño /
        62 m² cubiertos / J. E. Rodó 1703, Cordón, Montevideo
    """
    t = " ".join((txt or "").split())
    # La primera línea suele ser una marca ("PUBLICADO HOY", "PROYECTO"), no el
    # título: hay que saltarlas para quedarse con el nombre real del aviso.
    marcas = re.compile(r"^(PUBLICADO|PROYECTO|Ad$|MÁS VENDIDO|OFERTA|"
                        r"LLEGA GRATIS|ENVÍO)", re.I)
    lineas = [x.strip() for x in (txt or "").split("\n") if x.strip()]
    titulo = next((x for x in lineas if not marcas.match(x)), "")
    f = {"titulo": titulo[:120], "texto": t[:400]}

    # MELI mezcla alquileres en los resultados de venta: un alquiler de USD
    # 1.390 aparecería como un descuento del 99 % contra el modelo de venta.
    f["es_alquiler"] = bool(re.search(r"alquiler|alquila|por mes|/mes", t, re.I))

    mp = re.search(r"(US\$|\$)\s*([\d.]+)", t)
    if mp:
        try:
            monto = float(mp.group(2).replace(".", ""))
        except ValueError:
            monto = None
        f["moneda"] = "USD" if mp.group(1) == "US$" else "UYU"
        f["precio"] = monto
        f["precio_usd"] = monto if f["moneda"] == "USD" else None
    else:
        f["moneda"] = f["precio"] = f["precio_usd"] = None

    # "47 - 62 m²" (proyectos) toma el menor; "62 m² cubiertos" el valor directo
    mm = re.search(r"(\d+)(?:\s*-\s*(\d+))?\s*m²", t)
    f["m2"] = float(mm.group(1)) if mm else None
    md = re.search(r"(\d+)(?:\s*a\s*\d+)?\s*dormitorio", t)
    f["dormitorios"] = float(md.group(1)) if md else None
    mb = re.search(r"(\d+)\s*baño", t)
    f["banos"] = float(mb.group(1)) if mb else None
    f["es_proyecto"] = bool(re.search(r"PROYECTO|Desde\s*US\$|unidades disponibles", txt or ""))

    # la dirección suele ser la línea con más comas
    lineas = [x.strip() for x in (txt or "").split("\n") if x.count(",") >= 1]
    f["direccion"] = max(lineas, key=len)[:140] if lineas else None
    return f


def recolectar(objetivo=1000, verbose=True):
    ids, orden = set(), []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PERFIL, headless=True, locale="es-UY",
            viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=UA)
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        for base in BUSQUEDAS:
            if len(ids) >= objetivo:
                break
            for pagina in range(1):           # paginar no aporta: siempre los mismos
                if len(ids) >= objetivo:
                    break
                url = base if pagina == 0 else f"{base}_Desde_{pagina*50+1}"
                try:
                    pg.goto(url, wait_until="domcontentloaded", timeout=60000)
                    pg.wait_for_timeout(2500)
                except Exception as e:
                    print(f"    error: {str(e)[:60]}")
                    continue
                if "account-verification" in pg.url:
                    print("    ⚠ sesión caída: volvé a correr meli_sesion.py")
                    ctx.close()
                    return orden
                tarjetas = pg.evaluate("""() => {
                  const out = [];
                  document.querySelectorAll('li.ui-search-layout__item, .poly-card')
                    .forEach(c => {
                      const a = c.querySelector("a[href*='MLU-']");
                      out.push({href: a ? a.href : '', texto: c.innerText});
                    });
                  return out;
                }""")
                nuevos = 0
                for t in tarjetas:
                    m = re.search(r"MLU-?(\d{8,12})", t.get("href") or "")
                    if not m:
                        continue
                    mlu = "MLU" + m.group(1)
                    if mlu in ids:
                        continue
                    ids.add(mlu)
                    fila = parsear_tarjeta(t["texto"])
                    fila["id"] = mlu
                    fila["url"] = (t["href"] or "").split("#")[0]
                    fila["_busqueda"] = base
                    orden.append(fila)
                    nuevos += 1
                if verbose:
                    print(f"    {base.split('/')[-3]}/{base.split('/')[-2]} "
                          f"+{nuevos:<3} · total {len(ids)}")
                if not nuevos:
                    break
        ctx.close()
    return orden
