-- The calendar days that had closed at the run's instant (ref_date is loaded by export.py).
DROP TABLE IF EXISTS star_dim_date;

CREATE TABLE star_dim_date AS
SELECT date_key, date, weekday_es, period
FROM ref_date;
