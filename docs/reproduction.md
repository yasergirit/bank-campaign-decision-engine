# Reproduction guide

This project separates three different goals that are often conflated:

1. verifying source behavior without private or large artifacts,
2. running the exact published API model,
3. reconstructing the historical training experiment.

They have different inputs and guarantees. The commands below never make the
released holdout eligible for new model selection or tuning.

## 1. Clean-clone verification

Prerequisites are Python 3.12, `uv`, Docker, and Make.

```bash
git clone https://github.com/yasergirit/bank-campaign-decision-engine.git
cd bank-campaign-decision-engine
make install
make check
make test
docker build --tag bank-campaign-decision-engine:check .
```

These checks need neither the UCI data nor the trained model. Tests use synthetic
fixtures and estimators. The image build intentionally excludes raw data, model
artifacts, `.env`, and local output directories.

For the isolated PostgreSQL path:

```bash
cp .env.example .env
docker compose up -d --wait db
make db-test
docker compose down
```

The integration suite creates isolated test databases. It does not require or
modify a production database.

## 2. Run the exact published model

The serving contract requires this exact artifact:

```text
Release: model-v1
Asset: selected_model.pkl
Destination: artifacts/comparison-v1/selected_model.pkl
SHA-256: 5db3a7f63ce1ae99f5042f5c81c44c3cd9c314fc2c0471d120431e46c9f71354
```

Retrieve and verify it with:

```bash
make model
make serve
make serving-verify
```

`make model` downloads only the trusted asset from this repository's GitHub
release, verifies its SHA-256 before moving it into place, and refuses a mismatch.
The API checks the hash again before unpickling, along with the frozen feature
order, selected model name, Python version, and library versions. Never unpickle
an artifact from an untrusted source.

The Streamlit workspace is available at <http://127.0.0.1:18502>; Swagger is at
<http://127.0.0.1:18000/docs>. The smoke check submits only three synthetic,
unlabelled records and verifies deterministic scoring, policy edge cases, and CSV
ordering. It does not train, tune, or score the historical holdout.

This exact-pickle route is the supported way to reproduce the published API.

## 3. Optional data and SQL reconstruction

Download and validate the attributed UCI source only when inspecting the data or
rebuilding the offline feature store:

```bash
uv run --locked --inexact python -m bank_campaign.data.download
uv run --locked --inexact python -m bank_campaign.data.validate
uv run --locked --inexact python -m bank_campaign.data.audit
uv run --locked --inexact python -m bank_campaign.data.split
uv run --locked --inexact python -m bank_campaign.data.database load
uv run --locked --inexact python -m bank_campaign.data.database verify
```

Acquisition verifies the recorded source digest. The fixed source-order split is
60/20/20; later identical predictor records are excluded without consulting the
label. Database verification checks raw payload, nine-feature, label, and split
parity for all 41,188 rows.

## 4. Optional behavioral restoration

If the release asset is unavailable, the historical candidate can be fitted again
from the frozen training rows:

```bash
uv run --locked --inexact python -m bank_campaign.holdout restore
```

Restoration fits only the already selected `lightgbm_small` configuration and
checks its validation probabilities against the frozen predictions to 12 decimal
places. It does not compare candidates, choose a model, refit on validation/test,
or compute held-out metrics. A mismatch fails closed.

This is a **behavioral restoration**, not a promise of byte-identical pickle
serialization across operating systems or dependency builds. The API requires the
published artifact's exact SHA-256; a behaviorally equivalent restored pickle may
therefore be unsuitable for exact serving.

Do not overwrite the published release with a restored artifact. Use a separate
checkout for restoration: it replaces the local model output. To return to the
exact serving bundle, first move the restored file to a separate local backup;
`make model` intentionally refuses to overwrite a different existing file.

## Frozen evaluation boundary

The holdout was scored once after the model and selection policy were frozen.
Its published results are read-only evidence:

- ROC-AUC 0.525047, Average Precision 0.341664, Brier 0.278537.
- At capacity 500: ML 231 historical subscriptions, random mean 154.065,
  fixed prior-history rule 372.
- Model-expected subscriptions 39.67 versus 231 observed, showing substantial
  underprediction in this period.

Do not use these numbers to select new features, tune parameters, recalibrate the
model, or claim causal campaign profit. A new experiment needs a new evaluation
design and untouched evaluation data.

## Evidence map

| Question | Source |
|---|---|
| What was frozen before evaluation? | `reports/selection_freeze.json` |
| What exactly was released? | `reports/test_release.json` |
| What were the held-out results? | `reports/test_metrics.json` |
| How should the model be interpreted? | `docs/model_card.md` |
| How is local serving isolated? | `docs/delivery.md` |
| How was each project stage designed? | `reports/proje_raporu.md` |

Historical stage reports and pre-publication verification snapshots remain useful
audit evidence. This README and this guide define the current public workflow.
