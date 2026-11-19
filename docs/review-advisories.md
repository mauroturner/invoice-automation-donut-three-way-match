# Advertencias de las revisiones nativas

Cada hallazgo de las revisiones nativas de Gentle AI que el registro del documento de seguimiento anota para el proyecto 07 (L96, L98, L100, L102, L106, L108 y L110): 38 en total, entre advertencias, sugerencias y hallazgos que el registro no clasifica, con su ubicación y su estado. Los números y los estados de este archivo son los que cuentan el README, la ficha del modelo y el paso a paso, y `tests/docs/test_docs.py` los compara.

Los hallazgos se numeran dentro de cada tramo (`W` una advertencia, `S` una sugerencia, `H` un hallazgo que el registro no clasifica). Las ubicaciones son las que el registro da; las que no precisa dicen «según el registro» y nombran el archivo donde está lo que describe. Los estados son cuatro: **corregida** (con su commit), **refutada** o **dejada con evidencia** (con el motivo) y **abierta**.

**Lo que no se revisó.** La revisión nativa se negó por `lens_context_budget_exceeded` con el plan (`d5cf7d8..163125b`, como el plan del proyecto 06), con U17 (2.598 líneas en 58 archivos, la mayoría SQL de las tablas; L104) y con U19 (3.224 líneas; L104), y no se intentó con el commit generado de U22, `94db289` (3.764 líneas generadas, fuera del presupuesto; L110). Dividir U17 o U19 habría reescrito la historia ya confirmada. Lo que ninguna revisión vio lo sondea la evaluación independiente: el contrato del modelo estrella y de las medidas de U17, y el bloqueo, el orden de cierre, los rechazos y las opciones de la CLI de U19; el bloqueo de U19 es un port del bloqueo del proyecto 06, que sí se revisó. Siete tramos se aprobaron sin hallazgos y no figuran abajo: U02 (`ea8d8db..26f895c`), U03 con L94 (`26f895c..f218c9a`), U05 (`9c3af50..3eab4ea`), U07 (`3387afa..cb3804b`, escalada al ítem 2), las correcciones de L98 (`4b6144d..78f5748`), U18 (`a6ee201..428f1d9`) y U21 (`9103cf9..9ef04b9`); el tramo del D26 (`3eab4ea..35a21cc`) es pasivo y START lo rechazó por riesgo bajo, así que no hay revisión. Las revisiones de U22 (L110) están hechas: dos tramos, `u22a` y `u22b`, con tres sugerencias.

**Estado después de las correcciones de la evaluación independiente** (plan, sección 28.2; [evaluation.md](evaluation.md)). De las 38, 15 están **corregidas** (4 en L101, `78f5748` y `53e72c4`, y 11 en las correcciones: `ae3f21b`, `2db2648`, `b1465c8`, `328573c` y `e7e9739`), 1 **refutada**, 22 **dejadas con evidencia** y ninguna **abierta**. Las dejadas son de legibilidad, de los scripts de la investigación o de comportamientos documentados, y el motivo de cada una está en su bloque y en D63.

## research (L96)

Tramo `64ac898..a6c5774` (la investigación): aprobada con seis hallazgos.

- **W1**
  - Hallazgo: Advertencia: `s07_site_claims.py` protege la lectura de sus JSON, pero lee `github_LICENSE-CC-BY` y `arca_qr_spec.txt` sin protección.
  - Ubicación: `research/s07_site_claims.py` (según el registro).
  - Estado: **dejada con evidencia**. Los scripts de `research/` son el registro de lo que la investigación midió, con sus salidas versionadas: corregirlos cambiaría lo que dicen haber medido, y el flujo no los importa (D63).

- **H2**
  - Hallazgo: `dup_within` guarda la cantidad de imágenes únicas y no las repetidas dentro de la partición que su nombre dice.
  - Ubicación: `research/s01_structure.py:189`.
  - Estado: **dejada con evidencia**. Los scripts de `research/` son el registro de lo que la investigación midió, con sus salidas versionadas: corregirlos cambiaría lo que dicen haber medido, y el flujo no los importa (D63).

