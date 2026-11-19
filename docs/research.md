# Investigación previa: facturas que se cargan solas al ERP

Documento de la fase de investigación del proyecto 07 (T8 del plan de portafolio). Describe qué datos públicos existen, qué especifica ARCA para el código QR de la factura electrónica, qué modelos de documentos se pueden usar, qué ERP sirve para una demo local y qué contradice el texto de la página del sitio. No planifica ni construye el producto. Cada cifra sale de un script de `research/` (sección «Scripts y salidas») o de una fuente citada con su URL. Lo que es supuesto se marca como tal.

## Hallazgos principales

1. **CORD es real, pero no contiene lo que el sitio sugiere.** Son 1.000 recibos indonesios anotados (800 de entrenamiento, 100 de validación, 100 de prueba), licencia CC BY 4.0. Las anotaciones cubren líneas de menú, subtotal, impuestos y total. **No tienen proveedor, CUIT, fecha, punto de venta, número de factura ni moneda**.
2. **El QR de ARCA no está en CORD.** De los 1.000 recibos, un único código QR decodifica (recibo de índice 818, una cadena tipo pago, no el formato de ARCA). El escaneo también marcó 36 candidatos, y la revisión visual de uno de ellos (índice 102) no muestra ningún código: son falsos positivos. El formato de ARCA sí está especificado (versión 1, 13 campos), y la especificación reproduce exactamente su ejemplo en Base64.
3. **El QR de ARCA lo emite ARCA solo en dos servicios.** Según la especificación, el organismo incorpora el QR cuando la factura se autoriza mediante «Comprobantes en línea» o «Facturador Móvil». Una factura de prueba generada por otro camino no trae QR de ARCA: el QR de nuestras facturas sintéticas lo codificaría el propio generador.
4. **Donut (checkpoint publicado, licencia MIT) mide 89,6 % de precisión basada en distancia de árbol** sobre los 100 recibos de prueba, con 84,2 % de recuerdo por campo y 0,47 s por recibo en GPU. El paper reporta 90,9 % en CORD. Nuestra métrica es una reimplementación, no el script oficial.
5. **Donut no sirve para los campos de la factura argentina** (proveedor, CUIT, fecha, moneda). CORD no los anota, así que ningún ajuste fino sobre CORD puede enseñarlos. Ese trabajo tendría que venir de facturas sintéticas propias.
6. **Para el ERP, la recomendación provisional es ERPNext** (GPL-3.0, API REST documentada para crear facturas de proveedor). Odoo Community (LGPL-3) es la alternativa; su API externa, según su documentación, está ligada a planes de Odoo.com, y el uso en una instalación propia no quedó verificado.
7. **El sitio tiene al menos cuatro afirmaciones que no se sostienen con las fuentes** (sección «Contradicciones con el sitio»).

## Objetivo

Responder las siete preguntas de la investigación del plan (CORD, QR de ARCA, modelos de documentos, coincidencia con orden de compra, ERP, líneas base y riesgos) con evidencia verificable, y dejar lista la base para la fase de requerimientos. Quedan fuera la construcción del flujo, el generador de facturas definitivo, la instalación del ERP y cualquier dato real de contribuyentes.

## Fuentes

