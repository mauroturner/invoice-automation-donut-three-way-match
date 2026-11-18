-- What the flow read and decided, each issued once (plan, section 5; decisions D17, D21, D28; risk item 1: what was issued is not redone).
-- Every table here is insert-only: three triggers refuse, in the database itself and whoever writes, an update, a delete and a second insert of a key that is already there (INSERT OR REPLACE
-- and an upsert included: the trigger of the insert fires before the conflict is resolved), as in project 06. Amounts are decimal text ('1210.00'), never floating point; instants are text
-- 'YYYY-MM-DD HH:MM:SS' and ``date_key`` is the day as the integer yyyymmdd. ``review_queue`` is a view: the decisions that are ``review`` and the posts that failed.

CREATE TABLE IF NOT EXISTS extracted_fields (
    invoice_key TEXT NOT NULL REFERENCES invoices (invoice_key),
    field TEXT NOT NULL CHECK (field LIKE 'header.%' OR field LIKE 'totals.%'
                               OR field IN ('line.code', 'line.desc', 'line.qty', 'line.unit', 'line.unit_price', 'line.amount')),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0 AND (field LIKE 'line.%' OR ordinal = 0)),   -- the line; 0 in the header and the totals
    value_raw TEXT,
    value_norm TEXT,
    model_version TEXT NOT NULL,
    seconds REAL NOT NULL CHECK (seconds >= 0),
    PRIMARY KEY (invoice_key, field, ordinal)
);

