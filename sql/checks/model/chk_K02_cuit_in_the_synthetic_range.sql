-- K02: every CUIT that leaves the warehouse is in the synthetic range 30-0000xxxx-d (D05, M07). A violation is the CUIT.
SELECT cuit AS cuit
FROM star_dim_supplier
WHERE cuit IS NOT NULL AND cuit NOT GLOB '300000[0-9][0-9][0-9][0-9][0-9]';
