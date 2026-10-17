"""Transactional PostgreSQL ingestion and SQL/Python feature parity via Docker's psql."""

import argparse
import json
import re
import subprocess
from hashlib import sha256
from io import StringIO
from pathlib import Path

import pandas as pd

from bank_campaign.data.download import CSV_NAME, verify_manifest
from bank_campaign.data.schema import BASELINE_FEATURES, COLUMNS, FLOAT_COLUMNS, INTEGER_COLUMNS
from bank_campaign.data.split import build_assignments, freeze_splits
from bank_campaign.data.validate import validate_dataset
from bank_campaign.features import select_features

ROOT = Path(__file__).resolve().parents[3]
SQL_DIRECTORY = ROOT / "sql"


def run_sql(script: str, *, database: str | None = None) -> str:
    """Use the bundled client; credentials remain in the container environment."""
    if database is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database):
        raise ValueError("Invalid local PostgreSQL database name.")
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(ROOT),
        "-f",
        str(ROOT / "compose.yaml"),
        "exec",
        "-T",
    ]
    if database is not None:
        command.extend(["-e", f"POSTGRES_DB={database}"])
    command.extend(
        [
            "db",
            "sh",
            "-c",
            'exec psql -X -q -w -v ON_ERROR_STOP=1 -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
        ]
    )
    try:
        result = subprocess.run(
            command, input=script, capture_output=True, text=True, timeout=120, cwd=ROOT
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            "Cannot run local Docker PostgreSQL. Start it with docker compose up -d --wait db."
        ) from exc
    if result.returncode:
        raise ValueError(f"PostgreSQL command failed: {result.stderr.strip()[:1800]}")
    return result.stdout


def _columns(names) -> str:
    # Names come only from the source contract/static loader columns, never SQL input.
    return ", ".join('"' + name.replace('"', '""') + '"' for name in names)


def build_load_script(
    frame: pd.DataFrame,
    assignments: pd.DataFrame,
    *,
    dataset_sha256: str,
    split_sha256: str,
) -> str:
    """Build a single psql transaction; source data must pass the ingestion validator."""
    if tuple(frame.columns) != COLUMNS:
        raise ValueError("Expected the exact canonical raw-data columns.")
    for digest in (dataset_sha256, split_sha256):
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Expected a lowercase SHA-256 fingerprint.")
    try:
        pd.testing.assert_frame_equal(
            assignments.reset_index(drop=True), build_assignments(frame), check_dtype=False
        )
    except AssertionError as exc:
        raise ValueError("Split assignments differ from the frozen source-order policy.") from exc
    # psql recognizes a standalone backslash-dot line as end-of-COPY. The validated
    # UCI domain has no backslashes/newlines; reject them even for internal callers.
    if any(
        any(character in str(value) for character in ("\n", "\r", "\\"))
        for row in frame.itertuples(index=False, name=None)
        for value in row
    ):
        raise ValueError("Raw CSV values cannot contain newlines or backslashes.")
    raw = frame.reset_index(drop=True).copy()
    raw.insert(0, "row_id", range(len(raw)))
    raw["dataset_sha256"] = dataset_sha256
    splits = assignments.copy()
    splits["duplicate_of"] = pd.array(
        splits["duplicate_of"].where(~splits["included"], pd.NA).to_list(), dtype="Int64"
    )
    raw_columns, split_columns = _columns(raw.columns), _columns(splits.columns)
    schema = (SQL_DIRECTORY / "001_schema.sql").read_text()
    views = (SQL_DIRECTORY / "002_features.sql").read_text()
    return f"""BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '90s';
DO $lock$ BEGIN PERFORM pg_advisory_xact_lock(594103871); END $lock$;
{schema}
DO $source_guard$
BEGIN
    IF EXISTS (
        SELECT 1 FROM campaign.dataset_loads
        WHERE dataset_sha256 <> '{dataset_sha256}'
           OR row_count <> {len(frame)}
           OR split_assignment_sha256 <> '{split_sha256}'
    ) THEN
        RAISE EXCEPTION 'Database provenance differs; refusing to mix source/split versions';
    END IF;
END $source_guard$;
CREATE TEMP TABLE stage_raw (LIKE campaign.raw_records INCLUDING DEFAULTS) ON COMMIT DROP;
CREATE TEMP TABLE stage_splits (LIKE campaign.split_assignments INCLUDING DEFAULTS) ON COMMIT DROP;
COPY stage_raw ({raw_columns}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE);
{raw.to_csv(index=False, lineterminator=chr(10))}\\.
COPY stage_splits ({split_columns}) FROM STDIN WITH (FORMAT CSV, HEADER TRUE, NULL '');
{splits.to_csv(index=False, lineterminator=chr(10))}\\.
INSERT INTO campaign.dataset_loads (dataset_sha256, row_count, split_assignment_sha256)
VALUES ('{dataset_sha256}', {len(frame)}, '{split_sha256}') ON CONFLICT DO NOTHING;
INSERT INTO campaign.raw_records ({raw_columns})
SELECT {raw_columns} FROM stage_raw ON CONFLICT (row_id) DO NOTHING;
INSERT INTO campaign.split_assignments ({split_columns})
SELECT {split_columns} FROM stage_splits ON CONFLICT (row_id) DO NOTHING;
DO $contents_guard$
BEGIN
    IF EXISTS (
        (SELECT {raw_columns} FROM campaign.raw_records EXCEPT ALL SELECT {raw_columns} FROM stage_raw)
        UNION ALL
        (SELECT {raw_columns} FROM stage_raw EXCEPT ALL SELECT {raw_columns} FROM campaign.raw_records)
    ) OR EXISTS (
        (SELECT {split_columns} FROM campaign.split_assignments EXCEPT ALL SELECT {split_columns} FROM stage_splits)
        UNION ALL
        (SELECT {split_columns} FROM stage_splits EXCEPT ALL SELECT {split_columns} FROM campaign.split_assignments)
    ) THEN
        RAISE EXCEPTION 'Stored records/assignments differ from the verified input; transaction rolled back';
    END IF;
END $contents_guard$;
{views}
COMMIT;
"""


