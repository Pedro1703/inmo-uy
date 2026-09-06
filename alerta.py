#!/usr/bin/env python3
"""Alerta semanal por email con las mejores oportunidades nuevas.

Compara la corrida de hoy contra data/historico_ids.json para quedarse sólo con
lo que apareció desde la última vez. Sin eso el email repetiría todas las
semanas los mismos avisos y dejarías de abrirlo.

    python3 alerta.py --prueba    # arma el email y lo guarda, no lo envía
    python3 alerta.py             # envía

Credenciales por variables de entorno (en GitHub van como secrets):
    GMAIL_USER            casilla desde la que sale el mail
    GMAIL_APP_PASSWORD    contraseña de aplicación de Google (16 caracteres)
    ALERTA_DESTINO        a quién se le manda (por defecto, GMAIL_USER)
"""

import argparse
import json
import os
import smtplib
import ssl
import sys
from datetime import date
from email.message import EmailMessage

import pandas as pd

import config

AQUI = os.path.dirname(os.path.abspath(__file__))
HISTORICO = os.path.join(AQUI, "data", "historico_ids.json")
SITIO = "https://pedro1703.github.io/inmo-uy/"

# Qué se considera digno de un email. El límite de abajo es tan importante como
# el de arriba: una brecha de −75 % casi nunca es una ganga, es un m² mal
# tipeado, un precio en pesos publicado como dólares, o una casa cuyo "m2"
# repite la superficie del padrón. Mandar esos avisos todas las semanas es la
# forma más rápida de que dejes de abrir el email.
BRECHA_RANGO = (-50.0, -15.0)
TOPE_EMAIL = 12


def cargar_historico():
    if not os.path.exists(HISTORICO):
        return None
    try:
        with open(HISTORICO, encoding="utf-8") as fh:
            return set(json.load(fh))
    except (json.JSONDecodeError, OSError):
        return None


def guardar_historico(ids):
    os.makedirs(os.path.dirname(HISTORICO), exist_ok=True)
    with open(HISTORICO, "w", encoding="utf-8") as fh:
        json.dump(sorted(int(i) for i in ids), fh)


def seleccionar(scored, previos):
    """Novedades que valen un email. Devuelve (seleccion, es_primera_vez)."""
    buenos = scored[
        (scored.confianza == "alta")
        & (scored.condicion_especial == 0)
        & scored.brecha_pct.between(*BRECHA_RANGO)
    ]
    if previos is None:            # primera corrida: no hay con qué comparar
        return buenos.nsmallest(TOPE_EMAIL, "brecha_pct"), True
    nuevos = buenos[~buenos.id.isin(previos)]
    return nuevos.nsmallest(TOPE_EMAIL, "brecha_pct"), False


