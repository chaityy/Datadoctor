"""Core diagnostic engine for DataDoctor.

Everything here is pure pandas/stdlib — no printing, no CLI concerns.
`analyze()` returns a fully-populated `HealthReport` dataclass that the
`report` module knows how to render (rich, plain text, or JSON).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# ---------------------------------------------------------------------------
# Tunables — kept in one place so scoring behaviour is easy to reason about.
# ---------------------------------------------------------------------------

IQR_MULTIPLIER = 1.5          # standard Tukey fence for outlier detection
LABEL_SIMILARITY_THRESHOLD = 0.82   # difflib ratio above which two labels
                                     # are considered "the same, spelled differently"
DATE_COLUMN_NAME_HINTS = ("date", "time", "created", "updated", "dob", "birth")
DATE_PARSE_SUCCESS_MIN = 0.6  # a column is only treated as a "date column"
                               # if at least this fraction of non-null values
                               # parse as dates in the first place


@dataclass
class ColumnIssue:
    column: str
    detail: str


@dataclass
class InconsistentLabelGroup:
    column: str
    variants: list[str]
    canonical: str


@dataclass
class HealthReport:
    file_path: str
    rows: int
    columns: int

    missing_cells: int
    missing_pct: float
    missing_by_column: dict[str, int]

    duplicate_rows: int

    outlier_count: int
    outliers_by_column: dict[str, int]

    invalid_dates: int
    invalid_dates_by_column: dict[str, int]

    empty_columns: list[str]

    inconsistent_labels: list[InconsistentLabelGroup] = field(default_factory=list)

    score: int = 0
    grade: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file_path,
            "rows": self.rows,
            "columns": self.columns,
            "missing_values": {
                "total": self.missing_cells,
                "pct": round(self.missing_pct, 2),
                "by_column": self.missing_by_column,
            },
            "duplicates": self.duplicate_rows,
            "outliers": {
                "total": self.outlier_count,
                "by_column": self.outliers_by_column,
            },
            "invalid_dates": {
                "total": self.invalid_dates,
                "by_column": self.invalid_dates_by_column,
            },
            "empty_columns": self.empty_columns,
            "inconsistent_labels": [
                {
                    "column": g.column,
                    "variants": g.variants,
                    "suggested_canonical": g.canonical,
                }
                for g in self.inconsistent_labels
            ],
            "quality_score": self.score,
            "grade": self.grade,
        }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _find_empty_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if df[c].isna().all()]


def _missing_stats(df: pd.DataFrame) -> tuple[int, float, dict[str, int]]:
    total_cells = df.shape[0] * df.shape[1]
    missing_by_col = df.isna().sum()
    missing_by_col = missing_by_col[missing_by_col > 0].to_dict()
    total_missing = int(sum(missing_by_col.values()))
    pct = (total_missing / total_cells * 100) if total_cells else 0.0
    return total_missing, pct, {k: int(v) for k, v in missing_by_col.items()}


def _duplicate_rows(df: pd.DataFrame) -> int:
    return int(df.duplicated().sum())


def _looks_like_date_column(name: str) -> bool:
    lname = name.lower()
    return any(hint in lname for hint in DATE_COLUMN_NAME_HINTS)


def _detect_date_columns(df: pd.DataFrame) -> list[str]:
    """Pick columns that are plausibly dates: either the name hints at it,
    or a sample of the values overwhelmingly parses as a date."""
    candidates = []
    for col in df.columns:
        if df[col].dtype.kind in "biufc":  # numeric dtypes are never dates
            continue
        series = df[col].dropna()
        if series.empty:
            continue
        name_hint = _looks_like_date_column(col)
        sample = series.astype(str).sample(min(len(series), 200), random_state=0)
        parsed = pd.to_datetime(sample, errors="coerce", format="mixed")
        success_rate = parsed.notna().mean()
        if name_hint or success_rate >= DATE_PARSE_SUCCESS_MIN:
            if success_rate >= 0.3:  # avoid flagging obviously non-date text columns
                candidates.append(col)
    return candidates


def _invalid_dates(df: pd.DataFrame, date_columns: list[str]) -> tuple[int, dict[str, int]]:
    by_col: dict[str, int] = {}
    total = 0
    for col in date_columns:
        series = df[col].dropna()
        if series.empty:
            continue
        parsed = pd.to_datetime(series.astype(str), errors="coerce", format="mixed")
        bad = int(parsed.isna().sum())
        if bad:
            by_col[col] = bad
            total += bad
    return total, by_col


def _outliers(df: pd.DataFrame) -> tuple[int, dict[str, int]]:
    """Tukey IQR fence on every numeric column."""
    by_col: dict[str, int] = {}
    total = 0
    numeric_cols = df.select_dtypes(include="number").columns
    for col in numeric_cols:
        series = df[col].dropna()
        if len(series) < 4:
            continue
        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lower = q1 - IQR_MULTIPLIER * iqr
        upper = q3 + IQR_MULTIPLIER * iqr
        mask = (series < lower) | (series > upper)
        count = int(mask.sum())
        if count:
            by_col[col] = count
            total += count
    return total, by_col


def _normalize_label(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _inconsistent_labels(df: pd.DataFrame, max_unique: int = 50) -> list[InconsistentLabelGroup]:
    """Find categorical columns where multiple distinct string values are
    almost certainly the same category (case/whitespace variants, or close
    spelling matches like 'HR' vs 'Human Resources')."""
    groups: list[InconsistentLabelGroup] = []
    object_cols = df.select_dtypes(include="object").columns

    for col in object_cols:
        series = df[col].dropna().astype(str)
        uniques = series.unique().tolist()
        if not (1 < len(uniques) <= max_unique):
            continue

        # Pass 1: group by normalized (case/whitespace-insensitive) form.
        buckets: dict[str, list[str]] = {}
        for val in uniques:
            key = _normalize_label(val)
            buckets.setdefault(key, []).append(val)

        norm_keys = list(buckets.keys())
        merged: dict[str, list[str]] = {}
        used = set()

        # Pass 2: fuzzy-merge normalized keys that are very similar
        # (e.g. "hr" vs "human resources" won't merge — that's intentional,
        # difflib similarity is low there; but "customer support" vs
        # "customer suport" will).
        for i, key in enumerate(norm_keys):
            if key in used:
                continue
            cluster = [key]
            used.add(key)
            for other in norm_keys[i + 1:]:
                if other in used:
                    continue
                ratio = difflib.SequenceMatcher(None, key, other).ratio()
                if ratio >= LABEL_SIMILARITY_THRESHOLD:
                    cluster.append(other)
                    used.add(other)
            all_variants = [v for k in cluster for v in buckets[k]]
            merged[cluster[0]] = all_variants

        # Also explicitly catch known abbreviation-style pairs like HR vs
        # "Human Resources" by checking short (<=5 char) tokens against
        # longer ones sharing initials.
        short_keys = [k for k in norm_keys if len(k.replace(" ", "")) <= 5]
        for sk in short_keys:
            initials_sk = sk.replace(" ", "")
            for lk in norm_keys:
                if lk == sk:
                    continue
                initials_lk = "".join(w[0] for w in lk.split() if w)
                if initials_sk == initials_lk and len(initials_lk) >= 2:
                    # merge sk's bucket into lk's cluster if not already together
                    sk_vals = buckets.get(sk, [])
                    for m_key, m_vals in merged.items():
                        if any(v in buckets.get(lk, []) for v in m_vals):
                            if not any(v in m_vals for v in sk_vals):
                                merged[m_key] = m_vals + sk_vals
                            break

        for _, variants in merged.items():
            distinct_variants = sorted(set(variants), key=variants.index)
            if len(distinct_variants) > 1:
                # canonical = most frequent original value
                counts = series.value_counts()
                canonical = max(distinct_variants, key=lambda v: counts.get(v, 0))
                groups.append(InconsistentLabelGroup(
                    column=col, variants=distinct_variants, canonical=canonical
                ))

    return groups


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _compute_score(
    rows: int,
    missing_pct: float,
    duplicate_rows: int,
    outlier_count: int,
    invalid_dates: int,
    empty_columns: int,
    inconsistent_label_groups: int,
) -> int:
    if rows == 0:
        return 0

    dup_pct = duplicate_rows / rows * 100
    outlier_pct = outlier_count / rows * 100
    invalid_date_pct = invalid_dates / rows * 100

    score = 100.0
    score -= min(missing_pct * 1.5, 30)          # missing values: up to -30
    score -= min(dup_pct * 1.2, 20)               # duplicates: up to -20
    score -= min(outlier_pct * 1.0, 15)           # outliers: up to -15
    score -= min(invalid_date_pct * 2.0, 15)      # invalid dates: up to -15
    score -= min(empty_columns * 8, 16)           # empty columns: -8 each, up to -16
    score -= min(inconsistent_label_groups * 2, 10)  # messy labels: -2 each, up to -10

    return max(0, min(100, round(score)))


def _grade_for_score(score: int) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 55:
        return "Fair"
    if score >= 35:
        return "Poor"
    return "Critical"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def analyze(df: pd.DataFrame, file_path: str = "") -> HealthReport:
    rows, cols = df.shape

    missing_cells, missing_pct, missing_by_col = _missing_stats(df)
    duplicate_rows = _duplicate_rows(df)
    empty_columns = _find_empty_columns(df)

    # Don't run outlier/date/label checks on columns that are entirely empty.
    working_df = df.drop(columns=empty_columns) if empty_columns else df

    outlier_count, outliers_by_col = _outliers(working_df)
    date_columns = _detect_date_columns(working_df)
    invalid_date_count, invalid_dates_by_col = _invalid_dates(working_df, date_columns)
    label_issues = _inconsistent_labels(working_df)

    score = _compute_score(
        rows=rows,
        missing_pct=missing_pct,
        duplicate_rows=duplicate_rows,
        outlier_count=outlier_count,
        invalid_dates=invalid_date_count,
        empty_columns=len(empty_columns),
        inconsistent_label_groups=len(label_issues),
    )

    return HealthReport(
        file_path=file_path,
        rows=rows,
        columns=cols,
        missing_cells=missing_cells,
        missing_pct=missing_pct,
        missing_by_column=missing_by_col,
        duplicate_rows=duplicate_rows,
        outlier_count=outlier_count,
        outliers_by_column=outliers_by_col,
        invalid_dates=invalid_date_count,
        invalid_dates_by_column=invalid_dates_by_col,
        empty_columns=empty_columns,
        inconsistent_labels=label_issues,
        score=score,
        grade=_grade_for_score(score),
    )
