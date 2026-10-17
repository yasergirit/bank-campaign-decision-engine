# Dataset acquisition and audit

Run from the project root in the PyCharm terminal:

```bash
uv sync --locked --inexact
python -m bank_campaign.data.download
python -m bank_campaign.data.validate
python -m bank_campaign.data.audit
```

The project interpreter must be `.venv/bin/python`. Alternatively prefix Python
commands with `uv run --locked --inexact`.

The downloader fetches the official UCI combined archive, opens the nested
`bank-additional.zip`, and selects only `bank-additional-full.csv` and its data
dictionary. It never mixes dataset variants or extracts arbitrary ZIP paths.

Local files (ignored by Git):

- `data/raw/bank-additional-full.csv`
- `data/raw/bank-additional-names.txt`
- `data/raw/manifest.json`: acquisition timestamp, hashes, schema, row count,
  source links, and license information.

Subsequent downloads verify existing files against the local manifest and reuse
them without a network request. A changed file or incomplete acquisition fails
rather than silently replacing the data. Inspect that failure before restoring
files. The checksum records the downloaded version; it is not a publisher signature.

Committed outputs are `reports/data_audit.md` and `reports/data_audit.json`.
The audit verifies all 41,188 rows, reports unknown categories and duplicates,
and documents every source column's availability decision. It does not train a
model or turn the source record order into invented timestamps.

Source: https://archive.ics.uci.edu/dataset/222/bank+marketing
Archive: https://archive.ics.uci.edu/static/public/222/bank+marketing.zip
License: CC BY 4.0 as declared on the UCI dataset page.
Citation: Moro, S., Rita, P., & Cortez, P. (2014). Bank Marketing [Dataset].
UCI Machine Learning Repository. https://doi.org/10.24432/C5K306.
The included dictionary requests attribution to the related 2014 paper:
https://doi.org/10.1016/j.dss.2014.03.001.

## Frozen partitions (Stage 3)

Run `python -m bank_campaign.data.split` from the repository root. Assignments
are local in `data/processed/split_assignments.csv`; their hash and policy are
versionable in `reports/split_manifest.json`. No random shuffling is performed.
Original boundaries remain fixed when later identical predictor rows are excluded.
Training retains 24,705 rows, validation 8,235 and test 8,236.
Test is reserved and is not evaluated during baseline development.
