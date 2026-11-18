-- The candidate epochs with their validation figures, from the registry.
DROP TABLE IF EXISTS star_fact_choice;

CREATE TABLE star_fact_choice AS
SELECT epoch, cord_val_ted, val_known_line_exact, val_new_line_exact, kept, chosen, fit_splits
FROM ref_choice;
