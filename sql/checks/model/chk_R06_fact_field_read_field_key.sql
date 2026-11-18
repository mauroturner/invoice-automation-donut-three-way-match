-- R06: every fact_field_read[field_key] is a dim_field[field_key] (AC26). A violation is the key that has no row in the dimension and how many fact rows have it.
SELECT f.field_key AS fact_key, COUNT(*) AS fact_rows
FROM star_fact_field_read AS f
WHERE f.field_key IS NULL OR f.field_key NOT IN (SELECT field_key FROM star_dim_field)
GROUP BY f.field_key;
