-- The flow and its four baselines.
DROP TABLE IF EXISTS star_dim_baseline;

CREATE TABLE star_dim_baseline AS
SELECT baseline_key, label_es
FROM ref_baseline;
