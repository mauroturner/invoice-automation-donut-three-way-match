# Registro de decisiones

Una entrada por decisión, con sus siete campos: Contexto, Opciones, Decisión, Motivo, Afecta, Quién y Estado. Donde había un solo camino razonable, «Opciones» lo dice y explica por qué. Las propuestas del agente de planificación (Opus) quedan para revisión del dueño; las del dueño citan el registro del documento de seguimiento (`odd/tasks/portfolio-projects.md`). El dueño pidió autonomía (S12): cada pregunta abierta de research.md tiene aquí un valor por defecto razonable y reversible, y solo las elecciones de producto que le pertenecen van a su lista (plan, sección 29). Las cifras salen de `research.md`, de los documentos del proyecto 06 y de la comprobación del agente de planificación (`requirements.md`, sección 4), que leyó **solo las particiones `train` y `validation` de CORD** y nunca abrió `test`. Sus scripts y salidas se versionan en `research/plan/` (`p01_cord_validation.py` el checkpoint en la validación de CORD y el largo de los objetivos; `p02_invoice_prototype.py` un prototipo de factura sintética, el QR en páginas enteras, el checkpoint sin ajustar sobre facturas y el rango de CUIT; `p03_finetune_step.py` el tiempo y la memoria de un paso de ajuste; salidas `p01.out` a `p03.out`), y cada uno se corre desde la carpeta del proyecto con `.venv\Scripts\python -I research/plan/<script>.py` (con `HF_HUB_OFFLINE=1` si el checkpoint ya está en el caché).

**Correspondencia con las preguntas abiertas de research.md**: 1 (de dónde salen proveedor, CUIT, fecha, punto de venta y moneda) → D06; 2 (generación local o homologación de ARCA) → D04; 3 (tolerancias de precio y cantidad) → D14; 4 (ajustar Donut con facturas sintéticas o usar el checkpoint, y cómo separar las particiones sintéticas) → D09, D10 y D12; 5 (ERPNext u Odoo) → D16; 6 (pyzbar u OpenCV Aruco) → D07; 7 (reformular el sitio) → D33. Las preguntas que el pedido del plan agregó: la GPU → D11; la honestidad de los datos sintéticos y los CUIT → D05; las afirmaciones del sitio que cambian → D33.

Estados: **vigente**, **pendiente del dueño**, **reemplazada por Dxx**.

---

## D01 Enfoque: un flujo de cuentas a pagar que lee, ancla con el QR, cruza en tres vías y carga solo lo que cierra, evaluado una vez sobre facturas sintéticas que nadie vio

- **Contexto**: el sitio promete leer cada factura, cruzarla con la orden de compra, cargarla sola y dejar para revisar las que no cierran (S9). No hay un conjunto público de facturas argentinas con órdenes y remitos; CORD trae recibos reales sin los campos de una factura argentina (research.md). El checkpoint de Donut sin ajustar no lee una factura sintética (`p02`: 0 de 30 con cantidades, importes y total bien).
- **Opciones**: (a) solo un modelo de documentos medido en CORD, sin flujo ni ERP; (b) un flujo completo sobre facturas sintéticas con discrepancias sembradas, con el modelo ajustado, el QR, reglas explícitas y un ERP, evaluado una vez con criterios fijados antes, y CORD como medida del modelo sobre fotos reales; (c) el flujo sin modelo, leyendo solo el QR.
- **Decisión**: (b), con C01-C10 (`requirements.md`, sección 8.1; D19) y lo esperable escrito: probablemente no se cumpla C03 (diseño nuevo).
- **Motivo**: lo que cuentas a pagar necesita saber es cuánto se carga solo sin cargar mal; (a) no responde eso; (c) no ve las líneas, y la clase `over_receipt` lo prueba (C08).
- **Afecta**: todo el proyecto; `requirements.md`; `fact_evaluation_summary`; el README; el sitio.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D02 Fuentes: los seis Parquet de CORD del commit `7f0115a4` y el PDF de ARCA, fijados por tamaño y SHA-256; el flujo solo lee archivos locales

- **Contexto**: `research/s00_download.py` baja los seis fragmentos (2.307.284.272 bytes) y compara tamaño y SHA-256 con los identificadores LFS de Hugging Face; `research/s08_arca_docs.py` baja `QRespecificaciones.pdf` (91.626 bytes, `a6d0f71e…deaac`).
- **Opciones**: (a) una descarga endurecida dentro del paquete; (b) la descarga queda en los scripts de la investigación y el flujo comprueba tamaño y huella antes de leer.
- **Decisión**: (b). `[source]` con los seis fragmentos (`[[source.cord_files]]`) y el PDF; si uno falta o no coincide, `fetch_source` termina con `SourceError` (salida 3) y nombra el runbook R1.
- **Motivo**: sin red en el flujo desaparece una superficie de ataque y de variación; los valores de confianza ya están medidos (como D02 del 06).
- **Afecta**: `sources.py`; V01, V06; runbook R1; README «Cómo correrlo».
- **Quién**: agente de planificación.
- **Estado**: vigente; una descarga en el paquete no se pide. **Nota del 2026-11-18 (U04)**: **(1) `[Verificar]` del esquema resuelto**: `pq.read_schema` de los fragmentos de `train` y de `validation` da `image: struct<bytes: binary, path: string>` y `ground_truth: string`, sin metadatos de pandas, un solo grupo de filas y creados por `parquet-cpp-arrow version 8.0.0`; `image.path` viene nulo y `meta.split` de la validación dice `valid`. El pie del fragmento de `test` se lee igual (esquema y 100 filas) sin abrir ninguna fila. **(2) Clave nueva `[source] expected_rows`** (800, 100 y 100; 16, 4 y 4 en el modo rápido): sin ella la regla V05 no podía comparar los recuentos del CORD diminuto de las pruebas con los del real; es una clave sin sellar (describe los archivos, no lo que se calcula de ellos). **(3) El registro del modo rápido** (`data/quick/source.lock.json`: `{version, cord: {archivo: {sha256, size}}, arca_spec: {sha256, size}}`) lo escribe `fetch_source` solo después de que las fuentes, los pies y la lectura de `train` y `validation` pasaron sin una regla fallida; el tamaño se compara antes que la huella y un archivo de otro tamaño nunca se hashea. **(4) Medido sobre los archivos reales de `train` y `validation`** (900 recibos, sin abrir las filas de `test`): `menu` es un objeto en 346 recibos de `train` y en 46 de `validation`; hay un recibo con `total_price` en una lista (en `train`), dos sin `total` (en `train`) y las dos imágenes repetidas están en `train` (ninguna en `validation`); la imagen más grande tiene 12.192.768 píxeles, por debajo de `max_image_pixels` (16 millones). **(5) La mitad de V06 que falta** (que el ejemplo de la especificación se reproduce con `synth.qrpayload` byte a byte) se agrega con ese módulo en U06; `check_sources` ya comprueba el tamaño y la huella del PDF.

## D03 Qué se usa de CORD: `gt_parse` con `menu` siempre como lista, los totales como están, las imágenes solo para ajustar y medir; ninguna imagen se republica

