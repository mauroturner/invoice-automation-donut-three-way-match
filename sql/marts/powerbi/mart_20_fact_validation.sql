-- The result of each rule in each run.
DROP TABLE IF EXISTS star_fact_validation;

CREATE TABLE star_fact_validation AS
SELECT run_key, rule_id, name_es, severity, scope, aggregation,
       CASE WHEN passed = 1 THEN 'pasa' WHEN severity = 'bloqueante' THEN 'falla' ELSE 'advierte' END AS status, files_affected
FROM validation_results;
