# Prediction and campaign API — Stage 6

The service loads the existing frozen LightGBM pipeline once during application
startup. It does not train, evaluate the holdout, read targets, or need PostgreSQL
to score supplied records. SQL's `campaign.model_features` view and API requests
share the same nine-feature contract. `row_id` is a caller-supplied technical key,
not proof of a unique customer.

## Install and run in PyCharm

Open the project root and select `.venv/bin/python` as interpreter. In the
PyCharm terminal, from `<project-root>`:

```bash
uv sync --locked --inexact
uv pip install --python .venv/bin/python --require-hashes -r requirements-api.lock
.venv/bin/python -m uvicorn bank_campaign.api:app --host 127.0.0.1 --port 8000
```

The API component has its own hash-locked requirements so the recorded model's
`uv.lock` stays unchanged. Use `--inexact` when syncing/running to retain these
optional packages. HTTPX is included for API tests. Do not regenerate the model
or change its ML package versions to start the API.

Alternatively, add a **Python** Run Configuration in PyCharm:

- Run kind: **Module name**, value `uvicorn`.
- Parameters: `bank_campaign.api:app --host 127.0.0.1 --port 8000`.
- Interpreter: project's `.venv/bin/python`.
- Working directory: `<project-root>`.

Open [Swagger UI](http://127.0.0.1:8000/docs) and use **Try it out**.
Stop the server with PyCharm's Stop button or Ctrl+C in the terminal.
If port 8000 is occupied, stop your previous server or choose `--port 8001`.

## Routes

| Method | Path | Result |
|---|---|---|
| GET | `/health` | 200 ready or 503 not_ready; whether the model loaded |
| GET | `/model-info` | Selected model, version, artifact digest, features and limitations |
| POST | `/score` | Subscription probability and model version per input record |
| POST | `/campaign/select` | Capacity-limited ranked records and expected-value summary |

Requests and response examples are inspectable in Swagger. Run the synthetic,
label-free samples from a second terminal:

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/model-info
curl -sS -X POST http://127.0.0.1:8000/score \
  -H 'Content-Type: application/json' --data-binary @examples/score_request.json
curl -sS -X POST http://127.0.0.1:8000/campaign/select \
  -H 'Content-Type: application/json' --data-binary @examples/campaign_request.json
```

## Input and decision contract

Each record requires `row_id`, `age`, `job`, `marital`, `education`, `default`,
`housing`, `loan`, `previous`, and `poutcome`. No additional fields are accepted:
`duration`, `y`, split metadata and other raw columns yield **422**. Required
fields cannot be null or omitted. Categories must exactly match the official
source vocabulary in `data/schema.py`; valid `unknown` values are preserved.
Official categories absent in training are handled by the fitted encoder.

IDs must be distinct within a request and integer in 0..2^53−1; `previous` uses
the same nonnegative integer range. Age is integer 1..120. Numeric strings and
booleans are rejected for numeric fields. Batches support 0..10,000 records.
Empty requests produce empty outputs with zero summary totals.

Campaign requests additionally take `capacity` (integer 0..10,000), `cost`
(default 3), `value` (default 100), and `mode` (`capacity` or `positive_value`).
Cost/value must be finite, nonnegative JSON numbers up to 10^12. The score route
preserves input order; selection sorts by probability descending then row_id
ascending. Capacity may exceed batch size. `positive_value` keeps only records
with strictly positive `p * value - cost`, then takes at most capacity records.
`capacity` may select negative expected-value records; choose the other mode
when positive expected value is a requirement.

The summary returns `selected_count`, `expected_subscriptions = sum(p)`,
`assumed_cost = count * cost`, and `assumed_net_value = sum(p * value - cost)`.
These are uncalibrated model/assumption estimates, not observed conversions,
causal contact effects or achieved profit. The recorded fixed history rule beat
this model on the holdout; model information exposes this limitation.

## Model readiness and provenance

Only the fixed trusted local artifact path is supported. Before unpickling, the
loader verifies artifact bytes strictly against the selection freeze,
comparison metadata digest, feature order, and recorded
Python major/minor, pandas, scikit-learn and LightGBM versions. After loading it
checks binary classes and fitted input features. It hashes and deserializes the
same bytes. The manifests and pickle must come from this trusted local project;
these checks do not authenticate files obtained from an untrusted source.

Missing/incompatible bundles leave the process running but `/health` reports
503 and inference/model-info routes return 503. Restore the matching trusted
bundle, then restart the process. No model upload route exists. The version is
`run_id:model_name:original_artifact_digest_prefix`; model-info also exposes the
actual artifact digest. Raw training data and the database may be unavailable
without affecting an already valid serving bundle.

## Verify

```bash
uv run --locked --inexact pytest -q
uv run --locked --inexact ruff check src tests
uv run --locked --inexact mypy src
```

`reports/api_verification.json` records live HTTP checks and SQL-feature scoring
parity on the first three source rows, all in the training partition. Existing
frozen model and evaluation artifacts remain unchanged.