- **H3**
  - Hallazgo: El mismo módulo tiene estado muerto.
  - Ubicación: `research/s01_structure.py` (según el registro).
  - Estado: **dejada con evidencia**. Los scripts de `research/` son el registro de lo que la investigación midió, con sus salidas versionadas: corregirlos cambiaría lo que dicen haber medido, y el flujo no los importa (D63).

- **H4**
  - Hallazgo: `decode_opencv_aruco` duplica `decode_opencv`.
  - Ubicación: `research/s04_arca_qr.py:120-126`.
  - Estado: **dejada con evidencia**. Los scripts de `research/` son el registro de lo que la investigación midió, con sus salidas versionadas: corregirlos cambiaría lo que dicen haber medido, y el flujo no los importa (D63).

- **H5**
  - Hallazgo: La comprobación de Base64 compara dos constantes y nunca lee el texto extraído del PDF.
  - Ubicación: `research/s04_arca_qr.py` (según el registro).
  - Estado: **dejada con evidencia**. Los scripts de `research/` son el registro de lo que la investigación midió, con sus salidas versionadas: corregirlos cambiaría lo que dicen haber medido, y el flujo no los importa (D63).

- **H6**
  - Hallazgo: `full_size` se lee sin comprobar que no sea `None`.
  - Ubicación: `research/s06_erp_survey.py:66`.
  - Estado: **dejada con evidencia**. Los scripts de `research/` son el registro de lo que la investigación midió, con sus salidas versionadas: corregirlos cambiaría lo que dicen haber medido, y el flujo no los importa (D63).

## U01 (L96)

Tramo `bbc3d43..ea8d8db` (U01, escalada al ítem 5): aprobada con dos advertencias, las mismas dos que recibió el proyecto 06.

- **W1**
  - Hallazgo: Advertencia: dos pruebas leen `requirements-research.txt` y `../LANDING-EXPANSION.md`, fuera del candidato.
  - Ubicación: `tests/unit/test_dependencies.py` (según el registro).
  - Estado: **dejada con evidencia**. Las dos lecturas son a propósito: la prueba compara las dependencias con las de la investigación y con las filas de `LANDING-EXPANSION.md` (S5), como en el proyecto 06 (D63).

- **W2**
  - Hallazgo: Advertencia: la prueba de las versiones instaladas necesita el intérprete exacto aprovisionado.
  - Ubicación: `tests/unit/test_dependencies.py` (según el registro).
  - Estado: **dejada con evidencia**. La prueba existe para que la suite corra con el intérprete aprovisionado (RNF01, D35); con otro, falla y dice cuál, como en el proyecto 06 (D63).

## U04 (L96)

Tramo `f218c9a..9c3af50` (U04, escalada al ítem 2): aprobada con una sugerencia.

- **S1**
  - Hallazgo: `files.write_atomic` no hace `fsync` del directorio.
  - Ubicación: `src/invoiceflow/files.py:49`.
  - Estado: **dejada con evidencia**. El registro de la revisión (L96) anota que el comportamiento está documentado; no se cambió el código. La evaluación puede confirmar que lo documentado alcanza.

## U06 (L98)

Tramo `35a21cc..3387afa` (U06, escalada al ítem 1): aprobada con dos hallazgos.

- **W1**
  - Hallazgo: Advertencia: `COHORT_OF` se define y no se usa; cada `InvoiceTruth.cohort` recibía el nombre de la partición (`test_known`) mientras los proveedores traen `known` o `new_test`. El orquestador lo confirmó con un `grep`.
  - Ubicación: `src/invoiceflow/synth/invoices.py:41`.
  - Estado: **corregida** en `78f5748`: la verdad toma el vocabulario del proveedor (`known`, `new_validation`, `new_test`) y `COHORT_OF` quedó como una aserción; el RED fue `'train' == 'known'`.

- **S2**
  - Hallazgo: La segunda mitad de V06 compara las dos constantes de `qrpayload` y no lee el PDF aceptado fuera de `real_data`.
  - Ubicación: `tests/contract/test_sources_spec_example.py` (según el registro).
  - Estado: **dejada con evidencia**. La segunda mitad de V06 es la reproducción del ejemplo de la especificación por `synth.qrpayload`, que no necesita el PDF; el PDF real se acepta en cada corrida por su tamaño y su SHA-256 fijados, y la prueba lo lee de `data/raw/arca/` cuando está (D63).

