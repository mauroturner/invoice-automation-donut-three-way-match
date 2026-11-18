-- One row per invoice of the evaluated test: what was issued joined to the synthetic truth (eval_invoice) and to what the decision recorded. The supplier is the one the flow found; the supplier_key of an invoice it could not attribute is 'sin_identificar'.
DROP TABLE IF EXISTS star_fact_invoice;

CREATE TABLE star_fact_invoice AS
SELECT e.invoice_key AS invoice_key, CAST(strftime('%Y%m%d', dl.day) AS INTEGER) AS date_key, COALESCE(s.supplier_key, 'sin_identificar') AS supplier_key, e.cohort AS cohort_key,
       e.scan_profile AS scan_key, e.planted AS planted_key, e.file_format AS file_format, e.qr_cm AS qr_cm, e.decision AS decision, COALESCE(e.first_reason, 'none') AS first_reason_key,
       d.reasons AS reasons, e.posted AS posted, e.bad_post AS bad_post, e.straight_through AS straight_through, e.lines AS lines, e.line_exact AS lines_exact,
       CAST(d.total AS REAL) AS total, d.currency AS currency, d.seconds_total AS seconds, e.seal_state AS seal_state
FROM eval_invoice e
JOIN decisions d ON d.invoice_key = e.invoice_key
JOIN invoices i ON i.invoice_key = e.invoice_key
JOIN deliveries dl ON dl.delivery_id = i.delivery_id
LEFT JOIN ref_supplier s ON s.cuit = d.supplier_cuit;