| Fuente | Uso | Licencia / condición | URL |
| --- | --- | --- | --- |
| CORD v2, datos en Hugging Face (commit fijado `7f0115a4b758a71d6473b8d085751692da2fef98`) | Recibos, anotaciones | CC BY 4.0 (metadatos del repositorio: `license:cc-by-4.0`) | https://huggingface.co/datasets/naver-clova-ix/cord-v2 |
| CORD, repositorio y texto de licencia | Descripción del esquema, licencia | `LICENSE-CC-BY`: «Attribution 4.0 International» | https://github.com/clovaai/cord |
| Especificación del QR de factura electrónica (ARCA) | Campos, Base64, URL | Documento público del organismo | https://www.afip.gob.ar/fe/qr/documentos/QRespecificaciones.pdf |
| Página de conceptos del QR (ARCA) | Alcance del QR | Público | https://www.afip.gob.ar/fe/qr/conceptos-generales.asp |
| Preguntas frecuentes de web services (ARCA/AFIP) | CUIT de homologación | Público | https://www.afip.gob.ar/sire/documentos/Preguntas-Frecuentes-WS.pdf |
| Donut, modelo `naver-clova-ix/donut-base-finetuned-cord-v2` (commit `8003d433113256b4ce3a0f5bf604b29ff78a7451`) | Medición | MIT (tag `license:mit`) | https://huggingface.co/naver-clova-ix/donut-base-finetuned-cord-v2 |
| Paper de Donut (arXiv:2111.15664) | Cifra de referencia de CORD | Licencia de distribución de arXiv (no verificada en el texto) | https://arxiv.org/abs/2111.15664 |
| docTR (`mindee/doctr`) | Modelo alternativo | Apache-2.0 (API de GitHub) | https://github.com/mindee/doctr |
| Microsoft Dynamics 365, políticas de coincidencia de facturas | Definición de two-way y three-way match, tolerancias | Documentación pública | https://learn.microsoft.com/en-us/dynamics365/finance/accounts-payable/three-way-matching-policies |
| Documentación REST de Frappe | Creación de documentos por API | Pública | https://docs.frappe.io/framework/user/en/api/rest |
| Documentación de la API externa de Odoo 19 | Acceso por API | Pública | https://www.odoo.com/documentation/19.0/developer/reference/external_api.html |

Las fuentes de terceros (guías de facturación y blogs) se usaron solo para orientar búsquedas. No se cita ninguna cifra de ellas.

## Cómo se verificaron los datos

- **Descarga.** `research/s00_download.py` baja los seis fragmentos Parquet de CORD v2 al commit fijado, compara tamaño y SHA-256 con los identificadores LFS que la API de Hugging Face publica para ese commit, y guarda un `manifest.json` en `data/raw/cord/` (git-ignorado). Resultado: **6 de 6 fragmentos verificados, 2.307.284.272 bytes en total**. Los SHA-256 de los fragmentos quedan en el manifiesto; los de los archivos de GitHub son `README.md` 10.974 bytes (`827f4828…e9e7f6`) y `LICENSE-CC-BY` 18.650 bytes (`7e7170e3…a2661`).
- **Estructura.** `s01_structure.py` lee cada recibo, parsea la anotación y cuenta. No usa imágenes salvo para hashearlas y leer su tamaño.
- **Especificación de ARCA.** `s08_arca_docs.py` baja el PDF (91.626 bytes, SHA-256 `a6d0f71e…deaac`) y la página de conceptos (25.906 bytes, SHA-256 `0dbe8cc1…f80962`), y extrae el texto del PDF con `pdftotext`. `s04_arca_qr.py` reproduce el ejemplo del PDF: el JSON analiza, su Base64 coincide **byte a byte** con el texto del PDF (`base64_matches_pdf_example: True`), y los 13 campos son los mismos.
- **Modelo.** `s02_donut.py` corre el checkpoint publicado con `transformers` 5.22.1 y `torch` 2.14.1 + CUDA 13.0 en la RTX 3090, en fp32, un recibo a la vez. Verificado: CUDA disponible, GPU nombrada, memoria pico 1,53 GB.
- **Ejecuciones.** Cada script escribe su salida en `data/interim/research/`, con registro en `data/interim/research/logs/` (ambos git-ignorados). Ninguna cifra de este documento se escribió a mano sin esa trazabilidad, salvo las marcadas como citadas.

## Estructura de los datos

**Partición y conteos** (`s01`):

| Partición | Recibos | Observación |
| --- | --- | --- |
| train | 800 | Fragmentos 0 a 3 |
| validation | 100 | El campo `meta.split` vale `valid`; el README dice `dev` |
| test | 100 | |
| **Total** | **1.000** | 998 imágenes distintas: 2 recibos repetidos dentro de su partición, ninguno entre particiones |

`meta.image_id` no es único entre particiones (800 valores distintos en train, máximo 3 repeticiones): no sirve como clave global.

**Tamaño de imagen** (píxeles): ancho mínimo 204, mediana 864, máximo 3.024; alto mínimo 336, mediana 1.296, máximo 4.224; 74 tamaños distintos. Son fotos de celular, no PDFs.

