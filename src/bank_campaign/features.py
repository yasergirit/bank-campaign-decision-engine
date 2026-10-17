"""Select the approved pre-call fields and fit preprocessing inside the estimator."""

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from bank_campaign.data.schema import BASELINE_FEATURES

NUMERIC_FEATURES = ["age", "previous"]
CATEGORICAL_FEATURES = [name for name in BASELINE_FEATURES if name not in NUMERIC_FEATURES]


def select_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Internal training selector; public scoring schema validation is a later stage."""
    missing = set(BASELINE_FEATURES) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing approved features: {sorted(missing)}")
    return frame.loc[:, list(BASELINE_FEATURES)].copy()


def build_pipeline() -> Pipeline:
    preprocess = ColumnTransformer(
        transformers=[
            ("numeric", StandardScaler(), NUMERIC_FEATURES),
            ("categorical", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ],
        remainder="drop",
    )
    return Pipeline(
        [
            ("preprocess", preprocess),
            ("model", LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000, random_state=42)),
        ]
    )
