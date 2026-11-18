-- The fields the model reads.
DROP TABLE IF EXISTS star_dim_field;

CREATE TABLE star_dim_field AS
SELECT field_key, group_es, label_es
FROM ref_field;
