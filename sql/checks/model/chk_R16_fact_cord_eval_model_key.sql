-- R16: every fact_cord_eval[model_key] is a dim_model[model_key] (AC26). A violation is the key that has no row in the dimension and how many fact rows have it.
SELECT f.model_key AS fact_key, COUNT(*) AS fact_rows
FROM star_fact_cord_eval AS f
WHERE f.model_key IS NULL OR f.model_key NOT IN (SELECT model_key FROM star_dim_model)
GROUP BY f.model_key;