**Esquema de la anotación.** Cada recibo trae `gt_parse` (el árbol que Donut aprende), `valid_line` (líneas con palabras, cajas y categoría), `meta`, `roi`, `repeating_symbol` y `dontcare`. Las claves de primer nivel de `gt_parse` son solo `menu`, `sub_total`, `total` y, en un recibo, `void_menu`. Las categorías de `valid_line` son 29 distintas.

**Presencia por campo** (recibos de 1.000):

| Campo | Recibos | Porcentaje |
| --- | --- | --- |
| `total.total_price` | 972 | 97,2 % |
| `sub_total.subtotal_price` | 661 | 66,1 % |
| `sub_total.tax_price` | 444 | 44,4 % |
| `sub_total.service_price` | 123 | 12,3 % |
| `total.cashprice` | 648 | 64,8 % |
| `total.changeprice` | 620 | 62,0 % |
| `total.creditcardprice` | 151 | 15,1 % |
| `total.menuqty_cnt` | 283 | 28,3 % |

**Líneas de menú.** Todos los recibos tienen `menu`. Aparece como lista de objetos en 569 casos y como objeto único en 431: **el esquema no es uniforme**. Por recibo hay entre 1 y 22 líneas (mediana 2, media 2,58).

**Qué falta respecto de una factura argentina.** Ninguna clave de proveedor, nombre comercial, CUIT, fecha, número de comprobante, punto de venta ni moneda. El README de CORD dice que `store_info`, `payment_info` y `etc` se quitaron de las clases publicadas por motivos legales, y la lista de claves lo confirma.

**Importes.** Son cadenas sin unidad. Ejemplos: `60.000` (punto como separador de miles), `Rp 16.500`, `1,591,600`, `226,500.00`, `2.00` (cantidad). El formato sugiere rupias indonesias (IDR), pero el dato no lo declara: **supuesto**.

**Calidad de las anotaciones** (`s01`):

- Total legible: 958 de 1.000. Sin total: 28. Total no legible: 14, con errores de tipeo como `39,200,00`, `Rp 57,0000` y `TOTAL 47,499`.
- Coherencia subtotal + impuesto + servicio + otros − descuento = total: **569 coinciden y 66 no** (sobre 635 recibos comparables).
- Suma de los precios de línea = total: 501 coinciden, 438 no, 19 no comparables. La diferencia es esperable porque el precio de línea no siempre incluye impuesto o servicio; la comparación de subtotales es la más informativa.
- Dos recibos tienen `total_price` como lista (dos valores). Son casos aislados que hay que tratar.

**Idioma.** El README dice que son «miles de recibos indonesios». Una heurística de palabras (TUNAI, KEMBALI, PAJAK, TOTAL, JUMLAH, KASIR, SUBTOTAL) marca 464 de 1.000 recibos: es una medida débil, no una prueba de idioma por recibo.

## Código QR: CORD y especificación de ARCA

### ¿Contiene CORD algún QR? (`s03`)

Se escanearon los 1.000 recibos con `cv2.QRCodeDetector.detectAndDecode` tras reducir cada imagen a 1.280 píxeles en su lado mayor. Resultado: **36 candidatos, 1 decodificado**, en el índice 818 (orden de lectura: test 0 a 99, train 100 a 899, validation 900 a 999). La imagen muestra un QR impreso bajo el pie del recibo, con una cadena de 64 caracteres (`9DKMvj…`), que parece un código de pago o de comprobante electrónico, no el formato de ARCA. El recibo 102, uno de los 36 candidatos, no tiene ningún QR visible: es un falso positivo. Además, la palabra `QR` aparece en la anotación de 6 recibos.

**Conclusión:** CORD contiene al menos un QR impreso, de tipo no fiscal argentino. El número real de QR no está establecido: harían falta una revisión manual o un decodificador más robusto para contarlos. Para el proyecto basta con saber que CORD no sirve para entrenar la lectura del QR de ARCA.

### Qué especifica ARCA (`s08`, `s04`)

