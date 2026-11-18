-- The four cohorts of synthetic invoices.
DROP TABLE IF EXISTS star_dim_cohort;

CREATE TABLE star_dim_cohort AS
SELECT cohort_key, label_es
FROM ref_cohort;
