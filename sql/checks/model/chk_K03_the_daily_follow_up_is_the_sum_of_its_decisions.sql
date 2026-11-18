-- K03: a day's row counts its invoices as the sum of its auto_post and review (AC26). A violation is the day.
SELECT date_key, invoices, auto_post, review
FROM star_fact_daily_ops
WHERE invoices != auto_post + review;
