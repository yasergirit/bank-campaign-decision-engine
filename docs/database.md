# PostgreSQL and SQL feature pipeline — Stage 5

## Run from the PyCharm terminal

Docker Desktop must be running. The current workstation already has a private
`.env` and a loaded local database.

```bash
docker compose up -d --wait db
python -m bank_campaign.data.database load
python -m bank_campaign.data.database verify
```

`load` creates the schema/views and loads the verified source and frozen split
assignments atomically. Running it again validates the same records without
adding rows or updating the original load timestamp. `verify` is read-only with
respect to PostgreSQL and compares all persisted source fields, feature values,
row IDs, labels/split metadata and provenance against local verified inputs.
Neither command trains a model or calculates model performance.

On a fresh checkout, copy `.env.example` to `.env` and choose a local password
before starting Compose. Obtain the official source first with the existing
`python -m bank_campaign.data.download` command. `.env` is Git ignored.
The official PostgreSQL 18 image is pinned by digest in `compose.yaml`; named
volume storage survives container replacement and ordinary restarts.

## Database objects

| Object | Role |
|---|---|
| `campaign.dataset_loads` | Source/split fingerprints, expected row count and original loaded_at |
| `campaign.raw_records` | All 21 original fields plus stable source row_id and source fingerprint |
| `campaign.split_assignments` | Frozen train/validation/test assignments and duplicate exclusions |
| `campaign.model_features` | All row IDs and exactly the nine approved model fields |
| `campaign.training_metadata` | Separate target and split/exclusion metadata joined by row_id |

The raw table retains duration and y for audit. The model feature view omits
them. Excluded duplicate records remain in the raw table; use training_metadata
when selecting included training records. row_id is a source record position,
not a verified unique customer ID. Unknown categories and pdays=999 retain their
source meaning. No new modeling features are invented.

## Inspect SQL interactively

```bash
docker compose exec db sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Then try:

```sql
SELECT * FROM campaign.model_features ORDER BY row_id LIMIT 5;

SELECT split, count(*) AS assigned,
       count(*) FILTER (WHERE included) AS retained
FROM campaign.split_assignments
GROUP BY split
ORDER BY split;

SELECT f.*
FROM campaign.model_features AS f
JOIN campaign.training_metadata AS m USING (row_id)
WHERE m.split = 'train' AND m.included
ORDER BY f.row_id
LIMIT 5;
```

Type `\q` to exit psql.

Connection settings for a GUI database client: host `127.0.0.1`, default port
`55432`, database/user `bank_campaign`; use the local password from `.env`.
Custom POSTGRES_PORT/USER/DB settings in .env take precedence over these defaults.
The Python loader uses the psql client bundled inside the container; no host
PostgreSQL client or additional Python driver is required for this local stage.

## Transaction and verification behavior

1. Verify source acquisition hashes and the frozen source/split hashes.
2. Acquire a transaction-scoped advisory lock, apply the schema, and create temp staging tables.
3. COPY raw rows and split rows through psql standard input.
4. Insert provenance/raw/split records with conflict handling.
5. Compare complete staged and persistent records in both directions with EXCEPT ALL.
6. Create the feature/metadata views and COMMIT; any error rolls everything back.
7. Compare every raw source value exactly, verify provenance, and compare the
   nine SQL features and label/split metadata with Python.

The loader refuses to mix source or split versions. Existing mismatched records
cause a failure rather than silently repairing data. This stage manages one
frozen source snapshot per local application database; multi-dataset version
management would require a separate design.

The machine-readable result is `reports/database_verification.json`.

## Tests

```bash
uv run --locked --inexact pytest -q
BANK_CAMPAIGN_DB_TESTS=1 uv run --locked --inexact pytest -q
```

The ordinary suite skips PostgreSQL integration unless explicitly enabled.
Integration tests create UUID-named databases inside this project's local
container and remove only those databases. They verify repeat loads, full
feature/source parity, SQL constraint rollback, source-version rejection, and
corruption of fields excluded from modeling.

The frozen ML source files and lockfile are preserved; the PostgreSQL layer adds
new files and uses the existing Python runtime. Stage 4 results remain historical.

To stop the local database while retaining its data:

```bash
docker compose stop db
```

References:
- https://www.postgresql.org/docs/18/app-psql.html
- https://www.postgresql.org/docs/18/sql-copy.html
- https://docs.docker.com/compose/how-tos/startup-order/
