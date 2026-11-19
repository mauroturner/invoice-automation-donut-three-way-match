## Resumen de la evaluación

No se cumple: C01, C03, C04, C05, C06.

Cada cifra marcada (sintético) sale de facturas que genera este proyecto.

| ID | Criterio | Lo que prueba el umbral | Veredicto |
| --- | --- | --- | --- |
| C01 | El ajuste no olvida CORD | −6,56 puntos (límite inferior) | En la prueba de CORD (100 recibos), el modelo elegido tiene TED 85,4 y el checkpoint sin ajustar 89,6: la diferencia es −4,2 (intervalo del 95 %, −6,6 a −2,0); el límite inferior debía quedar por encima de −2,0 puntos. No se cumple. |
| C02 | Lee bien las líneas de proveedores conocidos | 0,959 (sintético) | Sobre 300 facturas sintéticas de proveedores conocidos, el 95,9 % de las líneas se leyó exacto; el umbral era 90 %. |
| C03 | Lee bien las líneas de un diseño nuevo | 0,000 (sintético) | Sobre 300 facturas sintéticas de proveedores con un diseño nuevo, el 0 % de las líneas se leyó exacto; el umbral era 80 %. No se cumple. |
| C04 | Lee el QR | 0,831 (sintético) | Entre las 586 facturas sintéticas impresas con QR intacto, el lector leyó el texto exacto de 487: 83,1 %; el umbral era 97 %. No se cumple. |
| C05 | No carga mal | 0,025 (límite superior de Wilson) (sintético) | Sobre 600 facturas sintéticas, 0 de 147 cargas automáticas fue mala; el límite superior del intervalo de Wilson es 2,5 % y el máximo era 2 %. No se cumple. |
| C06 | Se carga sola la mayoría de las facturas de proveedores conocidos | 0,697 (sintético) | Sobre las facturas sintéticas limpias de proveedores conocidos, el 69,7 % (147 de 211) se cargó solo y bien; el umbral era 85 %. No se cumple. |
| C07 | Manda a revisión lo que no cierra | 1,000 (sintético) | De las 167 facturas sintéticas con una discrepancia sembrada, 167 de 167 fueron a revisión (100 %); el umbral era 99 %. |
| C08 | El cruce de tres vías evita cargas malas | 5,7 puntos (límite inferior, por factura) (sintético) | Sobre las facturas sintéticas de la prueba, B1 (solo el QR en dos vías) cargó mal 46 de las 600 y el flujo 0: por factura, la diferencia es 7,7 puntos (intervalo del 95 %, 5,7 a 10,0); el límite inferior debía ser mayor que 0. Es la cifra sellada; la diferencia de las tasas de carga mala que fija el criterio, por carga, está entre los análisis posteriores al sello (D59). |
| C09 | Lo cargado es lo decidido | 1,000 (sintético) | 147 de 147 cargas automáticas se leyeron de vuelta del ERP iguales a lo decidido (sintético) y repetir el último día creó 0 facturas nuevas; debían coincidir todas y no crearse ninguna. |
| C10 | Variantes y seguimiento (informativo) | sin umbral | Informativo: sin umbral. Las líneas base, el recuerdo por clase sembrada, la lectura por campo, perfil y tamaño del QR y los 20 días de operación están en el resumen (0 variantes). |

### Análisis posteriores al sello

No reemplazan ninguna cifra sellada: se calcularon después del sello, sobre las mismas decisiones emitidas y la misma verdad sintética, y se publican al lado de lo sellado (D59).

- **C08 como lo fija el preregistro** (requirements, sección 8.1: la tasa de carga mala es cargas malas sobre facturas con `auto_post`): B1 cargó mal 46 de sus 197 cargas (23,4 %) y el flujo 0 de 147 (0,0 %); la diferencia de las tasas es 23,4 puntos (intervalo del 95 %, 17,5 a 29,8, remuestreo pareado por factura): se cumple también así (sintético). El veredicto sellado de C08 no cambia; el texto sellado, que pone esas tasas por carga al lado de la diferencia por factura, queda en el sello y en el almacén.
- **B1 con la orden que leyó el modelo** (D48, D60; la B1 sellada tomó la orden de la verdad): carga 189 facturas y 41 mal (21,7 % de sus cargas). C08 por factura, como el sello: 6,8 puntos (intervalo del 95 %, 5,0 a 9,0), se cumple; por carga, como el preregistro: 21,7 puntos (16,0 a 28,0), se cumple (sintético).

Cifras de la validación (no son la prueba; cada una dice con qué se ajustó su modelo):

- Checkpoint de CORD sin ajustar, en la validación de CORD (100 recibos, `fit_splits`: checkpoint): TED 94,00.
- Época 2 (dropped_cord, `fit_splits`: cord_train+synth_train): TED en la validación de CORD 87,98; líneas exactas en la validación de proveedores conocidos 93,8 % y de proveedores nuevos 0 % (sintético).
- Época 4 (kept, `fit_splits`: cord_train+synth_train): TED en la validación de CORD 92,78; líneas exactas en la validación de proveedores conocidos 97,1 % y de proveedores nuevos 0 % (sintético).
- Época 6 (dropped_cord, `fit_splits`: cord_train+synth_train): TED en la validación de CORD 91,98; líneas exactas en la validación de proveedores conocidos 96,8 % y de proveedores nuevos 0 % (sintético).
- Oráculo B3 sobre la validación (`fit_splits`: none): 300 de 300 facturas con la decisión que el generador sembró (sintético).
