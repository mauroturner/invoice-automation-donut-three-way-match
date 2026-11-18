-- The reading by cohort, field and scan profile: how many leaves the model wrote and how many were normalized to a value.
DROP TABLE IF EXISTS star_fact_field_read;

CREATE TABLE star_fact_field_read AS
SELECT i.cohort AS cohort_key, f.field AS field_key, i.scan_profile AS scan_key, COUNT(*) AS leaves, SUM(f.value_norm IS NOT NULL) AS correct,
       CAST(SUM(f.value_norm IS NOT NULL) AS REAL) / COUNT(*) AS recall, i.seal_state AS seal_state
FROM extracted_fields f JOIN eval_invoice i ON i.invoice_key = f.invoice_key
GROUP BY i.cohort, f.field, i.scan_profile, i.seal_state;
