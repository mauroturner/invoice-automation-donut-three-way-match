-- K01: the grain of fact_invoice (AC26): an invoice has one row per seal state. A violation is the invoice and how many rows it has.
SELECT invoice_key, seal_state, COUNT(*) AS fact_rows
FROM star_fact_invoice
GROUP BY invoice_key, seal_state
HAVING COUNT(*) > 1;
