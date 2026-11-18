"""The Spanish labels of the things the star model, the dashboard design and the site name (RNF13; plan sections 19 and 21): one place, so no document types them twice.

Identifiers (the keys of the dimensions) are English and fixed; what is shown is Spanish. ``ATTRIBUTION`` is the citation of RNF10 that every Parquet file, the design and the site carry.
"""

from __future__ import annotations

from invoiceflow.daily import HEADER_FIELDS, LINE_FIELDS, TOTAL_FIELDS
from invoiceflow.match import REASONS

ATTRIBUTION = ("Park et al. (2019), CORD: A Consolidated Receipt Dataset for Post-OCR Parsing, Clova AI, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), commit 7f0115a4. "
               "Las facturas, órdenes, remitos, proveedores y CUIT son sintéticos.")
SYNTHETIC_NOTE_ES = "Las facturas, órdenes, remitos, proveedores y CUIT son sintéticos; los recibos de CORD son reales."
NO_REASON = "none"
UNIDENTIFIED_SUPPLIER = "sin_identificar"
WEEKDAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

# reason: (label, the rule that raises it, in the order of ``match.REASONS``)
REASON_TEXTS: dict[str, tuple[str, str]] = {
    "file_rejected": ("Archivo rechazado", "La bandeja puso el archivo en cuarentena (V12 a V15): no hay una página que leer."),
    "qr_not_read": ("QR no leído", "Ningún lector decodificó un QR en la página."),
    "qr_invalid": ("QR inválido", "El QR se leyó pero no cumple la especificación de ARCA."),
    "extraction_incomplete": ("Lectura incompleta", "Un campo obligatorio del modelo no se pudo normalizar."),
    "unsupported_type": ("Tipo no soportado", "El tipo de comprobante no está entre los que el flujo carga."),
    "qr_print_mismatch": ("El QR y lo impreso difieren", "CUIT, punto de venta, número, fecha o total del QR no son los que se leyeron de la página."),
    "duplicate": ("Comprobante duplicado", "El ERP ya tiene un comprobante del mismo proveedor con ese punto de venta y número."),
    "no_po": ("Sin orden de compra", "No existe la orden de compra que la factura cita."),
    "supplier_mismatch": ("Proveedor distinto del de la orden", "El proveedor de la factura no es el de la orden."),
    "currency_mismatch": ("Moneda distinta de la de la orden", "La moneda de la factura no es la de la orden."),
    "date_out_of_range": ("Fecha fuera de rango", "La fecha no está entre la de la orden y el instante de la corrida."),
    "line_not_on_po": ("Línea que no está en la orden", "Un artículo de la factura no figura en la orden."),
    "line_arithmetic": ("La línea no cierra", "Cantidad por precio no es el importe de la línea, con la tolerancia de redondeo."),
    "total_arithmetic": ("El total no cierra", "Las líneas no suman el neto, o el neto más el IVA no es el total."),
    "price_out_of_tolerance": ("Precio fuera de tolerancia", "El precio unitario se aparta de la orden más que la tolerancia del cruce."),
    "qty_over_receipt": ("Cantidad mayor que lo recibido", "La cantidad facturada acumulada pasa lo recibido en los remitos hasta el día de la factura."),
    "qty_over_order": ("Cantidad mayor que lo pedido", "La cantidad facturada acumulada pasa lo pedido en la orden."),
    "post_failed": ("Carga fallida en el ERP", "El borrador no se creó o su lectura de vuelta no coincide."),
}
NONE_REASON_TEXT = ("Sin motivo: se carga sola", "La decisión es auto_post: ninguna regla del cruce se activa.")

FIELD_LABELS_ES: dict[str, str] = {
    "header.tipo": "Tipo de comprobante", "header.pto_vta": "Punto de venta", "header.nro": "Número", "header.fecha": "Fecha", "header.cuit_emisor": "CUIT del emisor",
    "header.razon_social": "Razón social", "header.cuit_receptor": "CUIT del receptor", "header.orden_compra": "Orden de compra", "header.moneda": "Moneda", "header.cae": "CAE",
    "line.code": "Código del artículo", "line.desc": "Descripción", "line.qty": "Cantidad", "line.unit": "Unidad", "line.unit_price": "Precio unitario", "line.amount": "Importe de la línea",
    "totals.neto": "Neto", "totals.iva_21": "IVA 21 %", "totals.iva_105": "IVA 10,5 %", "totals.total": "Total",
}
COHORT_LABELS_ES = {"test_known": "Prueba: proveedores conocidos", "test_new": "Prueba: diseño nuevo", "validation_known": "Validación: proveedores conocidos", "validation_new": "Validación: diseño nuevo"}
SCAN_LABELS_ES = {"clean": "Limpio", "mild": "Leve", "poor": "Pobre"}
PLANTED_LABELS_ES = {
    "none": "Sin discrepancia", "price_off": "Precio fuera de la orden", "over_receipt": "Más de lo recibido", "over_order": "Más de lo pedido", "total_mismatch": "Total que no cierra",
    "supplier_mismatch": "Proveedor distinto", "no_po": "Sin orden de compra", "duplicate": "Comprobante duplicado", "date_before_po": "Fecha anterior a la orden",
    "currency_mismatch": "Moneda distinta", "extra_line": "Línea de más", "qr_missing": "Sin QR", "qr_tampered": "QR alterado", "credit_note": "Nota de crédito",
}
BASELINE_LABELS_ES = {"flow": "El flujo", "b0": "B0: todo a revisión", "b1": "B1: solo el QR en dos vías", "b2": "B2: el checkpoint sin ajustar", "b3": "B3: el oráculo"}
MODEL_LABELS_ES = {"chosen": "El modelo elegido", "checkpoint": "El checkpoint sin ajustar"}
REASON_KEYS = REASONS
FIELD_KEYS = (*(f"header.{n}" for n in HEADER_FIELDS), *(f"line.{n}" for n, _, _ in LINE_FIELDS), *(f"totals.{n}" for n in TOTAL_FIELDS))
FIELD_GROUPS_ES = {"header": "Cabecera", "line": "Línea", "totals": "Totales"}


def weekday_es(day) -> str:
    return WEEKDAYS_ES[day.weekday()]


__all__ = ["ATTRIBUTION", "BASELINE_LABELS_ES", "COHORT_LABELS_ES", "FIELD_GROUPS_ES", "FIELD_KEYS", "FIELD_LABELS_ES", "MODEL_LABELS_ES", "NONE_REASON_TEXT", "NO_REASON", "PLANTED_LABELS_ES",
           "REASON_KEYS", "REASON_TEXTS", "SCAN_LABELS_ES", "SYNTHETIC_NOTE_ES", "UNIDENTIFIED_SUPPLIER", "WEEKDAYS_ES", "weekday_es"]
