# Diccionario de datos

Qué es cada dato del proyecto: lo que se usa de CORD, la tarea de factura que lee el modelo, los 13 campos del QR de ARCA, la verdad sintética, las clases sembradas, los 18 motivos de revisión, las particiones y, al final, una tabla por Parquet de `outputs/powerbi/`. Las 20 tablas salen del modelo semántico (`src/invoiceflow/dashboard/semantic_model.py`) con `invoiceflow.docs_sync.render_dictionary_tables`, y `tests/docs/test_docs.py` comprueba que este archivo contiene exactamente ese texto y que cada Parquet versionado tiene las columnas que se listan. Los nombres de tablas, columnas y archivos están en inglés; las descripciones, en español (RNF13).

**Qué es real y qué es sintético.** Los recibos de CORD son reales (Clova AI, CC BY 4.0). Las facturas, las órdenes, los remitos, los proveedores, los CUIT, los QR y las páginas son **sintéticos**: los genera el proyecto, y cada CUIT está en el rango `30-0000xxxx-d` (el del ejemplo de la especificación de ARCA), que no corresponde a ninguna empresa. Toda cifra que sale de facturas sintéticas lo dice: `(sintético)` en el texto, `synthetic: true` en los JSON y una columna `synthetic` en los Parquet.

## Los campos de CORD que se usan

CORD (Consolidated Receipt Dataset, 1.000 recibos: 800 de entrenamiento, 100 de validación y 100 de prueba) trae por recibo una imagen (`image`: bytes y ruta) y una anotación (`ground_truth`: un JSON con `gt_parse` y `meta`). El flujo lee de `gt_parse` solo lo que una factura también tiene:

| Campo | Qué es | Cómo se usa |
| --- | --- | --- |
| `menu` | Las líneas del recibo (nombre, cantidad, precio). En 569 recibos es una lista y en 431 un solo objeto; el flujo lo lee siempre como lista (D03) | Objetivo de la tarea de CORD que el modelo sigue repasando en cada época, y la medida de C01 |
| `sub_total` | Subtotal, impuesto, servicio y descuento, cuando están | Idem |
| `total` | El total y los medios de pago. De los totales, 958 se pueden leer, 14 tienen errores de tipeo y 28 faltan; los errores se dejan como están (D03) | Idem |

**Lo que CORD no trae:** proveedor, CUIT, fecha, punto de venta, número ni moneda. Por eso el flujo no puede aprender esos campos de CORD y los lee de las facturas sintéticas y de su QR (D06). Ninguna imagen de CORD está en el sitio, en el tablero ni en estos documentos.

## La tarea de factura (`<s_factura_ar>`)

El modelo (Donut ajustado) lee una página y devuelve un árbol con los valores como se imprimen; `normalize.py` los convierte en importes `Decimal`, fechas, CUIT y cantidades. Son 20 campos en tres grupos:

| Grupo | Campos |
| --- | --- |
| `header` | `cae`, `cuit_emisor`, `cuit_receptor`, `fecha`, `moneda`, `nro`, `orden_compra`, `pto_vta`, `razon_social`, `tipo` |
| `lines` (una lista) | `amount`, `code`, `desc`, `qty`, `unit`, `unit_price` |
| `totals` | `iva_105`, `iva_21`, `neto`, `total` |

## Los 13 campos del QR de ARCA

La especificación (`QRespecificaciones.pdf`, versión 1) define un JSON codificado en Base64 dentro de `https://www.arca.gob.ar/fe/qr/?p=<Base64>`. El ejemplo del PDF se reproduce byte a byte en cada corrida (V06). El QR de este proyecto lo arma el generador con ese formato: no lo emitió ARCA, y que ARCA lo aceptaría no se probó (D04).

| Campo | Tipo | Obligatorio | Qué es |
| --- | --- | --- | --- |
| `ver` | entero | sí | Versión del formato; solo el 1 |
| `fecha` | fecha AAAA-MM-DD | sí | Fecha de emisión |
| `cuit` | entero de 11 dígitos | sí | CUIT del emisor, con su dígito verificador |
| `ptoVta` | entero | sí | Punto de venta |
| `tipoCmp` | entero | sí | Tipo de comprobante |
| `nroCmp` | entero | sí | Número de comprobante |
| `importe` | decimal | sí | Importe total, hasta dos decimales |
| `moneda` | texto de 3 letras | sí | `PES` (ARS) o `DOL` (USD) |
| `ctz` | decimal | sí | Cotización, hasta seis decimales |
| `tipoDocRec` | entero | no | Tipo de documento del receptor |
| `nroDocRec` | entero | no | Número de documento del receptor |
| `tipoCodAut` | texto (E o A) | sí | Tipo de código de autorización |
| `codAut` | entero de 14 dígitos | sí | CAE o CAEA |

