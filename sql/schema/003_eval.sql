-- The closing evaluation, what it computed from what was issued and the truth (plan, section 5; decisions D19, D20, D21, D51). Each row belongs to ``seal_state`` = ``sealed`` (the first evaluation with the sealed
-- configuration: the one the seal holds) or ``post_seal`` (computed apart with another configuration, never replacing a sealed one). The ``sealed`` rows are insert-only: three triggers refuse an update, a delete
-- and a second insert of a key that is already sealed (INSERT OR REPLACE included). Amounts are never stored here as text money: these are rates and counts. ``eval_invoice`` is the truth joined to what was
-- emitted, one row per invoice of the test; its ``cohort`` is the split (``test_known``, ``test_new``: ``dim_cohort[cohort_key]``) and ``supplier_cohort`` the supplier's (``known``, ``new_test``).

CREATE TABLE IF NOT EXISTS eval_criteria (
    criterion_id TEXT NOT NULL CHECK (criterion_id GLOB 'C[0-9][0-9]'),
    name_es TEXT NOT NULL,
    value REAL,
    baseline_value REAL,
    ci_low REAL,
    ci_high REAL,
    threshold_es TEXT NOT NULL,
    passed INTEGER CHECK (passed IS NULL OR passed IN (0, 1)),
    seen_by_research TEXT NOT NULL,
    seen_by_planner TEXT NOT NULL,
    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
    verdict_es TEXT NOT NULL,
    seal_state TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    PRIMARY KEY (criterion_id, seal_state)
);

CREATE TABLE IF NOT EXISTS eval_invoice (
    invoice_key TEXT NOT NULL,
    cohort TEXT NOT NULL CHECK (cohort IN ('test_known', 'test_new')),
    supplier_cohort TEXT NOT NULL,
    planted TEXT NOT NULL,
    expected_decision TEXT NOT NULL CHECK (expected_decision IN ('auto_post', 'review')),
    decision TEXT NOT NULL CHECK (decision IN ('auto_post', 'review')),
    first_reason TEXT,
    first_reason_ok INTEGER CHECK (first_reason_ok IS NULL OR first_reason_ok IN (0, 1)),
    scan_profile TEXT NOT NULL,
    file_format TEXT NOT NULL,
    qr_cm REAL,
    qr_printed INTEGER NOT NULL CHECK (qr_printed IN (0, 1)),
    qr_read_ok INTEGER NOT NULL CHECK (qr_read_ok IN (0, 1)),
    posted INTEGER NOT NULL CHECK (posted IN (0, 1)),
    bad_post INTEGER NOT NULL CHECK (bad_post IN (0, 1)),
    straight_through INTEGER NOT NULL CHECK (straight_through IN (0, 1)),
    lines INTEGER NOT NULL CHECK (lines >= 0),
    line_exact INTEGER NOT NULL CHECK (line_exact >= 0),
    seal_state TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    PRIMARY KEY (invoice_key, seal_state)
);

CREATE TABLE IF NOT EXISTS eval_field (
    cohort TEXT NOT NULL,
    field TEXT NOT NULL,
    leaves INTEGER NOT NULL CHECK (leaves >= 0),
    correct INTEGER NOT NULL CHECK (correct >= 0),
    recall REAL,
    seal_state TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    PRIMARY KEY (cohort, field, seal_state)
);

CREATE TABLE IF NOT EXISTS eval_baseline (
    baseline TEXT NOT NULL CHECK (baseline IN ('b0', 'b1', 'b2', 'b3', 'flow')),
    cohort TEXT NOT NULL,
    auto_post INTEGER NOT NULL,
    bad_post INTEGER NOT NULL,
    bad_post_rate REAL,
    ci_low REAL,
    ci_high REAL,
    straight_through REAL,
    review_recall REAL,
    seal_state TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    PRIMARY KEY (baseline, cohort, seal_state)
);

CREATE TABLE IF NOT EXISTS eval_cord (
    model TEXT NOT NULL CHECK (model IN ('chosen', 'checkpoint')),
    receipt_key TEXT NOT NULL,
    ted REAL NOT NULL,
    leaves INTEGER NOT NULL,
    matched INTEGER NOT NULL,
    seal_state TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    PRIMARY KEY (model, receipt_key, seal_state)
);

CREATE TABLE IF NOT EXISTS eval_variants (
    variant TEXT NOT NULL,
    key TEXT NOT NULL,
    value REAL,
    detail_json TEXT NOT NULL CHECK (json_valid(detail_json)),
    seal_state TEXT NOT NULL CHECK (seal_state IN ('sealed', 'post_seal')),
    PRIMARY KEY (variant, key, seal_state)
);