def _read_query(
    query: str, *, database: str | None = None, as_strings: bool = False
) -> pd.DataFrame:
    output = run_sql(
        f"COPY ({query}) TO STDOUT WITH (FORMAT CSV, HEADER TRUE);\n", database=database
    )
    return pd.read_csv(StringIO(output), keep_default_na=False, dtype=str if as_strings else None)


def read_features(*, database: str | None = None) -> pd.DataFrame:
    frame = _read_query("SELECT * FROM campaign.model_features ORDER BY row_id", database=database)
    if tuple(frame.columns) != ("row_id", *BASELINE_FEATURES):
        raise ValueError("SQL feature view violates the approved feature allowlist/order.")
    for column in ("row_id", "age", "previous"):
        frame[column] = frame[column].astype("int64")
    for column in set(BASELINE_FEATURES) - {"age", "previous"}:
        frame[column] = frame[column].astype("str")
    return frame


def verify_database(
    frame: pd.DataFrame, assignments: pd.DataFrame, *, database: str | None = None
) -> dict:
    """Compare every field, label/split assignment and row ID; never score models."""
    raw = _read_query(
        f"SELECT row_id, {_columns(COLUMNS)}, dataset_sha256 FROM campaign.raw_records ORDER BY row_id",
        database=database,
        as_strings=True,
    )
    provenance = _read_query(
        "SELECT dataset_sha256, row_count FROM campaign.dataset_loads",
        database=database,
    )
    if (
        len(provenance) != 1
        or int(provenance.iloc[0]["row_count"]) != len(frame)
        or set(raw["dataset_sha256"]) != {provenance.iloc[0]["dataset_sha256"]}
    ):
        raise ValueError("Raw dataset provenance differs from the loaded source.")
    expected_raw = frame.reset_index(drop=True).copy()
    expected_raw.insert(0, "row_id", range(len(frame)))
    for column in ("row_id", *INTEGER_COLUMNS, *FLOAT_COLUMNS):
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    try:
        pd.testing.assert_frame_equal(
            raw.drop(columns="dataset_sha256"), expected_raw, check_dtype=False, rtol=0, atol=0
        )
    except AssertionError as exc:
        raise ValueError("Stored raw records differ from the validated source.") from exc
    sql_features = read_features(database=database)
    expected = select_features(frame.reset_index(drop=True))
    expected.insert(0, "row_id", range(len(expected)))
    try:
        pd.testing.assert_frame_equal(sql_features, expected)
    except AssertionError as exc:
        raise ValueError("SQL and Python features differ.") from exc
    metadata = _read_query(
        "SELECT * FROM campaign.training_metadata ORDER BY row_id", database=database
    )
    expected_metadata = assignments.copy()
    expected_metadata.insert(1, "target", frame["y"].map({"no": 0, "yes": 1}).to_numpy())
    expected_metadata["included"] = expected_metadata["included"].map({True: "t", False: "f"})
    expected_metadata["duplicate_of"] = expected_metadata["duplicate_of"].map(
        lambda value: "" if value == -1 else str(value)
    )
    metadata["duplicate_of"] = metadata["duplicate_of"].astype(str)
    try:
        pd.testing.assert_frame_equal(metadata, expected_metadata, check_dtype=False)
    except AssertionError as exc:
        raise ValueError("SQL label/split metadata differs from the frozen assignments.") from exc
    counts = _read_query(
        """SELECT split, count(*) AS assigned,
        count(*) FILTER (WHERE included) AS retained,
        count(*) FILTER (WHERE NOT included) AS excluded
        FROM campaign.split_assignments GROUP BY split ORDER BY split""",
        database=database,
    )
    return {
        "raw_rows": len(raw),
        "raw_payload_parity_passed": True,
        "feature_rows": len(sql_features),
        "feature_columns": list(BASELINE_FEATURES),
        "split_counts": counts.to_dict(orient="records"),
        "parity_passed": True,
    }


