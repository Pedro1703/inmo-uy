#!/usr/bin/env python3
"""Modelo hedónico de precios -> data/clean/scored.parquet.

La idea central: el precio por m² solo no distingue una oportunidad de un
apartamento chico, viejo y sin garaje. Así que se estima cuánto *debería* valer
cada inmueble dados sus propios atributos y su ubicación, y se mira la brecha.

    log(precio) ~ log(m2) + dormitorios + baños + garaje + piso + antigüedad
                  + vista al mar + tipo + efectos fijos de barrio

Un residual negativo grande = se pide menos de lo que predicen sus atributos.
Eso es la señal.

Se estima una regresión por segmento de mercado (Montevideo, Maldonado,
Canelones, litoral este) porque la estructura de precios de Punta del Este no
tiene nada que ver con la de Montevideo. Los departamentos del interior no
alcanzan masa crítica y quedan explícitamente sin puntuar.

Viviendas y terrenos se modelan por separado: un terreno no tiene dormitorios.
"""

import json
import os
import sys
import warnings

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

import config

warnings.filterwarnings("ignore")
AQUI = os.path.dirname(os.path.abspath(__file__))

# Diagnósticos de cada ajuste, para que el sitio pueda mostrar cómo se
# calcula el modelo en vez de pedir que se confíe en el número.
DIAGNOSTICOS = {}

REGRESORES_VIVIENDA = [
    # m2 es la superficie EDIFICADA; el padrón entra aparte porque una casa de
    # 95 m² sobre 600 m² de terreno no vale lo mismo que una sobre 200.
    "np.log(m2)", "np.log1p(m2_terreno)", "falta_terreno",
    "dormitorios", "banos", "garaje", "piso", "antiguedad",
    "vista_mar", "falta_antiguedad", "falta_piso", "C(tipo)", "C(barrio_fe)",
]
REGRESORES_TERRENO = ["np.log(m2)", "C(barrio_fe)"]


def ruta(rel):
    return os.path.join(AQUI, rel)


def segmento_de(depto):
    for nombre, deptos in config.SEGMENTOS.items():
        if depto in deptos:
            return nombre
    return "interior"


def winsorizar(s, limites):
    lo, hi = s.quantile(limites[0]), s.quantile(limites[1])
    return s.between(lo, hi)


def barrios_fe(df):
    """Barrios con masa crítica mantienen efecto fijo propio; el resto se
    agrupa por departamento para no estimar un coeficiente sobre 3 avisos."""
    conteo = df.zona.value_counts()
    grandes = set(conteo[conteo >= config.MIN_COMPARABLES_BARRIO].index)
    return df.zona.where(df.zona.isin(grandes), "otros · " + df.departamento)


def acotar_regresores(df):
    """Los regresores lineales extrapolan pésimo en los extremos: una casa de
    450 m² con 6 dormitorios y 5 baños acumula un premium lineal que el modelo
    nunca observó, y termina con un precio esperado disparatado que la corona
    falsamente como la mejor oportunidad del ranking. Se acotan al p99."""
    for c in ["dormitorios", "banos", "garaje", "piso", "antiguedad"]:
        if c in df.columns:
            tope = df[c].quantile(0.99)
            if np.isfinite(tope):
                df[c] = df[c].clip(upper=tope)
    return df


def estimar(df, regresores, etiqueta):
    """Ajusta el hedónico y devuelve el df con residuales, o None si no da."""
    if len(df) < config.MIN_COMPARABLES_SEGMENTO:
        print(f"  {etiqueta:34} n={len(df):>6,}  sin masa crítica → sin puntuar")
        return None

    df = df.copy()
    df["barrio_fe"] = barrios_fe(df)

    # statsmodels descarta filas con NaN sin avisar, y esas filas quedarían sin
    # score. Mejor hacerlo explícito acá y reportarlo.
    columnas = ["precio_usd", "m2", "barrio_fe"] + [
        c for c in ["dormitorios", "banos", "garaje", "piso", "antiguedad",
                    "vista_mar", "falta_antiguedad", "falta_piso", "tipo"]
        if any(c in r for r in regresores)]
    antes = len(df)
    df = df.dropna(subset=[c for c in columnas if c in df.columns])
    if antes != len(df):
        print(f"  {etiqueta:34} descarta {antes - len(df)} filas con datos faltantes")
    # Un solo barrio efectivo deja C(barrio_fe) sin variación.
    usables = [r for r in regresores
               if r != "C(barrio_fe)" or df.barrio_fe.nunique() > 1]
    usables = [r for r in usables
               if r != "C(tipo)" or df.tipo.nunique() > 1]

    df = acotar_regresores(df)
    # Superficies fuera del rango con soporte: la predicción ahí es
    # extrapolación, y se refleja bajando la confianza del resultado.
    lo, hi = df.m2.quantile([0.01, 0.99])
    df["fuera_de_soporte"] = (~df.m2.between(lo, hi)).astype(int)

    formula = "np.log(precio_usd) ~ " + " + ".join(usables)
    try:
        ajuste = smf.ols(formula, data=df).fit()
    except (ValueError, np.linalg.LinAlgError) as e:
        print(f"  {etiqueta:34} n={len(df):>6,}  no estimable ({e})")
        return None

    if ajuste.rsquared < config.MIN_R2:
        print(f"  {etiqueta:34} n={len(df):>6,}  R²={ajuste.rsquared:.3f} "
              f"< {config.MIN_R2} → sin puntuar (el modelo no explica el precio)")
        return None

    DIAGNOSTICOS[etiqueta] = {
        "n": int(ajuste.nobs),
        "r2": round(float(ajuste.rsquared), 4),
        "barrios": int(df.barrio_fe.nunique()),
        "coeficientes": {
            nombre: round(float(valor), 4)
            for nombre, valor in ajuste.params.items()
            if not nombre.startswith("C(barrio") and nombre != "Intercept"
        },
        "efectos_barrio": {
            nombre.split("[T.")[1].rstrip("]"): round(float(valor), 4)
            for nombre, valor in ajuste.params.items()
            if nombre.startswith("C(barrio")
        },
    }

    df["residual"] = ajuste.resid
    df["precio_esperado"] = np.exp(ajuste.fittedvalues)
    df["precio_m2_esperado"] = df.precio_esperado / df.m2
    # Brecha: cuánto más barato se pide respecto de lo predicho.
    df["brecha_pct"] = (df.precio_usd / df.precio_esperado - 1) * 100
    df["segmento_modelo"] = etiqueta
    df["r2_segmento"] = ajuste.rsquared

    print(f"  {etiqueta:34} n={len(df):>6,}  R²={ajuste.rsquared:.3f}  "
          f"barrios={df.barrio_fe.nunique()}")
    return df


