#!/usr/bin/env python3
"""Radar de novedades de MercadoLibre.

Medimos el solapamiento con InfoCasas sobre 1.002 avisos: ~72 % ya está en
InfoCasas, y el aporte incremental plausible es ~16 %. Replicar MELI entero no
compensa, pero casi todo lo que aporta son avisos marcados "publicado hoy /
esta semana": no es stock distinto, es el mismo mercado con unos días de
ventaja. Eso es lo que este radar captura.

Corre las búsquedas por barrio, se queda con lo nuevo que InfoCasas todavía no
tiene, lo puntúa con el mismo modelo hedónico y lo reporta.

    python3 meli_radar.py            # corrida semanal
    python3 meli_radar.py --todos    # no sólo novedades: todo lo no solapado

Requiere una sesión válida (se cae cada tanto):  python3 meli_sesion.py
"""

import argparse
import json
import os
import re
import sys
import warnings
from datetime import date

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

import clean
import config
import model as modelo
from sources import meli_web

warnings.filterwarnings("ignore")
AQUI = os.path.dirname(os.path.abspath(__file__))
HISTORICO = os.path.join(AQUI, "data", "meli", "radar_historico.json")
NOVEDADES = os.path.join(AQUI, "data", "meli", "novedades.parquet")


def barrio_de(direccion):
    """La dirección viene como 'calle 123, [Ciudad,] Barrio, Departamento'."""
    partes = [p.strip() for p in (direccion or "").split(",") if p.strip()]
    return partes[-2] if len(partes) >= 2 else None


def ya_vistos():
    if not os.path.exists(HISTORICO):
        return {}
    try:
        with open(HISTORICO, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}


def guardar_vistos(vistos):
    os.makedirs(os.path.dirname(HISTORICO), exist_ok=True)
    with open(HISTORICO, "w", encoding="utf-8") as fh:
        json.dump(vistos, fh)


def ajustar_modelo(ic):
    """Re-estima el hedónico de vivienda por segmento sobre la base actual."""
    base = ic[~ic.es_proyecto & ~ic.condicion.str.contains("llave")].copy()
    base["segmento"] = base.departamento.map(modelo.segmento_de)
    base = base[base.tipo != "Terreno"]
    ajustes = {}
    for seg, g in base.groupby("segmento"):
        if len(g) < config.MIN_COMPARABLES_SEGMENTO:
            continue
        g = g[modelo.winsorizar(g.precio_m2, config.WINSOR)].copy()
        g["barrio_fe"] = modelo.barrios_fe(g)
        g = modelo.acotar_regresores(g)
        try:
            a = smf.ols(
                "np.log(precio_usd) ~ np.log(m2) + np.log1p(m2_terreno) + "
                "falta_terreno + dormitorios + banos + garaje + piso + "
                "antiguedad + vista_mar + falta_antiguedad + falta_piso + "
                "C(tipo) + C(barrio_fe)", data=g).fit()
        except Exception:
            continue
        if a.rsquared >= config.MIN_R2:
            ajustes[seg] = (a, set(g.barrio_fe.unique()), g)
    return ajustes


