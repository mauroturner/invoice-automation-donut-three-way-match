-- What each closed day did: the follow-up that exists before the close.
DROP TABLE IF EXISTS star_fact_daily_ops;

CREATE TABLE star_fact_daily_ops AS
SELECT date_key, invoices, auto_post, review, posted, post_failed, quarantined, seconds_mean, seconds_p95
FROM daily_ops;