- **Alcance.** «La Agencia de Recaudación y Control Aduanero dispuso la implementación obligatoria de un código de respuesta rápida “QR” en los comprobantes electrónicos» (página de conceptos). La especificación añade: «Cuando la solicitud de autorización de emisión del comprobante electrónico se realice mediante el servicio “Comprobantes en línea” y/o la aplicación “Facturador Móvil”, el propio sistema de este Organismo incorporará el código “QR” al comprobante autorizado.» **Fuera de esos dos caminos, el emisor debe codificar el QR.**
- **Formato del código.** El texto codificado es `{URL}?p={DATOS CMPBASE64}`, con `{URL}` = `https://www.arca.gob.ar/fe/qr/` (definición del PDF). **Discrepancia interna:** el ejemplo del propio PDF usa `https://www.afip.gob.ar/fe/qr/?p=…`. Ambos hosts aparecen en la especificación.
- **Datos (versión 1 del JSON), 13 campos:**

| Campo | Tipo según la especificación | Obligatoriedad |
| --- | --- | --- |
| `ver` | numérico de 1 dígito (versión, valor 1) | obligatorio |
| `fecha` | full-date RFC 3339 (AAAA-MM-DD) | obligatorio |
| `cuit` | numérico de 11 dígitos (emisor) | obligatorio |
| `ptoVta` | numérico hasta 5 dígitos | obligatorio |
| `tipoCmp` | numérico hasta 3 dígitos | obligatorio |
| `nroCmp` | numérico hasta 8 dígitos | obligatorio |
| `importe` | decimal hasta 13 enteros y 2 decimales | obligatorio |
| `moneda` | 3 caracteres (p. ej. `DOL`) | obligatorio |
| `ctz` | decimal hasta 13 enteros y 6 decimales (1 en pesos) | obligatorio |
| `tipoDocRec` | numérico hasta 2 dígitos | de corresponder |
| `nroDocRec` | numérico hasta 20 dígitos | de corresponder |
| `tipoCodAut` | string: `A` (CAEA) o `E` (CAE) | obligatorio |
| `codAut` | numérico de 14 dígitos | obligatorio |

- **Ejemplo del PDF, reproducido.** El JSON `{"ver":1,"fecha":"2020-10-13","cuit":30000000007,…}` codificado en Base64 da exactamente el texto del PDF. El CUIT `30000000007` del ejemplo tiene dígito verificador válido (regla de módulo 11 de AFIP).

### CUIT de homologación

Las preguntas frecuentes de web services dicen que los CUIT para homologación son «oportunamente otorgadas por Espacio de diálogo». **No hay una lista pública de CUIT de prueba.** Por eso las facturas sintéticas usan CUIT inventados con dígito verificador válido (prefijo 30 y número de serie bajo, 40 generados en `s04`, todos válidos). Eso no garantiza que ningún CUIT generado no pertenezca a un contribuyente real: **supuesto** y riesgo (ver «Riesgos y huecos»).

### Generación y lectura (`s04`)

Se codificaron 40 cargas sintéticas (todas con la versión 12 del QR, 65 módulos por lado) con `qrcode` 8.2, nivel de corrección M. Cada una se decodifica y se compara exactamente con la URL original. Los 40 pasan el ida y vuelta de Base64 y de JSON.

| Condición (n = 40) | OpenCV `QRCodeDetector` | OpenCV `QRCodeDetectorAruco` | pyzbar (zbar) |
| --- | --- | --- | --- |
| limpio, 10 px por módulo | 65,0 % | 100,0 % | 100,0 % |
| limpio, 20 px por módulo | 65,0 % | 100,0 % | 100,0 % |
| JPEG calidad 30 | 67,5 % | 100,0 % | 100,0 % |
| desenfoque (radio 2) | 77,5 % | 100,0 % | 100,0 % |
| rotación de 25° | 92,5 % | 100,0 % | 100,0 % |
| 160 px de lado | 0,0 % | 0,0 % | 100,0 % |
| poco contraste y brillo bajo | 67,5 % | 100,0 % | 100,0 % |
| ruido de sal (2 % de píxeles) | 60,0 % | 85,0 % | 100,0 % |

**Recomendación para el plan:** pyzbar, con la advertencia de que su biblioteca nativa zbar imprime avisos (`pdf417.c`) que no afectan el resultado en este equipo. OpenCV con `QRCodeDetectorAruco` es la alternativa sin dependencias nativas. El detector clásico de OpenCV no sirve para este tamaño de código. Las pruebas usan cargas sintéticas limpias; las fotos reales de factura serán más difíciles y deben medirse con las primeras facturas reales.

## Modelos de documentos