## U08 (L98)

Tramo `cb3804b..5d0fedc` (U08 y U09): aprobada con tres hallazgos, los tres de U08.

- **W1**
  - Hallazgo: Advertencia: `normalize._clean` corta todo texto en `MAX_TEXT = 40` y devuelve `None` por encima, también una descripción o la razón social: una descripción larga quedaba «faltante» (`extraction_incomplete`).
  - Ubicación: `src/invoiceflow/normalize.py:38`.
  - Estado: **corregida** en `78f5748`: el tope de 40 caracteres vale solo para los analizadores de importe, cantidad, fecha, CUIT y entero; los textos libres los acota el `max_length` del decodificador.

- **S2**
  - Hallazgo: Sugerencia: una guarda redundante en `_text`.
  - Ubicación: `src/invoiceflow/normalize.py:105`.
  - Estado: **dejada con evidencia**. Una guarda de más no cambia ningún resultado (D63).

- **S3**
  - Hallazgo: Sugerencia: el mismo tope de 40 caracteres, visto desde otra función.
  - Ubicación: `src/invoiceflow/normalize.py` (según el registro).
  - Estado: **corregida** en `78f5748`: es el mismo tope, que la corrección anterior quitó de los textos libres.

## U10 (L100)

Tramo `5d0fedc..8f3ae1f` (U10, escalada al ítem 2): aprobada con una sugerencia.

- **S1**
  - Hallazgo: `load_base` vuelve al `.bin` (con `weights_only=True`) ante cualquier `OSError`, también un error de E/S pasajero.
  - Ubicación: `src/invoiceflow/model/train.py:223`.
  - Estado: **dejada con evidencia**. El respaldo lee el `.bin` de la revisión fijada con `weights_only=True`: los mismos pesos; un error pasajero solo cuesta tiempo, y la evaluación lo confirmó sin efecto en las cifras (D63).

## U11 (L100)

Tramo `8f3ae1f..26a338d` (U11, escalada al ítem 6): aprobada con tres hallazgos.

- **W1**
  - Hallazgo: Advertencia: `view` nombra un `SealedConfig` en unas funciones de `baselines.py` y un `ErpView` en otras.
  - Ubicación: `src/invoiceflow/baselines.py`.
  - Estado: **dejada con evidencia**. Los nombres siguen las firmas del plan (sección 15); cambiarlos no cambia nada que se lea (D63).

- **W2**
  - Hallazgo: Advertencia: las tablas de bordes de `test_match.py` viven en un módulo de accesorios fuera del candidato.
  - Ubicación: `tests/unit/test_match.py` y `tests/fixtures/match_cases.py`.
  - Estado: **dejada con evidencia**. Las tablas de bordes son accesorios compartidos, los «casos a mano» del plan (sección 24) (D63).

- **S3**
  - Hallazgo: Sugerencia: el oráculo compara con el literal `"auto_post"` en lugar de `AUTO_POST`.
  - Ubicación: `src/invoiceflow/baselines.py`.
  - Estado: **corregida** en `e7e9739`: el oráculo compara con `AUTO_POST`.

## U12 (L100)

Tramo `26a338d..8d2f1d9` (U12): aprobada con una sugerencia.

- **S1**
  - Hallazgo: `run_checks` toma una muestra de las violaciones sin `ORDER BY`.
  - Ubicación: `src/invoiceflow/warehouse.py:221`.
  - Estado: **dejada con evidencia**. En ninguna corrida hubo una violación; los ejemplos son informativos y el recuento es exacto (D63).

## U13 (L100)

Tramo `8d2f1d9..4b6144d` (U13, escalada al ítem 3): aprobada con dos hallazgos.

