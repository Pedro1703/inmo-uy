#!/usr/bin/env python3
"""Dashboard de oportunidades inmobiliarias.

    streamlit run app.py
"""

import os

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

import config

AQUI = os.path.dirname(os.path.abspath(__file__))

st.set_page_config(page_title="Oportunidades inmobiliarias · Uruguay",
                   page_icon="🏙️", layout="wide")

st.markdown("""
<style>
  .tarjeta {border:1px solid rgba(128,128,128,.25); border-radius:10px;
            padding:0; overflow:hidden; margin-bottom:14px;}
  .tarjeta img {width:100%; height:180px; object-fit:cover; display:block;}
  .cuerpo {padding:10px 12px 12px;}
  .precio {font-size:1.25rem; font-weight:700; line-height:1.2;}
  .brecha {display:inline-block; padding:2px 8px; border-radius:20px;
           font-size:.78rem; font-weight:700; color:#fff; background:#1b8a4b;}
  .brecha.tibia {background:#b7791f;}
  .zona {font-size:.85rem; opacity:.75; margin:4px 0 2px;}
  .datos {font-size:.82rem; opacity:.9;}
  .titulo {font-size:.83rem; opacity:.7; margin-top:6px;
           display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
           overflow:hidden;}
  .etiqueta {display:inline-block; font-size:.7rem; padding:1px 6px;
             border-radius:4px; background:rgba(200,120,0,.18); margin-top:6px;}
</style>
""", unsafe_allow_html=True)


@st.cache_data
def cargar():
    ruta = os.path.join(AQUI, config.SCORED)
    if not os.path.exists(ruta):
        return None
    return pd.read_parquet(ruta)


@st.cache_data
def cargar_novedades():
    ruta = os.path.join(AQUI, "data", "meli", "novedades.parquet")
    if not os.path.exists(ruta):
        return None
    return pd.read_parquet(ruta)


df = cargar()
if df is None:
    st.error("Todavía no hay datos puntuados.")
    st.code("python3 collect.py\npython3 clean.py\npython3 model.py", language="bash")
    st.stop()

st.title("Oportunidades inmobiliarias · Uruguay")
st.caption(
    "El score compara cada aviso con lo que **sus propios atributos** predicen "
    "(m² edificados, terreno, dormitorios, baños, garaje, piso, antigüedad, "
    "vista y barrio), no con el promedio del barrio. Score alto = se pide menos "
    "de lo esperado."
)

# ------------------------------------------------------------------- filtros
s = st.sidebar
s.header("Filtros")

deptos = sorted(df.departamento.dropna().unique())
sel_depto = s.multiselect("Departamento", deptos,
                          default=["montevideo"] if "montevideo" in deptos else deptos[:1])
d = df[df.departamento.isin(sel_depto)] if sel_depto else df

tipos = sorted(d.tipo.dropna().unique())
sel_tipo = s.multiselect("Tipo", tipos, default=tipos)
if sel_tipo:
    d = d[d.tipo.isin(sel_tipo)]

barrios = sorted(d.barrio.dropna().unique())
sel_barrio = s.multiselect("Barrio", barrios)
if sel_barrio:
    d = d[d.barrio.isin(sel_barrio)]

# --- búsqueda por palabra clave en el texto del aviso ---
s.markdown("**Buscar en el texto del aviso**")
incluir = s.text_input(
    "Debe contener", placeholder="ej: reciclado, azotea",
    help="Busca en título y descripción. Varias palabras separadas por coma = "
         "cualquiera de ellas (O). Separadas por espacio = todas (Y).")
excluir = s.text_input(
    "No debe contener", placeholder="ej: pozo, permuta",
    help="Descarta avisos que mencionen cualquiera de estos términos.")


def texto_de(datos):
    return (datos.titulo.fillna("") + " " + datos.descripcion.fillna("")).str.lower()


if incluir.strip():
    t = texto_de(d)
    if "," in incluir:
        terminos = [x.strip().lower() for x in incluir.split(",") if x.strip()]
        m = pd.Series(False, index=d.index)
        for termino in terminos:
            m |= t.str.contains(termino, regex=False)
    else:
        terminos = [x.strip().lower() for x in incluir.split() if x.strip()]
        m = pd.Series(True, index=d.index)
        for termino in terminos:
            m &= t.str.contains(termino, regex=False)
    d = d[m]

