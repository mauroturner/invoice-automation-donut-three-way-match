# Ficha del modelo: facturas que se cargan solas al ERP

Qué es el modelo, para qué sirve y para qué no, con qué datos, cómo se ajustó y se eligió, cómo se mide y qué no hace. Todo lo que sale de una factura es **sintético**: lo genera el proyecto. Los recibos de CORD son reales. Las cifras son las del resumen sellado de la corrida real del 2026-11-18 (commit `94db289`, sello de SHA-256 `51884acb…2ce2`); `tests/docs/test_docs.py` comprueba que cada veredicto de esta ficha es el del resumen.

## Uso previsto y no previsto

**Uso previsto.** Leer facturas de proveedores con un diseño que el modelo ya vio, anclar la cabecera en el QR, cruzarlas con la orden y el remito, y cargar como borrador las que cierran para que cuentas a pagar revise solo lo que no cierra. El modelo no decide por sí solo: la decisión es una regla explícita de 18 motivos en un orden fijo.

**Uso no previsto.**

- Cargar sin revisión facturas de un diseño nuevo: con un diseño que no vio, la lectura de las líneas cae al 0 % (C03).
- Leer los resultados sintéticos como los de una empresa real: las facturas, las órdenes, los remitos, los proveedores y los CUIT son de un generador propio, y la lectura de un diseño que el generador dibuja no dice nada de una factura escaneada en una oficina.
- Usar el QR generado como si ARCA lo hubiera emitido: sigue el formato de la especificación, pero no pasó por ARCA.
- Confirmar el borrador sin una persona, o cargar una factura sin QR leído y válido (el flujo nunca lo hace solo).
- Leer PDF con texto, de varias páginas o con programa: van a cuarentena.

## Datos

- **CORD** (Park et al., 2019, Clova AI, CC BY 4.0, commit `7f0115a4`): 1.000 recibos reales de comercios de Indonesia, 800 de entrenamiento, 100 de validación y 100 de prueba. No trae proveedor, CUIT, fecha, punto de venta, número ni moneda. La prueba de CORD se abre una sola vez, en el cierre, y el checkpoint publicado ya se había medido sobre ella en la investigación (89,62 %).
- **Facturas sintéticas**: 2.900 en cinco particiones (2.000 de entrenamiento, 200 de validación de proveedores conocidos, 100 de validación de proveedores nuevos y 600 de prueba, 300 de cada cohorte), dibujadas con cinco diseños (L1 a L5) y tres formatos, con 13 clases de discrepancia sembradas (167 de las 600 de la prueba). Los diseños y las fuentes de la validación de proveedores nuevos y de la prueba de proveedores nuevos son distintos entre sí y no están en el ajuste.
- **CUIT**: solo en el rango `30-0000xxxx-d`, el del ejemplo de ARCA; nombres de una lista fija con «(ficticio)»; cada página dice «DOCUMENTO SINTÉTICO — SIN VALIDEZ FISCAL». La prueba sintética se genera después del sello del lanzamiento.

## El ajuste y su elección

Donut (`donut-base-finetuned-cord-v2`, MIT; 201.121.912 parámetros, vocabulario de 57.580 fichas, imagen de 1280 × 960) se ajusta desde el checkpoint de CORD con dos tareas, la de CORD y la de factura, con los 800 recibos y las 2.000 facturas sintéticas por época. Seis épocas con candidatas en 2, 4 y 6, AdamW de 3·10⁻⁵ con calentamiento de 200 pasos, lote 1 con acumulación de 8 y bf16. El ajuste dura 72 min 45 s en la RTX 3090.

La elección se fijó antes (D12): entre las épocas que no pierden más de 2,0 puntos de TED frente al propio checkpoint en la validación de CORD (piso de 92,00 con un checkpoint en 94,00), la de mayor media de las tasas de líneas exactas de las dos validaciones sintéticas. Resultado: la época 2 (TED 87,98) y la 6 (91,98, por 0,02 puntos) quedan fuera; la 4 (92,78) se queda y lee el 97,1 % de las líneas de proveedores conocidos de la validación. En la validación de un diseño nuevo las tres leen el 0 %. El artefacto es un `safetensors` con su SHA-256 y el flujo se niega a leer una carpeta con un `.bin`, `.pt` o `.pkl`.

