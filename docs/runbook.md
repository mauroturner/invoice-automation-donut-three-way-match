# Runbook: qué hacer cuando algo no sale como se esperaba

Todos los comandos son para Windows (PowerShell), dentro de la carpeta `07-Ecommerce-Invoices-ERP/` y con el entorno del proyecto. El paquete no está instalado en el entorno: una vez por sesión,

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python -m invoiceflow --help
```

**Cómo se escribió este documento.** Está escrito desde lo que dicen el código, las pruebas y el registro de decisiones, con los mensajes tal como los emite el código (las rutas y las claves de corrida se abrevian); **no se corrió cada procedimiento** (por ejemplo, no se forzó un ajuste sin CUDA ni un disco lleno). Lo que sí se corrió una vez lo dice cada entrada. Donde el código nombra una entrada de este documento (`docs/runbook.md, R5`), es la que sigue.

Códigos de salida de la CLI (`src/invoiceflow/cli.py`): `0` terminó bien (también una corrida sin entrega pendiente y un `replay` sin pendientes), `1` falló una tarea, `2` uso o configuración incorrectos (también una tarea rechazada por el estado de su corrida, un bloqueo tomado, un ajuste sin CUDA, un `post_as` que no es `draft`, la generación de la prueba sin el sello del lanzamiento y un manifiesto de entregas o un ERP sustituto que no son los del almacén, R11) y `3` una fuente o una entrega rechazadas. Cada tarea que falla deja su estado en la corrida y su registro.

Tres principios valen para todo el documento:

- **Un rechazo de la fuente o de la entrega no deja nada a medias.** Una fuente que no pasa V01-V06 o una entrega que no pasa V10-V11 escribe su informe de validación (`reports\validation\latest.md` y `latest.json`, con una copia en `history\`) y el estado de la corrida, y nada más: ni almacén, ni Parquet, ni sello, ni cargas al ERP.
- **Una corrida cerrada no se vuelve a abrir.** Cualquier tarea de una corrida cerrada se rechaza con código `2` antes de escribir nada: la entrega siguiente es otra corrida.
- **El sello no se toca.** `reports\model_seal.json` guarda la configuración del lanzamiento y el primer resultado de la prueba; ninguna tarea lo reescribe con otros valores, y la prueba sintética no se genera sin él.

## R1 Falta una fuente de CORD o la especificación de ARCA, o no tienen el tamaño o la huella fijados

**Síntoma.** Cualquier corrida con la configuración por defecto sale con código `3`: `rechazada: …` con la regla V01 o V06 y el texto «runbook R1», o, si lo que no sale igual es el ejemplo de la especificación, V06.

**Qué significa.** El flujo no descarga nada (D02): exige los seis Parquet de CORD (2.307.284.272 bytes en total) y `QRespecificaciones.pdf` (91.626 bytes) en `data\raw\`, cada uno con el tamaño y el SHA-256 que la configuración fija, y que el ejemplo de la especificación se reproduzca byte a byte con el generador de QR (V06).

**Qué hacer.**

1. Bajarlos con los scripts de la investigación, que fijan CORD al commit `7f0115a4` y comprueban tamaño y SHA-256 antes de aceptar cada archivo:

   ```powershell
   .venv\Scripts\python research\s00_download.py
   .venv\Scripts\python research\s08_arca_docs.py
   ```

2. Si el tamaño es otro, el archivo es otro o se cortó: borrarlo y bajar de nuevo. Un archivo de otro tamaño no se calcula nunca su huella.
3. Si el ejemplo de la especificación no sale igual con los archivos buenos, se tocó `synth\qrpayload.py`: no se arregla bajando de nuevo.

## R2 Una regla de lectura de CORD (V02 a V05) falló

**Síntoma.** La tarea `fetch_source` o `load` sale con `3` y el informe nombra V02, V03, V04 o V05 con el recibo y el archivo: un esquema distinto, una imagen por encima de `max_image_pixels`, un `ground_truth` que no es un JSON con `gt_parse` y `meta`, o un recuento de filas que no es el esperado (`expected_rows`: 800, 100 y 100).

**Qué significa.** Los Parquet de CORD son una entrada no confiable. Una regla que falla no se salta: o el archivo cambió o la configuración dejó de decir lo que el archivo es. La prueba de CORD no se abre en ninguna de estas reglas: solo se lee el pie del archivo.

**Qué hacer.** Abrir `reports\validation\latest.md`, leer el recibo y la regla, y comprobar el archivo con R1. No se baja el límite de una regla para que pase: `max_image_pixels` y los recuentos esperados son los medidos (la imagen más grande de CORD tiene 12.192.768 píxeles, por debajo de 16 millones).

## R3 Una entrega rechazada, o archivos de la bandeja en cuarentena

**Síntoma.** O la corrida sale con `3` y el informe nombra V10 o V11, o el día corre y el informe dice que uno o más archivos fueron a `quarantine\` y que su factura fue a revisión con `file_rejected`.

**Qué significa.** Un archivo malo en la bandeja es lo normal en cuentas a pagar y no frena el día: V12 (el tipo por los primeros bytes), V13 (el tamaño), V14 (los píxeles) y V15 (un PDF que no es de una página con una sola imagen, o con programa, formulario, adjunto o texto) lo mueven a cuarentena y su factura va a revisión. V10 y V11 sí frenan: la entrega no es la siguiente del plan, o los archivos no son los del manifiesto.

**Qué hacer.** Para la cuarentena: mirar los archivos de `quarantine\` y el motivo de cada uno; un PDF con texto o de varias páginas no se lee (plan, sección 29, ítem 15). Para V10 y V11: ver R4.

## R4 Entregas fuera de orden o con archivos que no son los del manifiesto

**Síntoma.** `rechazada: … V10` (la entrega no es la primera del plan que no está `loaded`) o `V11` (los archivos de la bandeja o la verdad del día no coinciden con el manifiesto de la entrega).

**Qué significa.** Cada entrega diaria (`D2026-10-01` a `D2026-10-28`) se carga en el orden del plan y con los archivos y huellas que el manifiesto dice. Una entrega cargada no se carga dos veces y una que falta no se salta.

**Qué hacer.** No editar el manifiesto de entregas a mano. Si una entrega se perdió, volver a generar la bandeja de esa entrega con el lanzamiento intacto; si la bandeja se tocó, restaurarla y repetir `run`. Una entrega rechazada no escribe nada del día.

## R5 El ajuste no puede correr: sin CUDA, sin memoria de la GPU, `ModelChoiceError` o M06

**Síntoma.** `fit_model` sale con `2` y dice una de estas cosas: el ajuste real necesita CUDA y no está disponible; la GPU se quedó sin memoria; `ModelChoiceError` («ninguna época …») porque ninguna candidata queda dentro de los 2,0 puntos de TED del checkpoint en la validación de CORD; o M06 (el oráculo no da la decisión que el generador sembró en alguna factura de validación) antes de ajustar nada.

**Qué significa.** El ajuste real es lo único que exige la GPU (D11). La suite corre en CPU con un modelo diminuto. `ModelChoiceError` no es un error de código: dice que ninguna época es aceptable con la regla fijada, y el mensaje nombra la medida de cada época contra el checkpoint. La primera vez que se corrió, el error se debía a la métrica y no al modelo (ver la ficha del modelo, la reparación del lanzamiento, y D51).

**Qué hacer.**

1. Sin CUDA: instalar el torch de CUDA (`python -m pip install torch==2.14.1 --index-url https://download.pytorch.org/whl/cu130`) y comprobar `torch.cuda.is_available()`.
2. Sin memoria: cerrar lo que use la GPU; un paso de ajuste usa unos 7,4 GB con bf16 y lote 1 (`torch.cuda.max_memory_allocated`), y el límite del proyecto es 20 GB.
3. `ModelChoiceError`: leer cada época contra el checkpoint. Las épocas candidatas no se pierden: quedan en `data\models\.candidates-<corrida>\epoch-N`, cada una con su manifiesto de huellas, y el mensaje nombra la carpeta; ninguna se registra ni se sella, y se borran a mano cuando ya no hacen falta (D62). Cambiar la tolerancia (`cord_val_tolerance`) es otro análisis, no una corrección (R12).
4. M06: el oráculo y el generador no coinciden; es un defecto del código, no de la corrida.

