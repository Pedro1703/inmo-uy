#!/usr/bin/env python3
"""Abre un navegador para que resuelvas la verificación de MercadoLibre una vez.

La sesión queda guardada en data/meli/perfil/ y la reutiliza el scraper. No toca
tu perfil de Chrome ni necesita tu contraseña: lo que hagas en esta ventana es
lo único que se guarda.

    python3 meli_sesion.py
"""
import os, sys
from playwright.sync_api import sync_playwright

AQUI = os.path.dirname(os.path.abspath(__file__))
PERFIL = os.path.join(AQUI, "data", "meli", "perfil")
URL = "https://inmuebles.mercadolibre.com.uy/apartamentos/venta/montevideo/"
ESPERA = int(sys.argv[1]) if len(sys.argv) > 1 else 240

os.makedirs(PERFIL, exist_ok=True)
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PERFIL, headless=False, locale="es-UY",
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
        user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"))
    ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
    print(f"Ventana abierta. Tenés {ESPERA}s para resolver la verificación.")
    print("Cuando veas el listado de apartamentos, dejala así y esperá.\n")
    for i in range(ESPERA // 5):
        pg.wait_for_timeout(5000)
        n = pg.locator("li.ui-search-layout__item, .poly-card").count()
        if n:
            print(f"✓ Listado visible: {n} avisos en pantalla. Sesión guardada.")
            pg.wait_for_timeout(2000)
            ctx.close()
            sys.exit(0)
        if i % 6 == 0:
            print(f"  esperando… ({pg.url[:70]})")
    print("✗ No se llegó a ver el listado. La sesión igual quedó guardada.")
    ctx.close()
