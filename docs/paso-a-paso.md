# Paso a paso: cómo se hizo el proyecto

La cronología real, desde el registro del documento de seguimiento (`odd/tasks/portfolio-projects.md`, L90 a L110) y `git log` de esta carpeta: qué se hizo en cada paso, quién (qué agente y con qué modelo, S8), con qué prueba y en qué commit. Todo lo que dice este documento consta en el registro o en el historial; lo que el registro no dice, no se afirma (RNF14).

El dueño del proyecto pidió que cuando se termina un proyecto se siga con el siguiente sin preguntar (S12), que la estructura sea la del proyecto 01 (S10), que se acepten de forma automática las revisiones de Gentle AI (S11) y los modelos de los agentes: Opus para planes, arquitectura y evaluación, Sonnet para programar y Haiku para tareas acotadas como investigar (S8). El proyecto 07 empezó mientras se construía la página del proyecto 06 en marca-personal (L90, L92); después de su cierre se escribió el plan. Lo que sigue es lo que se hizo, en el orden en que pasó, todo el 2026-11-18 salvo el andamiaje del portafolio. **La evaluación independiente, sus correcciones y su comprobación no se hicieron todavía**, y la página del sitio y el `.pbix` tampoco.

## 1. El andamiaje y la investigación (L90, L91)

**Qué.** El proyecto parte de la carpeta vacía que creó el andamiaje del portafolio (`1d5d1e3`, 2026-11-09). Un agente con Haiku bajó CORD con un script fijado al commit del conjunto, comprobó las huellas de sus seis fragmentos, perfiló las anotaciones, buscó un QR de ARCA en los recibos (no hay: CORD tiene un solo QR, de pago), reprodujo el ejemplo de la especificación de ARCA byte a byte, midió tres lectores de QR sobre 40 códigos sintéticos, midió el checkpoint de Donut sobre la prueba de CORD (89,62 % de TED) y revisó ERPNext y Odoo sin instalar nada.

**Lo que encontró.** 1.000 recibos (800, 100 y 100) y sin proveedor, CUIT, fecha ni moneda; el QR de ARCA solo lo agregan «Comprobantes en línea» y «Facturador Móvil»; pyzbar lee el 100 % de los códigos sueltos en las condiciones medidas; las afirmaciones del sitio que el proyecto no puede sostener (el QR «en cada factura ARCA de prueba», los campos del emisor atribuidos a los recibos).

**Commit.** `a6c5774`, con `docs/research.md`, sus scripts `s00` a `s08` y `requirements-research.txt`.

## 2. Los requerimientos y el plan (L93)

**Qué.** Un agente con Opus leyó la investigación y los documentos del proyecto 06, y midió **sin abrir la prueba de CORD**: el checkpoint en la validación (93,65 %), que el checkpoint sin ajustar lee el 5,8 % de las cantidades de una factura sintética, que el tamaño impreso del QR y la resolución deciden más que el lector, y lo que cuesta un paso de ajuste en la RTX 3090 (0,162 s y 7,43 GB). Escribió 40 criterios de aceptación, los criterios C01-C10 con lo esperable escrito antes de medir, 43 decisiones y 23 unidades.

**Commit.** `163125b`, antes de cualquier código.

## 3. Una decisión del dueño (L94)

**Qué.** El plan proponía n8n (como dice el sitio); S10 pedía un DAG de Airflow. El agente le preguntó por Telegram con las dos salidas y el dueño respondió «2 Airflow (como P1-P6)». D26 se reescribió con esa cita (`35a21cc`), y el sitio cambia n8n por Airflow.

## 4. La construcción, unidad por unidad (L95, L97, L99, L101, L103, L105, L107, L109)

Agentes con Sonnet escribieron U01 a U22 (menos la unidad de velocidad, de Opus), cada unidad con su prueba primero y las de riesgo alto en su propio commit. Una unidad que supera las 1.500 líneas no cabe en una revisión nativa: desde L104 cada commit se mantiene por debajo.

