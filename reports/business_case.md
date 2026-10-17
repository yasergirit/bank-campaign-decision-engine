# Business case - measured evidence and decision limits

## Problem

A fixed outreach capacity K requires a ranked candidate list. The prototype
estimates historical subscription probability from nine assumed pre-call fields
and offers capacity-only or positive expected-value selection.

## Actual offline evidence

The validation AP-selected frozen model is lightgbm_small. Its released holdout
ROC-AUC is 0.525047, AP 0.341664, and Brier 0.278537. At K=500 it selects 231
historical subscriptions, compared with a repeated random mean of 154.065 and
372 for the predeclared history rule. The model did not beat that simple rule.

Expected subscriptions for the selected 500 records are only 39.6730 while
observed historical subscriptions are 231. Uncalibrated probabilities materially
underestimate outcomes in this period; monetary estimates are exploratory.

## Scenario formulas

For selected records S: expected_subscriptions=sum(p), assumed_cost=|S|*C,
assumed_net_value=sum(p*V-C). C and V are user assumptions in the same units.
For the recorded K=500/C=3/V=100 scenario: assumed cost=1500 and model-based
expected net value=2467.3021. These are neither measured bank economics nor
incremental revenue caused by a campaign. Historical observed labels do not
identify the counterfactual outcome without contact.

## Practical recommendation within this evidence

Treat this release as an educational decision-system demonstration. Preserve the
rule comparison and weak discrimination/calibration findings in the portfolio.
Do not translate the model's historical rank advantage over random into a real
campaign profit claim. A future operational decision requires fresh evaluation,
verified pre-call snapshots, meaningful costs/values, appropriate controls and
monitoring. New model choices must use a new evaluation design because the old
holdout has already been examined.

Source of every reported number: reports/test_metrics.json. This document does
not rerun training, predictions or evaluation.
