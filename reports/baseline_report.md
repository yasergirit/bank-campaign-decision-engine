# Stage 3 — fixed baseline validation

This report contains validation results only. The test partition has not been scored.

## Split policy

Original source positions are assigned 60/20/20 without shuffling.
Later identical source-predictor rows are excluded, retaining their first occurrence.
Duplicate detection uses all 20 predictors and does not inspect y.
Shared nine-feature profiles alone are not treated as duplicate people.

| Partition | Assigned | Retained | Excluded repeated records |
|---|---:|---:|---:|
| train | 24,712 | 24,705 | 7 |
| validation | 8,238 | 8,235 | 3 |
| test | 8,238 | 8,236 | 2 |

Source ordering is documented by UCI, but exact dates and customer IDs are absent.
Therefore this is not a proven customer-disjoint or precisely dated out-of-time evaluation.

## Fixed baseline specification

Features: age, job, marital, education, default, housing, loan, previous, poutcome.
Numeric age/previous are standardized; categorical fields are one-hot encoded.
Unknown remains an explicit observed category. Unseen validation categories are ignored by the encoder.
Scaling and encoding are fit only on the training partition, inside a Pipeline.
Logistic regression uses C=1, lbfgs, max_iter=1000, random_state=42 and no class weighting.
No hyperparameter search or probability calibration has been performed.
DummyClassifier(strategy=prior) predicts the training subscription prevalence for every record.

Training positive rate: 4.81%; validation positive rate: 11.07%.
These can differ because the split follows historical source order.

## Validation results

| Model | Average Precision ↑ | ROC-AUC ↑ | Brier score ↓ |
|---|---:|---:|---:|
| dummy_prior | 0.110747 | 0.500000 | 0.102408 |
| logistic_regression | 0.148423 | 0.572422 | 0.101965 |

Average Precision uses sklearn's non-interpolated AP definition, not trapezoidal PR-AUC.
A constant-score baseline has AP equal to validation prevalence and ROC-AUC 0.5.
Brier measures squared probability error; it is not a standalone calibration certificate.
Improvements on validation do not establish test performance, causal uplift, or actual profit.

## Reproduction and artifacts

```bash
uv sync --locked --inexact
python -m bank_campaign.data.split
python -m bank_campaign.train
```

Run from the repository root. Frozen assignments are checked before reuse; changes cause an error.
Baseline-v1 artifacts are replaced when repeating this fixed experiment; future variants need separate run IDs.
The trusted local pickle is stored in artifacts/baseline-v1/logistic_regression.pkl.
Metadata records package versions, model parameters, source hash, split hash and lockfile hash.
Validation predictions and model artifacts stay outside Git; aggregate reports are versionable.
No model promotion decision has been made. Capacity decisions and further comparison follow in Stage 4.