| Unidad | Qué entrega | Commit | Registro |
| --- | --- | --- | --- |
| U01 | El entorno con las versiones fijadas y la prueba de que toda importación es una dependencia directa | `ea8d8db` | L95 |
| U02 | La configuración con cada clave clasificada como sellada o no y las particiones reservadas | `26f895c`, `30c48ac` | L95 |
| U03 | El CORD diminuto, los casos a mano y el límite de tiempo de la suite | `f218c9a` | L95 |
| U04 | La lectura estricta de los seis Parquet de CORD, que indexa `train` y `validation` y nunca abre `test` | `9c3af50` | L95 |
| U05 | El rango de CUIT sintético y el maestro del ERP sintético | `3eab4ea` | L95 |
| (docs) | D26 reescrita para Airflow, con la cita del dueño | `35a21cc` | L97 |
| U06 | Las facturas sintéticas con su verdad, cinco diseños y la guarda del sello sobre la prueba | `3387afa` | L97 |
| U07 | La lectura y la validación del QR de ARCA y la cuarentena de la bandeja no confiable | `cb3804b` | L97 |
| U08 | La normalización de importes, fechas, CUIT y cantidades | `e434911` | L97 |
| U09 | La serialización de la tarea de factura para Donut y el registro de los extractores | `5d0fedc` | L97 |
| U10 | El ajuste, la elección de la época y el artefacto en `safetensors` con su huella | `8f3ae1f` | L99 |
| U11 | El cruce con 18 motivos ordenados, la decisión y las líneas base B0 a B3 | `26a338d` | L99 |
| U12 | El almacén, con tablas de solo inserción y los controles de carga | `8d2f1d9` | L99 |
| U13 | El cliente del ERP y su sustituto con la forma de Frappe, la carga idempotente y la lectura de vuelta | `4b6144d` | L99 |
| (correcciones) | La verdad toma el vocabulario de cohorte del proveedor y el tope de 40 caracteres solo vale para los analizadores numéricos | `78f5748` | L101 |
| U14 | El lanzamiento congelado y su sello, escrito una vez | `a4e99c3` | L101 |
| U15 | La corrida diaria: una transacción por factura, y la carga justo después de la decisión | `53e72c4` | L101 |
| U16 | La evaluación de cierre, los criterios con sus textos y el sello de la prueba | `1e12f6e` | L101 |
| U17 | El modelo estrella para Power BI, las medidas DAX que leen solo lo sellado y el diseño del tablero | `a6ee201` | L103 |
| U18 | Los cinco JSON del sitio y las figuras, con toda cifra sintética rotulada | `428f1d9` | L103 |
| U19 | El bloqueo de un solo escritor, las trece tareas, la CLI y el modo rápido | `d5168cb` | L103 |
| Velocidad | La suite de 186,5 s a 115,4 s sin quitar una prueba: el modelo diminuto lee 16 fichas, cada página y cada mundo diario se arma una vez, el maestro se cachea, y torch usa un hilo | `6bb009d`, `35d62e5`, `ecfe585`, `c707e72`, `46188f8`, `07b1e08` | L105 |
| (velocidad) | La lectura del checkpoint en el modo rápido, como la del modelo | `fc0f24a` | L107 |
| U20 | La API de entrada, con el token en cada pedido y un 409 si el bloqueo está tomado | `9103cf9` | L107 |
| U21 | El DAG `invoiceflow_daily` en Docker, con los secretos del entorno | `9ef04b9` | L107 |
| U22 | La corrida real y la prueba de humo contra ERPNext (sección 6) | `0462f72`, `93b13bf`, `4c50d45`, `c135752`, `b610b95`, `94db289` | L109 |

**Evidencia por unidad.** La prueba primero se vio fallar por la razón esperada antes de escribir el código (con las excepciones que L95 y L101 dicen), y las reglas de negocio se probaron con mutantes: unos 310 en U01 a U05, unos 140 en U06 a U09, unos 300 en U10 a U13, 47 en U14 a U16, 34 de 35 en U17 a U19 (el que sobrevive, `repeat_new` en 0, el modo rápido no lo puede observar), 26 de 28 en U20 y 51 de 51 en U21. Los que sobrevivieron son equivalentes, salvo el de U19 ya dicho.