Una factura sin QR leído y válido no se carga sola (D06): `qr_not_read` y `qr_invalid` son los motivos 2 y 3.

## La verdad sintética

El generador conoce todo lo que dibuja y lo guarda aparte, en `data/interim/truth/`; solo `evaluation` y `baselines` la leen (D23, M01). Por factura: `invoice_key` (`<partición>-<número>`), `split`, `cohort` (`known`, `new_validation` o `new_test`), `supplier_key`, `layout` (L1 a L5), `font`, `received_on`, `po_number`, el encabezado, las líneas y los totales verdaderos, el texto del QR y su tamaño impreso (`qr_cm`), el perfil de escaneo (`scan_profile`: `clean`, `mild` o `poor`), el formato del archivo (`png`, `jpeg` o `pdf`), la clase sembrada (`planted`), la decisión esperada y los motivos esperados.

## Las clases sembradas

13 clases de discrepancia que el generador siembra; en la prueba son 167 de las 600 facturas sintéticas y cada una tiene un motivo que debería atajarla (C07).

| Clase | Qué siembra | Facturas de la prueba | Motivo esperado |
| --- | --- | --- | --- |
| `price_off` | Precio fuera de la orden. El precio unitario de una línea sube del 3 % al 10 % sobre el de la orden (fuera de la tolerancia del 2 %). | 12 | `price_out_of_tolerance` |
| `over_receipt` | Más de lo recibido. Una línea factura más de lo que los remitos recibieron hasta el día. | 11 | `qty_over_receipt` |
| `over_order` | Más de lo pedido. Una línea factura más de lo que la orden pidió. | 13 | `qty_over_order` |
| `total_mismatch` | Total que no cierra. Las líneas no suman el neto o el neto más el IVA no es el total. | 8 | `total_arithmetic` |
| `supplier_mismatch` | Proveedor distinto. La factura cita una orden de otro proveedor. | 18 | `supplier_mismatch` |
| `no_po` | Sin orden de compra. La factura cita una orden que no existe. | 15 | `no_po` |
| `duplicate` | Comprobante duplicado. Se vuelve a escanear una factura anterior: mismo proveedor, punto de venta y número. | 9 | `duplicate` |
| `date_before_po` | Fecha anterior a la orden. La fecha de emisión es anterior a la de la orden. | 9 | `date_out_of_range` |
| `currency_mismatch` | Moneda distinta. La moneda de la factura no es la de la orden. | 15 | `currency_mismatch` |
| `extra_line` | Línea de más. Un artículo que la orden no tiene. | 18 | `line_not_on_po` |
| `qr_missing` | Sin QR. La página se imprime sin QR. | 14 | `qr_not_read` |
| `qr_tampered` | QR alterado. El QR se imprime con un importe o un número que no es el de la página. | 12 | `qr_print_mismatch` |
| `credit_note` | Nota de crédito. El tipo de comprobante no es el de una factura A. | 13 | `unsupported_type` |

## Los 18 motivos de revisión

Una factura recibe como mucho un motivo de decisión: el primero de esta lista que se cumple (D15). Sin ninguno, se carga sola (`auto_post`).

