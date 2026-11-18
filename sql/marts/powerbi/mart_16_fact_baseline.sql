-- The flow and the baselines B0 to B3, by cohort (the 'all' rows are the sum of the two and are not exported: they would double count in a chart).
DROP TABLE IF EXISTS star_fact_baseline;

CREATE TABLE star_fact_baseline AS
SELECT baseline AS baseline_key, cohort AS cohort_key, auto_post, bad_post, bad_post_rate, ci_low, ci_high, straight_through, review_recall, seal_state
FROM eval_baseline
WHERE cohort IN ('test_known', 'test_new');
