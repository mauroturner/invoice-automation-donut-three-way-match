-- Each reason of each decision that was issued.
DROP TABLE IF EXISTS star_fact_invoice_reason;

CREATE TABLE star_fact_invoice_reason AS
SELECT invoice_key, reason AS reason_key, rank
FROM decision_reasons;