### Donut (`s02`, `s05`)

**Qué es y cuánto cuesta en este equipo.** Modelo de transformador de imagen a texto que no usa OCR previo, licencia MIT, checkpoint `donut-base-finetuned-cord-v2`, afinado en CORD según su tarjeta (la tarjeta no nombra la partición de entrenamiento). La carga tardó 152,9 s la primera vez (incluye la descarga), y 1,9 s después con el caché. Memoria pico en GPU: 1,53 GB.

**Medición** (`s02`, sobre los 100 recibos de prueba, es decir la partición completa, selección sembrada con semilla 20261118):

| Medida | GPU RTX 3090, fp32 | CPU, fp32 (10 recibos) |
| --- | --- | --- |
| Precisión basada en distancia de árbol (media) | **89,62 %** | 89,75 % |
| Precisión (mediana) | 96,58 % | 90,59 % |
| Recuperación de hojas (campo + valor exacto) | **84,17 %** (1.095 de 1.301) | 79,59 % (78 de 98) |
| Recibos con árbol idéntico | 35 de 100 | 3 de 10 |
| Segundos por recibo (media / mediana) | **0,466 / 0,414** | 3,581 / 3,507 |

**Métrica.** Precisión por árbol: `1 − TED(predicción, gt) / TED(árbol vacío, gt)`, recortada a cero y promediada por recibo. TED es la distancia de edición de árbol de Zhang-Shasha, con la biblioteca `zss` 1,2,0. Es una reimplementación de la métrica que el paper llama «TED-based accuracy»; **no es el script oficial de los autores**, así que la comparación con su cifra es orientativa.

**Recuperación por campo** (GPU, hojas de la anotación): `total.total_price` 97,9 %; `total.changeprice` 100,0 %; `total.cashprice` 95,4 %; `sub_total.subtotal_price` 92,2 %; `sub_total.tax_price` 95,2 %; `menu[].price` 89,9 %; `menu[].cnt` 89,9 %; `menu[].nm` 76,4 %; `menu[].unitprice` 72,7 %.

**Cifra de referencia del paper.** La Tabla 2 del paper (arXiv:2111.15664) da, para CORD, precisión 90,9 %, F1 84,1 y 1,2 s por muestra. Lo leímos mediante un resumidor de la versión HTML, no en el PDF: **hay que confirmarla en el texto original antes de citarla**. Otras líneas base de esa tabla, según la misma lectura: LayoutLM 81,3 %, LayoutLMv2 82,4 %, BROS 70,0 %, SPADE 75,8 % y WYVERN 46,9 %. El paper menciona también 91,1 % con imágenes de 1280×960 en un texto aparte: no se pudo confirmar.

**Fuga posible.** La tarjeta del modelo dice que fue fue afinado en CORD sin indicar el reparto. Si incluyó la partición de prueba, nuestra cifra estaría inflada. **Supuesto:** no la incluyó. Se supone también que el paper usa la partición de prueba estándar.

**Límite del modelo para nuestro caso.** Donut aprende la estructura de la anotación. Aplicado a una factura argentina, faltarían los campos que CORD no anota (proveedor, CUIT, fecha, moneda, punto de venta). Y el texto está en indonesio con importes en rupias, no en español con pesos.

### docTR y Tesseract (sin medir)

- **docTR** (`python-doctr` 1.1.0, publicado en PyPI el 21 de agosto de 2026). Licencia Apache-2.0. Sus dependencias declaradas incluyen `torch`, `torchvision`, `onnx`, `opencv-python`, `pypdfium2`, `pyclipper`, `shapely`, `rapidfuzz` y `huggingface-hub`. **No se instaló**: pedía `torchvision` y dependencias de ONNX que no entraban en el alcance de esta fase. Queda como pendiente de la fase de planificación.
- **Tesseract.** No está en el `PATH` de este equipo y no se instaló. `pytesseract` es solo el envoltorio.

Sin OCR real no hay línea base de OCR más regex medida; la sección siguiente usa un sustituto que es una cota superior, no una línea base real.

## Coincidencia con la orden de compra

CORD no tiene órdenes de compra. Un conjunto sintético necesitaría, por orden: número, fecha, proveedor (nombre y CUIT inventado), líneas (artículo, cantidad, precio unitario, importe neto), impuestos, total y moneda; y, por cada orden, un remito (cantidades recibidas y fecha) y una factura generada con el mismo generador que el QR.

