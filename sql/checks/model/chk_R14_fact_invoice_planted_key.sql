-- R14: every fact_invoice[planted_key] is a dim_planted[planted_key] (AC26). A violation is the key that has no row in the dimension and how many fact rows have it.
SELECT f.planted_key AS fact_key, COUNT(*) AS fact_rows
FROM star_fact_invoice AS f
WHERE f.planted_key IS NULL OR f.planted_key NOT IN (SELECT planted_key FROM star_dim_planted)
GROUP BY f.planted_key;