CREATE TABLE IF NOT EXISTS qr_reads (
    invoice_key TEXT PRIMARY KEY REFERENCES invoices (invoice_key),
    reader TEXT CHECK (reader IS NULL OR reader IN ('pyzbar', 'aruco')),
    status TEXT NOT NULL CHECK (status IN ('ok', 'not_read', 'invalid')),
    invalid_reason TEXT,
    cuit TEXT,
    fecha TEXT,
    pto_vta INTEGER,
    tipo_cmp INTEGER,
    nro_cmp INTEGER,
    importe TEXT,
    moneda TEXT,
    ctz TEXT,
    doc_rec_tipo INTEGER,
    doc_rec_nro INTEGER,
    cod_aut_tipo TEXT,
    cod_aut INTEGER,
    seconds REAL NOT NULL CHECK (seconds >= 0),
    CHECK ((status = 'not_read') = (reader IS NULL AND invalid_reason IS NULL)),
    CHECK ((status = 'invalid') = (invalid_reason IS NOT NULL)),
    CHECK (status = 'ok' OR (cuit IS NULL AND fecha IS NULL AND importe IS NULL)),
    CHECK (status != 'ok' OR (cuit IS NOT NULL AND fecha IS NOT NULL AND pto_vta IS NOT NULL AND tipo_cmp IS NOT NULL AND nro_cmp IS NOT NULL AND importe IS NOT NULL
                              AND moneda IS NOT NULL AND ctz IS NOT NULL AND cod_aut_tipo IS NOT NULL AND cod_aut IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS decisions (
    invoice_key TEXT PRIMARY KEY REFERENCES invoices (invoice_key),
    decision TEXT NOT NULL CHECK (decision IN ('auto_post', 'review')),
    first_reason TEXT CHECK (first_reason IS NULL OR first_reason IN ('file_rejected', 'qr_not_read', 'qr_invalid', 'extraction_incomplete', 'unsupported_type', 'qr_print_mismatch', 'duplicate', 'no_po', 'supplier_mismatch', 'currency_mismatch', 'date_out_of_range', 'line_not_on_po', 'line_arithmetic', 'total_arithmetic', 'price_out_of_tolerance', 'qty_over_receipt', 'qty_over_order', 'post_failed')),
    reasons INTEGER NOT NULL CHECK (reasons >= 0),                    -- how many; the reasons themselves are in decision_reasons
    po_number TEXT,
    supplier_cuit TEXT,
    total TEXT,
    currency TEXT,
    run_key TEXT NOT NULL REFERENCES pipeline_runs (run_key),
    seconds_total REAL NOT NULL CHECK (seconds_total >= 0),
    CHECK ((decision = 'auto_post') = (first_reason IS NULL)),
    CHECK ((decision = 'auto_post') = (reasons = 0))
);

CREATE TABLE IF NOT EXISTS decision_reasons (
    invoice_key TEXT NOT NULL REFERENCES decisions (invoice_key),
    reason TEXT NOT NULL CHECK (reason IN ('file_rejected', 'qr_not_read', 'qr_invalid', 'extraction_incomplete', 'unsupported_type', 'qr_print_mismatch', 'duplicate', 'no_po', 'supplier_mismatch', 'currency_mismatch', 'date_out_of_range', 'line_not_on_po', 'line_arithmetic', 'total_arithmetic', 'price_out_of_tolerance', 'qty_over_receipt', 'qty_over_order', 'post_failed')),
    rank INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 18),
    detail_json TEXT NOT NULL CHECK (json_valid(detail_json)),        -- keys, numbers and fixed codes, never a text of the document
    PRIMARY KEY (invoice_key, reason)
);

CREATE TABLE IF NOT EXISTS erp_posts (
    invoice_key TEXT PRIMARY KEY REFERENCES decisions (invoice_key),
    erp_name TEXT,                                                    -- the name the ERP gave the draft (none when the post failed before it had one)
    status TEXT NOT NULL CHECK (status IN ('posted', 'already_posted', 'post_failed')),
    readback_equal INTEGER CHECK (readback_equal IS NULL OR readback_equal IN (0, 1)),
    seconds REAL NOT NULL CHECK (seconds >= 0),
    run_key TEXT NOT NULL REFERENCES pipeline_runs (run_key),
    CHECK (status = 'post_failed' OR erp_name IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS daily_ops (
    date_key INTEGER PRIMARY KEY CHECK (date_key BETWEEN 19000101 AND 29991231),
    invoices INTEGER NOT NULL CHECK (invoices >= 0),
    accepted INTEGER NOT NULL CHECK (accepted >= 0),
    quarantined INTEGER NOT NULL CHECK (quarantined >= 0),
    auto_post INTEGER NOT NULL CHECK (auto_post >= 0),
    review INTEGER NOT NULL CHECK (review >= 0),
    posted INTEGER NOT NULL CHECK (posted >= 0),
    post_failed INTEGER NOT NULL CHECK (post_failed >= 0),
    seconds_mean REAL,
    seconds_p95 REAL,
    run_key TEXT NOT NULL REFERENCES pipeline_runs (run_key)
);

CREATE TABLE IF NOT EXISTS model_registry (
    model_version TEXT PRIMARY KEY,
    trained_as_of TEXT NOT NULL,
    artifact_name TEXT NOT NULL,                                      -- the folder name inside [paths] models_dir, never a path of this machine
    artifact_sha256 TEXT NOT NULL CHECK (length(artifact_sha256) = 64),
    tokenizer_sha256 TEXT NOT NULL CHECK (length(tokenizer_sha256) = 64),
    config_sha256 TEXT NOT NULL CHECK (length(config_sha256) = 64),
    chosen_epoch INTEGER NOT NULL CHECK (chosen_epoch >= 1),
    choice_json TEXT NOT NULL CHECK (json_valid(choice_json)),        -- every candidate with its figures and fit_splits (D12, AC22)
    validation_digest TEXT NOT NULL CHECK (length(validation_digest) = 64)
);

CREATE VIEW IF NOT EXISTS review_queue AS
SELECT d.invoice_key AS invoice_key, d.reasons AS reasons, r.started_at AS opened_at, d.run_key AS run_key
FROM decisions d JOIN pipeline_runs r ON r.run_key = d.run_key
WHERE d.decision = 'review'
UNION ALL
SELECT p.invoice_key, d.reasons + 1, r.started_at, p.run_key
FROM erp_posts p JOIN decisions d ON d.invoice_key = p.invoice_key JOIN pipeline_runs r ON r.run_key = p.run_key
WHERE p.status = 'post_failed';

CREATE TRIGGER IF NOT EXISTS extracted_fields_no_update BEFORE UPDATE ON extracted_fields
BEGIN SELECT RAISE(ABORT, 'extracted_fields es de solo inserción: una lectura emitida no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS extracted_fields_no_delete BEFORE DELETE ON extracted_fields
BEGIN SELECT RAISE(ABORT, 'extracted_fields es de solo inserción: una lectura emitida no se borra'); END;

CREATE TRIGGER IF NOT EXISTS extracted_fields_no_second_insert BEFORE INSERT ON extracted_fields
WHEN EXISTS (SELECT 1 FROM extracted_fields WHERE invoice_key = NEW.invoice_key AND field = NEW.field AND ordinal = NEW.ordinal)
BEGIN SELECT RAISE(ABORT, 'extracted_fields es de solo inserción: una lectura emitida ya está'); END;

CREATE TRIGGER IF NOT EXISTS qr_reads_no_update BEFORE UPDATE ON qr_reads
BEGIN SELECT RAISE(ABORT, 'qr_reads es de solo inserción: una lectura del QR emitida no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS qr_reads_no_delete BEFORE DELETE ON qr_reads
BEGIN SELECT RAISE(ABORT, 'qr_reads es de solo inserción: una lectura del QR emitida no se borra'); END;

CREATE TRIGGER IF NOT EXISTS qr_reads_no_second_insert BEFORE INSERT ON qr_reads
WHEN EXISTS (SELECT 1 FROM qr_reads WHERE invoice_key = NEW.invoice_key)
BEGIN SELECT RAISE(ABORT, 'qr_reads es de solo inserción: una lectura del QR emitida ya está'); END;

CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions es de solo inserción: una decisión emitida no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions es de solo inserción: una decisión emitida no se borra'); END;

CREATE TRIGGER IF NOT EXISTS decisions_no_second_insert BEFORE INSERT ON decisions
WHEN EXISTS (SELECT 1 FROM decisions WHERE invoice_key = NEW.invoice_key)
BEGIN SELECT RAISE(ABORT, 'decisions es de solo inserción: una decisión emitida ya está'); END;

CREATE TRIGGER IF NOT EXISTS decision_reasons_no_update BEFORE UPDATE ON decision_reasons
BEGIN SELECT RAISE(ABORT, 'decision_reasons es de solo inserción: un motivo emitido no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS decision_reasons_no_delete BEFORE DELETE ON decision_reasons
BEGIN SELECT RAISE(ABORT, 'decision_reasons es de solo inserción: un motivo emitido no se borra'); END;

CREATE TRIGGER IF NOT EXISTS decision_reasons_no_second_insert BEFORE INSERT ON decision_reasons
WHEN EXISTS (SELECT 1 FROM decision_reasons WHERE invoice_key = NEW.invoice_key AND reason = NEW.reason)
BEGIN SELECT RAISE(ABORT, 'decision_reasons es de solo inserción: un motivo emitido ya está'); END;

CREATE TRIGGER IF NOT EXISTS erp_posts_no_update BEFORE UPDATE ON erp_posts
BEGIN SELECT RAISE(ABORT, 'erp_posts es de solo inserción: una carga emitida no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS erp_posts_no_delete BEFORE DELETE ON erp_posts
BEGIN SELECT RAISE(ABORT, 'erp_posts es de solo inserción: una carga emitida no se borra'); END;

CREATE TRIGGER IF NOT EXISTS erp_posts_no_second_insert BEFORE INSERT ON erp_posts
WHEN EXISTS (SELECT 1 FROM erp_posts WHERE invoice_key = NEW.invoice_key)
BEGIN SELECT RAISE(ABORT, 'erp_posts es de solo inserción: una carga emitida ya está'); END;

CREATE TRIGGER IF NOT EXISTS daily_ops_no_update BEFORE UPDATE ON daily_ops
BEGIN SELECT RAISE(ABORT, 'daily_ops es de solo inserción: la fila de un día emitido no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS daily_ops_no_delete BEFORE DELETE ON daily_ops
BEGIN SELECT RAISE(ABORT, 'daily_ops es de solo inserción: la fila de un día emitido no se borra'); END;

CREATE TRIGGER IF NOT EXISTS daily_ops_no_second_insert BEFORE INSERT ON daily_ops
WHEN EXISTS (SELECT 1 FROM daily_ops WHERE date_key = NEW.date_key)
BEGIN SELECT RAISE(ABORT, 'daily_ops es de solo inserción: la fila de un día emitido ya está'); END;

CREATE TRIGGER IF NOT EXISTS model_registry_no_update BEFORE UPDATE ON model_registry
BEGIN SELECT RAISE(ABORT, 'model_registry es de solo inserción: una versión del modelo registrada no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS model_registry_no_delete BEFORE DELETE ON model_registry
BEGIN SELECT RAISE(ABORT, 'model_registry es de solo inserción: una versión del modelo registrada no se borra'); END;

CREATE TRIGGER IF NOT EXISTS model_registry_no_second_insert BEFORE INSERT ON model_registry
WHEN EXISTS (SELECT 1 FROM model_registry WHERE model_version = NEW.model_version)
BEGIN SELECT RAISE(ABORT, 'model_registry es de solo inserción: una versión del modelo registrada ya está'); END;
