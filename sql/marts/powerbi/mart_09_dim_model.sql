-- The chosen model of the launch and the unadjusted checkpoint; the seal state is the one of the launch.
DROP TABLE IF EXISTS star_dim_model;

CREATE TABLE star_dim_model AS
SELECT m.model_key AS model_key, m.model_version AS model_version, m.chosen_epoch AS chosen_epoch, m.artifact_sha256 AS artifact_sha256, m.config_sha256 AS config_sha256,
       CASE WHEN m.model_key = 'chosen' THEN s.launch_state ELSE 'sealed' END AS seal_state
FROM ref_model m CROSS JOIN ref_seal s;
