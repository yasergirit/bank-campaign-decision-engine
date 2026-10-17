from __future__ import annotations

import subprocess
from collections.abc import Callable

import pandas as pd
import pytest

from bank_campaign.data.database import build_load_script, run_sql
from bank_campaign.data.schema import COLUMNS
from bank_campaign.data.split import build_assignments

DATASET_SHA256 = "a" * 64
SPLIT_SHA256 = "b" * 64

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
        record = {
            **VALID_ROW,
            "age": 30 + index,
            "job": "technician" if index % 2 else "admin.",
            "previous": index % 3,
            "poutcome": "failure" if index % 3 else "nonexistent",
            "y": "yes" if index % 4 == 0 else "no",
        }
        records.append(record)
    return pd.DataFrame(records, columns=COLUMNS)


def test_run_sql_uses_psql_stdin_and_on_error_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="42\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert run_sql("SELECT 42;", database="bank_campaign_test_safe") == "42\n"

    command = observed["command"]
    kwargs = observed["kwargs"]
    assert isinstance(command, list)
    assert command[:2] == ["docker", "compose"]
    assert "exec" in command
    assert "-T" in command
    assert "db" in command
    assert any("psql" in argument for argument in command)
    assert any("ON_ERROR_STOP=1" in argument for argument in command)
    assert any("bank_campaign_test_safe" in argument for argument in command)
    assert "SELECT 42;" not in command
    assert isinstance(kwargs, dict)
    assert kwargs["input"] == "SELECT 42;"
    assert kwargs["text"] is True
    assert kwargs["capture_output"] is True
    assert kwargs.get("shell", False) is False


@pytest.mark.parametrize(
    "database",
    ["", "with-hyphen", "name;DROP DATABASE postgres", "../postgres", "a" * 64],
)
def test_run_sql_rejects_unsafe_database_names(database: str) -> None:
    with pytest.raises(ValueError, match="database"):
        run_sql("SELECT 1;", database=database)


def test_run_sql_translates_nonzero_psql_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="relation does not exist")

    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(ValueError, match="relation does not exist"):
        run_sql("SELECT broken;")


def test_run_sql_translates_missing_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("docker executable was not found")

    monkeypatch.setattr(subprocess, "run", fail)

    with pytest.raises(ValueError, match="Docker PostgreSQL"):
        run_sql("SELECT broken;")


def test_build_load_script_contains_atomic_copy_and_parity_guards() -> None:
    frame = synthetic_frame()
    assignments = build_assignments(frame)

    script = build_load_script(
        frame,
        assignments,
        dataset_sha256=DATASET_SHA256,
        split_sha256=SPLIT_SHA256,
    )

    upper_script = script.upper()
    assert upper_script.count("BEGIN;") == 1
    assert upper_script.count("COMMIT;") == 1
    assert upper_script.index("BEGIN;") < upper_script.index("COPY")
    assert upper_script.index("COPY") < upper_script.index("COMMIT;")
    assert upper_script.count("COPY") >= 2
    assert "STAGE_RAW" in upper_script
    assert "STAGE_SPLITS" in upper_script
    assert "ON CONFLICT" in upper_script
    assert "DO NOTHING" in upper_script
    assert upper_script.count("EXCEPT") >= 4
    assert DATASET_SHA256 in script
    assert SPLIT_SHA256 in script
    assert "duration" in script
    assert "campaign.model_features" in script
    assert "campaign.training_metadata" in script


@pytest.mark.parametrize(
    ("dataset_hash", "split_hash"),
    [
        ("A" * 64, SPLIT_SHA256),
        ("a" * 63, SPLIT_SHA256),
        ("g" * 64, SPLIT_SHA256),
        (DATASET_SHA256, "B" * 64),
        (DATASET_SHA256, "b" * 65),
    ],
)
def test_build_load_script_rejects_noncanonical_hashes(dataset_hash: str, split_hash: str) -> None:
    frame = synthetic_frame()

    with pytest.raises(ValueError, match="SHA-256|hash"):
        build_load_script(
            frame,
            build_assignments(frame),
            dataset_sha256=dataset_hash,
            split_sha256=split_hash,
        )


@pytest.mark.parametrize(
    "columns",
    [
        COLUMNS[:-1],
        (*COLUMNS, "unexpected"),
        (COLUMNS[1], COLUMNS[0], *COLUMNS[2:]),
    ],
    ids=["missing", "extra", "reordered"],
)
def test_build_load_script_requires_exact_canonical_columns(columns: tuple[str, ...]) -> None:
    frame = synthetic_frame().reindex(columns=columns)

    with pytest.raises(ValueError, match="columns"):
        build_load_script(
            frame,
            build_assignments(synthetic_frame()),
            dataset_sha256=DATASET_SHA256,
            split_sha256=SPLIT_SHA256,
        )


@pytest.mark.parametrize("unsafe_text", ["admin.\nSELECT 1;", "admin.\rbroken", r"admin.\\copy"])
def test_build_load_script_rejects_copy_control_characters(unsafe_text: str) -> None:
    frame = synthetic_frame()
    frame.loc[0, "job"] = unsafe_text

    with pytest.raises(ValueError, match="newline|carriage|backslash|COPY"):
        build_load_script(
            frame,
            build_assignments(frame),
            dataset_sha256=DATASET_SHA256,
            split_sha256=SPLIT_SHA256,
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda assignments: assignments.assign(split="train"),
        lambda assignments: assignments.iloc[::-1].reset_index(drop=True),
        lambda assignments: assignments.drop(columns="duplicate_of"),
    ],
    ids=["changed-values", "changed-order", "missing-column"],
)
def test_build_load_script_rejects_assignments_not_derived_from_frame(
    mutate: Callable[[pd.DataFrame], pd.DataFrame],
) -> None:
    frame = synthetic_frame()
    assignments = mutate(build_assignments(frame))

    with pytest.raises(ValueError, match="assignment|split"):
        build_load_script(
            frame,
            assignments,
            dataset_sha256=DATASET_SHA256,
            split_sha256=SPLIT_SHA256,
        )