**Discrepancias sembradas** que el plan debería incluir y etiquetar: cantidad facturada mayor a la recibida; precio unitario fuera de tolerancia; total que no suma las líneas; proveedor o CUIT distinto al de la orden; factura sin orden; factura duplicada; fecha fuera de rango; moneda distinta.

**Reglas de coincidencia (fuente: Microsoft Dynamics 365).** La documentación distingue:

- *Two-way match:* la factura se compara con la línea de la orden de compra (precio y cantidad).
- *Three-way match:* además, la cantidad facturada debe coincidir con la cantidad recibida en el remito («the quantity on the invoice line matches the quantity received»).

Las tolerancias de precio son porcentajes configurables. La documentación da ejemplos con 15 % (importe total), 10 %, 8 % y 2 % (precio unitario), y no establece un valor universal. **No hay una tolerancia recomendada en la fuente:** la elección es una decisión de producto para el plan.

Fuera de la documentación de Microsoft, un resumen de búsqueda sobre Sage X3 y NetSuite describe tolerancias de cantidad en límites inferior y superior; no se leyó la fuente completa y no se cita como cifra.

## ERP

| Candidato | Licencia (GitHub) | Imagen oficial (tamaño comprimido) | Creación de factura de proveedor por API | Observaciones |
| --- | --- | --- | --- | --- |
| **ERPNext** (`frappe/erpnext`) | GPL-3.0; 41.414 estrellas; último push 18-nov-2026 | `frappe/erpnext:v15`, 895 MB | `POST /api/resource/:doctype` con `Authorization: token api_key:api_secret` (documentación de Frappe). El doctype es `Purchase Invoice` | Pila más pesada: su despliegue oficial con `frappe_docker` (MIT) usa base de datos y caché; no se probó |
| **Odoo Community** (`odoo/odoo`) | LGPL-3 (texto de `LICENSE`); 55.894 estrellas | `library/odoo:latest`, 808 MB | La documentación revisada de la API externa dice que su acceso está «solo disponible en planes Custom de Odoo» (planes de Odoo.com); **no verificamos la API de una instalación Community propia** | Un crear de factura de proveedor usaría `account.move` con `move_type = in_invoice`: esto es conocimiento general, **no verificado aquí** |
| Dolibarr | no evaluado | no evaluado | no evaluado | Se omitió por falta de tiempo |

**Recomendación provisional: ERPNext.** Razones: la creación por API está documentada en la fuente oficial, su licencia es GPL-3.0 y tiene una imagen oficial única. **Contra:** la pila completa es más pesada que una imagen de Odoo, y no probamos que `frappe_docker` levante en este equipo. **Sin instalar nada:** no se ejecutó `docker run`, ni se descargó imagen alguna más que sus metadatos.

## Líneas base

| Línea base | Medida | Resultado | Notas |
| --- | --- | --- | --- |
| Siempre vacío | recuperación de hojas | **0,0 %** (`s05`) | Por construcción, la precisión por árbol también es 0 |
| Valor más frecuente por campo, ajustado en train | recuperación de hojas en test | **19,45 %** (253 de 1.301) (`s05`) | Acierta `menu[].cnt` (62,4 %) y `total.cashprice` (24,6 %); falla en `total.total_price` (3,2 %) |
| Regex sobre el texto de las palabras anotadas (sustituto de OCR, cota superior) | total exacto | **55,79 %** (53 de 95 recibos con total legible) (`s05`) | No es OCR: usa las palabras anotadas. Un OCR real sería peor |
| Donut sin ajuste fino adicional (checkpoint CORD publicado) | precisión por árbol y recuperación | 89,62 % y 84,17 % (`s02`) | Es el referente que el plan debe superar en campos de CORD |

**Qué sería «bueno» para el plan** (propuesta, no derivada de datos): para los campos que CORD sí cubre, superar la línea de Donut en el conjunto de facturas propias; para los campos de factura argentina, no hay referente público, así que el plan debe fijar uno sobre el conjunto sintético con su propio etiquetado.

## Literatura

