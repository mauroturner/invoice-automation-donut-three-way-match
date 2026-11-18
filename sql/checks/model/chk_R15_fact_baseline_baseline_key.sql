-- R15: every fact_baseline[baseline_key] is a dim_baseline[baseline_key] (AC26). A violation is the key that has no row in the dimension and how many fact rows have it.
SELECT f.baseline_key AS fact_key, COUNT(*) AS fact_rows
FROM star_fact_baseline AS f
WHERE f.baseline_key IS NULL OR f.baseline_key NOT IN (SELECT baseline_key FROM star_dim_baseline)
GROUP BY f.baseline_key;
