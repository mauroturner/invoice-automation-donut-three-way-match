-- R13: every fact_qr_read[scan_key] is a dim_scan[scan_key] (AC26). A violation is the key that has no row in the dimension and how many fact rows have it.
SELECT f.scan_key AS fact_key, COUNT(*) AS fact_rows
FROM star_fact_qr_read AS f
WHERE f.scan_key IS NULL OR f.scan_key NOT IN (SELECT scan_key FROM star_dim_scan)
GROUP BY f.scan_key;