def puntuar(nuevos, ic, ajustes):
    """Precio esperado para los avisos de MELI.

    MELI publica menos atributos que InfoCasas: no trae garaje, piso,
    antigüedad ni terreno. Se imputan igual que en clean.py y se activan los
    indicadores de faltante, que es justamente para lo que existen. La
    predicción es por eso más incierta que la de un aviso de InfoCasas.
    """
    if nuevos.empty:
        return nuevos
    med = ic[ic.tipo != "Terreno"]
    filas = []
    for _, r in nuevos.iterrows():
        seg = modelo.segmento_de(r.departamento_ic)
        if seg not in ajustes:
            continue
        ajuste, barrios, g = ajustes[seg]
        zona = f"{r.departamento_ic} · {r.barrio_ic}"
        fe = zona if zona in barrios else f"otros · {r.departamento_ic}"
        fila = {
            "m2": r.m2, "m2_terreno": 0.0, "falta_terreno": 1,
            "dormitorios": r.dormitorios if pd.notna(r.dormitorios) else med.dormitorios.median(),
            "banos": r.banos if pd.notna(r.banos) else med.banos.median(),
            "garaje": med.garaje.median(), "piso": med.piso.median(),
            "antiguedad": med.antiguedad.median(),
            "vista_mar": False, "falta_antiguedad": 1, "falta_piso": 1,
            "tipo": r.tipo_ic, "barrio_fe": fe, "precio_usd": r.precio_usd,
        }
        try:
            esperado = float(np.exp(ajuste.predict(pd.DataFrame([fila]))[0]))
        except Exception:
            continue
        filas.append({**r.to_dict(), "precio_esperado": esperado,
                      "precio_m2_esperado": esperado / r.m2,
                      "brecha_pct": (r.precio_usd / esperado - 1) * 100,
                      "zona_estimada": fe, "segmento": seg})
    return pd.DataFrame(filas)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--todos", action="store_true",
                    help="no filtrar por 'publicado hoy/esta semana'")
    ap.add_argument("--objetivo", type=int, default=1200)
    args = ap.parse_args()

    limpio = os.path.join(AQUI, config.CLEAN)
    if not os.path.exists(limpio):
        sys.exit("Falta data/clean/avisos.parquet. Corré clean.py primero.")
    ic = pd.read_parquet(limpio)

    print(f"1) Leyendo MercadoLibre ({len(meli_web.BUSQUEDAS)} búsquedas)")
    filas = meli_web.recolectar(args.objetivo)
    if not filas:
        sys.exit("\nNo se obtuvo nada. La sesión probablemente caducó:\n"
                 "  python3 meli_sesion.py")
    m = pd.DataFrame(filas)
    print(f"   {len(m):,} avisos leídos")

    # --- novedades ---------------------------------------------------------
    m["nuevo"] = m.texto.str.contains("PUBLICADO HOY|PUBLICADO ESTA SEMANA",
                                      case=False, na=False)
    vistos = ya_vistos()
    m["ya_visto"] = m.id.isin(vistos)
    cand = m if args.todos else m[m.nuevo]
    es_alq = cand.get("es_alquiler", pd.Series(False, index=cand.index)).fillna(False)
    es_alq = es_alq | cand.url.str.contains("alquiler", case=False, na=False)
    # Un precio de venta por debajo del piso del pipeline es, casi siempre, un
    # alquiler mal clasificado.
    es_alq = es_alq | (cand.precio_usd < config.PRECIO_MIN_USD)
    # Mismo detector de condiciones que usa clean.py con InfoCasas: así "estrena
    # 2028" o "financiado en 65 cuotas" se descartan igual en las dos fuentes.
    cand = cand.copy()
    cand["condicion"] = (cand.titulo.fillna("") + " " + cand.texto.fillna("")
                         ).map(clean.condicion_de)
    especial = cand.condicion.str.contains("pozo|llave|financiado", na=False)
    # Mismos rangos de superficie que clean.py: un "1 m²" mal parseado produce
    # una brecha de +2.600 % que no significa nada.
    tipo_prov = np.where(cand._busqueda.str.contains("/casas/"), "Casa", "Apartamento")
    lim = pd.Series(tipo_prov, index=cand.index).map(
        lambda t: config.M2_RANGO.get(t, config.M2_RANGO_POR_DEFECTO))
    m2_ok = cand.m2.ge(lim.str[0]) & cand.m2.le(lim.str[1])
    cand = cand[~cand.ya_visto & cand.precio_usd.notna() & cand.m2.notna()
                & ~cand.es_proyecto & ~es_alq & ~especial & m2_ok]
    print(f"2) {int(m.nuevo.sum())} marcados como recientes · "
          f"{len(cand)} candidatos tras descartar vistos y proyectos")

    # --- descartar lo que InfoCasas ya tiene -------------------------------
    ick = ic[ic.precio_usd.notna() & ic.m2.notna()]
    por_precio = {}
    for p, s in zip(ick.precio_usd.round(0), ick.m2.round(0)):
        por_precio.setdefault(p, []).append(s)

    def solapa(p, s):
        return any(abs(x - s) <= 3 for x in por_precio.get(round(p), []))

    cand = cand[~cand.apply(lambda r: solapa(r.precio_usd, r.m2), axis=1)]
    print(f"3) {len(cand)} no están en InfoCasas")

    if cand.empty:
        guardar_vistos({**vistos, **{i: str(date.today()) for i in m.id}})
        print("\nSin novedades esta corrida.")
        return

    # --- normalizar y puntuar ----------------------------------------------
    cand = cand.copy()
    cand["barrio_ic"] = cand.direccion.map(barrio_de)
    # Sólo sirve si InfoCasas conoce ese barrio; si no, el efecto fijo no existe
    # y la predicción sería inventada.
    conocidos = set(ic.barrio.dropna().unique())
    cand["barrio_ic"] = cand.barrio_ic.where(cand.barrio_ic.isin(conocidos),
                                            "(barrio no identificado)")
    cand["departamento_ic"] = np.where(
        cand._busqueda.str.contains("maldonado"), "maldonado",
        np.where(cand._busqueda.str.contains("canelones"), "canelones", "montevideo"))
    cand["tipo_ic"] = np.where(cand._busqueda.str.contains("/casas/"),
                               "Casa", "Apartamento")
    print("4) Puntuando con el hedónico")
    res = puntuar(cand, ic, ajustar_modelo(ic))
    if res.empty:
        print("   ninguno pudo puntuarse (zona sin modelo)")
        guardar_vistos({**vistos, **{i: str(date.today()) for i in m.id}})
        return

    res = res.sort_values("brecha_pct")
    res["capturado"] = str(date.today())
    os.makedirs(os.path.dirname(NOVEDADES), exist_ok=True)
    if os.path.exists(NOVEDADES):
        previo = pd.read_parquet(NOVEDADES)
        res = pd.concat([previo, res], ignore_index=True).drop_duplicates("id", keep="last")
    res.to_parquet(NOVEDADES, index=False)
    guardar_vistos({**vistos, **{i: str(date.today()) for i in m.id}})

    hoy = res[res.capturado == str(date.today())]
    print(f"\n{len(hoy)} novedades · las más baratas respecto del modelo:\n")
    for _, r in hoy.head(12).iterrows():
        print(f"  {r.brecha_pct:+6.1f}% | USD {r.precio_usd:>9,.0f} | {r.m2:>4.0f} m² | "
              f"{r.precio_m2_esperado:>6,.0f} esp | {str(r.barrio_ic)[:16]:16} | "
              f"{str(r.titulo)[:34]}")
        print(f"           {r.url}")
    print(f"\n→ {NOVEDADES}")


if __name__ == "__main__":
    main()