| Orden | Motivo | Nombre | Regla |
| --- | --- | --- | --- |
| 1 | `file_rejected` | Archivo rechazado | La bandeja puso el archivo en cuarentena (V12 a V15): no hay una página que leer. |
| 2 | `qr_not_read` | QR no leído | Ningún lector decodificó un QR en la página. |
| 3 | `qr_invalid` | QR inválido | El QR se leyó pero no cumple la especificación de ARCA. |
| 4 | `extraction_incomplete` | Lectura incompleta | Un campo obligatorio del modelo no se pudo normalizar. |
| 5 | `unsupported_type` | Tipo no soportado | El tipo de comprobante no está entre los que el flujo carga. |
| 6 | `qr_print_mismatch` | El QR y lo impreso difieren | CUIT, punto de venta, número, fecha o total del QR no son los que se leyeron de la página. Tolerancia: `total_qr_tolerance` = 0,01. |
| 7 | `duplicate` | Comprobante duplicado | El ERP ya tiene un comprobante del mismo proveedor con ese punto de venta y número. |
| 8 | `no_po` | Sin orden de compra | No existe la orden de compra que la factura cita. |
| 9 | `supplier_mismatch` | Proveedor distinto del de la orden | El proveedor de la factura no es el de la orden. |
| 10 | `currency_mismatch` | Moneda distinta de la de la orden | La moneda de la factura no es la de la orden. |
| 11 | `date_out_of_range` | Fecha fuera de rango | La fecha no está entre la de la orden y el instante de la corrida. |
| 12 | `line_not_on_po` | Línea que no está en la orden | Un artículo de la factura no figura en la orden. |
| 13 | `line_arithmetic` | La línea no cierra | Cantidad por precio no es el importe de la línea, con la tolerancia de redondeo. Tolerancia: `rounding_per_line` = 0,01. |
| 14 | `total_arithmetic` | El total no cierra | Las líneas no suman el neto, o el neto más el IVA no es el total. Tolerancia: `rounding_per_line` = 0,01. |
| 15 | `price_out_of_tolerance` | Precio fuera de tolerancia | El precio unitario se aparta de la orden más que la tolerancia del cruce. Tolerancia: `price_tolerance_pct` = 2,0. |
| 16 | `qty_over_receipt` | Cantidad mayor que lo recibido | La cantidad facturada acumulada pasa lo recibido en los remitos hasta el día de la factura. Tolerancia: `qty_over_receipt` = 0,0. |
| 17 | `qty_over_order` | Cantidad mayor que lo pedido | La cantidad facturada acumulada pasa lo pedido en la orden. |
| 18 | `post_failed` | Carga fallida en el ERP | El borrador no se creó o su lectura de vuelta no coincide. |

## Las particiones

| Partición | Qué es | Facturas |
| --- | --- | --- |
| CORD `train` y `validation` | Recibos reales para ajustar y para medir el olvido | 800 y 100 |
| CORD `test` | Recibos reales que se abren una sola vez, en el cierre (AC23) | 100 |
| Sintética `train` | Para ajustar; 40 proveedores conocidos, diseños L1 a L3 | 2.000 |
| Sintética `validation_known` | Para elegir la época; los mismos proveedores | 200 |
| Sintética `validation_new` | Para elegir la época; 8 proveedores nuevos, diseño L4 | 100 |
| Sintética `test_known` | La prueba: los 40 conocidos con facturas nuevas | 300 |
| Sintética `test_new` | La prueba: 15 proveedores nuevos, diseño L5 y otra fuente | 300 |

La prueba sintética se genera después del sello del lanzamiento y solo si el sello existe (D10).

## Los archivos que el cierre deja al lado del sello

El cierre escribe dos archivos en `reports/`, al lado de `model_seal.json`, sin tocarlo; una repetición del cierre los reescribe iguales.

**`post_seal_analysis.json`** (D59, D60): los análisis posteriores al sello, calculados desde las mismas decisiones emitidas y la misma verdad sintética que la evaluación sellada, que no reemplazan ninguna cifra sellada. `seal_sha256` es la huella de los bytes del sello al lado del cual se calcularon (un archivo de otro sello no se publica) y `config_sha256` la de la configuración. `synthetic` es verdadero y `sealed` falso.

| Clave | Qué es |
| --- | --- |
| `invoices` | Facturas de la prueba (600) |
| `c08_preregistered` | C08 como lo fija la sección 8.1 de requirements: `b1` y `flow` con `posts` (cargas automáticas), `bad` (cargas malas) y `rate` (cargas malas sobre cargas) de la B1 sellada y del flujo; `value` la diferencia de las tasas; `ci_low` y `ci_high` su intervalo del 95 % (remuestreo pareado por factura, 2.000 remuestras, semilla 0, la tasa de cada lado sobre las facturas de la remuestra que cargó); `passed` si el límite inferior es mayor que 0 |
| `b1_read_order` | B1 como dice D48, con la orden que leyó el modelo: `baselines` por cohorte (`all`, `test_known`, `test_new`) con `auto_post`, `bad_post`, `bad_post_rate`, `ci_low`, `ci_high` (Wilson), `straight_through` y `review_recall`; `c08_per_invoice` (la forma del sello: `b1_bad`, `flow_bad`, `invoices`, `value`, `ci_low`, `ci_high`, `passed`) y `c08_preregistered` (la forma de la sección 8.1, como arriba) |
| `decisions` | Las decisiones que lo definen (`D59`, `D60`) |

