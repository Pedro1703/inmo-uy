# Detector de oportunidades inmobiliarias · Uruguay

Recolecta el stock en venta publicado, estima cuánto *debería* valer cada
inmueble dados sus atributos y ubicación, y rankea los que se piden por debajo
de esa estimación.

## Uso

```bash
pip install -r requirements.txt

python3 collect.py      # ~1,8 h para todo el país; reanudable con Ctrl-C
python3 clean.py        # normaliza y deduplica
python3 model.py        # estima el hedónico y puntúa
streamlit run app.py    # dashboard con mapa
```

Para una prueba rápida:

```bash
python3 collect.py --departamentos montevideo --tipos apartamentos --max-paginas 40
```

## Cómo se define una oportunidad

El precio por m² solo no alcanza: un apartamento barato por m² puede ser
simplemente chico, viejo y sin garaje. Así que se estima una regresión hedónica

```
log(precio) ~ log(m2) + dormitorios + baños + garaje + piso + antigüedad
              + vista al mar + tipo + efectos fijos de barrio
```

y se mira el **residual**: la diferencia entre el precio pedido y el que predicen
sus propios atributos. Residual negativo grande = candidato.

El `score` (0-100) es el percentil del residual dentro de su segmento de mercado.
La `brecha_pct` es la misma idea en plata: −25 % significa que se pide un cuarto
menos de lo que el modelo predice.

### Segmentación

Se estima **una regresión por segmento** (Montevideo · Maldonado · Canelones ·
Colonia+Rocha) porque la estructura de precios de Punta del Este no tiene nada
que ver con la de Montevideo. Viviendas y terrenos también van por separado: un
terreno no tiene dormitorios.

Medición del stock real por departamento:

```
maldonado    32.276      colonia    1.788       paysandu   170      florida    74
montevideo   26.685      rocha        743       lavalleja  125      san-jose   61
canelones     4.748                             resto      <50 c/u
```

El 97 % del mercado publicado está en cinco departamentos. **El interior no se
puntúa**: con 20-170 avisos no hay comparables para estimar nada, y es preferible
decirlo que devolver un número inventado. Lo mismo dentro de cada segmento: los
barrios con menos de 30 avisos caen al efecto fijo del departamento, y cada
resultado trae un campo `confianza` según cuántos comparables lo sostienen.

## Fuentes

| Fuente | Estado |
|---|---|
| InfoCasas | Activa. Next.js con `__NEXT_DATA__`: los avisos vienen serializados en el HTML, con coordenadas y superficie. |
| MercadoLibre | **No viable.** Probado con credenciales reales: el token funciona (`/users/me` responde 200) pero `/sites/MLU/search` devuelve 403, y listar items de otro vendedor está explícitamente restringido. Cerraron la búsqueda del catálogo. Diagnóstico completo en `sources/mercadolibre.py`. |
| Gallito | No viable. Detrás de un challenge de Cloudflare. |

### MercadoLibre como radar de novedades

Aunque no sirve como segunda fuente de stock, sí sirve para una cosa. Medimos el
solapamiento sobre 1.002 avisos suyos:

| Criterio | Solapan |
|---|---|
| Precio y m² idénticos | 51,3 % |
| Precio exacto, m² ±3 | **72,2 %** |
| Sólo precio exacto (cota superior) | 87,7 % |

Estable entre segmentos: Montevideo 72,1 %, Maldonado 72,5 %. De los 208 no
solapados, 48 son proyectos en pozo y 40 tienen datos dudosos, así que el aporte
incremental plausible es **~16 %** — y esa cifra es todavía una cota superior,
porque incluye avisos que están en ambos portales con el precio actualizado en
uno solo. Casi todos esos avisos dicen "publicado hoy / esta semana": no es
stock distinto, es el mismo mercado con unos días de ventaja.

Por eso MercadoLibre se usa como **radar de primicias**, no como segunda fuente:

```bash
python3 meli_sesion.py     # una vez: resolvés la verificación a mano
python3 meli_radar.py      # semanal: novedades no presentes en InfoCasas
```

Dos límites del sitio que condicionan el diseño: la **paginación está capada**
(`_Desde_49` devuelve los mismos 48 resultados, así que la variedad se consigue
con 51 búsquedas por barrio y rango de precio), y la **sesión hay que
revalidarla a mano** cada tanto con la verificación de MercadoLibre.

El precio esperado de estas novedades es menos confiable que el del resto del
tablero: MercadoLibre no publica garaje, piso, antigüedad ni terreno, así que se
imputan. Sirve para ordenar a quién mirar primero, no para comparar contra el
score de InfoCasas.

