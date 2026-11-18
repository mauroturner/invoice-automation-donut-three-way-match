-- The QR by cohort, scan profile, printed size and the reader that read it ('none' when no reader did).
DROP TABLE IF EXISTS star_fact_qr_read;

CREATE TABLE star_fact_qr_read AS
SELECT i.cohort AS cohort_key, i.scan_profile AS scan_key, i.qr_cm AS qr_cm, COALESCE(q.reader, 'none') AS reader, COUNT(*) AS printed, SUM(i.qr_read_ok) AS read_ok, i.seal_state AS seal_state
FROM eval_invoice i LEFT JOIN qr_reads q ON q.invoice_key = i.invoice_key
WHERE i.qr_printed = 1
GROUP BY i.cohort, i.scan_profile, i.qr_cm, COALESCE(q.reader, 'none'), i.seal_state;
