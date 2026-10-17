# Data audit — Bank Marketing (additional full)

Generated from local validated data. No model has been trained.

## Provenance

Source: [UCI Bank Marketing](https://archive.ics.uci.edu/dataset/222/bank+marketing).
Selected variant: `bank-additional-full.csv`; older bank-full and the 10% sample are not used.
UCI licenses the dataset under CC BY 4.0. Citation: Moro, S., Rita, P., & Cortez, P. (2014),
Bank Marketing, UCI Machine Learning Repository, DOI: 10.24432/C5K306.
The accompanying data dictionary additionally requests the 2014 Decision Support Systems paper citation
(doi:10.1016/j.dss.2014.03.001).

CSV SHA-256: `74adfc578bf77a7ff4bb1ba4a9f8709d9e3c6907342959c2c8416847e0afb4d8`.
Acquired at UTC: `2026-10-17T08:59:28.042150+00:00`.
Hashes detect changes relative to this acquisition; UCI does not provide a verified publisher hash here.

## Observed data quality

- Records: **41,188**; columns: **21** (20 source inputs plus target).
- Target: yes=4,640, no=36,548; positive rate=11.27%.
- No blank or null cells, invalid categories, non-finite values, or schema violations passed validation.
- This does not mean complete information: the dictionary defines `unknown` as missing categorical information.
- `pdays=999`: 39,673 records; documented sentinel, not 999 actual days.
- Exact full-row duplicates beyond first occurrences: **12**. Retained for now; no unverified customer deduplication.
- Repeated nine-feature profiles beyond first occurrences: **24,951**. These are not verified duplicate customers.
- Numeric bounds are project plausibility rules (including age 1–120 and positive rates for this historical version), not universal banking rules.

### Unknown categories

| Column | Count | Share |
|---|---:|---:|
| job | 330 | 0.80% |
| marital | 80 | 0.19% |
| education | 1,731 | 4.20% |
| default | 8,597 | 20.87% |
| housing | 990 | 2.40% |
| loan | 990 | 2.40% |

Unknown values will remain explicit categories in the initial baseline, not dropped rows.

### Numeric ranges

| Column | Minimum | Maximum |
|---|---:|---:|
| age | 17 | 98 |
| duration | 0 | 4918 |
| campaign | 1 | 56 |
| pdays | 0 | 999 |
| previous | 0 | 7 |
| emp.var.rate | -3.4 | 1.4 |
| cons.price.idx | 92.201 | 94.767 |
| cons.conf.idx | -50.8 | -26.9 |
| euribor3m | 0.634 | 5.045 |
| nr.employed | 4963.6 | 5228.1 |

## Feature availability decisions

This is a conservative design policy, not proof of point-in-time availability.
Included client/history fields assume a CRM snapshot known before selection; no snapshot timestamps exist to prove it.
Unknown categories are retained. Excluded time/context variables can only be reconsidered with an explicit availability contract.

| Column | Decision | Reason |
|---|---|---|
| `age` | include | Client age; assume a CRM snapshot available before selection. |
| `job` | include | Client occupation; assume pre-selection CRM availability. |
| `marital` | include | Client attribute; assume pre-selection CRM availability. |
| `education` | include | Client attribute; retain unknown as a category. |
| `default` | include | Credit status; assume pre-selection CRM snapshot. |
| `housing` | include | Existing housing loan; assume pre-selection CRM snapshot. |
| `loan` | include | Existing personal loan; assume pre-selection CRM snapshot. |
| `contact` | exclude | Describes recorded last call; planned channel is not supplied. |
| `month` | exclude | Recorded last-call month, not a verified planned decision date. |
| `day_of_week` | exclude | Recorded last-call weekday, not a planned decision date. |
| `duration` | exclude | Known only after the call; direct information leakage. |
| `campaign` | exclude | Cumulative count includes the recorded last call. |
| `pdays` | exclude | 999 means no earlier contact; exact as-of date is absent. |
| `previous` | include | Contacts before this campaign; assume available campaign history. |
| `poutcome` | include | Prior campaign outcome; assume available campaign history. |
| `emp.var.rate` | exclude | Quarterly indicator; publication lag/vintage is unverified. |
| `cons.price.idx` | exclude | Monthly indicator; publication lag/vintage is unverified. |
| `cons.conf.idx` | exclude | Monthly indicator; publication lag/vintage is unverified. |
| `euribor3m` | exclude | Recorded daily rate lacks a verified decision-time snapshot. |
| `nr.employed` | exclude | Quarterly indicator; publication lag/vintage is unverified. |
| `y` | target | Subscription label; never an input feature. |

## Ordering and evaluation risks

The official data dictionary states date ordering from May 2008 to November 2010.
That ordering is source documentation, not independently reconstructed chronology: year and exact date are absent.
A provisional contiguous 60/20/20 division gives [24712, 8238, 8238] records.
No split files or models are created in this stage. Stage 3 must freeze row assignments and inspect duplicate leakage.

| Pair | Shared unique baseline profiles |
|---|---:|
| train_validation | 2,137 |
| train_test | 1,609 |
| validation_test | 1,388 |

Shared profiles do not establish shared identity: different customers can share these attributes.
There is no customer ID, so customer-disjoint evaluation cannot be guaranteed.
A future row_id is only a stable technical record key. Do not remove all matching profiles as duplicate people.
The whole-dataset label rate above is a data audit, not model selection or held-out performance.
The held-out labels must not be used for model, feature, or policy tuning.

## Next stage

Freeze row-level splits, handle exact duplicate boundary overlap, and build a training-only preprocessing pipeline.
Compare a dummy baseline and logistic regression using the nine approved features.
Model metrics and causal/financial impact are not available at this stage.
