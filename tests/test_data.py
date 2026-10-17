from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from bank_campaign.data.download import extract_dataset
from bank_campaign.data.schema import (
    CATEGORICAL_VALUES,
    COLUMNS,
    FLOAT_COLUMNS,
    INTEGER_COLUMNS,
)
from bank_campaign.data.validate import validate_dataset

EXPECTED_COLUMNS = (
    "age",
    "job",
    "marital",
    "education",
    "default",
    "housing",
    "loan",
    "contact",
    "month",
    "day_of_week",
    "duration",
    "campaign",
    "pdays",
    "previous",
    "poutcome",
    "emp.var.rate",
    "cons.price.idx",
    "cons.conf.idx",
    "euribor3m",
    "nr.employed",
    "y",
)

VALID_ROW = {
    "age": "30",
    "job": "admin.",
    "marital": "married",
    "education": "university.degree",
    "default": "no",
    "housing": "yes",
    "loan": "no",
    "contact": "cellular",
    "month": "may",
    "day_of_week": "mon",
    "duration": "100",
    "campaign": "1",
    "pdays": "999",
    "previous": "0",
    "poutcome": "nonexistent",
    "emp.var.rate": "1.1",
    "cons.price.idx": "93.994",
    "cons.conf.idx": "-36.4",
    "euribor3m": "4.857",
    "nr.employed": "5191",
    "y": "no",
}

EXPECTED_CATEGORIES = {
    "job": {
        "admin.",
        "blue-collar",
        "entrepreneur",
        "housemaid",
        "management",
        "retired",
        "self-employed",
        "services",
        "student",
        "technician",
        "unemployed",
        "unknown",
    },
    "marital": {"divorced", "married", "single", "unknown"},
    "education": {
        "basic.4y",
        "basic.6y",
        "basic.9y",
        "high.school",
        "illiterate",
        "professional.course",
        "university.degree",
        "unknown",
    },
    "default": {"no", "yes", "unknown"},
    "housing": {"no", "yes", "unknown"},
    "loan": {"no", "yes", "unknown"},
    "contact": {"cellular", "telephone"},
    "month": {
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
    },
    "day_of_week": {"mon", "tue", "wed", "thu", "fri"},
    "poutcome": {"failure", "nonexistent", "success"},
    "y": {"no", "yes"},
}


def _write_csv(
    path: Path,
    *,
    header: tuple[str, ...] | list[str] = EXPECTED_COLUMNS,
    rows: list[dict[str, str]] | None = None,
) -> Path:
    selected_rows = [VALID_ROW] if rows is None else rows
    with path.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file, delimiter=";", quoting=csv.QUOTE_ALL)
        writer.writerow(header)
        for row in selected_rows:
            writer.writerow([row.get(column, "") for column in header])
    return path


def _row_with(**changes: str) -> dict[str, str]:
    return {**VALID_ROW, **changes}


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
        for name, contents in files.items():
            zip_file.writestr(name, contents)
    return archive.getvalue()


def _official_archive(*, include_csv: bool = True, include_names: bool = True) -> bytes:
    files: dict[str, bytes] = {}
    if include_csv:
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_ALL)
        writer.writerow(EXPECTED_COLUMNS)
        writer.writerow([VALID_ROW[column] for column in EXPECTED_COLUMNS])
        files["bank-additional/bank-additional-full.csv"] = buffer.getvalue().encode()
    if include_names:
        files["bank-additional/bank-additional-names.txt"] = b"Bank marketing data dictionary\n"
    return _zip_bytes(files)


def test_schema_matches_official_bank_additional_dataset() -> None:
    assert COLUMNS == EXPECTED_COLUMNS
    assert INTEGER_COLUMNS == ("age", "duration", "campaign", "pdays", "previous")
    assert FLOAT_COLUMNS == (
        "emp.var.rate",
        "cons.price.idx",
        "cons.conf.idx",
        "euribor3m",
        "nr.employed",
    )
    assert {name: set(values) for name, values in CATEGORICAL_VALUES.items()} == EXPECTED_CATEGORIES


def test_validate_dataset_returns_typed_dataframe(tmp_path: Path) -> None:
    dataset_path = _write_csv(
        tmp_path / "bank-additional-full.csv",
        rows=[VALID_ROW, _row_with(age="44", y="yes")],
    )

    frame = validate_dataset(dataset_path, expected_rows=2)

    assert isinstance(frame, pd.DataFrame)
    assert tuple(frame.columns) == EXPECTED_COLUMNS
    assert frame.shape == (2, 21)
    assert all(pd.api.types.is_integer_dtype(frame[column]) for column in INTEGER_COLUMNS)
    assert all(pd.api.types.is_float_dtype(frame[column]) for column in FLOAT_COLUMNS)
    assert frame.loc[1, "age"] == 44
    assert frame.loc[1, "y"] == "yes"