**Una reparación del lanzamiento.** El primer intento se detuvo con `ModelChoiceError` tras 5.851 s: la métrica `ted_accuracy` leía el orden de las claves del árbol y el checkpoint sin ajustar puntuaba 49,08 contra 93,65 que había medido el planificador, porque el objetivo canónico lleva las claves ordenadas (una lectura idéntica puntuaba 0,45). No había sello ni fila sellada; las épocas candidatas se perdieron con la carpeta de trabajo. La corrección compara los árboles sin el orden de las claves (el orden de una lista sigue contando), con la prueba primero, y una prueba con el modelo real mide el checkpoint en 94,00, a un punto de 93,65 (commit `4c50d45`, D51). La prueba de CORD no se abrió antes de la corrección. Quedan dos asuntos del dueño: **la definición de `ted_accuracy`** y **cómo anotar 94,00 contra 93,65**.

## El QR

Se lee con pyzbar y, solo si no devuelve nada, con OpenCV Aruco. Con códigos sueltos pyzbar acierta el 100 % en las ocho condiciones medidas en la investigación. En las páginas de la prueba, lo que decide es el tamaño impreso y el perfil de escaneo, no el lector:

| Perfil de escaneo | Tamaño impreso | Impresas con QR intacto | Leídas | Tasa |
| --- | --- | --- | --- | --- |
| clean | 2,5 cm | 83 | 83 | 100,0 % |
| clean | 3,0 cm | 64 | 64 | 100,0 % |
| clean | 4,0 cm | 84 | 84 | 100,0 % |
| mild | 2,5 cm | 87 | 67 | 77,0 % |
| mild | 3,0 cm | 80 | 80 | 100,0 % |
| mild | 4,0 cm | 74 | 74 | 100,0 % |
| poor | 2,5 cm | 33 | 0 | 0,0 % |
| poor | 3,0 cm | 46 | 0 | 0,0 % |
| poor | 4,0 cm | 35 | 35 | 100,0 % |

Todas las filas son sintéticas: 487 de 586 (83,1 %), contra el 97 % que pedía C04. El QR de una factura sintética no se envió a ARCA.

## El cruce y sus tolerancias

Dos vías (factura contra orden) y tres vías (además, la cantidad facturada acumulada contra lo recibido hasta el día de la factura). Tolerancias en la configuración y en la huella del sello (D14): 2 % del precio unitario, ninguna cantidad por encima de lo recibido y 0,01 de redondeo por línea; «dentro» incluye el borde. Son un valor por defecto del planificador; los valores son del dueño. El primer motivo que se cumple decide, y sin ninguno se carga como borrador:

| Primer motivo de revisión | Facturas | Parte de las que fueron a revisión |
| --- | --- | --- |
| `extraction_incomplete` | 260 | 57,4 % |
| `qr_not_read` | 113 | 24,9 % |
| `line_arithmetic` | 17 | 3,8 % |
| `line_not_on_po` | 9 | 2,0 % |
| `qr_print_mismatch` | 9 | 2,0 % |
| `qty_over_receipt` | 9 | 2,0 % |
| `currency_mismatch` | 7 | 1,5 % |
| `supplier_mismatch` | 6 | 1,3 % |
| `unsupported_type` | 6 | 1,3 % |
| `date_out_of_range` | 5 | 1,1 % |
| `no_po` | 5 | 1,1 % |
| `total_arithmetic` | 3 | 0,7 % |
| `duplicate` | 2 | 0,4 % |
| `price_out_of_tolerance` | 2 | 0,4 % |

Son 453 facturas sintéticas a revisión y 147 cargadas solas, de las 600 de la prueba. `extraction_incomplete` y `qr_not_read` explican el 82 % de las revisiones: el modelo no leyó un campo obligatorio o ningún lector vio el QR; cuántas son de diseños nuevos no se separó.

## Criterios y resultados

