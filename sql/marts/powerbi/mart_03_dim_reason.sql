-- The 18 reasons of the match, in their order, and 'none'.
DROP TABLE IF EXISTS star_dim_reason;

CREATE TABLE star_dim_reason AS
SELECT reason_key, rank, label_es, rule_es
FROM ref_reason;
