-- A delivery says how many files it brought and the warehouse holds exactly that many rows of ``invoices`` for it (accepted or quarantined).
SELECT d.delivery_id AS delivery_id, d.files AS declared, (SELECT COUNT(*) FROM invoices i WHERE i.delivery_id = d.delivery_id) AS found
FROM deliveries d
WHERE d.files != (SELECT COUNT(*) FROM invoices i WHERE i.delivery_id = d.delivery_id)
