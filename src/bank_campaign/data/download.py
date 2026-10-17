"""Download the official archive and retain only the selected dataset and dictionary."""

import argparse
import json
from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile

from bank_campaign.data.validate import validate_dataset

SOURCE_URL = "https://archive.ics.uci.edu/static/public/222/bank+marketing.zip"
SOURCE_PAGE = "https://archive.ics.uci.edu/dataset/222/bank+marketing"
CSV_NAME = "bank-additional-full.csv"
DICTIONARY_NAME = "bank-additional-names.txt"


def extract_dataset(archive_bytes: bytes) -> tuple[bytes, bytes]:
    """Select exact archive members in memory; never extract arbitrary paths."""
    try:
        with ZipFile(BytesIO(archive_bytes)) as outer:
            if "bank-additional.zip" in outer.namelist():
                payload = outer.read("bank-additional.zip")
            else:
                payload = archive_bytes
        with ZipFile(BytesIO(payload)) as archive:
            return (
                archive.read(f"bank-additional/{CSV_NAME}"),
                archive.read(f"bank-additional/{DICTIONARY_NAME}"),
            )
    except (BadZipFile, KeyError, OSError) as exc:
        raise ValueError("Archive is invalid or lacks the full CSV/data dictionary.") from exc


def verify_manifest(directory: Path) -> dict:
    """Verify local bytes against the first-download manifest, not a publisher signature."""
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        for name in (CSV_NAME, DICTIONARY_NAME):
            actual = sha256((directory / name).read_bytes()).hexdigest()
            if actual != manifest["files"][name]["sha256"]:
                raise ValueError(f"Checksum mismatch for {name}; local data changed.")
        if manifest["source_url"] != SOURCE_URL:
            raise ValueError("Manifest source URL differs from the configured source.")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot verify local data manifest: {exc}") from exc
    return manifest


def download_dataset(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / name for name in (CSV_NAME, DICTIONARY_NAME, "manifest.json")]
    if any(path.exists() for path in paths):
        manifest = verify_manifest(directory)
        validate_dataset(directory / CSV_NAME, expected_rows=41188)
        return manifest
    with urlopen(SOURCE_URL, timeout=60) as response:
        archive_bytes = response.read()
    csv_bytes, dictionary_bytes = extract_dataset(archive_bytes)
    temporary = directory / f"{CSV_NAME}.part"
    try:
        temporary.write_bytes(csv_bytes)
        frame = validate_dataset(temporary, expected_rows=41188)
    finally:
        temporary.unlink(missing_ok=True)
    manifest = {
        "source_url": SOURCE_URL,
        "source_page": SOURCE_PAGE,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "archive_sha256": sha256(archive_bytes).hexdigest(),
        "rows": len(frame),
        "columns": list(frame.columns),
        "license": "CC BY 4.0 (UCI dataset page)",
        "files": {
            name: {"sha256": sha256(content).hexdigest(), "bytes": len(content)}
            for name, content in ((CSV_NAME, csv_bytes), (DICTIONARY_NAME, dictionary_bytes))
        },
    }
    (directory / CSV_NAME).write_bytes(csv_bytes)
    (directory / DICTIONARY_NAME).write_bytes(dictionary_bytes)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/raw"))
    args = parser.parse_args()
    try:
        manifest = download_dataset(args.output_dir)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Download failed: {exc}\n")
    print(f"Data ready: {manifest['rows']:,} records in {args.output_dir}")
    print(f"CSV SHA-256: {manifest['files'][CSV_NAME]['sha256']}")


if __name__ == "__main__":
    main()
