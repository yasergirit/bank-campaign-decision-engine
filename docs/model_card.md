# Model cards: current frozen release and historical baseline

## Current serving release: comparison-v1 / lightgbm_small

The API serves the trusted exact-hash frozen LightGBM small pipeline: nine
pre-call features, selected on validation Average Precision. The held-out test
has already been released and must not be reused for tuning. AP=0.341664,
ROC-AUC=0.525047, Brier=0.278537. At K=500 the model selects 231 historical
subscriptions, versus random mean 154.065 and fixed history rule 372.
Probabilities are uncalibrated and underestimate outcomes; model-expected
subscriptions in that list total only 39.6730. No achieved profit or causal
contact effect is established. This is an offline educational prototype.

Serving requires the recorded Python major/minor and exact pandas/scikit-learn/
LightGBM package versions. The artifact is kept outside Git and container image
layers. Only trusted bundles may be unpickled. See docs/api.md, docs/delivery.md
and reports/test_metrics.json.

## Historical Stage 3 model card: baseline-v1

The following describes the earlier baseline at its original stage. Its phrases
about reserved test and pending alternatives are historical, not current status.

## Intended use and current status

An offline educational baseline estimating historical term-deposit subscription
probability from nine pre-call client/history fields. Not a final selected model.
Only validation results are available: see `../reports/baseline_report.md`.
No production deployment, test performance, causal uplift, or actual ROI is claimed.

## Training

Source: UCI Bank Marketing, `bank-additional-full.csv`, verified acquisition hash.
Fixed source-position 60/20/20 partitions; retain earliest identical predictor
record, excluding 12 later repeated records without consulting y. Row IDs are
technical positions, not customer identities. Source ordering is documented but
precise timestamps and customer-disjointness cannot be independently established.

Training: 24,705 rows. Validation: 8,235 rows. Reserved test: 8,236 rows.
Preprocessing fits only on training: StandardScaler for age/previous and
OneHotEncoder(handle_unknown="ignore") for the seven categorical features.
The model is unweighted logistic regression with fixed C=1, lbfgs and max_iter=1000.
A DummyClassifier(strategy="prior") is the reference probability estimator.
No tuning, resampling or calibration is performed in Stage 3.

## Interpretation and limitations

- Training positive prevalence is approximately 4.81%; validation is 11.07%.
  This observed period difference affects probabilities and baseline comparisons.
- Validation discrimination is modest; an improvement over dummy does not make
  this model ready for campaign decisions. Brier improvement is small.
- `duration`, recorded call timing, campaign count and macroeconomic indicators
  are excluded from the estimator. Duplicate hygiene uses all source predictors
  solely to detect exact repeated records; it is not feature engineering.
- Included CRM and prior-history attributes assume pre-selection availability,
  which the source lacks snapshot timestamps to prove.
- Repeated nine-feature profiles can represent different people; they are not
  automatically deduplicated as customers.
- Historical Portuguese data is not proof of performance for a current bank.
- Per-subgroup robustness, capacity policies, calibration checks and alternative
  models have not yet been evaluated and belong in later development.

## Reproducibility

Run `python -m bank_campaign.train` from the repository root after syncing uv.
Metadata records input/split hashes, source-code hashes, lockfile hash, versions,
and model parameters. Local model and validation predictions are under
`artifacts/baseline-v1/`. Only load model artifacts created by this trusted local
workflow. Repeating baseline-v1 replaces its outputs; preserve separate directories
for future experiment variants. Test evaluation is deferred until model/policy freeze.

## Method references

- https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html
- https://scikit-learn.org/stable/modules/generated/sklearn.dummy.DummyClassifier.html
- https://scikit-learn.org/stable/modules/generated/sklearn.preprocessing.OneHotEncoder.html


## Stage 4 follow-up (separate frozen experiment)

Baseline-v1 remains the historical Stage 3 run. Stage 4 compares three LightGBM
candidates on validation and selects lightgbm_small by AP; it is not the winner
of validation precision@500. The frozen held-out report is in
`reports/test_report.md`. Test ROC-AUC is 0.5250 and the fixed prior-history
rule outperforms ML at K=500. No production readiness or calibrated probabilities
are established. The test is now exposed; further model optimization requires
a new evaluation design.