## R6 El artefacto del modelo no coincide con su huella

**Síntoma.** Una tarea que lee el modelo se niega: la carpeta del artefacto no es la registrada, un archivo tiene otro SHA-256, o en la carpeta hay un archivo que el flujo no lee (`.bin`, `.pt`, `.pth`, `.pkl` o `.ckpt`).

**Qué significa.** El artefacto es un `safetensors` con su huella y su `model_registry` (D13). La huella protege de un archivo cambiado, no de quien cambie el archivo y el registro a la vez. Una carpeta de artefacto que el registro no tiene se registra de nuevo solo si el sello es de esta configuración y la carpeta tiene las huellas que el sello guarda (la reconstrucción de R11, o un registro que falló después de escribir el sello; D62); cualquier otra no se adopta ni se borra sola.

**Qué hacer.** No copiar ni cambiar un archivo del artefacto. Si el artefacto se perdió o se tocó, no se puede reconstruir sin volver a ajustar, y un ajuste nuevo en la GPU no reproduce el sello byte a byte (RNF01; el lanzamiento saldría con R7): eso es un análisis nuevo (R12). Si hay una carpeta que el sello no reconoce, quitarla a mano.

## R7 La evaluación no reproduce el sello, o la prueba se evalúa sin él

**Síntoma.** `evaluate` o el `report` se rehúsan: el sello no se reconstruye desde sus cifras, la parte `test` que esta corrida escribió no es la que dice el archivo, o se pide generar o evaluar la prueba sin la parte `launch` del sello.