**`cord_test_predictions.json`** (D63, A10 de la evaluación): lo que leyeron el modelo elegido y el checkpoint sin ajustar en cada recibo de la prueba de CORD en el cierre, la única lectura de esa partición, para recalcular el TED sin volver a leerla. `synthetic` es falso (recibos reales) y `attribution` cita CORD con su licencia CC BY 4.0. `receipts` tiene una entrada por recibo con `receipt_key`, `truth` (el `gt_parse` del recibo), `chosen` y `checkpoint` (el árbol que leyó cada modelo). No tiene imágenes.

**`summary.json` del sitio y `evaluation_summary.json`** llevan `post_seal_analysis`, el contenido de `post_seal_analysis.json` (en `summary.json` es `null` antes del sello, y `evaluation_summary.json` no lo tiene hasta que el archivo existe), y el criterio C08 lleva `verdict_es` (la frase de lo que el sello calculó, por factura) y `sealed_verdict_es` (la frase sellada, que pone las tasas por carga al lado de la diferencia por factura; D59).

## Las tablas del modelo estrella

Una dimensión es una tabla de claves únicas; un hecho está en un grano. Las 17 relaciones del modelo son de uno a varios, con filtro en una sola dirección. Los hechos de facturas y de la prueba contienen solo filas selladas (D54).

<!-- columns:start -->
### `dim_date`

Dimensión. Los días hábiles del calendario simulado que ya cerraron a la fecha de cálculo. Se versiona en `outputs/powerbi/dim_date.parquet`. Clave: `date_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `date_key` | entero 32 | — | Fecha, como AAAAMMDD. |
| `date` | fecha | — | Fecha. |
| `weekday_es` | texto | — | Nombre del día de la semana. |
| `period` | texto | — | history (hasta el lanzamiento) o test (los días de operación y el cierre). |

### `dim_supplier`

Dimensión. Los proveedores ficticios del maestro, más uno para las facturas sin proveedor atribuible. Se versiona en `outputs/powerbi/dim_supplier.parquet`. Clave: `supplier_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `supplier_key` | texto | — | Proveedor: su CUIT sintético, o sin_identificar cuando la factura no se pudo atribuir a ninguno. |
| `name_es` | texto | — | Razón social ficticia. |
| `cuit` | texto | — | CUIT sintético, del rango 30-0000xxxx-d. |
| `cohort` | texto | — | known, new_validation o new_test: si el ajuste vio su diseño. |
| `layout` | texto | — | Diseño de la factura del proveedor, de L1 a L5. |

### `dim_reason`

Dimensión. Los 18 motivos del cruce y «sin motivo». Se versiona en `outputs/powerbi/dim_reason.parquet`. Clave: `reason_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `reason_key` | texto | — | Motivo del cruce (uno de los 18, en su orden) o none cuando no hay ninguno. |
| `rank` | entero 8 | — | Lugar del motivo en el orden fijo del cruce, de 1 a 18; 0 si no hay motivo. |
| `label_es` | texto | — | Motivo, en español. |
| `rule_es` | texto | — | La regla que lo activa. |

### `dim_field`

Dimensión. Los campos que lee el modelo. Se versiona en `outputs/powerbi/dim_field.parquet`. Clave: `field_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `field_key` | texto | — | Campo que lee el modelo: header.<nombre>, line.<nombre> o totals.<nombre>. |
| `group_es` | texto | — | Grupo del campo: Cabecera, Línea o Totales. |
| `label_es` | texto | — | Campo, en español. |

### `dim_cohort`

Dimensión. Las cuatro cohortes de facturas sintéticas. Se versiona en `outputs/powerbi/dim_cohort.parquet`. Clave: `cohort_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `cohort_key` | texto | — | Cohorte de la factura: test_known, test_new, validation_known o validation_new. |
| `label_es` | texto | — | Cohorte, en español. |

### `dim_scan`

Dimensión. Los perfiles de escaneo. Se versiona en `outputs/powerbi/dim_scan.parquet`. Clave: `scan_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `scan_key` | texto | — | Perfil de escaneo de la página: clean, mild o poor. |
| `label_es` | texto | — | Perfil, en español. |

### `dim_planted`