if excluir.strip():
    t = texto_de(d)
    m = pd.Series(False, index=d.index)
    for termino in [x.strip().lower() for x in excluir.replace(",", " ").split() if x.strip()]:
        m |= t.str.contains(termino, regex=False)
    d = d[~m]

if len(d):
    p_min, p_max = int(d.precio_usd.min()), int(d.precio_usd.max())
    if p_min < p_max:
        rango = s.slider("Precio (USD)", p_min, p_max, (p_min, min(p_max, 400_000)),
                         step=5_000, format="%d")
        d = d[d.precio_usd.between(*rango)]

    dorm = s.slider("Dormitorios (mínimo)", 0, 6, 0)
    if dorm:
        d = d[d.dormitorios >= dorm]

score_min = s.slider("Score mínimo", 0, 100, 80)
d = d[d.score >= score_min]

sin_especiales = s.checkbox(
    "Excluir condiciones especiales", value=True,
    help="Avisos con inquilino adentro, saldo con ANV/BHU, en pozo o en remate. "
         "Se abaratan por una razón legítima, no por ser una oportunidad. "
         "Destildá si te interesan (comprar con renta ya instalada, por ejemplo).")
if sin_especiales and "condicion_especial" in d:
    d = d[d.condicion_especial == 0]

conf = s.multiselect("Confianza de la zona", ["alta", "media", "baja"],
                     default=["alta", "media"],
                     help=f"Cantidad de comparables que sostienen la estimación. "
                          f"Alta ≥100, media ≥{config.MIN_COMPARABLES_BARRIO}.")
if conf:
    d = d[d.confianza.isin(conf)]

# ------------------------------------------------------------------ métricas
if d.empty:
    st.warning("Ningún aviso cumple estos filtros. Bajá el score mínimo, "
               "ampliá la zona o revisá las palabras clave.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Oportunidades", f"{len(d):,}")
c2.metric("Precio mediano", f"USD {d.precio_usd.median():,.0f}")
c3.metric("Precio/m² mediano", f"USD {d.precio_m2.median():,.0f}")
c4.metric("Brecha mediana", f"{d.brecha_pct.median():+.1f}%",
          help="Diferencia entre el precio pedido y el que predice el modelo.")

orden = st.radio("Ordenar por", ["Score", "Brecha", "Precio", "Precio/m²"],
                 horizontal=True, label_visibility="collapsed")
clave = {"Score": ("score", False), "Brecha": ("brecha_pct", True),
         "Precio": ("precio_usd", True), "Precio/m²": ("precio_m2", True)}[orden]
d = d.sort_values(clave[0], ascending=clave[1])

feed, mapa_tab, tabla_tab, radar_tab = st.tabs(
    ["Feed", "Mapa", "Tabla", "Novedades ML"])

# ---------------------------------------------------------------------- feed
def tarjeta(r):
    clase = "brecha" if r.brecha_pct <= -20 else "brecha tibia"
    img = r.imagen if isinstance(r.imagen, str) and r.imagen.startswith("http") else ""
    gc = f" · GC {r.gc_usd:,.0f}" if r.gc_usd and r.gc_usd > 0 else ""
    etiqueta = (f'<div class="etiqueta">⚠ {r.condicion}</div>'
                if getattr(r, "condicion", "") else "")
    return f"""
<div class="tarjeta">
  <a href="{r.url}" target="_blank"><img src="{img}" loading="lazy"></a>
  <div class="cuerpo">
    <div class="precio">USD {r.precio_usd:,.0f}
      <span class="{clase}">{r.brecha_pct:+.0f}%</span></div>
    <div class="zona">{r.barrio} · {r.departamento}</div>
    <div class="datos">{r.m2:.0f} m² · {r.dormitorios:.0f} dorm ·
      {r.banos:.0f} baños · {r.precio_m2:,.0f} USD/m²
      <span style="opacity:.6">(esp. {r.precio_m2_esperado:,.0f})</span>{gc}</div>
    <div class="titulo">{str(r.titulo)[:110]}</div>
    {etiqueta}
    <div style="margin-top:8px"><a href="{r.url}" target="_blank">Ver aviso →</a></div>
  </div>
</div>"""