- **W1**
  - Hallazgo: Advertencia: `match.erp_view` deja pasar `ErpUnreachable` y `ErpRejected` de una lectura fallida del ERP, mientras `post_invoice` los atrapa.
  - Ubicación: `src/invoiceflow/match.py:278`.
  - Estado: **corregida** en `53e72c4`: una lectura fallida del ERP detiene la tarea y no escribe nada de esa factura; repetirla da las mismas decisiones (D52).

- **S2**
  - Hallazgo: Sugerencia: el `httpx.Client` de `make_client` no se cierra nunca.
  - Ubicación: `src/invoiceflow/erp/client.py:309`.
  - Estado: **dejada con evidencia**. El cliente vive lo que vive el proceso de la tarea y se cierra con él (D63).

## U14 (L102)

Tramo `78f5748..a4e99c3` (U14, escalada al ítem 1): aprobada con seis hallazgos.

- **W1**
  - Hallazgo: Advertencia: `register_model` confirma la fila de `model_registry` en su propia transacción antes de escribir el sello del lanzamiento; si `write_launch_seal` falla, el `except` borra solo la carpeta del artefacto y queda una fila huérfana.
  - Ubicación: `src/invoiceflow/registry.py:310`.
  - Estado: **corregida** en `ae3f21b`: el sello se escribe antes que la fila del registro, y una fila que falta después del sello se vuelve a insertar desde el sello (`b1465c8`; A5 de la evaluación, D62).

- **S2**
  - Hallazgo: Sugerencia: un `_rounded` privado importado entre módulos.
  - Ubicación: `src/invoiceflow/launch.py:212` (importa `_rounded` de `registry.py:170`).
  - Estado: **dejada con evidencia**. El redondeo de lo sellado es uno solo, el de `registry`; copiarlo lo dejaría en dos lugares (D63).

- **S3**
  - Hallazgo: Sugerencia: `checkpoint_ted` se asigna en dos ramas excluyentes.
  - Ubicación: `src/invoiceflow/launch.py:289` y `:292`.
  - Estado: **dejada con evidencia**. Las dos ramas son excluyentes por `kind`: el checkpoint publicado y el punto de partida del modelo diminuto (D63).

- **S4**
  - Hallazgo: Sugerencia: dos ramas idénticas alrededor de `write_launch_seal`.
  - Ubicación: `src/invoiceflow/launch.py` (según el registro).
  - Estado: **corregida** en `ae3f21b`: las dos ramas son una (`outcome in ("written", "post_seal")`).

- **S5**
  - Hallazgo: Sugerencia: el literal `"advertencia"` junto a `BLOCKING`.
  - Ubicación: `src/invoiceflow/launch.py:71` (`WARNING`; `validate.py:24` define `BLOCKING, WARNING, INFORMATIVE`).
  - Estado: **corregida** en `e7e9739`: `launch.py` toma `WARNING` de `validate.py`.

- **S6**
  - Hallazgo: Sugerencia: `metrics.normalise` se distingue por una letra de `normalize`.
  - Ubicación: `src/invoiceflow/metrics.py:60`.
  - Estado: **dejada con evidencia**. `normalise` es la forma normal de una hoja del TED y `normalize` la de un campo de la factura; cambiar el nombre no cambia nada (D63).

## U15 (L102)

Tramo `a4e99c3..53e72c4` (U15, escalada al ítem 1): aprobada con dos advertencias.

- **W1**
  - Hallazgo: Advertencia: el borrador del ERP se carga antes de confirmar el almacén.
  - Ubicación: `src/invoiceflow/daily.py` (según el registro).
  - Estado: **dejada con evidencia**. Es la ventana de falla que D52 ya enuncia: si el proceso cae entre el borrador y la confirmación, la repetición ve el comprobante y manda la factura a revisión como `duplicate`, que es el lado seguro.

- **W2**
  - Hallazgo: Advertencia: la rama `post_failed` no tiene prueba.
  - Ubicación: `src/invoiceflow/daily.py` (según el registro).
  - Estado: **refutada**. Ya no aplica: `tests/integration/test_daily.py` y `tests/integration/test_erp.py` cubren `post_failed`, como confirmó la evaluación (L112).

## U16 (L102)

Tramo `53e72c4..1e12f6e` (U16, escalada al ítem 1): aprobada con tres hallazgos.

