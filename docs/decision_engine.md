# Campaign decision engine — design and interpretation

## Model comparison contract

The production-sized configuration in `configs/comparison.json` defines three
LightGBM candidates plus dummy prior and logistic regression. Config validation
allows 1–3 distinct lightgbm_ names to support small synthetic test runs. It
rejects baseline-name collisions, unsupported policy/run identifiers, invalid
capacities, nonpositive random repetitions, and unsupported model parameters.
No class weighting, oversampling, validation fitting or early stopping is used.
Every estimator learns the same nine-field preprocessing on training data only.

The declared selection objective is validation Average Precision, then lower
Brier score, then lexical model name. That objective summarizes ranking across
thresholds. It does not optimize a fixed call budget; validation precision@500
can favor another model. The comparison report retains every candidate result
rather than declaring a universal winner. Calibration diagnostic bins do not
change predicted probabilities. No calibrator is fitted in this experiment.

## Selection functions

`select_campaign` in `src/bank_campaign/policy.py` accepts scored records:
`row_id` (unique nonnegative integer value) and `subscription_probability` (finite
numeric value in [0,1]). It never uses a target label to choose records.

- `capacity`: take at most K records, descending probability then ascending row_id.
- `positive_value`: first require p*V-C > 0, then the same ranking and K limit.
- Reject negative/nonfinite costs or values, invalid capacity, invalid/duplicate
  IDs, and invalid probabilities. Empty input/K=0 returns an empty selection.
- Preserve input data. `expected_net_value` is computed under the explicit C/V.

`campaign_summary` reports model-expected subscriptions, assumed cost and assumed
net value. `campaign_metrics` needs historical labels and positional selected
indices; it reports observed subscription count, precision and lift. Empty
selection has undefined precision/lift; zero population positives has undefined
lift. Never display these observed metrics for unlabeled uploads.

## Benchmark interpretation

Random sampling repeats with a fixed seed. The 5th/95th selection percentiles
measure variability between random lists, not uncertainty of future model skill.
The business rule ranks earlier campaign success, previous contact count, then
row_id. It is fixed before model comparison and does not inspect current labels.
The constant-score dummy's row_id tie-breaking is deterministic, not random;
use the repeated random benchmark to compare against random outreach.

## Freeze, restoration and held-out release

`selection_freeze.json` binds the chosen model, predeclared objective, features,
C/V/K scenario, policy modes, source data, split assignments, source/config/lock
hashes, model artifact and selected validation predictions. The model is not
refit on training+validation before held-out scoring.

The frozen model is initially a trusted local pickle. Clean-clone restoration
fits only that recorded candidate on training data, verifies the recorded
selected validation predictions (12-decimal representation), then records the
local pickle hash in a restoration ledger tied to the freeze hash. No test
labels, metrics, or model selection enter reconstruction.

Held-out evaluation uses a nonblocking POSIX file lock. A persistent attempt
marker precedes prediction, and frozen probabilities are atomically checkpointed
before metrics/reports. Recovery uses that checkpoint without loading/predicting
again. An interruption before a complete checkpoint is inspected rather than
silently rescored. Cached aggregate results rebuild a missing Markdown report.
Subsequent test results never drive feature, candidate, calibration or policy
changes in this run. New modeling work needs a new evaluation design.

## Limits

The source has no exact timestamps or verified customer identity. Historical
source ordering is not proof of customer-disjoint evaluation. Included CRM fields
assume pre-selection snapshot availability. The historical population shifts
strongly across source periods. Probabilities are uncalibrated and can materially
underestimate observed outcomes. This is a reproducible offline portfolio
prototype, not evidence of causal conversion gains or achieved financial return.

Reference: https://lightgbm.readthedocs.io/en/stable/Parameters.html
Reference: https://scikit-learn.org/stable/modules/calibration.html


## Released test findings and control-flow repair

The frozen model's held-out ROC-AUC is 0.5250. At K=500 it selects records with
231 historical subscriptions, versus a repeated random mean of 154.065 and
372 for the fixed prior-history rule. Model-expected subscriptions are only
39.67, so probability and monetary interpretation are especially limited.
The higher test AP than validation is not evidence of improved generalization: the
test prevalence is 30.83%, compared with 11.07% on validation and 4.81% on training.
The fixed rule outperforming ML is retained as a result, not hidden or used to
change the frozen model after test exposure.

An evaluation-control edge was repaired after the test release: an existing
release ledger now blocks scoring when both aggregate metrics and the local
prediction checkpoint are missing. No estimator, feature, probability, scenario,
metric definition, or test result changed. The original pre-test selection freeze
remains byte-identical. `reports/evaluation_runtime_revision.json` separately
binds the original/repaired holdout source hashes, original source snapshot,
freeze hash and completed release hash. Verification permits only that documented
control-module revision. The original source is retained in
`reports/runtime_snapshots/holdout-pre-release.py.txt`. This avoids presenting
post-release control code as if it had generated the original test results.