Dimensión. Las 13 clases sembradas y la ausencia de discrepancia. Se versiona en `outputs/powerbi/dim_planted.parquet`. Clave: `planted_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `planted_key` | texto | — | Discrepancia que el generador sembró (una de 13 clases) o none. |
| `label_es` | texto | — | Clase, en español. |
| `expected_reason_key` | texto | — | Primer motivo que el generador espera para la clase; vacío si no hay ninguno. |

### `dim_baseline`

Dimensión. El flujo y sus cuatro líneas base. Se versiona en `outputs/powerbi/dim_baseline.parquet`. Clave: `baseline_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `baseline_key` | texto | — | Línea base: flow (el flujo), b0, b1, b2 o b3. |
| `label_es` | texto | — | Línea base, en español. |

### `dim_model`

Dimensión. El modelo elegido del lanzamiento y el checkpoint sin ajustar. Se versiona en `outputs/powerbi/dim_model.parquet`. Clave: `model_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `model_key` | texto | — | Modelo medido en la prueba de CORD: chosen (el elegido) o checkpoint (sin ajustar). |
| `model_version` | texto | — | Versión del modelo (o la revisión del checkpoint). |
| `chosen_epoch` | entero 32 | época | Época elegida; vacía para el checkpoint. |
| `artifact_sha256` | texto | — | Huella del artefacto; vacía para el checkpoint. |
| `config_sha256` | texto | — | Huella de la configuración sellada; vacía para el checkpoint. |
| `seal_state` | texto | — | sealed (la configuración sellada) o post_seal (otra, posterior al sello). |

### `dim_pipeline_run`

Dimensión. Las corridas del flujo. Se versiona en `outputs/powerbi/dim_pipeline_run.parquet`. Clave: `run_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `run_key` | texto | — | Corrida que lo escribió. |
| `started_at` | fecha y hora | — | Inicio. |
| `finished_at` | fecha y hora | — | Fin. |
| `status` | texto | — | Estado: running, ok, failed, rejected o no_delivery. |
| `delivery_id` | texto | — | Entrega que cargó. |
| `as_of` | fecha y hora | — | Instante de cálculo de la corrida. |
| `config_sha256` | texto | — | Huella de la configuración sellada. |
| `artifact_sha256` | texto | — | Huella del artefacto del modelo. |
| `invoices` | entero 64 | facturas | Facturas de la entrega. |
| `peak_working_set_mb` | decimal | MB | Pico del conjunto de trabajo. |
| `peak_gpu_mb` | decimal | MB | Pico de memoria de la GPU. |
| `memory_counter` | texto | — | Contador de memoria que dio las cifras. |
| `message` | texto | — | Mensaje de la corrida. |

### `fact_invoice`

Hecho. Una fila por factura de la prueba evaluada: lo emitido unido a la verdad sintética. Se versiona en `outputs/powerbi/fact_invoice.parquet`. Grano: `invoice_key`, `seal_state`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `invoice_key` | texto | — | Factura: el nombre de su archivo sin extensión. |
| `date_key` | entero 32 | — | Fecha, como AAAAMMDD. |
| `supplier_key` | texto | — | Proveedor: su CUIT sintético, o sin_identificar cuando la factura no se pudo atribuir a ninguno. |
| `cohort_key` | texto | — | Cohorte de la factura: test_known, test_new, validation_known o validation_new. |
| `scan_key` | texto | — | Perfil de escaneo de la página: clean, mild o poor. |
| `planted_key` | texto | — | Discrepancia que el generador sembró (una de 13 clases) o none. |
| `file_format` | texto | — | png, jpeg o pdf. |
| `qr_cm` | decimal | cm | Lado del QR impreso. |
| `decision` | texto | — | auto_post (se carga sola) o review (a revisión). |
| `first_reason_key` | texto | — | Primer motivo de la decisión, o none si se cargó sola. |
| `reasons` | entero 32 | motivos | Cuántos motivos tiene la decisión. |
| `posted` | verdadero/falso | — | El borrador quedó en el ERP. |
| `bad_post` | verdadero/falso | — | Se cargó sola con una discrepancia sembrada o con campos que no son los verdaderos. |
| `straight_through` | verdadero/falso | — | Sin discrepancia, con QR impreso y cargada bien. |
| `lines` | entero 32 | líneas | Líneas de la verdad. |
| `lines_exact` | entero 32 | líneas | Líneas leídas exactas. |
| `total` | decimal | — | Total decidido (del QR si lo hay), en la moneda de la factura (sintético). |
| `currency` | texto | — | Moneda del total. |
| `seconds` | decimal | segundos | Segundos de procesamiento de la factura. |
| `seal_state` | texto | — | sealed (la configuración sellada) o post_seal (otra, posterior al sello). |