- **Contexto**: `menu` es lista en 569 recibos y objeto en 431; dos recibos tienen `total_price` como lista; hay dos imágenes repetidas dentro de su partición; las fotos son de comercios y no se verificó que estén anonimizadas (research.md).
- **Opciones**: (a) usar el `gt_parse` tal cual; (b) convertir `menu` objeto en lista de uno y conservar lo demás; (c) limpiar también los totales con errores de tipeo.
- **Decisión**: (b). Los totales con tipeo quedan como están: el objetivo de Donut es lo anotado, y la métrica compara con lo anotado. Ninguna imagen ni recorte de CORD va al sitio, al tablero ni a la documentación.
- **Motivo**: (a) obliga al modelo a aprender dos formas de lo mismo; (c) cambiaría la referencia contra la que se mide el checkpoint (89,62 % en la prueba) y la comparación de C01 dejaría de ser justa.
- **Afecta**: `cord.py`; RF03; AC03; `data.json`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D04 Facturas de prueba: generación local con el formato de ARCA; la homologación queda para el dueño

- **Contexto**: CORD no tiene ningún QR de ARCA (un solo QR, de pago). ARCA incorpora el QR solo en «Comprobantes en línea» y «Facturador Móvil»; fuera de ahí lo codifica el emisor (especificación). El circuito de homologación (WSAA y WSFEv1) necesita un certificado y un CUIT de homologación que da el Espacio de diálogo, y no hay una lista pública (research.md). El dueño no tiene el certificado.
- **Opciones**: (a) homologación de ARCA para pedir CAE de prueba; (b) generación local de facturas con el formato de la especificación, sin contacto con ARCA; (c) facturas reales de proveedores.
- **Decisión**: (b). `invoiceflow.synth` arma el maestro (proveedores, artículos, órdenes y remitos) y las facturas A con su QR exacto según la especificación (versión 1, 13 campos, `https://www.arca.gob.ar/fe/qr/?p=<Base64>`), dibujadas como imagen, con su verdad y discrepancias sembradas (plan, sección 7). El ejemplo del PDF se reproduce byte a byte en cada corrida (V06). (a) queda como pendiente del dueño (plan, sección 29, ítem 1).
- **Motivo**: (a) no está al alcance y, aun con él, un CAE de homologación no daría órdenes ni remitos para el cruce; (c) traería datos de contribuyentes. El QR propio sigue la especificación: lo que no prueba (que ARCA lo aceptaría) se dice.
- **Afecta**: `synth/`; RF04, RF05; V06; el sitio (fila 1).
- **Quién**: agente de planificación, con el pedido de esta fase («local generation is the default, homologation goes to the owner's pending list»).
- **Estado**: vigente; la homologación, pendiente del dueño.

## D05 Datos sintéticos honestos: CUIT solo en `30-0000xxxx-d`, nombres ficticios, leyenda en cada página y rótulo en cada cifra

- **Contexto**: un CUIT inventado con dígito válido puede coincidir con uno real por azar (research.md, riesgos). El ejemplo de la especificación usa `30000000007`. El rango de serie 1 a 9.999 con prefijo 30 tiene 9.089 CUIT válidos (`p02`).
- **Opciones**: (a) CUIT al azar con dígito válido; (b) solo el rango `30-0000xxxx-d` (el del ejemplo de ARCA), con una guarda en el generador y otra sobre todo lo publicado; (c) CUIT con un prefijo que ARCA no asigna (por ejemplo 99), que fallarían la validación del QR.
- **Decisión**: (b). `synthetic_cuit` nunca devuelve otro; M07 falla una tarea que escriba otro en el ERP o en una salida; `test_every_cuit_in_published_bytes_is_synthetic` recorre cada byte publicado. Los nombres son de una lista fija con «(ficticio)»; cada página dice «DOCUMENTO SINTÉTICO — SIN VALIDEZ FISCAL»; cada cifra que sale de facturas sintéticas lleva «(sintético)» o `synthetic: true`. Ningún componente consulta a ARCA.
- **Motivo**: (a) no se puede defender; (c) rompería la validación del QR y no mostraría el formato real. Que nadie tenga un CUIT del rango es un **supuesto** con riesgo residual no medido: el proyecto no consulta padrones (sería mandar datos a un tercero) y el dueño puede confirmarlo por muestreo (plan, sección 29, ítem 6).
- **Afecta**: `synth/cuit.py`; M07; RNF12; AC04, AC28; los textos; el sitio.
- **Quién**: agente de planificación, con el pedido de esta fase («no real taxpayers' CUITs ever»).
- **Estado**: vigente; la confirmación del rango, pendiente del dueño.

## D06 De dónde salen los campos del emisor: el QR manda; el modelo lee lo impreso y se coteja; la razón social sale del maestro del ERP; sin QR válido, a revisión

- **Contexto**: CORD no anota proveedor, CUIT, fecha, punto de venta ni moneda, así que ningún ajuste sobre CORD los enseña (research.md, pregunta 1). El QR trae CUIT del emisor, fecha, punto de venta, tipo, número, importe, moneda, cotización, receptor y CAE, pero no la razón social, el número de orden ni las líneas.
- **Opciones**: (a) todo del modelo; (b) todo del QR, y el modelo solo para las líneas; (c) el QR manda en sus campos, el modelo lee también lo impreso y la diferencia es un motivo (`qr_print_mismatch`), la razón social sale del maestro por el CUIT, y la orden y las líneas, del modelo.
- **Decisión**: (c), con `match.require_qr = true`: una factura sin QR leído y válido nunca se carga sola (`qr_not_read`, `qr_invalid`).
- **Motivo**: el QR es la referencia fiscal de esos campos y no depende de leer bien la imagen; cotejarlo con lo impreso ataja un QR adulterado (la clase `qr_tampered`) y un error del modelo en la cabecera; (a) pone la cabecera en manos de lo que peor se generaliza (C03); (b) no ve un QR que no coincide con lo impreso. Cargar sin QR es una decisión de producto del dueño (plan, sección 29, ítem 3).
- **Afecta**: `qr.py`, `match.py`, `decide.py`; RF10, RF11, RF13; AC14.
- **Quién**: agente de planificación.
- **Estado**: vigente; cargar sin QR, pendiente del dueño.

## D07 El lector del QR: pyzbar primero y OpenCV Aruco solo si pyzbar no lee nada; zbar en la imagen de Linux

- **Contexto**: con códigos sueltos, pyzbar acierta el 100 % en las ocho condiciones y Aruco falla con 160 px (0 %) y ruido de sal (85 %) (research.md). En páginas enteras (`p02`), pyzbar lee igual o más que Aruco en las 20 combinaciones; a 200 dpi con 4 cm, 30 de 30 en las cinco condiciones contra 12 a 30 de Aruco; a 150 dpi con 2,5 cm, ninguno lee una página degradada. pyzbar necesita la biblioteca nativa zbar: la rueda de Windows la trae y carga en este equipo; en Linux hace falta `libzbar0`. Aruco no tiene dependencias nativas.
- **Opciones**: (a) solo pyzbar; (b) solo Aruco; (c) pyzbar y, si no devuelve nada, Aruco.
- **Decisión**: (c), con el orden en `[qr] readers` y el lector que leyó guardado en `qr_reads.reader`; el `Dockerfile` instala `libzbar0` con su versión fijada.
- **Motivo**: pyzbar lee más en todo lo medido; Aruco cuesta poco como respaldo y no se probó que nunca lea lo que pyzbar no lee, así que C10 lo informa. Lo que más decide es el tamaño impreso y la resolución, y por eso el generador los sortea y la verdad los guarda (plan, sección 7.2).
- **Afecta**: `qr.py`; `docker/Dockerfile`; AC08; `fact_qr_read`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D08 Formato de entrada: PNG, JPEG y PDF de una página con una imagen; lo demás va a cuarentena

- **Contexto**: cuentas a pagar recibe PDF; una factura escaneada es un PDF con una imagen; una generada por un sistema suele traer capa de texto. Leer un PDF cualquiera necesita un renderizador (pypdfium2 o Poppler), que es otra biblioteca nativa.
- **Opciones**: (a) solo imágenes; (b) imágenes y PDF de una página con una sola imagen incrustada, que se extrae con pypdf (Python puro) con topes; (c) cualquier PDF, renderizado.
- **Decisión**: (b). El generador escribe los tres formatos (`file_formats`); un PDF con texto, de varias páginas, con JavaScript o adjuntos, o con más de `pdf_max_objects` objetos va a cuarentena (V15) y su factura a revisión con `file_rejected`.
- **Motivo**: (a) no se parece a la entrada real; (c) agrega una dependencia nativa y una superficie de ataque mayor sin que este proyecto tenga facturas con texto para probarla. Leer PDF con texto queda pendiente (plan, sección 29, ítem 15).
- **Afecta**: `intake.py`; V12-V15; AC09; `LANDING-EXPANSION.md` (pypdf).
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D09 El modelo de documentos: Donut ajustado desde el checkpoint de CORD con dos tareas, repasando CORD en cada época

- **Contexto**: el checkpoint `donut-base-finetuned-cord-v2` (MIT) mide 89,62 % de TED en la prueba de CORD y 93,65 % en la validación (`p01`), pero sin ajustar lee el 5,8 % de las cantidades de una factura sintética y ninguna factura completa (`p02`). El sitio nombra Donut y docTR (S9). docTR no se instaló (pide torchvision y ONNX) y Tesseract no está en el equipo (research.md). Un paso de ajuste cuesta 0,162 s con bf16 y lote 1 (`p03`).
- **Opciones**: (a) el checkpoint sin cambios; (b) ajustar desde `donut-base` (sin CORD) con CORD y facturas sintéticas; (c) ajustar desde el checkpoint de CORD con dos tareas (`<s_cord-v2>` y `<s_factura_ar>`), con los 800 recibos de CORD `train` y las 2.000 facturas sintéticas `train` en cada época; (d) docTR para el texto y reglas para los campos.
- **Decisión**: (c), 6 épocas con candidatas en 2, 4 y 6, AdamW 3·10⁻⁵ con calentamiento de 200 pasos, lote 1 con acumulación de 8, bf16 (plan, sección 9.2). El sitio deja de nombrar docTR (D33).
- **Motivo**: (a) no lee una factura (`p02`); (b) parte de cero en CORD, necesita otra descarga y más épocas, y su cifra de CORD no se podría comparar con la del checkpoint; (c) es el camino más corto a leer facturas sin olvidar CORD, y C01 mide si lo olvida; (d) suma un modelo sin medir y dos dependencias nativas, y la regla de los campos sería otro modelo que mantener. **[Inferido]**: 2.800 imágenes por época a unos 0,2 s son unos 9 min por época, y el lanzamiento entra en RNF02 (3 h).
- **Afecta**: `model/`; RF16; C01-C03; RNF02; el sitio (fila 5).
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D10 Particiones: CORD `train`, `validation` y `test` sellada; sintéticas `train`, `validation_known`, `validation_new`, `test_known` y `test_new`, con diseños, fuentes y proveedores reservados y la prueba generada después del sello

- **Contexto**: el generador es determinista y cualquiera podría producir la prueba en cualquier momento; un modelo de plantillas se ve bien en las plantillas que vio (research.md, «Tamaño» y «Idioma y moneda»). La investigación ya leyó la prueba de CORD con el checkpoint (D42).
- **Opciones**: (a) una sola partición sintética al azar; (b) particiones por semilla, con los mismos proveedores; (c) particiones por semilla y por proveedor y diseño: `train` y `validation_known` con 40 proveedores en L1-L3; `validation_new` con 8 proveedores en L4 y otra fuente; `test_known` con los 40 conocidos y facturas nuevas; `test_new` con 15 proveedores en L5 y otra fuente más; la prueba se genera en cada entrega diaria y solo si el sello tiene `launch` (`GenerationRefusedError`).
- **Decisión**: (c). `validation_new` y `test_new` usan diseños y fuentes distintos entre sí (`_check` lo exige), para que la elección vea un diseño nuevo sin ver el de la prueba. CORD `test` se abre solo en `evaluate_cord` del cierre (AC23).
- **Motivo**: (a) y (b) medirían la memoria de la plantilla, no la lectura; (c) separa lo que importa en cuentas a pagar: los proveedores de siempre (`test_known`) y uno nuevo (`test_new`). La guarda del sello hace que «leída una vez» sea una propiedad del código, no una promesa.
- **Afecta**: `synth/invoices.py`, `cord.py`; RNF07; AC06, AC23; C02, C03, C10.
- **Quién**: agente de planificación.
- **Estado**: vigente. **Nota del 2026-11-18 (U04)**: la guarda de la partición `test` de CORD es `cord.read_split(cfg, "test", closing_run=True)`: mira la pila de llamadas y exige que quien llama sea `evaluate_cord` del módulo `invoiceflow.evaluation` y que la corrida sea la del cierre; cualquier otra cosa es `LeakageError` **en la llamada**, antes de abrir una fila (`read_split` no es un generador: devuelve uno después de comprobar). `LeakageError` y la pregunta «quién llama» viven en `invoiceflow/leakage.py` (no en `truth.py` como esbozaba la sección 23 del plan) para que `cord.py` no importe la verdad sintética; `truth.py` (U06) los reusa. Es un cable de aviso contra un descuido, no una defensa contra quien falsifique el nombre de un módulo: la otra mitad es la prueba estática (`test_no_module_but_the_closing_evaluation_reads_the_test_split`). `fetch_source`, `build_index` y `check_footers` nunca abren las filas de `test` (una prueba espía cada apertura de filas); el índice canónico solo tiene `train` y `validation`.

## D11 La GPU: el ajuste real la exige; la suite por defecto corre en CPU con un modelo diminuto y sin descargas

- **Contexto**: el equipo tiene una RTX 3090 de 24 GB (L90). Donut tarda 0,47 s por recibo en GPU y 3,58 s en CPU (research.md); un paso de ajuste, 0,162 s en GPU (`p03`); en CPU sería del orden de diez veces más **[Inferido]**, unas 10 h para el lanzamiento.
- **Opciones**: (a) todo en GPU, también las pruebas; (b) el ajuste real en GPU (sin CUDA, salida 2 con el runbook R5), la lectura en GPU o CPU, y la suite por defecto en CPU con un `VisionEncoderDecoderModel` diminuto armado de configuración, sin el checkpoint ni la red; las pruebas con el checkpoint o la GPU, con las marcas `model` y `gpu`; (c) un ajuste en CPU con menos datos.
- **Decisión**: (b). La imagen de Docker trae torch de CPU y el servicio lee en CPU; correrlo con la GPU en Docker Desktop es un pendiente (plan, sección 29, ítem 9).
- **Motivo**: (a) haría la suite imposible en un equipo sin GPU y lenta en este; (c) cambiaría el modelo por la máquina. La suite prueba el código del ajuste con el modelo diminuto (que la pérdida baje, que las fichas nuevas existan, que la elección y el artefacto sigan sus reglas); la calidad del modelo real la mide la corrida de U22.
- **Afecta**: `model/train.py`, `model/tiny.py`; RNF04; AC17; marcas de pytest; `docker/Dockerfile`.
- **Quién**: agente de planificación, con el pedido de esta fase («the RTX 3090 must not be required by the default suite»).
- **Estado**: vigente.

## D12 La elección: épocas candidatas medidas en las validaciones, con una tolerancia contra el propio checkpoint en CORD

- **Contexto**: el checkpoint da 93,65 % en la validación de CORD y 89,62 % en la prueba (`p01`, research.md): o la validación es más fácil o el checkpoint la vio. No hay forma de saberlo con los datos públicos.
- **Opciones**: (a) la última época; (b) la de mejor pérdida de validación; (c) entre las épocas que no pierden más de `cord_val_tolerance` (2,0) puntos de TED frente al checkpoint en la misma validación de CORD (medido en el lanzamiento), la de mayor media de las tasas de líneas exactas de `validation_known` y `validation_new`; empate, la más temprana; sin ninguna, `ModelChoiceError`.
- **Decisión**: (c).
- **Motivo**: la tolerancia es relativa al propio checkpoint, así que no depende de si la validación es más fácil o estuvo en su ajuste; la media de las dos validaciones sintéticas premia leer un diseño nuevo sin ver el de la prueba; (a) y (b) no miden lo que importa (la pérdida no es la exactitud de una línea).
- **Afecta**: `model/choose.py`; M04; AC18; `fact_choice`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D13 El artefacto: `safetensors` y `tokenizer.json` con su huella; nunca pickle

- **Contexto**: un checkpoint de PyTorch en `.bin` se lee con `pickle` y puede ejecutar código; el proyecto 06 guardó su modelo en `.npz` sin objetos (D20 del 06).
- **Opciones**: (a) `torch.save`; (b) `save_pretrained(safe_serialization=True)` y el tokenizador en JSON, con el SHA-256 de cada archivo comprobado antes de leer y una negativa si en la carpeta hay `.bin`, `.pt`, `.pth`, `.pkl` o `.ckpt`.
- **Decisión**: (b). El checkpoint base del caché de Hugging Face se carga con `use_safetensors=True` si la revisión fijada lo trae **[Verificar en U10; si no lo trae, se carga una vez en el lanzamiento con la revisión fijada, se guarda en `safetensors` y el resto del proyecto solo lee esa copia]**.
- **Motivo**: un solo camino sin `pickle` para todo lo que el flujo lee; la huella protege de un archivo cambiado, no de quien cambie el archivo y el registro a la vez (como D20 del 06).
- **Afecta**: `model/artifact.py`; M02; AC19; runbook R6.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D14 Las tolerancias del cruce: 2 % del precio unitario, ninguna cantidad por encima de lo recibido, 0,01 de redondeo por línea; en la configuración

- **Contexto**: la documentación de Microsoft Dynamics 365 sobre coincidencia de tres vías dice que las tolerancias son porcentajes configurables y da ejemplos de 15 % (importe total), 10 %, 8 % y 2 % (precio unitario), sin un valor universal (research.md, pregunta 3). Es una decisión de producto.
- **Opciones**: (a) sin tolerancia; (b) 2 % del precio unitario (el ejemplo más estricto de la fuente para el precio), cantidad facturada acumulada ≤ recibida hasta el día (0 de exceso) y ≤ pedida, redondeo de 0,01 por línea y entre el total y el del QR; (c) tolerancias amplias (10 %) para cargar más.
- **Decisión**: (b), en `[match]` y en la huella; «dentro» incluye el borde; los bordes se prueban a mano (`test_every_tolerance_boundary_by_hand`). La clase sembrada `price_off` va de +3 % a +10 %, fuera de la tolerancia.
- **Motivo**: (a) mandaría a revisión diferencias de redondeo; (c) cargaría precios que cuentas a pagar querría ver. Facturar menos de lo recibido es una factura parcial legítima; facturar más, no. Los valores son del dueño (plan, sección 29, ítem 2): cambiarlos después del sello es otro análisis.
- **Afecta**: `match.py`; `config/pipeline.toml [match]`; AC13; C05-C08.
- **Quién**: agente de planificación; la elección final, del dueño.
- **Estado**: vigente; los valores, pendientes del dueño.

## D15 Las reglas del cruce: 18 motivos en un orden fijo, dos y tres vías, y una regla explícita en lugar de un modelo o de RAG

- **Contexto**: el sitio dice «una regla explícita se audita más fácil que otro modelo» y nombra RAG como herramienta del modelo en uso. Microsoft distingue la coincidencia de dos vías (factura contra orden) y de tres (además, cantidad facturada contra recibida).
- **Opciones**: (a) un clasificador entrenado sobre las facturas sintéticas; (b) un modelo de lenguaje con recuperación de la orden (RAG); (c) reglas explícitas con motivos en un orden fijo (plan, sección 11).
- **Decisión**: (c). La búsqueda de la orden es una consulta al ERP por número; no hay generación. El sitio deja de nombrar RAG (D33).
- **Motivo**: (a) aprendería al generador, no a cuentas a pagar; (b) agrega un componente que puede inventar y que no se puede auditar regla por regla; (c) da un motivo por factura que cuentas a pagar entiende, y el oráculo B3 prueba la regla contra la intención del generador (M06).
- **Afecta**: `match.py`, `decide.py`; RF12, RF13; AC13, AC14; `dim_reason`; el sitio (fila 8).
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D16 ERP: un sustituto local con la forma de la API REST de ERPNext, y una prueba de humo contra ERPNext v15 en Docker; Odoo no

- **Contexto**: ERPNext (GPL-3.0) documenta `POST /api/resource/:doctype` con `Authorization: token`; su imagen pesa 895 MB comprimida y su pila (`frappe_docker`, MIT) suma base de datos, caché y varios procesos; no se probó en este equipo. La API externa de Odoo, según su documentación, está ligada a planes de Odoo.com; en una instalación propia no se verificó (research.md). La suite rápida tiene 120 s de presupuesto (D31).
- **Opciones**: (a) ERPNext real para todo; (b) Odoo Community; (c) un sustituto local (FastAPI y SQLite) que sirve la forma de los recursos de Frappe que usa el cliente, con el mismo `ErpClient` sobre `httpx`, y una prueba de humo con ERPNext v15 real (marca `erpnext`, una vez en U22); (d) un ERP de juguete con otra API.
- **Decisión**: (c). Si la pila de ERPNext no levanta en este equipo, se dice aquí con el error y el proyecto sigue con el sustituto.
- **Motivo**: (a) haría cada prueba de integración de minutos; (b) agrega una incertidumbre de licencia y de API que no se resolvió; (d) no probaría nada sobre un ERP real. Con (c), la suite es rápida y la carga es real (crear, buscar, leer de vuelta, no duplicar), y la prueba de humo comprueba que el cliente habla con ERPNext de verdad.
- **Afecta**: `erp/`; RF14; AC15, AC16, AC36; `docker/erpnext/`; el sitio (fila 7).
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D17 La carga en el ERP: un borrador por factura, idempotente por proveedor y número, con lectura de vuelta

- **Contexto**: una factura de compra cargada y confirmada mueve la contabilidad; cargar dos veces la misma es el error que más cuesta deshacer.
- **Opciones**: (a) cargar y confirmar (`docstatus = 1`); (b) cargar como borrador (`docstatus = 0`), buscar antes por (`supplier`, `bill_no`) y no duplicar, y leer de vuelta cada campo.
- **Decisión**: (b). `erp.post_as` acepta solo `draft` (otro valor sale con 2 antes de escribir: la lección B2 del 06); `bill_no` es `<punto de venta>-<número>`.
- **Motivo**: un borrador deja la confirmación a una persona, que es lo que haría cuentas a pagar al empezar con un flujo nuevo; la búsqueda previa y el 409 del sustituto hacen que repetir un día no cree nada (C09).
- **Afecta**: `erp/client.py`, `daily.py`; RF14; AC15; C09.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D18 Líneas base: todo a revisión, solo el QR en dos vías, el checkpoint sin ajustar y el oráculo

- **Contexto**: la investigación midió líneas base de lectura en CORD (0 %, 19,45 %, 55,79 %); para el flujo no hay ninguna. `p02` mostró que el checkpoint sin ajustar no lee una factura.
- **Opciones**: (a) sin líneas base; (b) B0 todo a revisión, B1 solo el QR y dos vías por el total, B2 el checkpoint sin ajustar con las reglas, B3 los campos verdaderos con las reglas.
- **Decisión**: (b), todas con `decide` y sobre las mismas facturas (plan, sección 15).
- **Motivo**: B0 dice qué cuesta no automatizar; B1 dice qué agrega leer las líneas y los remitos (C08); B2 dice qué agrega el ajuste; B3 separa los errores de lectura de los de la regla y, en validación, prueba la regla (M06).
- **Afecta**: `baselines.py`; AC25; C08, C10; `fact_baseline`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D19 Criterios C01-C10 y lo que se vio antes de fijarlos

- **Contexto**: los criterios se fijan antes de construir y lo esperable se escribe (lecciones de los proyectos 03 a 06). Lo visto: el checkpoint en la prueba de CORD (research.md) y en la validación (`p01`), el QR en códigos sueltos (research.md) y en páginas (`p02`), el checkpoint sin ajustar en facturas (`p02`).
- **Opciones**: (a) criterios solo de lectura; (b) de lectura, de QR, de seguridad (carga mala), de automatización (paso directo), de revisión, de comparación con B1 y de integridad del ERP.
- **Decisión**: (b), con los umbrales y las definiciones de `requirements.md`, sección 8.1, y las columnas «visto».
- **Motivo**: en cuentas a pagar, lo que más importa es no cargar mal (C05) y, después, cuánto se carga solo (C06); la lectura (C02, C03) explica las dos. Lo esperable: C01, C02, C05, C07, C08 y C09 deberían cumplirse; C04 y C06, inciertos; **C03 probablemente no se cumpla**.
- **Afecta**: `requirements.md`, sección 8.1; `evaluation.py`; el resumen; el README; el sitio.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D20 Intervalos: Wilson para proporciones y remuestreo pareado por factura o por recibo para las diferencias

- **Contexto**: las facturas de la prueba son independientes por construcción (cada una con su semilla), salvo los duplicados sembrados, que dependen de una anterior; los recibos de CORD también. Las proporciones de C05 están cerca de 0, donde el intervalo normal falla.
- **Opciones**: (a) el intervalo normal; (b) Wilson para una proporción y remuestreo pareado (2.000, semilla 0) para una diferencia; (c) remuestreo por día.
- **Decisión**: (b). Para C08, el remuestreo toma la misma factura para B1 y para el flujo.
- **Motivo**: Wilson se comporta bien cerca de 0 y de 1 (con 0 cargas malas sobre 300, su límite superior es de 1,26 %); el día no agrupa nada que comparta una causa en un generador con semilla por factura **[Inferido]**.
- **Afecta**: `metrics.py`; C01, C05, C08; `[evaluation]`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D21 La prueba se lee una vez: lanzamiento congelado y sello con una huella que cubre cada clave que cambia un valor sellado

- **Contexto**: las lecciones de los proyectos 02 a 06 (la prueba tocada más de una vez, una huella que no cubría una clave, una evaluación con otra configuración antes de sellar). Aquí, además, la prueba sintética se puede regenerar.
- **Opciones**: (a) evaluar cuando haga falta; (b) el diseño del 06: `config_sha256` de la vista sellada (y `config_file_sha256` aparte), partes `launch` y `test` escritas una vez, `post_seal` con otra configuración, la evaluación con otra configuración antes de sellar rechazada, y además la generación de la prueba atada al sello (D10).
- **Decisión**: (b), con `FINGERPRINT_KEYS` del plan, sección 4, y las huellas observadas de CORD, del PDF, del maestro y de las fuentes.
- **Motivo**: un solo camino probado en seis proyectos; la generación atada al sello cierra la puerta que un generador determinista abre.
- **Afecta**: `config.py`, `registry.py`, `synth/invoices.py`; RF17, RF19; AC20, AC24.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D22 Operación simulada: un lanzamiento, 20 días hábiles de octubre de 2026 con 30 facturas cada uno, y un cierre

- **Contexto**: el sitio promete un tablero «día por día»; los proyectos 05 y 06 simularon la operación con entregas diarias.
- **Opciones**: (a) una sola corrida con todas las facturas; (b) un lanzamiento (`L2026-09-30`), 20 entregas diarias (`D2026-10-01` a `D2026-10-28`, días hábiles) de 15 facturas de proveedores conocidos y 15 de nuevos, con los movimientos del maestro (órdenes y remitos) cargados en el ERP el día de su fecha, y un cierre (`C2026-10-29`).
- **Decisión**: (b). Los feriados no se modelan **[Supuesto]**.
- **Motivo**: cargar los remitos el día de su fecha hace que el ERP de cada día sea el que era, y que la regla «remitos hasta el día» sea natural (AC11); 600 facturas dan intervalos útiles (D20); el tablero tiene sus días.
- **Afecta**: `deliveries.py`, `synth/master.py`, `daily.py`; RF06, RF18; `fact_daily_ops`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D23 La verdad sintética vive aparte y ninguna tarea del flujo la lee

- **Contexto**: el generador conoce todo (los campos, la clase sembrada, la decisión esperada); un flujo que lea esa verdad, aunque sea por accidente, daría resultados perfectos y falsos.
- **Opciones**: (a) la verdad junto a la imagen; (b) la verdad en `data/interim/truth/`, leída solo por `truth.read_truth`, que exige que quien llama sea `evaluation` o `baselines`, con una prueba estática sobre los módulos del flujo y una de ejecución; el extractor de prueba que la lee vive en `tests/`, no en `src/`.
- **Decisión**: (b), con M01.
- **Motivo**: es la fuga más fácil de cometer en un proyecto con datos sintéticos y la más difícil de ver después; ponerla en el código, con su prueba, la vuelve imposible (como `FORBIDDEN_COLUMNS` del 06).
- **Afecta**: `truth.py`, `model/extractors.py`; RNF07; AC10; M01.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D24 Almacén: archivos para las facturas, SQLite para el flujo y otro SQLite para el sustituto del ERP

- **Contexto**: las facturas son imágenes; lo leído, lo decidido y lo evaluado es relacional; el ERP es otro sistema y no debe compartir tablas con el flujo.
- **Opciones**: (a) todo en un SQLite; (b) archivos, el SQLite del flujo con tablas de solo inserción (los disparadores del 06) y el SQLite del sustituto, que solo toca `erp/standin.py`.
- **Decisión**: (b).
- **Motivo**: separar el ERP obliga a pasar por su API, como pasaría con ERPNext real; las filas de solo inserción hacen que lo emitido no cambie (RF18).
- **Afecta**: `warehouse.py`, `erp/standin.py`; `sql/schema/`; AC21.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D25 Validación: qué bloquea, qué va a cuarentena y qué se informa

- **Contexto**: un archivo malo en la bandeja es lo normal en cuentas a pagar; una fuente que no coincide no lo es.
- **Opciones**: (a) cualquier problema bloquea la entrega; (b) las fuentes y la entrega bloquean (V01-V06, V10, V11); un archivo malo va a cuarentena y su factura a revisión (V12-V16); las del modelo bloquean donde se indica (M01-M07).
- **Decisión**: (b), con la agregación de cada regla (`source`, `per_run`, `latest`) como el 06.
- **Motivo**: un PDF raro no debe parar el día de los demás; una fuente cambiada sí debe parar todo.
- **Afecta**: `validate.py`, `intake.py`; plan, sección 6; AC09; `validation_rules.json`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D26 Orquestación: n8n en Docker que llama a un servicio local que corre la misma tupla de tareas que la CLI

- **Contexto**: el sitio dice que «cada correo con una factura dispara un flujo» con n8n y APIs REST (S9); el landing declara n8n (S5). S10 leyó la estructura del proyecto 01 como «un DAG de Airflow probado en Docker», con el supuesto de que la estructura se reusa y no las herramientas puntuales. La licencia de n8n es la Sustainable Use License, gratuita para uso propio (S7) y no de código abierto (research.md).
- **Opciones**: (a) Airflow, como los proyectos 01 a 06; (b) n8n con un disparador diario y uno manual que llama por HTTP a una API de entrada (FastAPI) que corre la tupla `TASKS`; el disparador por correo, diseñado y desactivado; (c) n8n con el nodo de ejecutar comandos.
- **Decisión**: (b). La CLI de Windows corre la misma tupla. La prueba de Docker importa el flujo y lo ejecuta dos veces en modo rápido (plan, sección 22).
- **Motivo**: (a) contradiría la página sin necesidad; (b) es fiel al sitio y deja la lógica en Python, donde se prueba; (c) depende de un nodo que las versiones recientes desactivan por defecto **[Verificar en U21]** y mezcla la orquestación con el entorno. Volver a Airflow es barato: la tupla no depende del orquestador. Es una lectura de S10 que el dueño puede corregir.
- **Afecta**: `n8n/`, `api.py`, `docker/`; RF24, RF25; AC34, AC35; el sitio (fila 7).
- **Quién**: agente de planificación.
- **Estado**: vigente; la lectura de S10, para revisión del dueño.

## D27 Servicios locales: la API de entrada y el sustituto del ERP solo en `127.0.0.1` o en la red interna, con un token del entorno

- **Contexto**: el proyecto 05 construyó una API local de solo lectura con sus guardas (D27 del 05). Aquí la API escribe (corre una entrega) y el sustituto recibe cargas.
- **Opciones**: (a) sin autenticación porque es local; (b) token del entorno comparado con `hmac.compare_digest`, 401 sin él, `serve` solo en `127.0.0.1`, en Docker solo en la red interna, errores sin rutas ni trazas, y 409 si el bloqueo está tomado.
- **Decisión**: (b).
- **Motivo**: un servicio que escribe y que otro programa (n8n) llama necesita saber quién llama; el costo es una variable de entorno.
- **Afecta**: `api.py`, `erp/standin.py`; RF24; RNF11; AC16, AC34.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D28 Estado de la corrida, cierre en orden y rechazos, con las correcciones de los proyectos 05 y 06 desde el principio

- **Contexto**: las evaluaciones de los proyectos 02 a 06 encontraron corridas cerradas que se reanudaban, tareas sin corrida que salían con 1 y dejaban `failed`, cargas sin validación y manejadores de fallas que tapaban el error.
- **Opciones**: un solo camino razonable: el diseño del 06 con los rechazos propios de este proyecto (la diaria sin sello, el ajuste real sin CUDA).
- **Decisión**: plan, sección 22, «Estado, cierre y rechazos».
- **Motivo**: cada rechazo tiene su prueba con nombre (plan, sección 24.2) y su origen.
- **Afecta**: `pipeline.py`; AC29, AC30.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D29 Un solo escritor: el bloqueo de los proyectos 05 y 06

- **Contexto**: el bloqueo del 05, con la corrección A6 del 06 (quitar las carpetas vacías que creó), pasó tres evaluaciones.
- **Opciones**: un solo camino: copiarlo con `INVOICEFLOW_*`.
- **Decisión**: `lock.py` del 06; la API de entrada lo toma en cada `POST /runs`.
- **Motivo**: n8n puede disparar dos veces (el manual y el diario); el bloqueo hace que la segunda reciba 409 sin escribir.
- **Afecta**: `lock.py`, `api.py`; AC31, AC34.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D30 Rutas contra la carpeta del proyecto y modo rápido aislado

- **Contexto**: lecciones L14 del 01 y A2 del 03 (rutas contra el directorio de trabajo) y B3 del 01 (el modo rápido escribía en salidas versionadas).
- **Opciones**: un solo camino: el del 06.
- **Decisión**: `INVOICEFLOW_HOME` o la carpeta del proyecto; `pipeline.quick.toml` con todo bajo `data/quick/`, el modelo diminuto y tres días.
- **Motivo**: probado en cuatro proyectos; en el modo rápido además el ERP es un SQLite propio bajo `data/quick/`.
- **Afecta**: `config.py`; AC33.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D31 La suite rápida: límite de 180 s, presupuesto de trabajo de 120 s y un tiempo previsto por unidad desde el principio

- **Contexto**: el proyecto 06 pasó de 75,8 s a 125 s en tres unidades del flujo completo y el dueño eligió «2 subir límite» a 180 s (L79). Aquí torch suma unos 3 s de importación y el modelo diminuto otros pocos.
- **Opciones**: (a) 120 s como el 05; (b) 180 s, el último valor que fijó el dueño, con un presupuesto de 120 s y una tabla de segundos por unidad (plan, sección 24.1); (c) sin límite.
- **Decisión**: (b): 92 s previstos, 28 s de margen.
- **Motivo**: el límite del dueño es el vigente en el portafolio **[Supuesto]**; planificar por unidad evita descubrirlo tarde, como en el 06. Si el dueño prefiere otro para el 07, se ajusta antes de U03 (plan, sección 29, ítem 13).
- **Afecta**: `tests/conftest.py`; RNF15; AC40.
- **Quién**: agente de planificación, con la decisión del dueño del 06 (L79).
- **Estado**: vigente; el límite para el 07, para confirmar por el dueño.

## D32 `pytest-xdist`: no se fija

- **Contexto**: el 06 no lo fijó (D30 del 06) y el dueño eligió subir el límite antes que paralelizar (L79).
- **Opciones**: (a) fijarlo; (b) no.
- **Decisión**: (b).
- **Motivo**: la decisión del dueño; los accesorios de sesión con torch costarían por proceso.
- **Afecta**: `requirements-dev.txt`.
- **Quién**: agente de planificación.
- **Estado**: vigente; pendiente del dueño si la suite se acerca al límite.

## D33 Contradicciones con el sitio: el QR, los campos, docTR, RAG, el ERP y el correo

- **Contexto**: research.md encontró cuatro afirmaciones que no se sostienen (el QR en cada factura ARCA de prueba, los campos atribuidos a CORD, el ajuste con los recibos que enseñaría toda la factura, la licencia de n8n si se amplía el texto); este plan agrega docTR (no se usa), RAG (no hay), el ERP (un sustituto con la API de ERPNext y una prueba de humo) y el correo (desactivado).
- **Opciones**: (a) dejar el sitio como está; (b) proponer cada cambio con su motivo en `SITE-UPDATES.md` (plan, sección 30) para que el dueño decida.
- **Decisión**: (b), 16 filas.
- **Motivo**: S9; el texto es del dueño (research.md, pregunta 7).
- **Afecta**: `SITE-UPDATES.md`; la página `invoices-erp`.
- **Quién**: agente de planificación; el texto final, del dueño.
- **Estado**: pendiente del dueño.

## D34 Tecnologías fuera del landing: PyTorch, Transformers, Pillow, qrcode, pyzbar con ZBar, OpenCV, zss y pypdf

- **Contexto**: el landing declara Python, pandas, SQL, Docker, n8n, REST APIs, FastAPI, TensorFlow, ERPs y Power BI (S5); `LANDING-EXPANSION.md` ya tiene NumPy, SQLite, pyarrow, matplotlib, pytest, pydantic, uvicorn y httpx. Donut está en el sitio como herramienta del proyecto, pero corre sobre PyTorch, no sobre TensorFlow.
- **Opciones**: (a) buscar una versión de Donut en TensorFlow; (b) usar PyTorch y anotar cada librería nueva con el 07.
- **Decisión**: (b): PyTorch (el ajuste y la lectura), Hugging Face Transformers con tokenizers, sentencepiece, huggingface-hub y safetensors (Donut), Pillow (dibujo y lectura de imágenes), qrcode (generar el QR), pyzbar con ZBar y OpenCV (leerlo), zss (la distancia de árbol de la métrica) y pypdf (la imagen del PDF). U23 escribe las filas; las ya anotadas suman el 07.
- **Motivo**: el checkpoint publicado es de PyTorch; reimplementarlo en TensorFlow sería otro proyecto.
- **Afecta**: `requirements*.txt`; RNF09; AC38; `LANDING-EXPANSION.md`.
- **Quién**: agente de planificación.
- **Estado**: vigente; sumar al landing, pendiente del dueño.

## D35 Entorno y versiones: tres archivos de requisitos y torch desde el índice de PyTorch

- **Contexto**: `requirements-research.txt` fija torch 2.14.1 (del índice CUDA 13.0, porque la rueda de PyPI en Windows es de CPU), transformers 5.22.1, tokenizers 0.23.3, sentencepiece 0.2.2, huggingface-hub 2.4.0, numpy 2.4.6, pandas 2.3.3, pyarrow 26.0.1, Pillow 13.0.0, qrcode 8.2, opencv-python-headless 4.14.0.94, pyzbar 0.1.9, zss 1.2.0; en el `.venv` está safetensors 0.8.0. Los proyectos 05 y 06 fijaron fastapi 0.162.0, pydantic 2.14.2, uvicorn 0.54.0, httpx 0.28.1, matplotlib 3.11.2 y pytest 9.2.0. La imagen de Docker no tiene GPU.
- **Opciones**: (a) un solo archivo; (b) `requirements.txt` (flujo y servicios sin torch), `requirements-ml.txt` (más torch y Donut) y `requirements-dev.txt` (más pytest y httpx), con torch instalado antes desde el índice de PyTorch (CUDA en Windows, CPU en Docker) y fijado con `==` para que pip lo encuentre.
- **Decisión**: (b), con las versiones de arriba; pypdf y cualquier dependencia transitiva nueva se fijan en U01 con al menos 14 días en PyPI **[Verificar en U01]**.
- **Motivo**: torch no se instala igual en los dos entornos; separar los archivos deja la regla «cada import es una dependencia fijada» comprobable en los dos.
- **Afecta**: `requirements*.txt`; `docker/Dockerfile`; AC38; runbook R1.
- **Quién**: agente de planificación.
- **Estado**: vigente. **Nota del 2026-11-18 (U01)**: tres hechos. **(1) `[Verificar]` de torch resuelto**: `pip install --dry-run -r requirements-dev.txt` sobre el `.venv` no reemplaza nada, porque `torch==2.14.1` coincide con la versión pública de la rueda `2.14.1+cu130`. **(2) `httpx` es una dependencia de ejecución, no de desarrollo**: `erp/client.py` (U13) lo importa y la prueba de dependencias de U01 exige que todo lo que importan `src/` y `research/` sea un pin directo de `requirements.txt` o de `requirements-ml.txt`; el esbozo de la sección 2 del plan lo ponía en `requirements-dev.txt`. **(3) Regla de los 14 días**: pypdf queda en 6.21.4 (subida el 2026-10-28; 7.0.0 es del 2026-11-06). El entorno de la investigación traía varias transitivas con menos de 14 días y U01 las bajó a la versión más nueva que ya los tenía: pydantic 2.14.3 → 2.14.2 con pydantic-core 2.50.2, click 8.5.1 → 8.5.0, annotated-doc 0.0.7 → 0.0.5, typing-inspection 0.4.5 → 0.4.4, httpx2 y httpcore2 2.16.2 → 2.16.0, markdown-it-py 4.3.0 → 4.2.0, regex 2026.11.18 → 2026.9.29 y typer 0.29.2 → 0.27.3 (`pip check` limpio por su código de salida y los imports de Donut siguen funcionando). Ningún pin de `requirements-research.txt` cambió. `huggingface-hub` 2.4.0 trae `httpx2` (no `httpx`): los dos paquetes conviven y `starlette` 1.10 prefiere `httpx2` para su cliente de prueba (el 05 filtraba el aviso; aquí se decide en U13).

## D36 Accesorios de prueba: un CORD diminuto con el esquema real, facturas a 50 dpi, el extractor de prueba y el modelo diminuto

- **Contexto**: las pruebas no pueden leer los 2,3 GB de CORD ni el checkpoint de 800 MB; el flujo necesita lecturas correctas para probar `auto_post`.
- **Opciones**: (a) solo datos reales con marcas; (b) un CORD diminuto con el esquema real (16, 4 y 4 recibos de 96 × 128 px), el generador real a 50 dpi, un extractor de prueba en `tests/` que lee la verdad con errores sembrados y el modelo diminuto para el código del ajuste.
- **Decisión**: (b) (plan, sección 24).
- **Motivo**: la suite prueba las reglas con lecturas conocidas (el extractor de prueba) y el código del modelo con el diminuto; lo real queda en las marcas `real_data`, `model` y `gpu`.
- **Afecta**: `tests/fixtures/`; `tests/conftest.py`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D37 Salidas para Power BI y diseño del tablero en HTML

- **Contexto**: S4: el `.pbix` no se arma desde aquí; el sitio promete «facturas procesadas, excepciones por motivo y tiempo de carga, día por día».
- **Opciones**: un solo camino: el del 06 (modelo estrella en Parquet desde una especificación, DAX validado sin ejecutarse, HTML autocontenido con vistas previas reales).
- **Decisión**: plan, secciones 19 y 20: 10 dimensiones, 10 hechos, 17 relaciones, 16 medidas y 5 páginas, con la leyenda «Datos sintéticos salvo CORD». «Tiempo de carga» es el tiempo de procesamiento por factura medido.
- **Motivo**: probado en seis proyectos; las medidas de la prueba leen solo filas selladas (D71 del 04).
- **Afecta**: `dashboard/`, `export.py`; AC26, AC27.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D38 Exportación para el sitio: cinco JSON con manifiesto, sin imágenes de CORD y con todo CUIT en el rango sintético

- **Contexto**: el sitio copia los archivos de cada proyecto; las imágenes de CORD no se republican (D03); los CUIT publicados tienen que ser sintéticos (D05).
- **Opciones**: un solo camino: el contrato del 06 con las guardas de este proyecto.
- **Decisión**: plan, sección 21; `example_invoice` lleva cajas y valores de una factura sintética, no una imagen; la imagen de ejemplo va a `docs/figures/` con la leyenda.
- **Motivo**: lo publicado no puede llevar nada que el proyecto no tenga derecho a mostrar ni nada que parezca real sin serlo.
- **Afecta**: `site_export.py`; AC28.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D39 Memoria: medida y con el contador nombrado, también la de la GPU

- **Contexto**: A3 del 02: una cifra de memoria sin contador no dice nada. `p03` midió 7,43 GB con `torch.cuda.max_memory_allocated`.
- **Opciones**: un solo camino: `sysinfo.peak_memory()` del 06 más el pico de GPU con su nombre.
- **Decisión**: RNF03 (16 GB de `PeakWorkingSetSize`, 20 GB de `torch.cuda.max_memory_allocated`).
- **Motivo**: el ajuste es el paso que más memoria pide, y es de la GPU.
- **Afecta**: `sysinfo.py`; RNF03; AC37, AC39.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D40 Idioma y nombres: paquete `invoiceflow`, alcance de commit `invoices-erp`

- **Contexto**: los proyectos usan un paquete corto (`promolift`) y el alcance de la investigación (`invoices-erp`, `a6c5774`).
- **Opciones**: un solo camino razonable.
- **Decisión**: `invoiceflow`, `INVOICEFLOW_*`, flujo de n8n `invoice-intake`, proyecto de Compose `invoiceflow`, alcance `invoices-erp`; documentación en español neutro, código en inglés.
- **Motivo**: consistencia con los proyectos 01 a 06.
- **Afecta**: todo.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D41 Unidades de trabajo, commits y estrategia de entrega

- **Contexto**: 23 unidades y unas 9.400 líneas (plan, sección 27); las revisiones nativas no caben con miles de líneas generadas (L82 y L87 del registro).
- **Opciones**: (a) un commit por fase; (b) un commit por unidad, cada unidad de riesgo alto sola, y lo generado de U22 en un commit aparte.
- **Decisión**: (b), con `ask-on-risk` y la estrategia de PR del dueño (plan, sección 29, ítem 14).
- **Motivo**: cada unidad de riesgo alto se revisa sola; la parte escrita a mano de la corrida real no queda tapada por lo generado.
- **Afecta**: el registro; `git log`.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D42 Lo que la investigación ya vio de la prueba de CORD y qué limita

- **Contexto**: `research/s02_donut.py` corrió el checkpoint sin cambios sobre los 100 recibos de `test` (89,62 % de TED, 84,17 % de recuerdo de hojas). La tarjeta del checkpoint no dice con qué partición se ajustó; la validación da 4,0 puntos más (`p01`).
- **Opciones**: (a) usar la prueba de CORD como si nadie la hubiera visto; (b) declararla vista para el checkpoint y usarla una sola vez para el modelo propio, con C01 como una diferencia pareada contra el checkpoint, y presentar las cifras absolutas como cota superior; (c) no usar la prueba de CORD.
- **Decisión**: (b). La cifra de la investigación no eligió nada de este plan salvo el diseño de C01 (una diferencia, no un valor absoluto).
- **Motivo**: si el checkpoint vio la prueba, los dos modelos la heredan (el propio parte de él), así que la diferencia sigue midiendo el olvido; un valor absoluto no se podría defender; (c) tiraría la única medida sobre fotos reales.
- **Afecta**: C01; `evaluate_cord`; el README; la ficha del modelo; el sitio.
- **Quién**: agente de planificación.
- **Estado**: vigente.

## D43 La cifra del artículo de Donut (90,9 % en CORD) no se cita hasta confirmarla

- **Contexto**: research.md leyó la Tabla 2 del artículo (arXiv:2111.15664) con un resumidor de la versión HTML, no en el PDF, y nuestra métrica es una reimplementación.
- **Opciones**: (a) citarla; (b) no citarla en el sitio ni en el README hasta que alguien la lea en el PDF, y si se cita, junto a la propia con su definición.
- **Decisión**: (b).
- **Motivo**: una cifra leída de segunda mano y medida con otro código no se compara con la propia sin aviso (como D37 del 06).
- **Afecta**: README; ficha; el sitio (fila 16); plan, sección 29, ítem 10.
- **Quién**: agente de planificación.
- **Estado**: pendiente del dueño.
