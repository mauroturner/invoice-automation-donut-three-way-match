-- R04: every fact_invoice[first_reason_key] is a dim_reason[reason_key] (AC26). A violation is the key that has no row in the dimension and how many fact rows have it.
SELECT f.first_reason_key AS fact_key, COUNT(*) AS fact_rows
FROM star_fact_invoice AS f
WHERE f.first_reason_key IS NULL OR f.first_reason_key NOT IN (SELECT reason_key FROM star_dim_reason)
GROUP BY f.first_reason_key;