def _verify_provenance(
    dataset_sha256: str, split_sha256: str, rows: int, *, database: str | None = None
) -> None:
    provenance = _read_query(
        "SELECT dataset_sha256, row_count, split_assignment_sha256 FROM campaign.dataset_loads",
        database=database,
    )
    if (
        len(provenance) != 1
        or provenance.iloc[0]["dataset_sha256"] != dataset_sha256
        or int(provenance.iloc[0]["row_count"]) != rows
        or provenance.iloc[0]["split_assignment_sha256"] != split_sha256
    ):
        raise ValueError("Database provenance differs from the frozen source/split.")


def load_database(*, database: str | None = None) -> dict:
    directory = ROOT / "data/raw"
    acquisition = verify_manifest(directory)
    frame = validate_dataset(directory / CSV_NAME, expected_rows=41188)
    frozen = json.loads((ROOT / "reports/selection_freeze.json").read_text())
    if acquisition["files"][CSV_NAME]["sha256"] != frozen["source_csv_sha256"]:
        raise ValueError("Dataset differs from the frozen modeling source.")
    assignments, manifest = freeze_splits(
        frame,
        csv_sha256=frozen["source_csv_sha256"],
        assignment_path=ROOT / "data/processed/split_assignments.csv",
        manifest_path=ROOT / "reports/split_manifest.json",
    )
    if manifest["assignment_sha256"] != frozen["split_assignment_sha256"]:
        raise ValueError("Split fingerprint differs from the frozen modeling source.")
    run_sql(
        build_load_script(
            frame,
            assignments,
            dataset_sha256=frozen["source_csv_sha256"],
            split_sha256=manifest["assignment_sha256"],
        ),
        database=database,
    )
    result = verify_database(frame, assignments, database=database)
    _verify_provenance(
        frozen["source_csv_sha256"], manifest["assignment_sha256"], len(frame), database=database
    )
    result.update(
        {
            "source_csv_sha256": frozen["source_csv_sha256"],
            "split_assignment_sha256": manifest["assignment_sha256"],
            "schema_sha256": sha256((SQL_DIRECTORY / "001_schema.sql").read_bytes()).hexdigest(),
            "views_sha256": sha256((SQL_DIRECTORY / "002_features.sql").read_bytes()).hexdigest(),
        }
    )
    return result


def verify_loaded_database(*, database: str | None = None) -> dict:
    directory = ROOT / "data/raw"
    acquisition = verify_manifest(directory)
    frozen = json.loads((ROOT / "reports/selection_freeze.json").read_text())
    if acquisition["files"][CSV_NAME]["sha256"] != frozen["source_csv_sha256"]:
        raise ValueError("Dataset differs from the frozen modeling source.")
    frame = validate_dataset(directory / CSV_NAME, expected_rows=41188)
    assignments, manifest = freeze_splits(
        frame,
        csv_sha256=frozen["source_csv_sha256"],
        assignment_path=ROOT / "data/processed/split_assignments.csv",
        manifest_path=ROOT / "reports/split_manifest.json",
    )
    if manifest["assignment_sha256"] != frozen["split_assignment_sha256"]:
        raise ValueError("Split fingerprint differs from the frozen modeling source.")
    _verify_provenance(
        frozen["source_csv_sha256"], manifest["assignment_sha256"], len(frame), database=database
    )
    return verify_database(frame, assignments, database=database)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["load", "verify"])
    parser.add_argument("--database", help="Optional database name in the local project container")
    args = parser.parse_args()
    try:
        result = (
            load_database(database=args.database)
            if args.action == "load"
            else verify_loaded_database(database=args.database)
        )
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Database operation failed: {exc}\n")
    if args.action == "load" and args.database is None:
        (ROOT / "reports/database_verification.json").write_text(
            json.dumps(result, indent=2) + "\n"
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
