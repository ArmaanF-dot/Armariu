-- Armariu deal radar schema (SQLite). Safe to run repeatedly.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Borrower profile, filled by `radar.ingest enrich` (REST API).
CREATE TABLE IF NOT EXISTS companies (
    company_number   TEXT PRIMARY KEY,
    name             TEXT,
    company_status   TEXT,
    company_type     TEXT,
    date_of_creation TEXT,
    sic_codes        TEXT,          -- JSON array
    enriched_at      TEXT
);

-- One row per registered charge (current state).
CREATE TABLE IF NOT EXISTS charges (
    company_number           TEXT NOT NULL,
    charge_id                TEXT NOT NULL,
    charge_number            INTEGER,
    charge_code              TEXT,
    status                   TEXT,   -- outstanding | fully-satisfied | part-satisfied | satisfied
    created_on               TEXT,   -- date the charge was created
    delivered_on             TEXT,   -- date it reached Companies House
    satisfied_on             TEXT,
    classification_text      TEXT,
    secured_text             TEXT,
    particulars_text         TEXT,
    contains_fixed           INTEGER,
    contains_floating        INTEGER,
    floating_covers_all      INTEGER,
    contains_negative_pledge INTEGER,
    more_than_four_persons   INTEGER,
    first_seen_at            TEXT NOT NULL,
    last_event_at            TEXT,
    last_timepoint           INTEGER,
    deleted                  INTEGER NOT NULL DEFAULT 0,
    raw_json                 TEXT,
    PRIMARY KEY (company_number, charge_id)
);
CREATE INDEX IF NOT EXISTS idx_charges_delivered ON charges (delivered_on);
CREATE INDEX IF NOT EXISTS idx_charges_satisfied ON charges (satisfied_on);
CREATE INDEX IF NOT EXISTS idx_charges_status    ON charges (status);

-- Persons entitled (the chargees / lenders) on each charge.
CREATE TABLE IF NOT EXISTS charge_persons (
    company_number TEXT NOT NULL,
    charge_id      TEXT NOT NULL,
    name_raw       TEXT NOT NULL,
    name_norm      TEXT NOT NULL,
    PRIMARY KEY (company_number, charge_id, name_norm)
);
CREATE INDEX IF NOT EXISTS idx_persons_norm ON charge_persons (name_norm);

-- Append-only history of every event we processed.
CREATE TABLE IF NOT EXISTS charge_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    company_number TEXT NOT NULL,
    charge_id      TEXT NOT NULL,
    event_type     TEXT,            -- created | updated | deleted | backfill ...
    timepoint      INTEGER,
    published_at   TEXT,
    fields_changed TEXT,
    status_before  TEXT,
    status_after   TEXT,
    seen_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_charge ON charge_events (company_number, charge_id);

-- One row per distinct lender name. Cached so each name is classified once.
CREATE TABLE IF NOT EXISTS lender_classifications (
    name_norm     TEXT PRIMARY KEY,
    display_name  TEXT,
    category      TEXT NOT NULL,
    parent_firm   TEXT,
    confidence    REAL,
    source        TEXT NOT NULL,    -- human | seed | agent
    review_status TEXT NOT NULL,    -- ok | auto_ok | needs_review
    reason        TEXT,
    updated_at    TEXT
);

-- Which agent result files have been applied (by content hash).
CREATE TABLE IF NOT EXISTS applied_results (
    filename      TEXT PRIMARY KEY,
    sha256        TEXT NOT NULL,
    applied_at    TEXT NOT NULL,
    rows_ok       INTEGER,
    rows_rejected INTEGER
);

-- Each borrower's full charge list from the REST API, older charges included, so the radar can
-- tell a refinancing from a first loan. Kept apart from `charges`: the register, tiers and
-- weekly digests only ever count charges seen on the stream.
CREATE TABLE IF NOT EXISTS borrower_history (
    company_number TEXT NOT NULL,
    charge_id      TEXT NOT NULL,
    status         TEXT,
    created_on     TEXT,
    delivered_on   TEXT,
    satisfied_on   TEXT,
    classification TEXT,
    persons        TEXT,            -- JSON array of names as filed
    PRIMARY KEY (company_number, charge_id)
);
CREATE TABLE IF NOT EXISTS borrower_history_fetched (
    company_number TEXT PRIMARY KEY,
    fetched_at     TEXT NOT NULL,
    total          INTEGER
);
