#!/usr/bin/env python3
"""Normaliza data/raw/avisos.jsonl -> data/clean/avisos.parquet.

Tres cosas que importan más de lo que parece:

1. Republicaciones. El mismo inmueble aparece listado por varias inmobiliarias.
   Sin deduplicar por (coordenada, m2, precio) el ranking final muestra tres
   veces la misma "oportunidad".
2. Proyectos en pozo. Su precio es un "desde" que no compara con usados; se
   marcan y quedan fuera de la estimación.
3. Datos faltantes. `antiquity` falta en ~80 % de los avisos y `floor` en ~30 %.
   Descartar esas filas tiraría la mayoría de la muestra, así que se imputan y
   se agrega un indicador de faltante (el modelo estima su efecto por separado).
"""

import json
import os
import re
import sys

import numpy as np
import pandas as pd

import config

AQUI = os.path.dirname(os.path.abspath(__file__))


def ruta(rel):
    return os.path.join(AQUI, rel)


# Un precio muy por debajo del mercado casi nunca es una ganga: suele ser una
# propiedad con una condición que la abarata legítimamente. Detectarlas es lo
# que separa una oportunidad real de un falso positivo. No se descartan —
# comprar con inquilino ya instalado puede ser exactamente lo que busca un
# inversor— pero se etiquetan para poder filtrarlas.
CONDICIONES = [
    ("ocupado",    r"con renta|c/\s?renta|con inquilin|alquilad|nuda propiedad|"
                   r"usufructo|ocupad[oa]"),
    ("financiado", r"\banv\b|\bbhu\b|\bmevir\b|saldo|en cuotas|financia|"
                   r"entrega inicial|cuota inicial|\d+\s*cuotas"),
    ("pozo",       r"en pozo|en construcc|desde u\$s|entrega 20[2-9]\d|"
                   r"pre.?venta|preventa|estrena 20[2-9]\d|a estrenar 20[2-9]\d"),
    ("remate",     r"remate|subasta|judicial|sucesi[oó]n"),
    # "Venta de llave" es el traspaso de un fondo de comercio: se vende el
    # negocio, no el inmueble. Aparece clasificado como Casa o Local y su
    # precio no compara con nada — hay que sacarlo del modelo, no sólo marcarlo.
    # Ojo con "llave en mano": significa obra terminada, no traspaso de negocio.
    ("llave",      r"venta de llave|vendo llave|fondo de comercio|"
                   r"traspaso de negocio|llave de negocio"),
]


def condicion_de(titulo):
    """Etiqueta las condiciones especiales mencionadas en el título."""
    t = (titulo or "").lower()
    halladas = [nombre for nombre, patron in CONDICIONES if re.search(patron, t)]
    return ", ".join(halladas)


