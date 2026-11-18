-- CORD's test split, once, per model: the mean TED and the leaf recall over its receipts.
DROP TABLE IF EXISTS star_fact_cord_eval;

CREATE TABLE star_fact_cord_eval AS
SELECT c.model AS model_key, 'all' AS field, 'test' AS split, AVG(c.ted) AS ted_mean, CAST(SUM(c.matched) AS REAL) / SUM(c.leaves) AS leaf_recall, COUNT(*) AS receipts,
       NULL AS seconds_mean, m.fit_splits AS fit_splits, c.seal_state AS seal_state
FROM eval_cord c JOIN ref_model m ON m.model_key = c.model
GROUP BY c.model, c.seal_state;