**Qué significa.** El sello (`reports\model_seal.json`) tiene dos partes que se escriben una vez: `launch` y `test`. La prueba sintética se genera solo si hay `launch`; la prueba de CORD se abre una sola vez, en el cierre. Una evaluación con otra configuración antes de sellar se rechaza antes de escribir, y una después del sello es un `post_seal` aparte que no reemplaza nada (AC20, AC24).

**Qué hacer.** No editar el sello ni los hechos sellados. Si el sello no se reproduce, hay un cambio en la configuración que la huella cubre, en la verdad sintética o en el generador: comparar `config_sha256` de la corrida con el del sello y mirar cuáles claves difieren (el mensaje nombra hasta seis). Para otro análisis con otras tolerancias, ver R12.

## R8 El bloqueo: otro proceso escribe, o `database is locked`

**Síntoma.** Una corrida, una tarea o `POST /runs` sale con `2` y dice que el bloqueo (`pipeline.lock`) lo tiene otro proceso, o una escritura del almacén falla con `database is locked`.

**Qué significa.** Un solo proceso escribe la carpeta de datos a la vez (D29, RNF16): la CLI, el DAG de Airflow y la API de entrada toman el mismo bloqueo, y la segunda llamada recibe 409 `run_locked` en la API o falla sin escribir en el DAG. Un escritor que se solapa espera hasta cierto tiempo por el bloqueo de escritura de SQLite antes de fallar.

**Qué hacer.**

1. Esperar a que termine la corrida que lo tiene. No borrar el archivo mientras el proceso corre.
2. Si el proceso murió, el mensaje dice cómo comprobarlo y que se borre `pipeline.lock` solo si ya terminó; el bloqueo se libera solo cuando quien lo tomó termina, y las carpetas vacías que creó se quitan con él.
3. Para retomar: `python -m invoiceflow run --run-key <corrida> --from <tarea>`.

## R9 El ERP no responde o rechaza: el sustituto, ERPNext y el token

**Síntoma.** La tarea `post` falla con `ErpAuthError` (401 o 403), `ErpUnreachable` (sin respuesta o 5xx) o `ErpConfigError` (sin dirección o sin token), o una factura queda en revisión con `post_failed` y su etapa (`read_back`, `rejected`, `unreachable`).

**Qué significa.** Un token que el ERP no acepta no es un fallo de una factura: se lanza para que la corrida falle y diga la causa, en lugar de mandar las 30 facturas a revisión sin decir por qué. Una lectura del ERP que falla detiene la tarea y no escribe nada de esa factura; repetirla da las mismas decisiones (D52). Con ERPNext real, un número de factura repetido responde 417 y no 409, y por omisión crea un segundo borrador; el cliente trata 409 y 417 igual y su búsqueda previa es lo que vuelve idempotente la carga (D50).

**Qué hacer.**

1. El token va en una variable del entorno (`INVOICEFLOW_ERP_TOKEN`), nunca en un archivo; el sustituto, si no hay token, hace uno para el proceso.
2. Con `[erp] mode = "http"` el flujo habla con ERPNext real: la dirección en `base_url`, el token `api_key:api_secret` y el asistente de configuración terminado (sin él no hay empresa ni año fiscal). **Abierto:** el número de orden que imprime una factura no es el nombre que ERPNext asigna, así que `mode = http` necesita un campo con los números del maestro (D50).
3. Para la prueba de humo: `docker\erpnext\compose.yaml`, `python -m pytest -m erpnext`, una vez. Pasó el 2026-11-18 con ERPNext v15.124.4.

## R10 Airflow o Docker no arrancan

**Síntoma.** `docker compose up airflow-init` no termina bien, el DAG `invoiceflow_daily` no aparece, o `python -m pytest -m docker` falla.

