-- Every run that loaded a delivery has the two blocking rules of the delivery (V10 and V11) in its validation.
SELECT r.run_key AS run_key
FROM pipeline_runs r
WHERE r.delivery_id IS NOT NULL
  AND (SELECT COUNT(*) FROM validation_results v WHERE v.run_key = r.run_key AND v.rule_id IN ('V10', 'V11')) < 2
