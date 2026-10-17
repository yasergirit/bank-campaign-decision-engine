from __future__ import annotations

import os
import re
from collections.abc import Iterator
from uuid import uuid4

import pandas as pd
import pytest

from bank_campaign.data.database import (
    build_load_script,
    read_features,
    run_sql,
    verify_database,
)
from bank_campaign.data.schema import BASELINE_FEATURES, COLUMNS
from bank_campaign.data.split import build_assignments

DATASET_SHA256 = "a" * 64
SPLIT_SHA256 = "b" * 64

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("BANK_CAMPAIGN_DB_TESTS") != "1",
        reason="set BANK_CAMPAIGN_DB_TESTS=1 to run isolated PostgreSQL tests",
    ),
]

VALID_ROW: dict[str, object] = {
    "age": 30,
    "job": "admin.",
    "marital": "married",
    "education": "university.degree",
    "default": "no",
    "housing": "yes",
    "loan": "no",
    "contact": "cellular",
    "month": "may",
    "day_of_week": "mon",
    "duration": 100,
    "campaign": 1,
    "pdays": 999,
    "previous": 0,
    "poutcome": "nonexistent",
    "emp.var.rate": 1.1,
    "cons.price.idx": 93.994,
    "cons.conf.idx": -36.4,
    "euribor3m": 4.857,
    "nr.employed": 5191.0,
    "y": "no",
}


def synthetic_frame(*, rows: int = 12) -> pd.DataFrame:
    records = []
    for index in range(rows):
        records.append(
            {
                **VALID_ROW,
                "age": 30 + index,
                "job": "technician" if index % 2 else "admin.",
                "previous": index % 3,
                "poutcome": "failure" if index % 3 else "nonexistent",
                "y": "yes" if index % 4 == 0 else "no",
            }
        )
    return pd.DataFrame(records, columns=COLUMNS)


@pytest.fixture(scope="module")
def isolated_database() -> Iterator[str]:
    database = f"bank_campaign_test_{uuid4().hex}"
    run_sql(f'CREATE DATABASE "{database}";', database="postgres")
    try:
        yield database
    finally:
        run_sql(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE);', database="postgres")


@pytest.fixture(scope="module")
def loaded_database(isolated_database: str) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    frame = synthetic_frame()
    assignments = build_assignments(frame)
    script = build_load_script(
        frame,
        assignments,
        dataset_sha256=DATASET_SHA256,
        split_sha256=SPLIT_SHA256,
    )
    run_sql(script, database=isolated_database)
    return isolated_database, frame, assignments


def _scalar(sql: str, *, database: str) -> str:
    query = sql.strip().removesuffix(";")
    output = run_sql(f"COPY ({query}) TO STDOUT WITH (FORMAT CSV);", database=database)
    values = [line.strip() for line in output.splitlines() if line.strip()]
    assert values
    return values[-1]


def _tamper_first_copy_row(script: str) -> str:
    match = re.search(r"(?m)^COPY .* FROM STDIN.*;$", script)
    assert match is not None
    data_start = script.index("\n", match.end()) + 1
    data_end = script.index("\n\\.", data_start)
    rows = script[data_start:data_end].splitlines()
    assert len(rows) >= 2
    rows[1] = rows[1].replace("admin.", "astronaut", 1)
    assert "astronaut" in rows[1]
    return script[:data_start] + "\n".join(rows) + script[data_end:]


def test_load_is_idempotent_and_preserves_original_loaded_at(
    loaded_database: tuple[str, pd.DataFrame, pd.DataFrame],
) -> None:
    database, frame, assignments = loaded_database
    before = _scalar(
        "SELECT loaded_at::text FROM campaign.dataset_loads "
        f"WHERE dataset_sha256 = '{DATASET_SHA256}';",
        database=database,
    )

    run_sql(
        build_load_script(
            frame,
            assignments,
            dataset_sha256=DATASET_SHA256,
            split_sha256=SPLIT_SHA256,
        ),
        database=database,
    )

    after = _scalar(
        "SELECT loaded_at::text FROM campaign.dataset_loads "
        f"WHERE dataset_sha256 = '{DATASET_SHA256}';",
        database=database,
    )
    assert after == before
    assert int(_scalar("SELECT count(*) FROM campaign.raw_records;", database=database)) == len(
        frame
    )
    assert int(
        _scalar("SELECT count(*) FROM campaign.split_assignments;", database=database)
    ) == len(assignments)


