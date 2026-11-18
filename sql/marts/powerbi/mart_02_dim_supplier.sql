-- The synthetic suppliers of the master and one member for the invoices that cannot be attributed to any.
DROP TABLE IF EXISTS star_dim_supplier;

CREATE TABLE star_dim_supplier AS
SELECT supplier_key, name_es, cuit, cohort, layout
FROM ref_supplier;
