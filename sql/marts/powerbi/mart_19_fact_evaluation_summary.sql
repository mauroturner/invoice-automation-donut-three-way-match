-- The criteria and their verdicts: the sentence the summary publishes for each row (C08 per invoice), the sealed one beside it and the figure the threshold tests (ref_criterion_text).
DROP TABLE IF EXISTS star_fact_evaluation_summary;

CREATE TABLE star_fact_evaluation_summary AS
SELECT c.criterion_id, c.name_es, c.value, c.baseline_value, c.ci_low, c.ci_high, c.threshold_es, c.passed, c.seen_by_research, c.seen_by_planner, c.synthetic,
       COALESCE(t.verdict_es, c.verdict_es) AS verdict_es, c.seal_state,
       COALESCE(t.sealed_verdict_es, c.verdict_es) AS sealed_verdict_es, t.threshold_figure_es
FROM eval_criteria c
LEFT JOIN ref_criterion_text t ON t.criterion_id = c.criterion_id AND t.seal_state = c.seal_state;