def test_read_features_has_exact_feature_boundary_and_python_dtypes(
    loaded_database: tuple[str, pd.DataFrame, pd.DataFrame],
) -> None:
    database, frame, _ = loaded_database

    features = read_features(database=database)

    assert tuple(features.columns) == ("row_id", *BASELINE_FEATURES)
    assert features["row_id"].tolist() == list(range(len(frame)))
    assert features["row_id"].dtype == "int64"
    assert features["age"].dtype == "int64"
    assert features["previous"].dtype == "int64"
    for column in set(BASELINE_FEATURES) - {"age", "previous"}:
        assert pd.api.types.is_string_dtype(features[column])
    assert {"y", "duration", "split"}.isdisjoint(features.columns)


def test_verify_database_proves_full_python_sql_parity(
    loaded_database: tuple[str, pd.DataFrame, pd.DataFrame],
) -> None:
    database, frame, assignments = loaded_database

    result = verify_database(frame, assignments, database=database)

    expected_counts = {}
    for split in ("train", "validation", "test"):
        selected = assignments.loc[assignments["split"] == split]
        expected_counts[split] = {
            "assigned": len(selected),
            "retained": int(selected["included"].sum()),
            "excluded": int((~selected["included"]).sum()),
        }
    actual_counts = {
        row["split"]: {
            "assigned": row["assigned"],
            "retained": row["retained"],
            "excluded": row["excluded"],
        }
        for row in result["split_counts"]
    }
    assert result["raw_rows"] == len(frame)
    assert result["raw_payload_parity_passed"] is True
    assert result["feature_rows"] == len(frame)
    assert result["feature_columns"] == list(BASELINE_FEATURES)
    assert actual_counts == expected_counts
    assert result["parity_passed"] is True
    assert "metrics" not in result
    assert "predictions" not in result


@pytest.mark.parametrize(
    ("column", "tampered_value"),
    [
        ("duration", 101),
        ("contact", "telephone"),
        ("emp.var.rate", 1.2),
    ],
    ids=["post-call-duration", "excluded-contact-channel", "economic-indicator"],
)
def test_verify_database_detects_tampering_in_excluded_raw_columns(
    loaded_database: tuple[str, pd.DataFrame, pd.DataFrame],
    column: str,
    tampered_value: object,
) -> None:
    database, original, assignments = loaded_database
    original_value = original.loc[0, column]
    quoted_column = '"' + column.replace('"', '""') + '"'

    def sql_literal(value: object) -> str:
        if isinstance(value, str):
            return "'" + value.replace("'", "''") + "'"
        return str(value)

    try:
        run_sql(
            f"UPDATE campaign.raw_records SET {quoted_column} = "
            f"{sql_literal(tampered_value)} WHERE row_id = 0;",
            database=database,
        )

        with pytest.raises(ValueError, match="Stored raw records differ"):
            verify_database(original, assignments, database=database)
    finally:
        run_sql(
            f"UPDATE campaign.raw_records SET {quoted_column} = "
            f"{sql_literal(original_value)} WHERE row_id = 0;",
            database=database,
        )

    assert verify_database(original, assignments, database=database)["parity_passed"] is True


def test_constraint_failure_rolls_back_entire_load(isolated_database: str) -> None:
    database = f"bank_campaign_test_{uuid4().hex}"
    run_sql(f'CREATE DATABASE "{database}";', database="postgres")
    try:
        frame = synthetic_frame()
        script = build_load_script(
            frame,
            build_assignments(frame),
            dataset_sha256="c" * 64,
            split_sha256="d" * 64,
        )
        with pytest.raises(ValueError):
            run_sql(_tamper_first_copy_row(script), database=database)

        schema_exists = _scalar(
            "SELECT count(*) FROM pg_namespace WHERE nspname = 'campaign';", database=database
        )
        assert schema_exists == "0"
    finally:
        run_sql(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE);', database="postgres")


def test_alternate_source_is_refused_without_changing_first_load(
    loaded_database: tuple[str, pd.DataFrame, pd.DataFrame],
) -> None:
    database, original, original_assignments = loaded_database
    changed = original.copy()
    changed.loc[0, "age"] = 99

    with pytest.raises(ValueError):
        run_sql(
            build_load_script(
                changed,
                build_assignments(changed),
                dataset_sha256="e" * 64,
                split_sha256="f" * 64,
            ),
            database=database,
        )

    assert _scalar("SELECT count(*) FROM campaign.dataset_loads;", database=database) == "1"
    assert _scalar(
        "SELECT age FROM campaign.raw_records WHERE row_id = 0;", database=database
    ) == str(original.loc[0, "age"])
    assert (
        verify_database(original, original_assignments, database=database)["parity_passed"] is True
    )
