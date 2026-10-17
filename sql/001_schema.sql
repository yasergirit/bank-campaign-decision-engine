-- Persistent PostgreSQL schema for the audited UCI Bank Marketing dataset.
-- Source column names intentionally match bank-additional-full.csv exactly.

CREATE SCHEMA IF NOT EXISTS campaign;

CREATE TABLE IF NOT EXISTS campaign.dataset_loads (
    dataset_sha256 TEXT PRIMARY KEY
        CHECK (dataset_sha256 ~ '^[0-9a-f]{64}$'),
    row_count INTEGER NOT NULL
        CHECK (row_count > 0),
    split_assignment_sha256 TEXT NOT NULL
        CHECK (split_assignment_sha256 ~ '^[0-9a-f]{64}$'),
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE campaign.dataset_loads IS
    'One provenance record per loaded source dataset and frozen split assignment.';

CREATE TABLE IF NOT EXISTS campaign.raw_records (
    row_id BIGINT PRIMARY KEY
        CHECK (row_id >= 0),
    "age" INTEGER NOT NULL
        CHECK ("age" BETWEEN 1 AND 120),
    "job" TEXT NOT NULL
        CHECK ("job" IN (
            'admin.', 'blue-collar', 'entrepreneur', 'housemaid', 'management',
            'retired', 'self-employed', 'services', 'student', 'technician',
            'unemployed', 'unknown'
        )),
    "marital" TEXT NOT NULL
        CHECK ("marital" IN ('divorced', 'married', 'single', 'unknown')),
    "education" TEXT NOT NULL
        CHECK ("education" IN (
            'basic.4y', 'basic.6y', 'basic.9y', 'high.school', 'illiterate',
            'professional.course', 'university.degree', 'unknown'
        )),
    "default" TEXT NOT NULL
        CHECK ("default" IN ('yes', 'no', 'unknown')),
    "housing" TEXT NOT NULL
        CHECK ("housing" IN ('yes', 'no', 'unknown')),
    "loan" TEXT NOT NULL
        CHECK ("loan" IN ('yes', 'no', 'unknown')),
    "contact" TEXT NOT NULL
        CHECK ("contact" IN ('cellular', 'telephone')),
    "month" TEXT NOT NULL
        CHECK ("month" IN (
            'jan', 'feb', 'mar', 'apr', 'may', 'jun',
            'jul', 'aug', 'sep', 'oct', 'nov', 'dec'
        )),
    "day_of_week" TEXT NOT NULL
        CHECK ("day_of_week" IN ('mon', 'tue', 'wed', 'thu', 'fri')),
    "duration" INTEGER NOT NULL
        CHECK ("duration" >= 0),
    "campaign" INTEGER NOT NULL
        CHECK ("campaign" >= 1),
    "pdays" INTEGER NOT NULL
        CHECK ("pdays" >= 0),
    "previous" INTEGER NOT NULL
        CHECK ("previous" >= 0),
    "poutcome" TEXT NOT NULL
        CHECK ("poutcome" IN ('failure', 'nonexistent', 'success')),
    "emp.var.rate" DOUBLE PRECISION NOT NULL
        CHECK (
            "emp.var.rate" > '-Infinity'::DOUBLE PRECISION
            AND "emp.var.rate" < 'Infinity'::DOUBLE PRECISION
        ),
    "cons.price.idx" DOUBLE PRECISION NOT NULL
        CHECK (
            "cons.price.idx" > 0
            AND "cons.price.idx" > '-Infinity'::DOUBLE PRECISION
            AND "cons.price.idx" < 'Infinity'::DOUBLE PRECISION
        ),
    "cons.conf.idx" DOUBLE PRECISION NOT NULL
        CHECK (
            "cons.conf.idx" > '-Infinity'::DOUBLE PRECISION
            AND "cons.conf.idx" < 'Infinity'::DOUBLE PRECISION
        ),
    "euribor3m" DOUBLE PRECISION NOT NULL
        CHECK (
            "euribor3m" > 0
            AND "euribor3m" > '-Infinity'::DOUBLE PRECISION
            AND "euribor3m" < 'Infinity'::DOUBLE PRECISION
        ),
    "nr.employed" DOUBLE PRECISION NOT NULL
        CHECK (
            "nr.employed" > 0
            AND "nr.employed" > '-Infinity'::DOUBLE PRECISION
            AND "nr.employed" < 'Infinity'::DOUBLE PRECISION
        ),
    "y" TEXT NOT NULL
        CHECK ("y" IN ('yes', 'no')),
    dataset_sha256 TEXT NOT NULL
        REFERENCES campaign.dataset_loads (dataset_sha256)
);

COMMENT ON TABLE campaign.raw_records IS
    'Validated source rows; row_id is the zero-based original CSV record position.';
COMMENT ON COLUMN campaign.raw_records."duration" IS
    'Post-call duration retained for audit only; it is intentionally absent from model_features.';

CREATE TABLE IF NOT EXISTS campaign.split_assignments (
    row_id BIGINT PRIMARY KEY
        REFERENCES campaign.raw_records (row_id),
    split TEXT NOT NULL
        CHECK (split IN ('train', 'validation', 'test')),
    included BOOLEAN NOT NULL,
    duplicate_of BIGINT
        REFERENCES campaign.raw_records (row_id),
    CHECK (
        (included AND duplicate_of IS NULL)
        OR (
            NOT included
            AND duplicate_of IS NOT NULL
            AND duplicate_of >= 0
            AND duplicate_of < row_id
        )
    )
);

COMMENT ON TABLE campaign.split_assignments IS
    'Frozen source-order split membership and predictor-only duplicate exclusions.';

CREATE INDEX IF NOT EXISTS split_assignments_lookup_idx
    ON campaign.split_assignments (split, included, row_id);
