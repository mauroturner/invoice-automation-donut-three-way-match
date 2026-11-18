-- R08: every fact_field_read[cohort_key] is a dim_cohort[cohort_key] (AC26). A violation is the key that has no row in the dimension and how many fact rows have it.
SELECT f.cohort_key AS fact_key, COUNT(*) AS fact_rows
FROM star_fact_field_read AS f
WHERE f.cohort_key IS NULL OR f.cohort_key NOT IN (SELECT cohort_key FROM star_dim_cohort)
GROUP BY f.cohort_key;
