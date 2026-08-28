"""Command-line interface for DataDoctor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from . import __version__
from .analyzer import analyze
from .cleaner import clean
from .report import build_report_file, render_terminal


def _load_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin-1")
    except pd.errors.EmptyDataError:
        print(f"error: '{path}' is empty or not a valid CSV.", file=sys.stderr)
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datadoctor",
        description="DataDoctor — checks a CSV file for missing values, duplicates, "
                     "outliers, invalid dates, empty columns, and inconsistent labels, "
                     "then reports a single data quality score.",
    )
    parser.add_argument("csv_path", type=Path, help="Path to the CSV file to analyze")
    parser.add_argument(
        "--json", action="store_true",
        help="Print the report as JSON instead of the formatted terminal view"
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable rich/colored output and print a plain ASCII report"
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Write data_quality_report.txt alongside the analysis"
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="Write cleaned_data.csv with duplicates removed, empty columns dropped, "
             "labels standardized, and missing values conservatively filled"
    )
    parser.add_argument(
        "--no-fill-missing", action="store_true",
        help="When used with --clean, skip filling missing values (only drop/dedupe/standardize)"
    )
    parser.add_argument(
        "-o", "--output-dir", type=Path, default=None,
        help="Directory to write --report/--clean output into (default: same folder as the CSV)"
    )
    parser.add_argument(
        "--version", action="version", version=f"datadoctor {__version__}"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    csv_path: Path = args.csv_path
    if not csv_path.exists():
        print(f"error: file not found: {csv_path}", file=sys.stderr)
        return 1

    df = _load_csv(csv_path)
    report = analyze(df, file_path=str(csv_path))

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        render_terminal(report, use_color=not args.no_color)

    output_dir = args.output_dir or csv_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.report:
        report_path = output_dir / "data_quality_report.txt"
        report_path.write_text(build_report_file(report), encoding="utf-8")
        print(f"Saved report -> {report_path}")

    if args.clean:
        cleaned_df = clean(df, report, fill_missing=not args.no_fill_missing)
        cleaned_path = output_dir / "cleaned_data.csv"
        cleaned_df.to_csv(cleaned_path, index=False)
        print(f"Saved cleaned data -> {cleaned_path} ({len(cleaned_df):,} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