CREATE TRIGGER IF NOT EXISTS eval_criteria_no_update BEFORE UPDATE ON eval_criteria WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_criteria es de solo inserción: lo sellado no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS eval_criteria_no_delete BEFORE DELETE ON eval_criteria WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_criteria es de solo inserción: lo sellado no se borra'); END;

CREATE TRIGGER IF NOT EXISTS eval_criteria_no_second_insert BEFORE INSERT ON eval_criteria WHEN NEW.seal_state = 'sealed' AND EXISTS (SELECT 1 FROM eval_criteria WHERE criterion_id = NEW.criterion_id AND seal_state = 'sealed')
BEGIN SELECT RAISE(ABORT, 'eval_criteria es de solo inserción: lo sellado ya está'); END;

CREATE TRIGGER IF NOT EXISTS eval_invoice_no_update BEFORE UPDATE ON eval_invoice WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_invoice es de solo inserción: lo sellado no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS eval_invoice_no_delete BEFORE DELETE ON eval_invoice WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_invoice es de solo inserción: lo sellado no se borra'); END;

CREATE TRIGGER IF NOT EXISTS eval_invoice_no_second_insert BEFORE INSERT ON eval_invoice WHEN NEW.seal_state = 'sealed' AND EXISTS (SELECT 1 FROM eval_invoice WHERE invoice_key = NEW.invoice_key AND seal_state = 'sealed')
BEGIN SELECT RAISE(ABORT, 'eval_invoice es de solo inserción: lo sellado ya está'); END;

CREATE TRIGGER IF NOT EXISTS eval_field_no_update BEFORE UPDATE ON eval_field WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_field es de solo inserción: lo sellado no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS eval_field_no_delete BEFORE DELETE ON eval_field WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_field es de solo inserción: lo sellado no se borra'); END;

CREATE TRIGGER IF NOT EXISTS eval_field_no_second_insert BEFORE INSERT ON eval_field WHEN NEW.seal_state = 'sealed' AND EXISTS (SELECT 1 FROM eval_field WHERE cohort = NEW.cohort AND field = NEW.field AND seal_state = 'sealed')
BEGIN SELECT RAISE(ABORT, 'eval_field es de solo inserción: lo sellado ya está'); END;

CREATE TRIGGER IF NOT EXISTS eval_baseline_no_update BEFORE UPDATE ON eval_baseline WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_baseline es de solo inserción: lo sellado no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS eval_baseline_no_delete BEFORE DELETE ON eval_baseline WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_baseline es de solo inserción: lo sellado no se borra'); END;

CREATE TRIGGER IF NOT EXISTS eval_baseline_no_second_insert BEFORE INSERT ON eval_baseline WHEN NEW.seal_state = 'sealed' AND EXISTS (SELECT 1 FROM eval_baseline WHERE baseline = NEW.baseline AND cohort = NEW.cohort AND seal_state = 'sealed')
BEGIN SELECT RAISE(ABORT, 'eval_baseline es de solo inserción: lo sellado ya está'); END;

CREATE TRIGGER IF NOT EXISTS eval_cord_no_update BEFORE UPDATE ON eval_cord WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_cord es de solo inserción: lo sellado no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS eval_cord_no_delete BEFORE DELETE ON eval_cord WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_cord es de solo inserción: lo sellado no se borra'); END;

CREATE TRIGGER IF NOT EXISTS eval_cord_no_second_insert BEFORE INSERT ON eval_cord WHEN NEW.seal_state = 'sealed' AND EXISTS (SELECT 1 FROM eval_cord WHERE model = NEW.model AND receipt_key = NEW.receipt_key AND seal_state = 'sealed')
BEGIN SELECT RAISE(ABORT, 'eval_cord es de solo inserción: lo sellado ya está'); END;

CREATE TRIGGER IF NOT EXISTS eval_variants_no_update BEFORE UPDATE ON eval_variants WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_variants es de solo inserción: lo sellado no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS eval_variants_no_delete BEFORE DELETE ON eval_variants WHEN OLD.seal_state = 'sealed'
BEGIN SELECT RAISE(ABORT, 'eval_variants es de solo inserción: lo sellado no se borra'); END;

CREATE TRIGGER IF NOT EXISTS eval_variants_no_second_insert BEFORE INSERT ON eval_variants WHEN NEW.seal_state = 'sealed' AND EXISTS (SELECT 1 FROM eval_variants WHERE variant = NEW.variant AND key = NEW.key AND seal_state = 'sealed')
BEGIN SELECT RAISE(ABORT, 'eval_variants es de solo inserción: lo sellado ya está'); END;
