-- A quarantined file has the rule that quarantined it and was never hashed; an accepted one has its hash and no rule.
SELECT invoice_key, status, rule_id, file_sha256 IS NOT NULL AS hashed
FROM invoices
WHERE (status = 'quarantined' AND (rule_id IS NULL OR file_sha256 IS NOT NULL))
   OR (status = 'accepted' AND (rule_id IS NOT NULL OR file_sha256 IS NULL))
