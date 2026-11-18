-- R03: every fact_invoice[supplier_key] is a dim_supplier[supplier_key] (AC26). A violation is the key that has no row in the dimension and how many fact rows have it.
SELECT f.supplier_key AS fact_key, COUNT(*) AS fact_rows
FROM star_fact_invoice AS f
WHERE f.supplier_key IS NULL OR f.supplier_key NOT IN (SELECT supplier_key FROM star_dim_supplier)
GROUP BY f.supplier_key;