Criterios fijados antes de construir, con lo esperable escrito (C02 a C09 se miden sobre facturas sintéticas): C03 probablemente no se cumplía; C04 y C06, inciertos; el resto, probablemente sí. De los nueve criterios con umbral **se cumplen cuatro (C02, C07, C08 y C09) y no se cumplen cinco (C01, C03, C04, C05 y C06)**. C01 y C05 no se cumplen y se esperaba que sí. C10 es informativo.

| Criterio | Umbral | Lo que prueba el umbral | Veredicto |
| --- | --- | --- | --- |
| C01 El ajuste no olvida CORD | límite inferior del intervalo del 95 % de TED(elegido) − TED(checkpoint) en la prueba de CORD > −2,0 puntos | −6,56 puntos (límite inferior) | En la prueba de CORD (100 recibos), el modelo elegido tiene TED 85,4 y el checkpoint sin ajustar 89,6: la diferencia es −4,2 (intervalo del 95 %, −6,6 a −2,0); el límite inferior debía quedar por encima de −2,0 puntos. No se cumple. |
| C02 Lee bien las líneas de proveedores conocidos | tasa de líneas exactas en test_known ≥ 90 % | 0,959 (sintético) | Sobre 300 facturas sintéticas de proveedores conocidos, el 95,9 % de las líneas se leyó exacto; el umbral era 90 %. |
| C03 Lee bien las líneas de un diseño nuevo | tasa de líneas exactas en test_new ≥ 80 % | 0,000 (sintético) | Sobre 300 facturas sintéticas de proveedores con un diseño nuevo, el 0 % de las líneas se leyó exacto; el umbral era 80 %. No se cumple. |
| C04 Lee el QR | de las facturas impresas con QR intacto, la parte con el texto exacto ≥ 97 % | 0,831 (sintético) | Entre las 586 facturas sintéticas impresas con QR intacto, el lector leyó el texto exacto de 487: 83,1 %; el umbral era 97 %. No se cumple. |
| C05 No carga mal | límite superior del intervalo de Wilson de la tasa de carga mala, en toda la prueba, ≤ 2 % | 0,025 (límite superior de Wilson) (sintético) | Sobre 600 facturas sintéticas, 0 de 147 cargas automáticas fue mala; el límite superior del intervalo de Wilson es 2,5 % y el máximo era 2 %. No se cumple. |
| C06 Se carga sola la mayoría de las facturas de proveedores conocidos | paso directo en test_known ≥ 85 % | 0,697 (sintético) | Sobre las facturas sintéticas limpias de proveedores conocidos, el 69,7 % (147 de 211) se cargó solo y bien; el umbral era 85 %. No se cumple. |
| C07 Manda a revisión lo que no cierra | de las facturas con discrepancia sembrada, la parte con revisión ≥ 99 % | 1,000 (sintético) | De las 167 facturas sintéticas con una discrepancia sembrada, 167 de 167 fueron a revisión (100 %); el umbral era 99 %. |
| C08 El cruce de tres vías evita cargas malas | límite inferior del intervalo del 95 % de (tasa de carga mala de B1 − la del flujo) > 0 | 5,7 puntos (límite inferior, por factura) (sintético) | Sobre las facturas sintéticas de la prueba, B1 (solo el QR en dos vías) cargó mal 46 de las 600 y el flujo 0: por factura, la diferencia es 7,7 puntos (intervalo del 95 %, 5,7 a 10,0); el límite inferior debía ser mayor que 0. Es la cifra sellada; la diferencia de las tasas de carga mala que fija el criterio, por carga, está entre los análisis posteriores al sello (D59). |
| C09 Lo cargado es lo decidido | el 100 % de los borradores leídos de vuelta del ERP coincide campo por campo, y repetir un día no crea ninguna factura nueva | 1,000 (sintético) | 147 de 147 cargas automáticas se leyeron de vuelta del ERP iguales a lo decidido (sintético) y repetir el último día creó 0 facturas nuevas; debían coincidir todas y no crearse ninguna. |
| C10 Variantes y seguimiento (informativo) | sin umbral: que esté escrito y sea consistente | sin umbral | Informativo: sin umbral. Las líneas base, el recuerdo por clase sembrada, la lectura por campo, perfil y tamaño del QR y los 20 días de operación están en el resumen (0 variantes). |

