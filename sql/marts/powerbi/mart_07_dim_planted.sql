-- The 13 planted classes and the absence of one.
DROP TABLE IF EXISTS star_dim_planted;

CREATE TABLE star_dim_planted AS
SELECT planted_key, label_es, expected_reason_key
FROM ref_planted;
