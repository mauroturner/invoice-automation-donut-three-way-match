-- A run counts the files of its delivery, and a run that is still ``running`` has not finished.
SELECT r.run_key AS run_key, r.status AS status, r.invoices AS declared, (SELECT COUNT(*) FROM invoices i WHERE i.delivery_id = r.delivery_id) AS found
FROM pipeline_runs r
WHERE (r.delivery_id IS NOT NULL AND r.invoices IS NOT NULL AND r.invoices != (SELECT COUNT(*) FROM invoices i WHERE i.delivery_id = r.delivery_id))
   OR (r.status = 'running' AND r.finished_at IS NOT NULL)