with feed:
    por_pagina = 24
    paginas = max(1, (len(d) - 1) // por_pagina + 1)
    pag = st.number_input(f"Página (de {paginas})", 1, paginas, 1, key="pag_feed")
    trozo = d.iloc[(pag - 1) * por_pagina: pag * por_pagina]
    st.caption(f"Mostrando {len(trozo)} de {len(d):,} · ordenado por {orden.lower()}")
    for i in range(0, len(trozo), 4):
        for col, (_, r) in zip(st.columns(4), trozo.iloc[i:i + 4].iterrows()):
            col.markdown(tarjeta(r), unsafe_allow_html=True)

# ---------------------------------------------------------------------- mapa
with mapa_tab:
    m = d.dropna(subset=["lat", "lon"]).head(4000).copy()
    if "geo_imputada" in m and m.geo_imputada.sum():
        st.caption(
            f"{int(m.geo_imputada.sum())} de {len(m)} avisos no traen coordenada "
            "propia: se ubican en el centro de su barrio. El score no depende de "
            "la coordenada, pero la posición es aproximada.")
    if len(m):
        t = ((m.score - m.score.min()) / max(m.score.max() - m.score.min(), 1e-9))
        m["r"] = (240 * (1 - t)).astype(int)
        m["g"] = (60 + 150 * t).astype(int)
        m["b"] = 70
        m["radio"] = np.clip(m.precio_usd / 3000, 40, 260)
        m["precio_txt"] = m.precio_usd.map(lambda v: f"USD {v:,.0f}")
        m["pm2_txt"] = m.precio_m2.map(lambda v: f"{v:,.0f}")
        m["brecha_txt"] = m.brecha_pct.map(lambda v: f"{v:+.1f}%")

        sel = st.pydeck_chart(
            pdk.Deck(
                map_style=None,
                initial_view_state=pdk.ViewState(
                    latitude=float(m.lat.median()), longitude=float(m.lon.median()),
                    zoom=11),
                layers=[pdk.Layer(
                    "ScatterplotLayer", data=m, id="avisos",
                    get_position="[lon, lat]", get_radius="radio",
                    get_fill_color="[r, g, b, 170]", pickable=True,
                    auto_highlight=True, radius_min_pixels=3)],
                tooltip={"html": "<b>{precio_txt}</b> · {m2} m²<br/>"
                                 "{pm2_txt} USD/m² · {brecha_txt}<br/>"
                                 "{barrio}<br/><i>clic para abrir el aviso</i>"},
            ),
            on_select="rerun", selection_mode="single-object", key="mapa")

        elegidos = (sel.selection.objects.get("avisos", [])
                    if sel and getattr(sel, "selection", None) else [])
        if elegidos:
            o = elegidos[0]
            st.markdown("---")
            izq, der = st.columns([1, 2])
            if o.get("imagen"):
                izq.image(o["imagen"], width="stretch")
            der.markdown(
                f"### USD {o.get('precio_usd', 0):,.0f}\n"
                f"**{o.get('barrio')}** · {o.get('departamento')}  \n"
                f"{o.get('m2', 0):.0f} m² · {o.get('dormitorios', 0):.0f} dorm · "
                f"{o.get('banos', 0):.0f} baños  \n"
                f"{o.get('precio_m2', 0):,.0f} USD/m² "
                f"(esperado {o.get('precio_m2_esperado', 0):,.0f}) · "
                f"**{o.get('brecha_pct', 0):+.1f}%**  \n\n"
                f"{str(o.get('titulo'))[:160]}  \n\n"
                f"[Abrir el aviso en InfoCasas →]({o.get('url')})")
        else:
            st.caption("Hacé clic en un punto para ver el aviso y su link.")

# --------------------------------------------------------------------- tabla
with tabla_tab:
    cols = ["score", "brecha_pct", "precio_usd", "m2", "precio_m2",
            "precio_m2_esperado", "dormitorios", "banos", "garaje",
            "zona", "tipo", "confianza", "condicion", "url", "titulo"]
    tabla = d[cols].head(500)
    st.dataframe(
        tabla, hide_index=True, width="stretch", height=520,
        column_config={
            "score": st.column_config.ProgressColumn("Score", min_value=0,
                                                     max_value=100, format="%.0f"),
            "brecha_pct": st.column_config.NumberColumn("Brecha", format="%+.1f%%"),
            "precio_usd": st.column_config.NumberColumn("Precio USD", format="%d"),
            "m2": st.column_config.NumberColumn("m²", format="%d"),
            "precio_m2": st.column_config.NumberColumn("USD/m²", format="%d"),
            "precio_m2_esperado": st.column_config.NumberColumn("USD/m² esp.",
                                                                format="%d"),
            "dormitorios": st.column_config.NumberColumn("Dorm", format="%d"),
            "banos": st.column_config.NumberColumn("Baños", format="%d"),
            "garaje": st.column_config.NumberColumn("Gar", format="%d"),
            "condicion": st.column_config.TextColumn("Condición"),
            "url": st.column_config.LinkColumn("Aviso", display_text="abrir"),
            "titulo": st.column_config.TextColumn("Título", width="large"),
        })
    st.download_button("Descargar ranking (CSV)",
                       d[cols].to_csv(index=False).encode("utf-8"),
                       "oportunidades.csv", "text/csv")

# ------------------------------------------------------- novedades de MELI
with radar_tab:
    nov = cargar_novedades()
    st.markdown("#### Novedades de MercadoLibre")
    st.caption(
        "Medimos el solapamiento entre los dos portales sobre 1.002 avisos: "
        "**~72 % de MercadoLibre ya está en InfoCasas**, y el aporte incremental "
        "plausible es ~16 %. Casi todo ese 16 % son avisos *publicados hoy o esta "
        "semana*: no es stock distinto, es el mismo mercado con unos días de "
        "ventaja. Por eso MercadoLibre se usa como radar de primicias y no como "
        "segunda fuente.")
    if nov is None or nov.empty:
        st.info("Todavía no hay novedades. Corré: `python3 meli_radar.py`")
    else:
        st.caption(
            "⚠ El precio esperado acá es **menos confiable** que en el resto del "
            "tablero: MercadoLibre no publica garaje, piso, antigüedad ni "
            "terreno, así que esos atributos se imputan.")
        c1, c2, c3 = st.columns(3)
        c1.metric("Novedades acumuladas", f"{len(nov):,}")
        c2.metric("Última corrida", str(nov.capturado.max()))
        c3.metric("Por debajo del modelo", f"{int((nov.brecha_pct < 0).sum()):,}")
        vista = nov.sort_values("brecha_pct")[
            ["brecha_pct", "precio_usd", "m2", "precio_m2_esperado",
             "dormitorios", "banos", "barrio_ic", "capturado", "url", "titulo"]]
        st.dataframe(
            vista, hide_index=True, width="stretch", height=420,
            column_config={
                "brecha_pct": st.column_config.NumberColumn("Brecha", format="%+.1f%%"),
                "precio_usd": st.column_config.NumberColumn("Precio USD", format="%d"),
                "m2": st.column_config.NumberColumn("m²", format="%d"),
                "precio_m2_esperado": st.column_config.NumberColumn("USD/m² esp.", format="%d"),
                "dormitorios": st.column_config.NumberColumn("Dorm", format="%d"),
                "banos": st.column_config.NumberColumn("Baños", format="%d"),
                "barrio_ic": st.column_config.TextColumn("Barrio"),
                "capturado": st.column_config.TextColumn("Detectado"),
                "url": st.column_config.LinkColumn("Aviso", display_text="abrir"),
                "titulo": st.column_config.TextColumn("Título", width="medium"),
            })

st.caption(
    "Las oportunidades son candidatas a visitar, no decisiones de compra: el "
    "modelo ve atributos publicados, no estado de conservación, orientación ni "
    "calidad de construcción. Verificá siempre el aviso original."
)
