-- R05: every fact_invoice_reason[reason_key] is a dim_reason[reason_key] (AC26). A violation is the key that has no row in the dimension and how many fact rows have it.
SELECT f.reason_key AS fact_key, COUNT(*) AS fact_rows
FROM star_fact_invoice_reason AS f
WHERE f.reason_key IS NULL OR f.reason_key NOT IN (SELECT reason_key FROM star_dim_reason)
GROUP BY f.reason_key;