@pytest.mark.parametrize(
    "header",
    [
        EXPECTED_COLUMNS[:-1],
        (*EXPECTED_COLUMNS, "unexpected"),
        (EXPECTED_COLUMNS[1], EXPECTED_COLUMNS[0], *EXPECTED_COLUMNS[2:]),
        (EXPECTED_COLUMNS[0], EXPECTED_COLUMNS[0], *EXPECTED_COLUMNS[2:]),
    ],
    ids=["missing", "extra", "reordered", "duplicate"],
)
def test_validate_dataset_rejects_noncanonical_header(
    tmp_path: Path, header: tuple[str, ...]
) -> None:
    dataset_path = _write_csv(tmp_path / "invalid-header.csv", header=header)

    with pytest.raises(ValueError):
        validate_dataset(dataset_path)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("age", "not-a-number"),
        ("age", "30.5"),
        ("age", "0"),
        ("age", "121"),
        ("duration", "-1"),
        ("campaign", "0"),
        ("pdays", "-1"),
        ("previous", "-1"),
        ("emp.var.rate", "inf"),
        ("cons.conf.idx", "-inf"),
        ("cons.price.idx", "0"),
        ("euribor3m", "0"),
        ("nr.employed", "0"),
    ],
)
def test_validate_dataset_rejects_invalid_numeric_values(
    tmp_path: Path, column: str, value: str
) -> None:
    dataset_path = _write_csv(
        tmp_path / f"invalid-{column}.csv", rows=[_row_with(**{column: value})]
    )

    with pytest.raises(ValueError):
        validate_dataset(dataset_path)


@pytest.mark.parametrize(
    ("column", "value"),
    [("job", "astronaut"), ("y", "maybe")],
)
def test_validate_dataset_rejects_unknown_categories(
    tmp_path: Path, column: str, value: str
) -> None:
    dataset_path = _write_csv(
        tmp_path / f"invalid-{column}.csv", rows=[_row_with(**{column: value})]
    )

    with pytest.raises(ValueError):
        validate_dataset(dataset_path)


@pytest.mark.parametrize("column", ["age", "job"])
def test_validate_dataset_rejects_empty_cells(tmp_path: Path, column: str) -> None:
    dataset_path = _write_csv(tmp_path / f"empty-{column}.csv", rows=[_row_with(**{column: ""})])

    with pytest.raises(ValueError):
        validate_dataset(dataset_path)


def test_validate_dataset_rejects_header_only_file(tmp_path: Path) -> None:
    dataset_path = _write_csv(tmp_path / "empty.csv", rows=[])

    with pytest.raises(ValueError):
        validate_dataset(dataset_path)


def test_validate_dataset_rejects_wrong_expected_row_count(tmp_path: Path) -> None:
    dataset_path = _write_csv(tmp_path / "one-row.csv")

    with pytest.raises(ValueError):
        validate_dataset(dataset_path, expected_rows=2)


def test_extract_dataset_reads_official_archive() -> None:
    csv_bytes, dictionary_bytes = extract_dataset(_official_archive())

    assert b"age" in csv_bytes
    assert b"admin." in csv_bytes
    assert dictionary_bytes == b"Bank marketing data dictionary\n"


def test_extract_dataset_reads_nested_official_archive_without_extracting_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    escaped_path = tmp_path.parent / "extract-dataset-must-not-write-this-file"
    assert not escaped_path.exists()
    outer_archive = _zip_bytes(
        {
            "bank-additional.zip": _official_archive(),
            "../../extract-dataset-must-not-write-this-file": b"unsafe",
        }
    )
    monkeypatch.chdir(tmp_path)

    csv_bytes, dictionary_bytes = extract_dataset(outer_archive)

    assert b"admin." in csv_bytes
    assert dictionary_bytes.startswith(b"Bank marketing")
    assert not escaped_path.exists()
    assert list(tmp_path.iterdir()) == []


def test_extract_dataset_rejects_invalid_zip() -> None:
    with pytest.raises(ValueError):
        extract_dataset(b"this is not a zip archive")


@pytest.mark.parametrize(
    "archive",
    [
        _official_archive(include_csv=False),
        _official_archive(include_names=False),
        _zip_bytes(
            {
                "bank-additional.zip": _official_archive(include_names=False),
            }
        ),
    ],
    ids=["missing-csv", "missing-dictionary", "nested-missing-dictionary"],
)
def test_extract_dataset_rejects_archives_with_missing_members(archive: bytes) -> None:
    with pytest.raises(ValueError):
        extract_dataset(archive)


def test_local_manifest_detects_tampering(tmp_path: Path) -> None:
    import json
    from hashlib import sha256

    from bank_campaign.data.download import (
        CSV_NAME,
        DICTIONARY_NAME,
        SOURCE_URL,
        verify_manifest,
    )

    files = {CSV_NAME: b"original CSV", DICTIONARY_NAME: b"original dictionary"}
    manifest = {
        "source_url": SOURCE_URL,
        "files": {name: {"sha256": sha256(content).hexdigest()} for name, content in files.items()},
    }
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    assert verify_manifest(tmp_path) == manifest
    (tmp_path / CSV_NAME).write_bytes(b"changed CSV")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        verify_manifest(tmp_path)


def test_validator_does_not_drop_a_short_row(tmp_path: Path) -> None:
    path = _write_csv(tmp_path / "short-row.csv")
    with path.open("a") as handle:
        handle.write("30;admin.\n")
    with pytest.raises(ValueError, match="fields"):
        validate_dataset(path)


def test_missing_manifest_fails_clearly(tmp_path: Path) -> None:
    from bank_campaign.data.download import verify_manifest

    with pytest.raises(ValueError, match="manifest"):
        verify_manifest(tmp_path)
