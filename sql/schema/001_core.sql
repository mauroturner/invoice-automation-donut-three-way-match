-- What came in and what the runs did (plan, section 5; decisions D24, D28): the deliveries, the runs, the validation of each run and one row per file of the inbox.
-- ``deliveries`` and ``pipeline_runs`` are updated by the flow (a run is ``running`` until ``report`` closes it); ``validation_results`` and ``invoices`` are insert-only (three triggers
-- each, the same that every table of 002_flow.sql has): what a validation said and which file was received cannot be rewritten afterwards. Instants are text 'YYYY-MM-DD HH:MM:SS'.

CREATE TABLE IF NOT EXISTS deliveries (
    delivery_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('launch', 'daily', 'close')),
    day TEXT NOT NULL,
    as_of TEXT NOT NULL,
    files INTEGER NOT NULL CHECK (files >= 0),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    status TEXT NOT NULL CHECK (status IN ('published', 'loaded', 'rejected')),
    run_key TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_key TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed', 'rejected', 'no_delivery')),
    delivery_id TEXT REFERENCES deliveries (delivery_id),
    as_of TEXT,
    config_sha256 TEXT CHECK (config_sha256 IS NULL OR length(config_sha256) = 64),
    artifact_sha256 TEXT CHECK (artifact_sha256 IS NULL OR length(artifact_sha256) = 64),
    invoices INTEGER CHECK (invoices IS NULL OR invoices >= 0),
    peak_working_set_mb REAL,
    peak_gpu_mb REAL,
    memory_counter TEXT,
    message TEXT
);

CREATE TABLE IF NOT EXISTS validation_results (
    run_key TEXT NOT NULL REFERENCES pipeline_runs (run_key),
    rule_id TEXT NOT NULL,
    name_es TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('bloqueante', 'advertencia', 'informativa')),
    scope TEXT NOT NULL,
    aggregation TEXT NOT NULL CHECK (aggregation IN ('source', 'per_run', 'latest')),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    files_affected INTEGER NOT NULL CHECK (files_affected >= 0),
    examples_json TEXT NOT NULL CHECK (json_valid(examples_json)),   -- the keys of the files (their names), never their contents
    PRIMARY KEY (run_key, rule_id)
);

CREATE TABLE IF NOT EXISTS invoices (
    invoice_key TEXT PRIMARY KEY,                                    -- the file's name without its extension
    delivery_id TEXT NOT NULL REFERENCES deliveries (delivery_id),
    file_name TEXT NOT NULL,
    file_sha256 TEXT CHECK (file_sha256 IS NULL OR length(file_sha256) = 64),   -- a quarantined file is never hashed
    file_format TEXT,
    bytes INTEGER NOT NULL CHECK (bytes >= 0),
    pixels INTEGER,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'quarantined')),
    rule_id TEXT,                                                    -- the rule that quarantined it
    received_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS validation_results_no_update BEFORE UPDATE ON validation_results
BEGIN SELECT RAISE(ABORT, 'validation_results es de solo inserción: lo que una validación dijo no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS validation_results_no_delete BEFORE DELETE ON validation_results
BEGIN SELECT RAISE(ABORT, 'validation_results es de solo inserción: lo que una validación dijo no se borra'); END;

CREATE TRIGGER IF NOT EXISTS validation_results_no_second_insert BEFORE INSERT ON validation_results
WHEN EXISTS (SELECT 1 FROM validation_results WHERE run_key = NEW.run_key AND rule_id = NEW.rule_id)
BEGIN SELECT RAISE(ABORT, 'validation_results es de solo inserción: esa regla ya se informó en la corrida'); END;

CREATE TRIGGER IF NOT EXISTS invoices_no_update BEFORE UPDATE ON invoices
BEGIN SELECT RAISE(ABORT, 'invoices es de solo inserción: un archivo recibido no se cambia'); END;

CREATE TRIGGER IF NOT EXISTS invoices_no_delete BEFORE DELETE ON invoices
BEGIN SELECT RAISE(ABORT, 'invoices es de solo inserción: un archivo recibido no se borra'); END;

CREATE TRIGGER IF NOT EXISTS invoices_no_second_insert BEFORE INSERT ON invoices
WHEN EXISTS (SELECT 1 FROM invoices WHERE invoice_key = NEW.invoice_key)
BEGIN SELECT RAISE(ABORT, 'invoices es de solo inserción: ese archivo ya se recibió'); END;