Los intervalos del 95 % de los criterios que tienen uno, con los decimales del resumen:

- C01, intervalo del 95 %, −6,6 a −2,0 puntos de TED (remuestreo pareado por recibo).
- C02, intervalo del 95 %, 94,6 a 96,8 % de las líneas sintéticas exactas (Wilson).
- C03, intervalo del 95 %, 0,0 a 0,3 % de las líneas sintéticas exactas (Wilson).
- C05, intervalo del 95 %, 0,0 a 2,5 % de cargas malas sintéticas (Wilson); con 147 cargas ningún resultado con cero cargas malas lo cumplía y hacían falta al menos 189.
- C08, intervalo del 95 %, 5,7 a 10,0 puntos de diferencia sintética entre B1 y el flujo (remuestreo pareado por factura): la diferencia por factura, como se selló.

**Los análisis posteriores al sello** (D59, D60; `reports/post_seal_analysis.json`), que no reemplazan ninguna cifra sellada:

- **C08 como lo fija el preregistro**, la diferencia de las tasas de carga mala (cargas malas sobre cargas automáticas) de B1 y del flujo: B1 cargó mal 46 de sus 197 cargas sintéticas (23,4 %) y el flujo 0 de 147; la diferencia es de 23,4 puntos, con un intervalo del 95 % de 17,5 a 29,8 (sintético): se cumple también así. El C08 sellado es la diferencia por factura (46 de 600 contra 0 de 600); su frase sellada ponía las tasas por carga al lado de esa diferencia, y el resumen cuenta ahora solo lo sellado.
- **B1 con la orden que leyó el modelo**, como dice D48 (la B1 sellada tomaba la orden de la verdad): 189 cargas y 41 malas (sintético; 197 y 46 la sellada); C08 queda en 6,83 puntos por factura (de 5,00 a 9,00) y en 21,69 por carga (de 16,02 a 27,98). El veredicto no cambia en ninguna forma.

## Limitaciones

- **Datos sintéticos de un generador propio.** Lo que se mide es la lectura de lo que el generador dibuja, con cinco diseños; no hay una sola factura real. Que el flujo lea facturas de una empresa es otra pregunta, que este proyecto no responde.
- **Un solo tipo de comprobante** (factura A): las notas de crédito y los demás tipos van a revisión (`unsupported_type`).
- **CORD es indonesio** y no es de facturas: sirve para medir si el ajuste olvida lo que el checkpoint ya leía, no para medir la lectura de una factura argentina.
- **Una posible fuga del checkpoint.** El checkpoint da 93,65 en la validación de CORD y 89,62 en la prueba, y no se sabe con qué se ajustó; que no vio la prueba de CORD se supone y no se muestra. Por eso la cifra de C01 no es una medida independiente del ajuste.
- **Un diseño nuevo no se lee.** C03: 0 % de las líneas sintéticas exactas. El ajuste no generaliza a un diseño que no vio; con otro conjunto de diseños, otro resultado.
- **C04 cuenta las 12 `qr_tampered` como QR intacto.** «Intacto» quiere decir impreso entero: las `qr_missing` no imprimen ninguno, y una `qr_tampered` imprime un QR entero con otro contenido, que el lector lee exacto. Por eso C04 da 487 de 586 (83,1 %) con ellas y 477 de 574 (83,1 %) sin ellas (sintético): la cifra y el veredicto no cambian, y la tabla de arriba las incluye (A8 de la evaluación, D63).
- **C05 no se pudo cumplir con tan pocas cargas.** Con 147 cargas automáticas sintéticas el intervalo de Wilson no baja de 2,5 %; el flujo no cargó ninguna mal, pero la prueba no alcanza para decir que el máximo sea 2 %.
- **El ERP de la corrida es un sustituto local.** Contra ERPNext v15.124.4 real corrió una prueba de humo (una vez) y encontró cuatro diferencias: el filtro de tabla hija lista un documento por cada fila que coincide (corregido, `0462f72`); un número de factura repetido por omisión crea un segundo borrador y, con la unicidad activada, responde 417 y no 409; un `name` mandado se ignora salvo en el artículo; y hacen falta el grupo del artículo, la fecha requerida y `set_posting_time`. **Abierto:** el número de orden que imprime una factura no es el nombre que ERPNext asigna, así que `mode = http` necesitaría un campo con los números del maestro; no se probó cuál.
- **El tiempo del cierre.** El cierre de la corrida sellada tardó 1.032 s contra 10 minutos (RNF02): no se cumplió. La evaluación midió que el 86 % era la pasada del checkpoint sobre las 600 páginas para B2, de a una página; con la pasada de a ocho páginas, que da los mismos 600 árboles (D61), el cierre de la reconstrucción de R11 sobre una copia de los datos tardó 411 s y reprodujo el sello byte a byte. Las cifras de la corrida sellada son las de su registro; la reconstrucción no vuelve a ajustar.
- **Solo borradores**, y sin el disparador por correo ni el aviso a cuentas a pagar.
- **Los diseños sintéticos no tienen texto en PDF**: un PDF de una página con una imagen es lo que se acepta.