**Qué significa.** El DAG corre en Docker con todos los secretos del entorno (sin `env_file` ni valores literales), un servicio para el ERP y otro para la API, y un `init.sh` con `set -euo pipefail` y sin retornos de carro. La imagen de Debian 13 necesita `libzbar0t64` (no `libzbar0`) para pyzbar, y `pip check` de la imagen movió `python-dateutil` a 2.9.0.post0 y `setuptools` a 84.2.1 (D58). La prueba de Docker tardó 119 s la última vez.

**Qué hacer.** Docker Desktop con memoria suficiente; copiar `.env.example` a `.env` y completar cada secreto; si `init.sh` falla, leer su primera línea con error (se detiene en la primera). Un archivo `.sh` con retornos de carro rompe el arranque: el repositorio los guarda con `LF`.

## R11 Reconstruir desde cero

**Síntoma.** El almacén no tiene las columnas que dice su definición SQL, se perdió, o un archivo del flujo se perdió o se tocó, y quedan el sello (`reports\model_seal.json`) y el artefacto sellado (`data\models\model-v1-…`).

**Qué significa.** Se reconstruye todo lo demás desde el sello, sin volver a ajustar (D62): el lanzamiento encuentra el artefacto sellado sin su fila en `model_registry`, comprueba sus huellas contra las del sello y lo registra de nuevo con las cifras del sello (la tarea `fit_model` dice `mode=adopt`); las 20 entregas diarias se regeneran con la semilla sellada y el modelo las vuelve a leer; y el cierre vuelve a abrir la prueba de CORD por el único camino que la abre y tiene que reproducir el sello (`evaluate` dice `state=repeat` y `seal=unchanged`; si no, R7). Lo único que no se reconstruye sin ajustar es el artefacto (R6). Un almacén que creó una versión anterior no se migra: se reconstruye así. Las salidas versionadas llevan su propio `manifest.json`, que la corrida escribe al final. Las tablas emitidas son de solo inserción, así que no se editan. Los tiempos y la memoria del ajuste quedan en el registro de la corrida que lo hizo: la reconstrucción no ajusta. Es para el ERP sustituto local, el modo por defecto.

**Qué hacer.**

1. Mover a otra carpeta, sin borrarlos hasta comprobar la reconstrucción, estos cuatro: `data\warehouse\`, `data\interim\`, `dataaw\deliveries\` (las entregas y su manifiesto) y `data\erp\` (el ERP sustituto). No mover `reports\model_seal.json`, `data\models\`, `dataaw\cord\` ni `dataawrca\`.
2. `python -m invoiceflow replay`: el lanzamiento sin ajuste, las 20 diarias y el cierre. Con la GPU tarda unos 27 minutos (el 2026-11-19, sobre una copia de los datos: 26 min 33 s, el lanzamiento 61 s, cada diaria de 51 a 59 s y el cierre 411 s).
3. Comprobar que la tarea `evaluate` del cierre dice `state=repeat` y `seal=unchanged` y que `git status` muestra solo lo que depende de la corrida (las claves y las horas de las corridas, los tiempos y las reglas del modelo de la corrida del lanzamiento, M02 y M03 en lugar de M04 a M06, porque no ajustó). Correr `python -m invoiceflow run` una vez (sin entregas pendientes termina como `no_delivery`, la corrida que la prueba real espera después del replay) y `python -m pytest -m real_data`.

Si se movió solo una parte, `replay` y `run` salen con `2` sin escribir nada y dicen qué falta mover: con el manifiesto de entregas y sin el almacén, «el manifiesto de entregas dice que … están cargadas y el almacén no las tiene»; con el ERP sustituto y sin entregas, «el ERP sustituto ya tiene … documentos». `tests/e2e/test_rebuild.py` sigue estos pasos sobre una copia del modo rápido.

## R12 Empezar un análisis nuevo: otras tolerancias, otro generador, borrar el sello

**Síntoma.** Se quiere probar otra tolerancia de precio, otro generador de facturas o volver a evaluar la prueba.

**Qué significa.** La prueba sintética y la de CORD se leen una vez. Cambiar una tolerancia, el generador o una clave de la huella después del sello es un análisis posterior al sello (`post_seal`), que se guarda aparte con su impacto medido y no reemplaza lo sellado. Borrar el sello para repetir la prueba no es una corrección: es otra evaluación con otro sello.

**Qué hacer.** Para un análisis nuevo, copiar la carpeta del proyecto, cambiar la clave y correr `replay` ahí; el sello del proyecto original queda como estaba. Anotar el motivo como una decisión nueva en `docs/decisions.md`.
