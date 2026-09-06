"""Parámetros del detector de oportunidades inmobiliarias en Uruguay."""

# ---------------------------------------------------------------- recolección
BASE = "https://www.infocasas.com.uy"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Segundos entre requests. El sitio no declara crawl-delay en robots.txt;
# 1 s es deliberadamente conservador.
DELAY = 1.0
TIMEOUT = 30
REINTENTOS = 4

OPERACION = "venta"
TIPOS = ["apartamentos", "casas", "terrenos"]

# Ordenados por volumen de stock real (medido, no supuesto). Los últimos
# aportan ~700 avisos entre todos: se recolectan igual, pero el modelo no
# los puntúa (ver SEGMENTOS y MIN_COMPARABLES_*).
DEPARTAMENTOS = [
    "montevideo", "maldonado", "canelones", "colonia", "rocha",
    "paysandu", "lavalleja", "florida", "san-jose", "salto",
    "tacuarembo", "soriano", "rivera", "durazno", "cerro-largo",
    "artigas", "flores", "rio-negro", "treinta-y-tres",
]

# --------------------------------------------------------------- normalización
# Tipo de cambio para pasar gastos comunes (publicados en $) a USD.
UYU_POR_USD = 40.0

# Rangos de superficie válidos por tipo. Un solo rango global no sirve: 1.000 m²
# es un error de carga en un apartamento y una chacra chica en un terreno.
M2_RANGO = {
    "Apartamento": (15, 800),
    "Casa": (30, 5_000),
    "Terreno": (50, 100_000),
    "Chacra o Campo": (200, 1_000_000),
    "Local Comercial": (10, 5_000),
    "Oficina": (10, 3_000),
}
M2_RANGO_POR_DEFECTO = (15, 10_000)
PRECIO_MIN_USD, PRECIO_MAX_USD = 20_000, 5_000_000

# Winsorización de precio/m² antes de estimar.
WINSOR = (0.01, 0.99)

# --------------------------------------------------------------------- modelo
# Mercados con dinámica de precios propia: se estima una regresión por segmento.
# Punta del Este y Montevideo no comparten estructura de precios.
SEGMENTOS = {
    "montevideo": ["montevideo"],
    "maldonado":  ["maldonado"],
    "canelones":  ["canelones"],
    "litoral_este": ["colonia", "rocha"],
}

# Un barrio necesita esta masa crítica para tener efecto fijo propio;
# por debajo cae al efecto fijo del departamento.
MIN_COMPARABLES_BARRIO = 30
# Un segmento necesita esto para ser modelado. El interior no llega: se marca
# como no puntuable en vez de producir scores inventados.
MIN_COMPARABLES_SEGMENTO = 200

# R² mínimo para que un segmento sea puntuable. Los terrenos rondan 0,15-0,45:
# su precio depende de padrón, servicios, zonificación y frente, nada de lo cual
# publica el aviso. Puntuarlos igual sería vender ruido como señal, así que se
# recolectan y se muestran, pero sin score.
MIN_R2 = 0.55

# ------------------------------------------------------------------- archivos
RAW = "data/raw/avisos.jsonl"
CLEAN = "data/clean/avisos.parquet"
SCORED = "data/clean/scored.parquet"

# --------------------------------------------------------------- mercadolibre
# Las credenciales se leen de un archivo .env local que NO se versiona (está en
# .gitignore). Nunca escribirlas acá: config.py sí va al repo.
import os as _os


def _cargar_env():
    ruta = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".env")
    if not _os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if linea and not linea.startswith("#") and "=" in linea:
                clave, valor = linea.split("=", 1)
                _os.environ.setdefault(clave.strip(), valor.strip())


_cargar_env()

MELI_CLIENT_ID = _os.environ.get("MELI_CLIENT_ID", "")
MELI_CLIENT_SECRET = _os.environ.get("MELI_CLIENT_SECRET", "")
MELI_ACTIVO = bool(MELI_CLIENT_ID and MELI_CLIENT_SECRET)