def main():
    entrada = ruta(config.CLEAN)
    if not os.path.exists(entrada):
        sys.exit(f"No existe {entrada}. Corré primero: python3 clean.py")

    df = pd.read_parquet(entrada)
    print(f"Avisos limpios: {len(df):,}")

    # Los proyectos en pozo publican un precio "desde": no son comparables.
    base = df[~df.es_proyecto].copy()
    print(f"Excluidos por ser proyecto en pozo: {len(df) - len(base):,}")

    # Ventas de llave: el precio es del negocio, no del inmueble.
    if "condicion" in base.columns:
        n_llave = base.condicion.str.contains("llave").sum()
        base = base[~base.condicion.str.contains("llave")]
        print(f"Excluidos por ser venta de llave (fondo de comercio): {n_llave:,}")

    base["segmento"] = base.departamento.map(segmento_de)
    base["familia"] = np.where(base.tipo == "Terreno", "terreno", "vivienda")

    # Outliers de precio/m² dentro de cada segmento×familia.
    mantener = (base.groupby(["segmento", "familia"], group_keys=False)
                .precio_m2.apply(lambda s: winsorizar(s, config.WINSOR)))
    n_out = (~mantener).sum()
    base = base[mantener]
    print(f"Excluidos por precio/m² extremo (winsor {config.WINSOR}): {n_out:,}\n")

    print("Estimación por segmento:")
    partes = []
    for (seg, fam), grupo in base.groupby(["segmento", "familia"]):
        if seg == "interior":
            print(f"  {seg + ' · ' + fam:34} n={len(grupo):>6,}  "
                  f"stock insuficiente → sin puntuar")
            continue
        regs = REGRESORES_TERRENO if fam == "terreno" else REGRESORES_VIVIENDA
        res = estimar(grupo, regs, f"{seg} · {fam}")
        if res is not None:
            partes.append(res)

    if not partes:
        sys.exit("\nNingún segmento tuvo datos suficientes. Ampliá la recolección.")

    out = pd.concat(partes, ignore_index=True)

    # Score 0-100 dentro de cada segmento×familia: 100 = mayor descuento
    # respecto de lo que predicen sus atributos.
    out["score"] = (out.groupby(["segmento_modelo"]).residual
                    .rank(pct=True, ascending=False) * 100).round(1)

    # Confianza: cuántos comparables sostienen la estimación de esa zona.
    out["n_comparables"] = out.groupby("barrio_fe").id.transform("size")
    out["confianza"] = np.where(
        out.n_comparables >= 100, "alta",
        np.where(out.n_comparables >= config.MIN_COMPARABLES_BARRIO,
                 "media", "baja"))
    # Una superficie sin soporte invalida la estimación por más comparables
    # que tenga el barrio.
    out.loc[out.fuera_de_soporte == 1, "confianza"] = "baja"

    salida = ruta(config.SCORED)
    out.to_parquet(salida, index=False)

    diag = ruta("data/clean/diagnosticos.json")
    with open(diag, "w", encoding="utf-8") as fh:
        json.dump(DIAGNOSTICOS, fh, ensure_ascii=False, indent=1)

    print(f"\nAvisos puntuados: {len(out):,}")
    print("\nTop 10 oportunidades (score más alto):")
    cols = ["score", "brecha_pct", "precio_usd", "m2", "precio_m2",
            "precio_m2_esperado", "zona", "confianza"]
    top = out.nlargest(10, "score")[cols + ["titulo"]]
    for _, r in top.iterrows():
        print(f"  {r.score:5.1f} | {r.brecha_pct:+6.1f}% | "
              f"USD {r.precio_usd:>9,.0f} | {r.m2:>5.0f} m² | "
              f"{r.precio_m2:>6,.0f} vs {r.precio_m2_esperado:>6,.0f} esp | "
              f"{r.confianza:5} | {str(r.zona)[:34]}")
    print(f"\n→ {salida}")


if __name__ == "__main__":
    main()