## 5. Las revisiones nativas (L96, L98, L100, L102, L104, L106, L108)

**Qué.** La revisión nativa de Gentle AI revisó la investigación y casi cada unidad, con el consentimiento aceptado de forma automática (S11); cada una corrió en un árbol aparte. Dio 38 hallazgos entre las revisiones de la investigación, de U01 a U16, de la unidad de velocidad, de U18, U20, U21 y de U22 (ver [review-advisories.md](review-advisories.md)).

**Lo que no se pudo revisar.** El plan (`d5cf7d8..163125b`), U17 (2.598 líneas en 58 archivos, casi todas SQL) y U19 (3.224 líneas) pasaron el presupuesto de contexto de la revisión (`lens_context_budget_exceeded`), y el commit generado de U22 (`94db289`, 3.764 líneas) quedó fuera por la misma razón. Dividir esos commits habría reescrito la historia confirmada; la evaluación independiente sondea con más cuidado lo que ninguna revisión vio.

**Lo que corrigieron.** Cuatro hallazgos (uno de U06, dos de U08 y uno de U13) se corrigieron en `78f5748` y `53e72c4` (L101); las demás esperan a las correcciones que siguen a la evaluación.

## 6. La corrida real y la reparación del lanzamiento (L109)

**Qué.** Un agente con Sonnet corrió el flujo completo una vez en la RTX 3090: el ajuste, 20 entregas diarias de 30 facturas y el cierre con la prueba sintética y la prueba de CORD. Antes, la prueba de humo con ERPNext v15.124.4 real (una vez) encontró cuatro diferencias con el sustituto y dos defectos del cliente: el filtro de tabla hija listaba un documento por cada fila que coincide (`0462f72`) y el ERP exige campos que el sustituto ignoraba (`93b13bf`).

**El lanzamiento falló una vez.** El primer intento se detuvo con `ModelChoiceError` después de 5.851 s: el checkpoint sin ajustar puntuaba 49,08 de TED en la validación de CORD contra los 93,65 del planificador, porque la métrica leía el orden de las claves del árbol y el objetivo canónico las lleva ordenadas. No había sello ni fila sellada. La corrección tuvo su prueba primero (un árbol reordenado e idéntico puntuaba 0,045) y una prueba con el modelo real que mide el checkpoint en 94,00 (`4c50d45`); se reanudó con `run --run-key … --from fit_model` y `replay`, sin tocar una tolerancia. La decisión de aceptarla como un defecto es del orquestador y el dueño puede revertirla.

**Resultado.** Cuatro de los nueve criterios con umbral se cumplen (C02, C07, C08 y C09, todos sintéticos) y no se cumplen C01, C03, C04, C05 y C06. El cierre tardó 1.032 s contra los 10 minutos que pedía. Una prueba de la opción `--config` que reescribía `docs/figures` (`c135752`) y la prueba de las cifras de la corrida real con las notas de las decisiones (`b610b95`) siguieron. Las salidas generadas, el sello (SHA-256 `51884acb…2ce2`), las figuras y el diseño del tablero van en `94db289`, un commit aparte de lo escrito a mano.

## 7. Las revisiones de U22 (L110)

La revisión nativa leyó lo escrito a mano de U22 en dos tramos, `bbc0fff..93b13bf` (566 líneas, ítem 5) y `93b13bf..b610b95` (259 líneas, ítem 6), y aprobó los dos con tres sugerencias en total: el archivo de la prueba de humo no se escribe de forma atómica, la guarda de rutas locales de `read_smoke` solo prueba una de sus cuatro formas, y `_runs` de la prueba de la corrida real lee nueve columnas por posición. Las tres se corrigieron después de la evaluación (`328573c` y `b1465c8`).

## 8. La documentación (U23)