- Park, S., et al. (2019). *CORD: A Consolidated Receipt Dataset for Post-OCR Parsing*. Document Intelligence Workshop, NeurIPS. Citado en el README de CORD.
- Hwang, W., et al. (2019). *Post-OCR parsing: building simple and robust parser via BIO tagging*. Mismo workshop. Citado en el README de CORD.
- Kim, G., et al. (2021). *OCR-free Document Understanding Transformer* (Donut). arXiv:2111.15664. Tabla 2 con CORD (cifras de la lectura del HTML, a confirmar).
- Especificación de ARCA: «Especificaciones del QR incluido en las facturas electrónicas», versión 1 del JSON (`QRespecificaciones.pdf`).
- Microsoft Learn, «Three-way matching policies» (Dynamics 365 Finance).

## Contradicciones con el sitio

Cada afirmación de `src/i18n/projects/invoices-erp.ts` (solo lectura, en marca-personal) se comparó con las salidas de `s01`, `s03`, `s04`, `s06` y `s08`, con el verificador `s07`:

1. **«1.000 recibos anotados, aprox.»** — **Consistente.** Son 1.000 exactos en la versión pública (800/100/100). El README de CORD dice que el conjunto completo tiene «más de 11.000 recibos», pero solo se publicó la muestra de 1.000.
2. **«CC BY 4.0, licencia de CORD».** — **Consistente**, según `LICENSE-CC-BY`.
3. **«QR en cada factura ARCA de prueba».** — **Contradicho en su sentido natural.** CORD no es fuente de QR de ARCA: su único QR decodificable tiene otro formato. ARCA incorpora el QR solo en «Comprobantes en línea» y «Facturador Móvil». Un QR en la factura de prueba lo codificaría nuestro generador, no el servicio de prueba de ARCA. El sitio debe decir eso.
4. **Campos «Proveedor y CUIT del emisor», «Fecha de emisión», «Moneda» (atribuidos a los recibos anotados).** — **Contradichos para CORD.** Ninguno de esos campos existe en CORD (`s01`, `s07`). El CUIT del emisor sí existe en el QR de ARCA, pero eso es otra fuente.
5. **«Un modelo abierto como Donut… se ajusta con los recibos anotados».** — **Parcialmente consistente.** Donut está afinado en CORD. Pero ese ajuste no puede enseñar proveedor, CUIT, fecha ni moneda, porque CORD no los anota. La frase sugiere que el ajuste cubre toda la factura.
6. **«ERP de código abierto, como ERPNext u Odoo Community».** — **Consistente.** ERPNext (GPL-3.0) y Odoo Community (LGPL-3).
7. **n8n en la sección de producto.** — **El sitio no la presenta como código abierto; si el texto se amplía, no conviene hacerlo.** Su `LICENSE.md` es la «Sustainable Use License» (leída con `s06`), que no es una licencia de código abierto según la definición de la OSI.
8. **«Recibos… en indonesio».** — **Consistente** con el README de CORD. La heurística de palabras es débil.
9. **«Renglones de la factura» e «Importe total».** — **Consistente:** CORD anota líneas de menú y total.

## Riesgos y huecos

- **Privacidad.** Los recibos son fotos de comercios. Las vistas previas que generamos muestran encabezados borrosos, pero no se ha verificado que todos los recibos estén anonimizados. No se republican imágenes.
- **Datos sintéticos.** Las facturas de prueba deben declararse como sintéticas en el sitio y en cada archivo. Un QR válido con CUIT inventado no prueba nada sobre facturación real.
- **CUIT.** Los CUIT generados pueden coincidir con contribuyentes reales por azar. Mitigación propuesta: prefijo 30 y serie baja, y no usar ningún CUIT en un sistema conectado a ARCA. Riesgo residual **no medido**.
- **Fuga.** Donut pudo ver la partición de prueba de CORD. No hay forma de descartarlo con los datos públicos. Cualquier cifra del plan sobre CORD debe llevar esa advertencia.
- **Idioma y moneda.** Donut y CORD son indonesios con importes en rupias. Una factura argentina en español con pesos y CUIT no se parece a ellos. Es un cambio de dominio, no un ajuste.
- **Tamaño.** 1.000 recibos y 100 de prueba bastan para medir, no para entrenar un extractor robusto. El plan necesitará más datos sintéticos.
- **GPU.** La inferencia en CPU tarda 3,58 s por recibo, casi ocho veces más que en GPU. En una máquina sin GPU, el procesamiento de un lote grande llevaría horas.
- **Pilas no probadas.** ERPNext en Docker, docTR, Tesseract y la API de Odoo Community quedan sin verificar.
- **Dependencias externas.** `pdftotext` (Poppler) no está fijado en `requirements-research.txt`; `s08` lo necesita.

