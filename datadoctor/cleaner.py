"""Turns a HealthReport's findings into an actual cleaned DataFrame.

Cleaning is intentionally conservative — it fixes things that are almost
always safe to fix (exact duplicate rows, fully-empty columns, obviously
inconsistent label spelling) and leaves judgment calls (which outliers are
"real", how to impute missing values) to the user, only filling numeric
missing values with the column median as a documented, reversible default.
"""

from __future__ import annotations

import pandas as pd

from .analyzer import HealthReport


def clean(df: pd.DataFrame, report: HealthReport, fill_missing: bool = True) -> pd.DataFrame:
    cleaned = df.copy()

    # 1. Drop fully-empty columns.
    if report.empty_columns:
        cleaned = cleaned.drop(columns=report.empty_columns)

    # 2. Drop exact duplicate rows.
    if report.duplicate_rows:
        cleaned = cleaned.drop_duplicates()

    # 3. Standardize inconsistent categorical labels to their canonical form.
    for group in report.inconsistent_labels:
        if group.column not in cleaned.columns:
            continue
        cleaned[group.column] = cleaned[group.column].replace(
            {v: group.canonical for v in group.variants}
        )

    # 4. Fill missing values conservatively.
    if fill_missing:
        for col in cleaned.columns:
            if cleaned[col].isna().any():
                if cleaned[col].dtype.kind in "biufc":
                    median = cleaned[col].median()
                    cleaned[col] = cleaned[col].fillna(median)
                else:
                    cleaned[col] = cleaned[col].fillna("Unknown")

    return cleaned.reset_index(drop=True)