def html_email(sel, scored, primera):
    hoy = date.today().isoformat()
    intro = ("Primera corrida: te mando las mejores oportunidades del momento. "
             "Desde la semana que viene vas a recibir sólo lo que aparezca nuevo."
             if primera else
             "Estas son las oportunidades que <strong>aparecieron esta semana</strong> "
             "y no estaban antes.")

    if sel.empty:
        cuerpo = """<p style="font-size:15px">Esta semana no apareció ninguna
        oportunidad que cumpla los criterios (descuento de entre 15 % y 50 %
        respecto del modelo, barrio con comparables suficientes y sin
        condiciones especiales).</p>
        <p style="font-size:15px">El robot corrió bien: simplemente no hubo nada
        que valga la pena mostrarte.</p>"""
    else:
        filas = []
        for _, r in sel.iterrows():
            img = r.imagen if isinstance(r.imagen, str) and r.imagen.startswith("http") else ""
            filas.append(f"""
<tr><td style="padding:0 0 16px">
  <table width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #e2e2de;border-radius:10px;overflow:hidden">
    <tr>
      <td width="150" valign="top">
        <a href="{r.url}"><img src="{img}" width="150" height="115"
           style="display:block;object-fit:cover;background:#eee" alt=""></a></td>
      <td valign="top" style="padding:11px 14px;font-family:-apple-system,Helvetica,Arial,sans-serif">
        <div style="font-size:17px;font-weight:700;color:#1a1a1a">
          USD {r.precio_usd:,.0f}
          <span style="background:#1b8a4b;color:#fff;font-size:12px;padding:2px 8px;
                       border-radius:20px;margin-left:6px">{r.brecha_pct:+.0f}%</span></div>
        <div style="font-size:13px;color:#6b6b6b;margin:3px 0">{r.barrio} · {r.departamento}</div>
        <div style="font-size:13px;color:#1a1a1a">{r.m2:.0f} m² · {r.dormitorios:.0f} dorm ·
          {r.banos:.0f} baños · {r.precio_m2:,.0f} USD/m²
          <span style="color:#6b6b6b">(esperado {r.precio_m2_esperado:,.0f})</span></div>
        <div style="font-size:12px;color:#6b6b6b;margin-top:5px">{str(r.titulo)[:95]}</div>
        <div style="margin-top:8px"><a href="{r.url}"
           style="font-size:13px;color:#1a1a1a;font-weight:600">Ver el aviso →</a></div>
      </td>
    </tr>
  </table></td></tr>""")
        cuerpo = f"""<p style="font-size:15px">{intro}</p>
        <table width="100%" cellpadding="0" cellspacing="0">{''.join(filas)}</table>"""

    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f7f7f5">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f7f7f5;padding:26px 12px">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0"
       style="max-width:620px;background:#fff;border-radius:12px;padding:26px;
              font-family:-apple-system,Helvetica,Arial,sans-serif;color:#1a1a1a">
  <tr><td>
    <h1 style="margin:0 0 4px;font-size:20px">Oportunidades inmobiliarias</h1>
    <div style="color:#6b6b6b;font-size:13px;margin-bottom:18px">{hoy} ·
      {len(scored):,} avisos analizados</div>
    {cuerpo}
    <div style="margin:22px 0 0;padding-top:16px;border-top:1px solid #e2e2de">
      <a href="{SITIO}" style="display:inline-block;background:#1a1a1a;color:#fff;
         padding:10px 18px;border-radius:8px;text-decoration:none;font-size:14px;
         font-weight:600">Ver el tablero completo →</a>
    </div>
    <p style="color:#6b6b6b;font-size:12px;line-height:1.5;margin:18px 0 0">
      Son candidatas a visitar, no decisiones de compra: el modelo ve lo que el
      aviso publica, no el estado de conservación ni la orientación. Una brecha
      muy grande suele indicar un dato mal cargado o algo que el aviso no cuenta.
      Verificá siempre el aviso original.
    </p>
  </td></tr>
</table></td></tr></table></body></html>"""


def enviar(html, asunto):
    usuario = os.environ.get("GMAIL_USER", "").strip()
    clave = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    destino = os.environ.get("ALERTA_DESTINO", "").strip() or usuario
    if not usuario or not clave:
        sys.exit("Faltan GMAIL_USER / GMAIL_APP_PASSWORD.")

    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = usuario
    msg["To"] = destino
    msg.set_content("Este correo necesita un lector con HTML.")
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
        s.login(usuario, clave)
        s.send_message(msg)
    return destino


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prueba", action="store_true",
                    help="guarda el email en un archivo en vez de enviarlo")
    args = ap.parse_args()

    ruta = os.path.join(AQUI, config.SCORED)
    if not os.path.exists(ruta):
        sys.exit("Falta data/clean/scored.parquet. Corré model.py primero.")
    scored = pd.read_parquet(ruta)

    previos = cargar_historico()
    sel, primera = seleccionar(scored, previos)
    print(f"{len(scored):,} avisos analizados · "
          f"{'primera corrida' if primera else f'{len(previos):,} ya conocidos'}")
    print(f"{len(sel)} para el email")

    html = html_email(sel, scored, primera)
    asunto = (f"{len(sel)} oportunidades nuevas · {date.today().isoformat()}"
              if len(sel) else f"Sin novedades · {date.today().isoformat()}")

    if args.prueba:
        salida = os.path.join(AQUI, "data", "alerta_prueba.html")
        with open(salida, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"→ {salida}  (no se envió nada)")
        for _, r in sel.iterrows():
            print(f"  {r.brecha_pct:+6.1f}% | USD {r.precio_usd:>9,.0f} | "
                  f"{r.m2:>4.0f} m² | {r.barrio}")
        return

    destino = enviar(html, asunto)
    guardar_historico(set(scored.id))
    print(f"Enviado a {destino} · histórico actualizado ({len(scored):,} ids)")


if __name__ == "__main__":
    main()
