# Stage 4 — validation model and campaign comparison

Selected model: **lightgbm_small**.
Selection uses validation Average Precision descending, then Brier ascending, then name.
Candidate configurations, capacity points and the history rule were declared before fitting in configs/comparison.json.
Models fit only training data and use the same nine approved fields. No test predictions were made in comparison.

## Validation probabilities

| Model | AP ↑ | ROC-AUC ↑ | Brier ↓ |
|---|---:|---:|---:|
| dummy_prior | 0.110747 | 0.500000 | 0.102408 |
| logistic_regression | 0.148423 | 0.572422 | 0.101965 |
| lightgbm_small | 0.156660 | 0.584898 | 0.101857 |
| lightgbm_medium | 0.143710 | 0.561174 | 0.101831 |
| lightgbm_long | 0.140416 | 0.554931 | 0.101813 |

## Selected-model scenario

Assumptions: {'capacity': 500, 'cost': 3.0, 'value': 100.0}.
These are user-defined units, not actual banking revenue/cost or causal uplift.

| Mode | Selected | Historical subscriptions | Precision | Lift | Expected subscriptions | Assumed net value |
|---|---:|---:|---:|---:|---:|---:|
| capacity | 500 | 89 | 0.1780 | 1.607 | 33.41 | 1841.02 |
| positive_value | 500 | 89 | 0.1780 | 1.607 | 33.41 | 1841.02 |

Capacity mode takes at most K by score; positive_value additionally requires p*V-C>0.
Ties break by numeric row_id. Selection itself never reads target labels.
Observed subscriptions are historical outcomes, not conversions caused by this selection.

## Random and fixed-history benchmarks

Random selection repeats 200 times per capacity with seed 42. The 5th/95th percentiles describe selection variability, not confidence bounds on model performance.
The fixed rule ranks prior-campaign success, then previous-contact count, then row_id. It is a transparent comparison, not a validated banking policy.

| Capacity | Random observed mean | Random p05 | Random p95 | Fixed-rule observed |
|---|---:|---:|---:|---:|
| 100 | 11.02 | 5.0 | 17.0 | 40 |
| 500 | 54.98 | 44.0 | 68.0 | 78 |
| 1000 | 110.52 | 96.9 | 125.0 | 116 |

## Probability and evaluation limits

See calibration_validation.csv for fixed probability-bin diagnostics. No calibrator was fitted in this bounded experiment.
Ranking selection optimizes validation AP, not precision at a specific K, financial return or probability calibration. Treat monetary estimates as exploratory scenarios.
The AP winner is not the precision@500 winner; see campaign_validation.csv. No top-500-optimal claim is made.
The constant-score dummy uses row_id to break ties and is not a randomized campaign baseline; use the repeated random benchmark for that comparison.
The historical training/validation prevalence shift remains; no fit uses validation or test labels.
Chronological source order is documented, but exact dates/customer-disjointness cannot be verified.
The final selection freeze must be written before a single held-out evaluation. That report is a separate final check; it must not drive retuning.
