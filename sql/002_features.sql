-- Stable SQL interfaces consumed by training and parity checks.
-- model_features contains only the nine pre-call fields approved in schema.py.

CREATE OR REPLACE VIEW campaign.model_features AS
SELECT
    records.row_id,
    records."age",
    records."job",
    records."marital",
    records."education",
    records."default",
    records."housing",
    records."loan",
    records."previous",
    records."poutcome"
FROM campaign.raw_records AS records;

COMMENT ON VIEW campaign.model_features IS
    'All raw rows with the exact nine baseline model features; labels, split data, and post-call fields are excluded.';

CREATE OR REPLACE VIEW campaign.training_metadata AS
WITH labeled_records AS (
    SELECT
        row_id,
        CASE "y"
            WHEN 'yes' THEN 1
            WHEN 'no' THEN 0
        END AS target
    FROM campaign.raw_records
)
SELECT
    labels.row_id,
    labels.target,
    assignments.split,
    assignments.included,
    assignments.duplicate_of
FROM labeled_records AS labels
JOIN campaign.split_assignments AS assignments
    ON assignments.row_id = labels.row_id;

COMMENT ON VIEW campaign.training_metadata IS
    'One-to-one label and frozen split metadata for joining to model_features by row_id.';