def _num(v):
    """Convierte a float; trata 0, '' y no-numéricos como faltante."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return np.nan
    return f if np.isfinite(f) else np.nan


def _primero(loc, clave):
    """locations.<clave> viene como lista o como dict vacío según el aviso."""
    v = (loc or {}).get(clave)
    if isinstance(v, list) and v:
        return (v[0] or {}).get("name")
    if isinstance(v, dict) and v:
        return v.get("name")
    return None


def _gastos_usd(aviso):
    """Gastos comunes a USD. currency.id: 1 = U$S, 2 = $."""
    gc = aviso.get("commonExpenses") or {}
    monto = _num(gc.get("amount"))
    if not np.isfinite(monto) or monto <= 0:
        return np.nan
    moneda = ((gc.get("currency") or {}).get("id"))
    return monto if moneda == 1 else monto / config.UYU_POR_USD


def extraer(aviso):
    loc = aviso.get("locations") or {}
    tipo = (aviso.get("property_type") or {}).get("name")

    # Superficie: hay que separar lo EDIFICADO del PADRÓN. En las casas, `m2`
    # normalmente repite `m2Terrain` (una casa de 95 m² construidos sobre un
    # terreno de 617 se publica como "617 m²"). Calcular el precio/m² sobre eso
    # da 287 USD/m² en Carrasco Norte y corona como oportunidad algo que no lo
    # es; peor todavía, mezcla en la misma regresión casas medidas por terreno
    # con casas medidas por construcción.
    total = _num(aviso.get("m2"))
    construido = _num(aviso.get("m2Built"))
    terreno = _num(aviso.get("m2Terrain"))
    if not (np.isfinite(construido) and construido > 0):
        construido = np.nan
    if not (np.isfinite(terreno) and terreno > 0):
        terreno = np.nan

    if tipo == "Terreno":
        sup = terreno if np.isfinite(terreno) else total
        construido = np.nan
    else:
        if not np.isfinite(construido):
            # `m2` sirve como superficie edificada sólo si no está repitiendo
            # el terreno.
            construido = (total if (not np.isfinite(terreno) or total != terreno)
                          else np.nan)
        sup = construido

    link = aviso.get("link") or ""
    if link and not link.startswith("http"):
        link = config.BASE + ("" if link.startswith("/") else "/") + link

    return {
        "id": aviso.get("id"),
        "titulo": aviso.get("title"),
        "url": link,
        "imagen": aviso.get("img"),
        # Texto del aviso: habilita la búsqueda por palabra clave y suele decir
        # cosas que ningún campo estructurado captura (reciclado, permuta,
        # "acepta canje", estado de conservación).
        "descripcion": (aviso.get("description") or "").strip(),
        "precio_usd": _num(aviso.get("price_amount_usd")),
        "m2": sup,
        "m2_terreno": terreno,
        # 0 dormitorios/baños es "no informado", no un inmueble sin baño;
        # tomarlo literal hunde el precio esperado de esos avisos.
        "dormitorios": _num(aviso.get("bedrooms")) or np.nan,
        "banos": _num(aviso.get("bathrooms")) or np.nan,
        "garaje": _num(aviso.get("garage")),
        "piso": _num(aviso.get("floor")),
        "antiguedad": _num(aviso.get("antiquity")),
        "vista_mar": bool(aviso.get("seaview")),
        "gc_usd": _gastos_usd(aviso),
        "lat": _num(aviso.get("latitude")),
        "lon": _num(aviso.get("longitude")),
        "barrio": _primero(loc, "neighbourhood") or _primero(loc, "city"),
        "depto_aviso": _primero(loc, "state"),
        "departamento": aviso.get("_departamento"),
        "tipo": tipo,
        "es_proyecto": bool(aviso.get("isProject")) or bool(aviso.get("isProjectUnit")),
        "precio_oculto": bool(aviso.get("hidePrice")),
        "publicado": aviso.get("created_at"),
        "capturado": aviso.get("_capturado"),
    }


def main():
    entrada = ruta(sys.argv[1] if len(sys.argv) > 1 else config.RAW)
    if not os.path.exists(entrada):
        sys.exit(f"No existe {entrada}. Corré primero: python3 collect.py")

    filas = []
    with open(entrada, encoding="utf-8") as fh:
        for linea in fh:
            try:
                filas.append(extraer(json.loads(linea)))
            except (json.JSONDecodeError, AttributeError):
                continue

    df = pd.DataFrame(filas)
    n0 = len(df)
    print(f"Avisos crudos: {n0:,}\n")

    print("Completitud de campos clave:")
    for c in ["precio_usd", "m2", "lat", "dormitorios", "banos",
              "antiguedad", "piso", "gc_usd"]:
        print(f"  {c:14} {df[c].notna().mean():6.1%}")

    # --- filtros de validez -------------------------------------------------
    pasos = []
    df = df.drop_duplicates(subset="id");            pasos.append(("id duplicado", n0 - len(df)))
    n = len(df); df = df[~df.precio_oculto];         pasos.append(("precio oculto", n - len(df)))
    n = len(df); df = df[df.precio_usd.between(config.PRECIO_MIN_USD, config.PRECIO_MAX_USD)]
    pasos.append((f"precio fuera de [{config.PRECIO_MIN_USD/1000:.0f}k, "
                  f"{config.PRECIO_MAX_USD/1e6:.0f}M] USD", n - len(df)))
    n = len(df)
    limites = df.tipo.map(lambda t: config.M2_RANGO.get(t, config.M2_RANGO_POR_DEFECTO))
    df = df[df.m2.ge(limites.str[0]) & df.m2.le(limites.str[1])]
    pasos.append(("m² fuera del rango válido para su tipo", n - len(df)))
    # Coordenadas basura (lat/lon en 0, o fuera de Uruguay continental): se
    # anulan acá y se imputan más abajo con el centroide del barrio. El modelo
    # usa efectos fijos de barrio, no la coordenada, así que descartar estos
    # avisos perdería ~4 % de la muestra sin ninguna necesidad.
    fuera = ~(df.lat.between(-35.5, -30.0) & df.lon.between(-58.6, -53.0))
    df.loc[fuera, ["lat", "lon"]] = np.nan

    # Republicaciones: mismo inmueble, distinta inmobiliaria.
    n = len(df)
    df = df.assign(_k=list(zip(df.lat.round(5), df.lon.round(5),
                               df.m2.round(0), df.precio_usd.round(0))))
    df = df.sort_values("publicado", ascending=False).drop_duplicates(subset="_k")
    df = df.drop(columns="_k")
    pasos.append(("republicaciones (misma coord+m²+precio)", n - len(df)))

    # Geolocalización imputada: centroide de los avisos geocodificados del
    # mismo barrio. Sirve para ubicarlo en el mapa; se marca para poder
    # distinguirlo de una coordenada real.
    centroide = df[df.lat.notna()].groupby("barrio")[["lat", "lon"]].median()
    df["geo_imputada"] = df.lat.isna().astype(int)
    df["lat"] = df.lat.fillna(df.barrio.map(centroide["lat"]))
    df["lon"] = df.lon.fillna(df.barrio.map(centroide["lon"]))
    n = len(df); df = df[df.lat.notna() & df.lon.notna()]
    pasos.append(("sin coordenada ni barrio para imputarla", n - len(df)))

    print("\nDescartes:")
    for etiqueta, cuantos in pasos:
        if cuantos:
            print(f"  −{cuantos:>6,}  {etiqueta}")

    # --- derivados ----------------------------------------------------------
    df["precio_m2"] = df.precio_usd / df.m2
    df["barrio"] = df.barrio.fillna("(sin barrio)")
    df["tipo"] = df.tipo.fillna("(sin tipo)")
    df["zona"] = df.departamento + " · " + df.barrio
    df["condicion"] = df.titulo.map(condicion_de)
    df["condicion_especial"] = (df.condicion != "").astype(int)

    # Faltantes: imputar + marcar, en vez de perder la fila.
    for col in ["antiguedad", "piso", "dormitorios", "banos", "garaje"]:
        df[f"falta_{col}"] = df[col].isna().astype(int)
        df[col] = df[col].fillna(df[col].median())
    df["gc_usd"] = df.gc_usd.fillna(0.0)
    # Terreno ausente = sin padrón propio (el caso de casi todo apartamento),
    # así que el faltante es 0 y no la mediana. El indicador distingue "no
    # tiene" de "no se informó".
    df["falta_terreno"] = df.m2_terreno.isna().astype(int)
    df["m2_terreno"] = df.m2_terreno.fillna(0.0)

    salida = ruta(config.CLEAN)
    os.makedirs(os.path.dirname(salida), exist_ok=True)
    df.to_parquet(salida, index=False)

    print(f"\nAvisos válidos: {len(df):,} ({len(df)/n0:.0%} de los crudos)")
    print(f"  de los cuales en pozo: {df.es_proyecto.sum():,}")
    print(f"\nCondiciones especiales detectadas en el título "
          f"({df.condicion_especial.sum():,} avisos):")
    for etiqueta, _ in CONDICIONES:
        cuantos = df.condicion.str.contains(etiqueta).sum()
        if cuantos:
            print(f"  {etiqueta:12} {cuantos:>6,}")
    print(f"\nPrecio/m² USD por departamento (mediana):")
    resumen = (df[~df.es_proyecto].groupby("departamento")
               .agg(n=("id", "size"), precio_m2=("precio_m2", "median"))
               .query("n >= 20").sort_values("precio_m2", ascending=False))
    for depto, r in resumen.iterrows():
        print(f"  {depto:16} n={int(r.n):>6,}   {r.precio_m2:>7,.0f}")
    print(f"\n→ {salida}")


if __name__ == "__main__":
    main()
