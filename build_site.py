#!/usr/bin/env python3
"""Genera el sitio estático en docs/ a partir de data/clean/scored.parquet.

El tablero es HTML+JS sin servidor porque va a GitHub Pages: así no se duerme
nunca y no cuesta nada. Reproduce lo que hace app.py (filtros, mapa, feed,
búsqueda por palabra clave) pero del lado del navegador.

    python3 build_site.py
"""

import json
import os
import shutil
from datetime import date

import pandas as pd

import config

AQUI = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(AQUI, "docs")

# Cuántos avisos se publican. El sitio filtra del lado del cliente, así que el
# JSON entero viaja al navegador: hay que mantenerlo liviano.
TOPE = 3000
# Las descripciones completas son 28 MB; truncadas alcanzan para buscar.
DESC_MAX = 400

COLUMNAS = ["id", "titulo", "url", "imagen", "precio_usd", "m2", "precio_m2",
            "precio_m2_esperado", "brecha_pct", "score", "dormitorios", "banos",
            "garaje", "barrio", "departamento", "tipo", "confianza", "condicion",
            "lat", "lon", "gc_usd", "geo_imputada", "descripcion"]


def exportar_datos():
    scored = pd.read_parquet(os.path.join(AQUI, config.SCORED))
    d = scored.nlargest(TOPE, "score").copy()

    d["descripcion"] = d.descripcion.fillna("").str.slice(0, DESC_MAX)
    d["condicion"] = d.condicion.fillna("")
    for c in ["dormitorios", "banos", "garaje", "geo_imputada"]:
        d[c] = d[c].fillna(0).astype(int)
    for c in ["precio_usd", "m2", "precio_m2", "precio_m2_esperado",
              "brecha_pct", "score", "gc_usd"]:
        d[c] = d[c].round(1)
    d["lat"] = d.lat.round(5)
    d["lon"] = d.lon.round(5)

    registros = d[COLUMNAS].to_dict(orient="records")
    diag_path = os.path.join(AQUI, "data", "clean", "diagnosticos.json")
    diagnosticos = {}
    if os.path.exists(diag_path):
        with open(diag_path, encoding="utf-8") as fh:
            diagnosticos = json.load(fh)

    meta = {
        "actualizado": date.today().isoformat(),
        "modelo": diagnosticos,
        "total_analizados": int(len(scored)),
        "publicados": len(registros),
        "departamentos": sorted(d.departamento.dropna().unique().tolist()),
        "barrios": sorted(d.barrio.dropna().unique().tolist()),
        "tipos": sorted(d.tipo.dropna().unique().tolist()),
    }
    os.makedirs(os.path.join(DOCS, "data"), exist_ok=True)
    destino = os.path.join(DOCS, "data", "oportunidades.json")
    with open(destino, "w", encoding="utf-8") as fh:
        json.dump({"meta": meta, "avisos": registros}, fh,
                  ensure_ascii=False, separators=(",", ":"))
    return meta, os.path.getsize(destino)


if __name__ == "__main__":
    ruta = os.path.join(AQUI, config.SCORED)
    if not os.path.exists(ruta):
        raise SystemExit("Falta data/clean/scored.parquet. Corré model.py primero.")

    meta, peso = exportar_datos()
    plantilla = os.path.join(AQUI, "plantilla_sitio.html")
    shutil.copyfile(plantilla, os.path.join(DOCS, "index.html"))
    # Evita que GitHub Pages procese el sitio con Jekyll.
    open(os.path.join(DOCS, ".nojekyll"), "w").close()

    print(f"docs/data/oportunidades.json  {peso/1e6:.2f} MB")
    print(f"  {meta['publicados']:,} avisos publicados de {meta['total_analizados']:,}")
    print(f"  {len(meta['barrios'])} barrios · actualizado {meta['actualizado']}")
    print("docs/index.html")