**Qué.** Este README, la ficha del modelo, el diccionario, el runbook, este paso a paso y las advertencias los escribió un agente con Sonnet con la prueba primero (`tests/docs/test_docs.py`). Las cifras salen de los archivos sellados y del resumen; el diccionario toma sus 20 tablas del modelo semántico; una prueba compara los intervalos con el resumen a los decimales del documento. También dejó las filas del 07 en `SITE-UPDATES.md` y las nueve del `LANDING-EXPANSION.md` que U01 debía.

## 9. La evaluación independiente (L112)

**Qué.** Un agente con Opus que no construyó el proyecto, en un clon aparte del commit `832271f` con su propio `.venv` y los datos copiados, recalculó cada cifra con lectores propios, decodificó de nuevo los 600 QR, regeneró la prueba con la semilla sellada (los mismos bytes), repitió una diaria y el cierre reales en copias (el mismo sello) y sondeó cada clase de defecto de la sección 24.2 del plan. Los veredictos se reproducen: cuatro de nueve criterios con umbral se cumplen. Encontró cuatro bloqueantes (C08, sobre facturas sintéticas, calculado por factura y no por carga, con un texto que mezclaba las dos; la línea base B1 con la orden de la verdad; el runbook R11 que no reconstruía; y el cierre de 1.032 s contra 600 s) y once advertencias. El informe está transcrito en [evaluation.md](evaluation.md).

## 10. Las correcciones

**Qué.** Un agente con Opus corrigió con la prueba primero, cada corrección de riesgo alto en su commit: C08 (sintético) como lo fija el preregistro, publicado al lado del sellado como análisis posterior al sello (`dda519e`, D59), y B1 con la orden que leyó el modelo, también al lado (`146925f` y `8f981c6`, D60); `POST /runs` que responde 200 a una corrida ya guardada (`2db2648`); el sello antes que la fila del registro y las épocas candidatas conservadas si la elección falla (`ae3f21b`, D62); las predicciones de CORD del cierre guardadas al lado del sello (`ebd3d3e`); la pasada del checkpoint de a ocho páginas, con los mismos árboles (`cf9bb56`, D61); R11 que reconstruye desde el sello sin ajustar y rechaza una reconstrucción a medias (`b1465c8`, D62); y las sugerencias baratas de las revisiones (`328573c`, `e7e9739`). Ningún valor sellado se reescribió. Lo que cambió en las salidas versionadas y cómo se regeneraron está en [evaluation.md](evaluation.md), sección «Las correcciones».

## 11. La comprobación y la segunda corrección (L115)

**Qué.** Otro agente con Opus, en dos clones (`832271f` y `8816a14`) y con lectores propios, rehízo los cuatro bloqueantes: B2 y B4 corregidos; B1 y B3 corregidos en el código, pero con dos hallazgos nuevos que los dejaban abiertos (N1: el paso 1 de R11 tenía caracteres de control donde iban `\r` y `\a`; N2: el diseño del tablero y la tabla de Power BI publicaban todavía la frase de C08 (sintético) mezclada) y tres advertencias (N3 a N5). Un agente con Sonnet los corrigió: la ruta de R11 y una prueba de bytes de control (`de2fe9a`), el hecho `fact_evaluation_summary` y el diseño con la frase publicada y la columna «Lo que prueba el umbral» (`e046e44`, y las salidas regeneradas en `f1557b9`), la ficha y los textos de recuperación con dos pruebas nuevas (`e2cc709`). El informe y lo que se hizo con cada hallazgo están en [evaluation.md](evaluation.md), secciones «Comprobación» y «La segunda corrección»; el `data/` del repositorio sigue siendo el de la corrida sellada (D64).

## Lo que sigue

1. **La página del sitio** `invoices-erp` en marca-personal, con las filas de `SITE-UPDATES.md`.
2. **Del dueño:** el `.pbix`, si el `data/` del repositorio se reconstruye con R11 en el lugar (D64), si el presupuesto de la suite pasa de 120 s a un valor que cumpla (N5), la definición de `ted_accuracy` y cómo anotar 94,00 contra 93,65, la numeración de las órdenes para `mode = http`, la homologación de ARCA y si una versión posterior, con su propio sello, calcula C08 (sintético) por carga y B1 con la orden leída (D59, D60).