### `fact_invoice_reason`

Hecho. Cada motivo de cada decisión emitida; se relaciona con la dimensión de motivos y no con el hecho de facturas (no hay una dimensión de facturas). Se versiona en `outputs/powerbi/fact_invoice_reason.parquet`. Grano: `invoice_key`, `reason_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `invoice_key` | texto | — | Factura: el nombre de su archivo sin extensión. |
| `reason_key` | texto | — | Motivo del cruce (uno de los 18, en su orden) o none cuando no hay ninguno. |
| `rank` | entero 8 | — | Lugar del motivo en el orden del cruce. |

### `fact_field_read`

Hecho. La lectura por cohorte, campo y perfil de escaneo. Se versiona en `outputs/powerbi/fact_field_read.parquet`. Grano: `cohort_key`, `field_key`, `scan_key`, `seal_state`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `cohort_key` | texto | — | Cohorte de la factura: test_known, test_new, validation_known o validation_new. |
| `field_key` | texto | — | Campo que lee el modelo: header.<nombre>, line.<nombre> o totals.<nombre>. |
| `scan_key` | texto | — | Perfil de escaneo de la página: clean, mild o poor. |
| `leaves` | entero 32 | hojas | Hojas que el modelo escribió. |
| `correct` | entero 32 | hojas | Hojas que se normalizaron a un valor (no dice si es el verdadero: eso lo dicen las líneas exactas). |
| `recall` | decimal | proporción | Parte de las hojas que se normalizaron. |
| `seal_state` | texto | — | sealed (la configuración sellada) o post_seal (otra, posterior al sello). |

### `fact_qr_read`

Hecho. La lectura del QR por cohorte, perfil, tamaño impreso y lector. Se versiona en `outputs/powerbi/fact_qr_read.parquet`. Grano: `cohort_key`, `scan_key`, `qr_cm`, `reader`, `seal_state`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `cohort_key` | texto | — | Cohorte de la factura: test_known, test_new, validation_known o validation_new. |
| `scan_key` | texto | — | Perfil de escaneo de la página: clean, mild o poor. |
| `qr_cm` | decimal | cm | Lado del QR impreso. |
| `reader` | texto | — | Lector que lo leyó (pyzbar o aruco) o none si ninguno. |
| `printed` | entero 32 | facturas | QR impresos. |
| `read_ok` | entero 32 | facturas | QR leídos con el texto exacto. |
| `seal_state` | texto | — | sealed (la configuración sellada) o post_seal (otra, posterior al sello). |

### `fact_daily_ops`

Hecho. El seguimiento de cada día de operación (existe antes del cierre). Se versiona en `outputs/powerbi/fact_daily_ops.parquet`. Grano: `date_key`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `date_key` | entero 32 | — | Fecha, como AAAAMMDD. |
| `invoices` | entero 32 | facturas | Facturas del día. |
| `auto_post` | entero 32 | facturas | Cargadas solas. |
| `review` | entero 32 | facturas | A revisión. |
| `posted` | entero 32 | facturas | Borradores que quedaron en el ERP. |
| `post_failed` | entero 32 | facturas | Cargas que fallaron. |
| `quarantined` | entero 32 | archivos | Archivos en cuarentena. |
| `seconds_mean` | decimal | segundos | Segundos medios por factura. |
| `seconds_p95` | decimal | segundos | Segundos del percentil 95. |

### `fact_baseline`

Hecho. El flujo y las líneas base B0 a B3 sobre las mismas facturas, por cohorte. Se versiona en `outputs/powerbi/fact_baseline.parquet`. Grano: `baseline_key`, `cohort_key`, `seal_state`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `baseline_key` | texto | — | Línea base: flow (el flujo), b0, b1, b2 o b3. |
| `cohort_key` | texto | — | Cohorte de la factura: test_known, test_new, validation_known o validation_new. |
| `auto_post` | entero 32 | facturas | Facturas que cargaría sola. |
| `bad_post` | entero 32 | facturas | De ellas, cargas malas. |
| `bad_post_rate` | decimal | proporción | Tasa de carga mala. |
| `ci_low` | decimal | proporción | Límite inferior de Wilson. |
| `ci_high` | decimal | proporción | Límite superior de Wilson. |
| `straight_through` | decimal | proporción | Paso directo. |
| `review_recall` | decimal | proporción | Parte de las sembradas que van a revisión. |
| `seal_state` | texto | — | sealed (la configuración sellada) o post_seal (otra, posterior al sello). |

### `fact_cord_eval`

Hecho. La prueba de CORD del elegido y del checkpoint, que se abre una sola vez en el cierre. Se versiona en `outputs/powerbi/fact_cord_eval.parquet`. Grano: `model_key`, `field`, `split`, `seal_state`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `model_key` | texto | — | Modelo medido en la prueba de CORD: chosen (el elegido) o checkpoint (sin ajustar). |
| `field` | texto | — | Campo medido: all (el árbol entero; no se mide campo por campo). |
| `split` | texto | — | Partición de CORD: test. |
| `ted_mean` | decimal | puntos | TED medio (0 a 100) sobre los recibos. |
| `leaf_recall` | decimal | proporción | Parte de las hojas acertadas. |
| `receipts` | entero 32 | recibos | Recibos. |
| `seconds_mean` | decimal | segundos | Segundos por recibo; vacío: la prueba de CORD no los mide. |
| `fit_splits` | texto | — | Con qué particiones se ajustó el modelo que dio la cifra: cord_train+synth_train, o checkpoint si no se ajustó aquí. |
| `seal_state` | texto | — | sealed (la configuración sellada) o post_seal (otra, posterior al sello). |

### `fact_choice`

Hecho. Las épocas candidatas con sus cifras de validación. Se versiona en `outputs/powerbi/fact_choice.parquet`. Grano: `epoch`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `epoch` | entero 32 | — | Época candidata. |
| `cord_val_ted` | decimal | puntos | TED en la validación de CORD. |
| `val_known_line_exact` | decimal | proporción | Líneas exactas en validation_known. |
| `val_new_line_exact` | decimal | proporción | Líneas exactas en validation_new. |
| `kept` | verdadero/falso | — | Conserva CORD: no pierde más que la tolerancia. |
| `chosen` | verdadero/falso | — | Es la época que eligió el lanzamiento. |
| `fit_splits` | texto | — | Con qué particiones se ajustó el modelo que dio la cifra: cord_train+synth_train, o checkpoint si no se ajustó aquí. |

### `fact_evaluation_summary`

Hecho. Los criterios de la prueba y su veredicto. Se versiona en `outputs/powerbi/fact_evaluation_summary.parquet`. Grano: `criterion_id`, `seal_state`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `criterion_id` | texto | — | Criterio, de C01 a C10. |
| `name_es` | texto | — | Nombre del criterio. |
| `value` | decimal | — | Valor medido. |
| `baseline_value` | decimal | — | Valor de la base con que se compara. |
| `ci_low` | decimal | — | Límite inferior del intervalo. |
| `ci_high` | decimal | — | Límite superior del intervalo. |
| `threshold_es` | texto | — | Umbral, en español. |
| `passed` | verdadero/falso | — | Cumple; vacío en el informativo. |
| `seen_by_research` | texto | — | Si la investigación lo vio antes. |
| `seen_by_planner` | texto | — | Si el plan lo vio antes. |
| `synthetic` | verdadero/falso | — | La cifra sale de las facturas sintéticas. |
| `verdict_es` | texto | — | Veredicto, en español. |
| `seal_state` | texto | — | sealed (la configuración sellada) o post_seal (otra, posterior al sello). |

### `fact_validation`

Hecho. El resultado de cada regla en cada corrida. Se versiona en `outputs/powerbi/fact_validation.parquet`. Grano: `run_key`, `rule_id`.

| Columna | Tipo | Unidad | Significado |
| --- | --- | --- | --- |
| `run_key` | texto | — | Corrida que lo escribió. |
| `rule_id` | texto | — | Regla de validación. |
| `name_es` | texto | — | Nombre de la regla. |
| `severity` | texto | — | bloqueante, advertencia o informativa. |
| `scope` | texto | — | Alcance: fuente, entrega, archivo o modelo. |
| `aggregation` | texto | — | Cómo se agrega entre corridas: source, per_run o latest. |
| `status` | texto | — | pasa, falla (bloqueante que no pasa) o advierte. |
| `files_affected` | entero 64 | archivos | Archivos afectados. |
<!-- columns:end -->
