# Stage 4 — frozen held-out evaluation

Frozen selected model: **lightgbm_small**. Test records: 8,236.
Test subscription prevalence: 30.83%.
The selection freeze existed before scoring this partition. This result does not change the selected model or policy.
The fitted model is the training-only candidate; no train+validation refit was performed.

| Estimator | Average Precision ↑ | ROC-AUC ↑ | Brier ↓ |
|---|---:|---:|---:|
| lightgbm_small | 0.341664 | 0.525047 | 0.278537 |
| training-prior reference | 0.308281 | 0.500000 | 0.280944 |

Frozen illustrative assumptions: {'capacity': 500, 'cost': 3.0, 'value': 100.0}.

| Mode | Selected | Historical subscriptions | Precision | Lift | Model-expected subscriptions | Assumed net value |
|---|---:|---:|---:|---:|---:|---:|
| capacity | 500 | 231 | 0.4620 | 1.499 | 39.67 | 2467.30 |
| positive_value | 500 | 231 | 0.4620 | 1.499 | 39.67 | 2467.30 |

Random selection: mean 154.06 historical subscriptions; 5th/95th selection percentiles 139.9/168.0 (200 repetitions).
Fixed prior-history rule: 372 historical subscriptions at the same capacity.

The random interval describes simulated selection variability, not statistical confidence for model generalization.
No causal uplift, achieved profit, customer-disjointness or current-bank applicability is established.
Probabilities were not calibrated. Compare expected counts with historical observed counts before interpreting monetary scenarios.
This test is now exposed. Further changes require a new experimental validation design, not repeated optimization on this holdout.
Re-running the evaluate command verifies the freeze and returns cached results without predicting again.