## Tiempos y memoria

Medidos en el equipo de desarrollo (una RTX 3090), en la corrida real:

- El lanzamiento tardó 5.868,4 s (la suma de las tareas de su estado; el límite es 3 h; el reloj de la corrida, unos 12.000 s, incluye el intento que falló) y el ajuste solo, 72 min 45 s.
- Cada corrida diaria de 30 facturas tardó de 49 a 60 s (límite 120 s); por factura, 1,379 s de media y 2,300 s de máximo del p95.
- La prueba de Docker tardó 119 s (límite 15 min).
- **El cierre de la corrida sellada tardó 1.032 s contra el límite de 10 minutos de RNF02: no se cumplió.** `evaluate` tomó 1.024,5 s.
- **Con el código corregido** (la pasada del checkpoint de a ocho páginas, D61), el runbook R11 seguido sobre una copia de los datos el 2026-11-19 (el lanzamiento sin ajuste, las 20 diarias y el cierre, en 26 min 33 s) dio un cierre de 411 s, con `evaluate` en 405,1 s, `state=repeat` y `seal=unchanged`; las 20 diarias, de 51 a 59 s; el pico de `PeakWorkingSetSize` del cierre fue de 2.822,3 MB y el de `torch.cuda.max_memory_allocated` de 6.789,5 MB (los lotes de ocho páginas). `-m real_data` pasó entero (7 pruebas) sobre esa copia.
- Memoria (RNF03): el pico de `PeakWorkingSetSize` del lanzamiento fue de 6.624,3 MB, el de cada corrida diaria entre 1.660 y 2.404 MB y el del cierre de 2.787,1 MB (el límite es 16 GB de ese contador).
- Memoria de la GPU: el pico de `torch.cuda.max_memory_allocated` fue de 7.805,4 MB en el lanzamiento, de 1.463,3 MB en las corridas diarias y de 3.001,9 MB en el cierre (el límite es 20 GB de ese contador).
- Informativo: el pico de `PeakPagefileUsage` (memoria comprometida) del lanzamiento fue de 16.261,6 MB y el de las corridas diarias, de hasta 5.683,2 MB; el límite de RNF03 es del conjunto de trabajo y no de ese contador.

## La suite rápida

La suite rápida, la selección por defecto, recoge 1.294 pruebas y termina en 118,16 s y 119,17 s en dos corridas seguidas en reposo, con el código de las correcciones de la evaluación (`30f622e`), con el presupuesto de trabajo en 120 s y el límite en 180 s (RNF15, D31). La evaluación la había medido en 126,06 s y 120,13 s, antes de que las correcciones le sumaran catorce; para volver debajo del presupuesto la matriz del QR de cada texto se codifica una vez por proceso, las pruebas del lanzamiento que no miden lo que el ajuste aprende no entrenan y «no se escribió nada» se comprueba por el tamaño y la hora de cada archivo, sin quitar ninguna aserción (D63). El margen es de un segundo o dos: una unidad que sume pruebas debe volver a medirla. Se midió en el equipo de desarrollo. Las pruebas con CORD real (`real_data`), con la GPU (`gpu`), con el checkpoint (`model`), con Docker (`docker`) y con ERPNext (`erpnext`) no están en la selección por defecto.
