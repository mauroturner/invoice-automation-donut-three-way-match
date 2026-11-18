-- The runs of the pipeline, as the warehouse recorded them.
DROP TABLE IF EXISTS star_dim_pipeline_run;

CREATE TABLE star_dim_pipeline_run AS
SELECT run_key, started_at, finished_at, status, delivery_id, as_of, config_sha256, artifact_sha256, invoices, peak_working_set_mb, peak_gpu_mb, memory_counter, message
FROM pipeline_runs;