La recolección respeta `robots.txt` (las rutas de listado están permitidas), usa
gzip y espera 1 s entre requests.

## Limpieza que no es opcional

- **Republicaciones.** El mismo inmueble listado por varias inmobiliarias. Se
  deduplica por `(coordenada, m², precio)`; sin esto el ranking muestra tres
  veces la misma oportunidad.
- **Proyectos en pozo.** Publican un precio "desde" que no compara con usados:
  se marcan y se excluyen de la estimación.
- **Faltantes.** `antiguedad` falta en ~80 % de los avisos y `piso` en ~30 %.
  Se imputan y se agrega un indicador de faltante, en vez de descartar la fila y
  perder la mayor parte de la muestra.
- **Outliers.** Winsorización de precio/m² al 1 %/99 % dentro de cada segmento,
  para que un error de tipeo no arrastre la estimación.

## Por qué un descuento grande casi nunca es una ganga

La primera auditoría del ranking fue reveladora: el top no eran oportunidades,
eran propiedades con una condición que las abarata **legítimamente**.

| Señal en el aviso | Qué significa |
|---|---|
| "con renta", "con inquilino", "nuda propiedad" | Se vende con el inquilino adentro: el comprador no puede ocuparla |
| "+ ANV", "+ BHU", "saldo", "en cuotas" | El precio publicado no incluye una deuda que el comprador asume |
| "en pozo", "preventa", "entrega 2027" | Precio "desde", sobre algo que todavía no existe |
| "remate", "judicial", "sucesión" | Título con complicaciones |
| "venta de llave", "fondo de comercio" | Se vende el negocio, no el inmueble — se excluye del modelo |

`clean.py` las detecta y las etiqueta en la columna `condicion`. **No las
descarta**: comprar con renta ya instalada puede ser exactamente lo que busca un
inversor. El dashboard las excluye por defecto y se pueden volver a incluir con
un checkbox.

### Superficie edificada ≠ superficie del padrón

El error más caro del proyecto. En las casas, el campo `m2` normalmente repite
`m2Terrain`:

```
Carrasco Norte   m2=617   m2Built=95    m2Terrain=617
Punta Colorada   m2=625   m2Built=140   m2Terrain=625
```

Calcular el precio/m² sobre eso daba "287 USD/m² en Carrasco Norte" y llenaba el
ranking de casas con parque grande. Peor: metía en la misma regresión casas
medidas por terreno contra casas medidas por construcción. Ahora `m2` es la
superficie **edificada** y el padrón entra como regresor propio
(`np.log1p(m2_terreno)`), que es como se forma el precio de verdad. El R² de
Montevideo subió de 0,811 a 0,833 y el de Maldonado de 0,782 a 0,825.

### Los terrenos no se puntúan

Sus regresiones dan R² de 0,15 a 0,55: el precio de un terreno depende de
padrón, servicios, zonificación y metros de frente, y nada de eso está en el
aviso. Se recolectan y se muestran, pero sin score — `config.MIN_R2` corta
cualquier segmento que no explique el precio, en vez de vender ruido como señal.

Dos correcciones más que salieron de mirar los resultados:

- **Extrapolación.** Dormitorios y baños entran linealmente, así que una casa de
  450 m² con 6 dormitorios acumulaba un premium que el modelo nunca observó y
  aparecía primera con un "esperado" de 8.253 USD/m². Los regresores se acotan
  al p99 y las superficies fuera de soporte bajan a confianza `baja`.
- **Ceros que no son ceros.** `dormitorios = 0` significa "no informado", no un
  apartamento sin dormitorios. Tomarlo literal hundía el precio esperado.

## Archivos

```
config.py                 parámetros: departamentos, umbrales, segmentos, tipo de cambio
collect.py                scraping reanudable        -> data/raw/avisos.jsonl
meli_sesion.py            abre el navegador para validar la sesión de MercadoLibre
meli_radar.py             radar semanal de novedades -> data/meli/novedades.parquet
sources/meli_web.py       lectura del listado de MercadoLibre (Playwright)
clean.py                  normalización y dedup      -> data/clean/avisos.parquet
model.py                  hedónico y scoring         -> data/clean/scored.parquet
app.py                    dashboard Streamlit
sources/infocasas.py      adaptador activo
sources/mercadolibre.py   adaptador preparado, inactivo
```

## Advertencia

Las oportunidades son **candidatas a visitar**, no decisiones de compra. El
modelo ve lo que el aviso publica: no ve estado de conservación, orientación,
calidad de construcción, ni si el precio ya tiene una reserva. Un residual muy
negativo también puede ser un error de carga (m² mal tipeado, precio en pesos
publicado como dólares). Verificá siempre el aviso original.
