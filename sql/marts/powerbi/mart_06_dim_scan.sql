-- The scan profiles.
DROP TABLE IF EXISTS star_dim_scan;

CREATE TABLE star_dim_scan AS
SELECT scan_key, label_es
FROM ref_scan;
