#!/usr/bin/env python3
"""Recolecta el stock en venta de InfoCasas hacia data/raw/avisos.jsonl.

La corrida nacional completa son ~3.200 páginas (~1,8 h), así que el proceso es
reanudable: cada aviso se persiste en el momento y los ids ya vistos se releen
al arrancar. Cortar con Ctrl-C y volver a lanzar retoma donde quedó.

    python3 collect.py                                    # todo el país
    python3 collect.py --departamentos montevideo --tipos apartamentos --max-paginas 3
"""

import argparse
import json
import os
import sys
from datetime import date

import config
from sources import infocasas

AQUI = os.path.dirname(os.path.abspath(__file__))


def ruta(rel):
    return os.path.join(AQUI, rel)


def ids_existentes(archivo):
    """Ids ya persistidos, para no volver a escribirlos al reanudar."""
    vistos = set()
    if not os.path.exists(archivo):
        return vistos
    with open(archivo, encoding="utf-8") as fh:
        for linea in fh:
            try:
                vistos.add(json.loads(linea)["id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return vistos


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--departamentos", nargs="+", default=config.DEPARTAMENTOS)
    ap.add_argument("--tipos", nargs="+", default=config.TIPOS)
    ap.add_argument("--max-paginas", type=int, default=None,
                    help="tope de páginas por búsqueda (para corridas de humo)")
    ap.add_argument("--salida", default=config.RAW)
    args = ap.parse_args()

    salida = ruta(args.salida)
    os.makedirs(os.path.dirname(salida), exist_ok=True)

    vistos = ids_existentes(salida)
    if vistos:
        print(f"Reanudando: {len(vistos):,} avisos ya recolectados.\n")

    sesion = infocasas._sesion()
    hoy = date.today().isoformat()
    nuevos = total_paginas = 0

    try:
        with open(salida, "a", encoding="utf-8") as fh:
            for depto in args.departamentos:
                for tipo in args.tipos:
                    etiqueta = f"{tipo}/{depto}"
                    agregados = ultima_pag = 0
                    for aviso, pagina, limite in infocasas.recorrer(
                        sesion, config.OPERACION, tipo, depto, args.max_paginas
                    ):
                        if pagina != ultima_pag:
                            ultima_pag = pagina
                            total_paginas += 1
                            print(f"\r  {etiqueta:34} pág {pagina:>4}/{limite:<4} "
                                  f"nuevos {agregados:>6}", end="", flush=True)
                        aid = aviso.get("id")
                        if aid is None or aid in vistos:
                            continue
                        vistos.add(aid)
                        aviso["_departamento"] = depto
                        aviso["_tipo_busqueda"] = tipo
                        aviso["_capturado"] = hoy
                        fh.write(json.dumps(aviso, ensure_ascii=False) + "\n")
                        agregados += 1
                        nuevos += 1
                    fh.flush()
                    if ultima_pag:
                        print(f"\r  {etiqueta:34} pág {ultima_pag:>4}/{ultima_pag:<4} "
                              f"nuevos {agregados:>6}")
                    else:
                        print(f"  {etiqueta:34} sin resultados")
    except KeyboardInterrupt:
        print("\n\nInterrumpido. Volvé a lanzar el comando para retomar.")

    print(f"\n{nuevos:,} avisos nuevos · {total_paginas:,} páginas · "
          f"{len(vistos):,} en total")
    print(f"→ {salida}")


if __name__ == "__main__":
    sys.exit(main())