- **W1**
  - Hallazgo: Advertencia: `DOCSTATUS` solo traduce `draft`, así que otro `post_as` válido lanzaría `KeyError`.
  - Ubicación: `src/invoiceflow/daily.py:49`.
  - Estado: **dejada con evidencia**. No es alcanzable: `config.py` rechaza cualquier `post_as` que no sea `draft` (D17), como confirmó la evaluación (L112).

- **W2**
  - Hallazgo: Advertencia: `_bad_post` recibe un parámetro `intact` que nunca lee.
  - Ubicación: `src/invoiceflow/evaluation.py:95`.
  - Estado: **corregida** en `328573c`: `_bad_post` ya no recibe el QR que no leía.

- **S3**
  - Hallazgo: Sugerencia: un auxiliar `_group` muerto.
  - Ubicación: `src/invoiceflow/evaluation.py:112`.
  - Estado: **corregida** en `328573c`: el auxiliar se quitó.

## speed (L106)

Tramo `d5168cb..07b1e08` (la unidad de velocidad, cierre del tramo): aprobada con una sugerencia.

- **S1**
  - Hallazgo: `tests/fixtures/daily_world._base` respeta su `folder` solo en la primera llamada por vista, porque el caché `_BASES` devuelve las rutas de la primera base.
  - Ubicación: `tests/fixtures/daily_world.py:72`.
  - Estado: **dejada con evidencia**. El caché es por vista a propósito (lo que depende solo de la vista se hace una vez por sesión) y cada mundo copia los archivos de la base a su carpeta; la evaluación lo confirmó sin efecto (D63).

## U20 (L108)

Tramo `07b1e08..9103cf9` (el largo de lectura del modelo diminuto y U20, escalada al ítem 2): aprobada con una advertencia y una sugerencia sobre las mismas líneas.

- **W1**
  - Hallazgo: Advertencia: en `POST /runs`, la lectura posterior a la corrida ocurre cuando `run_all` ya confirmó; si falla, la respuesta es 503 `warehouse_unavailable` aunque la corrida salió bien, y un cliente podría repetir una corrida que ya ocurrió.
  - Ubicación: `src/invoiceflow/api.py:173-179`.
  - Estado: **corregida** en `2db2648`: una corrida ya guardada se responde con 200, su clave, su estado y su entrega, y los números en `null` si no se pudieron leer (A4 de la evaluación, D63).

- **S2**
  - Hallazgo: Sugerencia, sobre las mismas líneas que la advertencia anterior.
  - Ubicación: `src/invoiceflow/api.py:173-179`.
  - Estado: **corregida** en `2db2648`, con la advertencia anterior.

## U22a (L110)

Tramo `bbc0fff..93b13bf` (566 líneas, escalada al ítem 5): aprobada con dos sugerencias.

- **S1**
  - Hallazgo: La prueba de humo escribe `reports/erpnext_smoke.json` con `write_bytes` y no con `write_atomic`: una corrida interrumpida dejaría un archivo truncado y cada exportación posterior fallaría en `read_smoke` hasta reescribirlo.
  - Ubicación: `tests/e2e/test_erpnext_smoke.py:222`.
  - Estado: **corregida** en `328573c`: la prueba de humo escribe el archivo con `write_atomic`.

- **S2**
  - Hallazgo: La guarda `_LOCAL_PATH` de `read_smoke` rechaza cuatro formas de ruta y la prueba de contrato solo cubre la de una unidad de Windows.
  - Ubicación: `src/invoiceflow/site_export.py:59`.
  - Estado: **corregida** en `328573c`: la prueba de contrato cubre las cuatro formas de ruta.

## U22b (L110)

Tramo `93b13bf..b610b95` (259 líneas, escalada al ítem 6): aprobada con una sugerencia.

- **S1**
  - Hallazgo: `_runs` devuelve tuplas de nueve columnas que se leen por posición: un `SELECT` reordenado desplazaría cada uso sin avisar.
  - Ubicación: `tests/e2e/test_real.py:67-69`.
  - Estado: **corregida** en `b1465c8`: `_runs` devuelve filas que se leen por el nombre de la columna.