## Preguntas abiertas para el plan

1. ¿Los campos de proveedor, CUIT, fecha, punto de venta y moneda se extraen del texto de la factura, o se leen directamente del QR cuando existe? Si es lo segundo, el modelo de documentos cubre solo renglones y totales, y eso cambia el alcance.
2. ¿Las facturas de prueba se generan como PDF propio con QR (sin contacto con ARCA), o se intenta el circuito de homologación de web services? El segundo exige certificado y acceso; no lo probamos.
3. ¿Qué tolerancias de precio y de cantidad usa el caso de negocio? La fuente no fija valores.
4. ¿Se afina Donut con facturas sintéticas propias, o se usa el checkpoint CORD sin cambios para renglones y totales?
5. ¿ERPNext o Odoo Community? Hay que probar `frappe_docker` en este equipo antes de decidir.
6. ¿Qué lector de QR se fija: pyzbar (100 % en todas las condiciones) u OpenCV Aruco (sin dependencias nativas, 85 a 100 %)?
7. ¿Se reformula en el sitio la afirmación sobre el QR y los campos (contradicciones 3 a 5)? Es decisión del dueño del sitio.

## Scripts y salidas

Todos los scripts están en `research/`. Se ejecutan con el Python del entorno: `.venv/Scripts/python.exe -I research/<script>.py`. Las salidas van a `data/interim/research/` (git-ignorado), y sus registros a `data/interim/research/logs/`.

| Script | Qué hace | Salida principal citada |
| --- | --- | --- |
| `s00_download.py` | Descarga CORD v2 al commit fijado y verifica tamaño y SHA-256 | 6 de 6 fragmentos, 2.307.284.272 bytes |
| `s01_structure.py` | Conteos, esquema, presencia por campo, líneas, totales, imágenes, duplicados | `cord_structure.json` |
| `s02_donut.py` | Donut en GPU (100 recibos) y CPU (10 recibos): precisión por árbol, recuperación, tiempos | `donut_eval.json`, `donut_predictions.jsonl` |
| `s03_cord_qr_scan.py` | Busca QR en las 1.000 imágenes con OpenCV | `cord_qr_scan.json` (36 candidatos, 1 decodificado) |
| `s04_arca_qr.py` | Reproduce el ejemplo de ARCA; CUIT sintéticos; codifica y decodifica con tres lectores | `arca_qr_eval.json` |
| `s05_baselines.py` | Siempre vacío, valor frecuente y regex sobre palabras anotadas | `baselines.json` |
| `s06_erp_survey.py` | Licencias y tamaños de imagen de ERP (metadatos públicos, sin descargar imágenes) | `erp_survey.json` |
| `s07_site_claims.py` | Verifica cada afirmación del sitio contra las salidas | impresión en `logs/s07_site_claims.log` |
| `s08_arca_docs.py` | Descarga la especificación y la página de ARCA, calcula SHA-256, extrae texto | `arca_qr_spec.txt` |

**Entorno.** `requirements-research.txt`: `torch` 2.14.1 desde el índice CUDA 13.0 (`https://download.pytorch.org/whl/cu130`), `transformers` 5.22.1, `tokenizers` 0.23.3, `sentencepiece` 0.2.2, `huggingface-hub` 2.4.0, `numpy` 2.4.6, `pandas` 2.3.3, `pyarrow` 26.0.1, `Pillow` 13.0.0, `qrcode` 8.2, `opencv-python-headless` 4.14.0.94, `pyzbar` 0.1.9, `zss` 1.2.0. Todas las versiones tienen al menos dos semanas de antigüedad al 18 de noviembre de 2026.

**Caché de modelos.** El checkpoint de Donut (unos 800 MB) se guardó en el caché de Hugging Face del usuario (`~/.cache/huggingface`), fuera del repositorio.
