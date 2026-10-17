# Initial data and decision contract

Status: data validation, frozen splits, model comparison, campaign policies,
PostgreSQL parity, strict raw-feature FastAPI scoring and Streamlit UI are
implemented. Held-out results were released after the selection freeze; they
must not be used for further tuning. The planned-contract wording below records
the original design; implemented API bounds are in docs/api.md.

The initial feature allowlist is defined once in
`src/bank_campaign/data/schema.py` as `BASELINE_FEATURES`:
`age`, `job`, `marital`, `education`, `default`, `housing`, `loan`, `previous`,
and `poutcome`. These assume pre-selection CRM/history snapshots. Source data
has no snapshot timestamps to independently verify that assumption.
All 21 source-column decisions and reasons appear in `reports/data_audit.md`.
Time/context fields remain excluded until their decision-time availability can
be justified. `pdays=999` means no prior contact; `unknown` values are preserved.

Raw validation requires the exact ordered 21-column schema, 41,188 records for
the official file, valid categories, finite numbers, and no blank cells. Range
checks are dataset-specific project plausibility rules, not general banking rules.

## Prediction task

- Unit: an input record; a record is not a verified unique customer.
- Decision time: before a planned campaign call.
- Target in historical training data: `y`, with `yes` mapped to 1 and `no` to 0.
- Output meaning: estimated probability of term-deposit subscription under the
  historical data-generating process, not incremental campaign impact.
- Planned dataset: `bank-additional-full.csv` from UCI Bank Marketing.

## Input boundaries

- Historical ingestion may contain `y` and `duration`; they are never predictors.
- Scoring inputs must reject both `y` and `duration`.
- `duration` is unavailable before a call, and is excluded from every model.
- `row_id` is a local technical record key, not a real customer identifier or feature.
- The data audit must establish which other columns are available at decision time.
  Features with uncertain availability are excluded from the initial allowlist.
- Unknown categories and sentinel values retain their documented meaning until
  their handling is explicitly defined in the audit.

## Planned scoring output

Each input record produces `row_id`, `subscription_probability` in [0, 1],
and `model_version`. Required feature names and types will be frozen after the
audit and shared by training and serving.

## Selection policy

Parameters: maximum capacity `K` (nonnegative integer), cost per selected record
`C` and value per subscription `V` (both finite, nonnegative numbers in the same
user-defined monetary unit).

Two explicit modes are planned:

1. Capacity-only: select the highest-probability records, up to `min(K, N)`.
2. Positive expected-value scenario: retain records satisfying `p * V - C > 0`,
   then select at most `K` in descending probability order.

Break probability ties by `row_id`. Empty input or `K = 0` yields an empty
selection. A scenario may select fewer than `K` records. Reject invalid or
non-finite probabilities and parameters; do not silently coerce them.

Scenario totals: expected subscriptions `sum(p)` and hypothetical net value
`sum(p * V - C)` over selected records. These are assumption-based estimates.
Observed conversions, precision, and lift are available only in labeled offline
evaluation; an unlabeled candidate upload cannot supply those measurements.

## Evaluation boundaries

- Verify source ordering before adopting a 60/20/20 contiguous record split.
- Source order is not a substitute for exact timestamps or customer-level splits.
- Fit preprocessing only on training data; select models/policies on validation.
- Freeze decisions before evaluating on held-out test data.
- Report Average Precision, ROC-AUC, Brier score, precision@k and lift@k with
  explicit definitions. Mark undefined metrics instead of fabricating zeroes.
- The source-order 60/20/20 policy is frozen in `reports/split_manifest.json`.
  Stable row_id is the zero-based original source position, not the DataFrame index.
- Duplicate detection compares all 20 source predictors, excluding y, and retains
  the earliest record. This is offline duplicate hygiene, not a model feature or
  proof of customer identity. Post-call fields are never passed to the estimator.
- Later identical predictor records remain accounted for as excluded assignments;
  no retained predictor-identical records span partitions.
- Source schema and checksums are in the data audit. Stage 3 reports validation
  only; test labels are not passed to a model or metric calculation.
- Average Precision is the implemented PR metric (non-interpolated AP). ROC-AUC
  and AP are reported as undefined/null for single-class metric inputs; Brier
  remains defined. Training/validation require both classes for this experiment.


## Stage 4 decision contract

The model choice uses validation AP, then Brier, then name; it is not a claim of
optimal precision at K=500. The recorded scenario is K=500, C=3, V=100 with both
capacity and positive_value modes evaluated only after freezing. Runtime policy
validation and observed campaign metrics are in policy.py. Unlabeled demo/CSV
selection reports model expectations only. Probability calibration has not been
fitted; the observed diagnostics limit financial interpretation. See
`docs/decision_engine.md` for test-release, cache and reconstruction boundaries.


## PostgreSQL contract — Stage 5

The database stores the single frozen source snapshot and assignments.
`campaign.model_features` exposes exactly row_id followed by BASELINE_FEATURES;
labels, source hash, split metadata, and post-call fields are absent.
`campaign.training_metadata` separately exposes target/split/included/duplicate_of.
All source rows are retained; training consumers must join metadata and filter
`split='train' AND included`. Full raw payload and all nine feature values are
compared with the validated Python source. Different source/split fingerprints or
existing mismatched content are refused.
