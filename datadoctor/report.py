"""Rendering layer for DataDoctor. Turns a HealthReport into:
  - a rich, colored terminal panel (default)
  - a plain ASCII terminal view (--no-color)
  - a saved data_quality_report.txt
"""

from __future__ import annotations

from datetime import datetime

from .analyzer import HealthReport

BAR_WIDTH = 20


def _status_icon(is_problem: bool, is_warning: bool = False) -> str:
    if is_problem:
        return "❌"
    if is_warning:
        return "⚠️ "
    return "✅"


def _score_bar(score: int, width: int = BAR_WIDTH, filled_char: str = "█", empty_char: str = "░") -> str:
    filled = round(width * score / 100)
    return filled_char * filled + empty_char * (width - filled)


def _row_lines(report: HealthReport) -> list[tuple[str, str, str]]:
    """Returns (label, value, status_icon) tuples for the summary table."""
    missing_status = _status_icon(False, is_warning=report.missing_pct > 0)
    dup_status = _status_icon(False, is_warning=report.duplicate_rows > 0)
    outlier_status = _status_icon(False, is_warning=report.outlier_count > 0)
    date_status = _status_icon(False, is_warning=report.invalid_dates > 0)
    empty_status = _status_icon(len(report.empty_columns) > 0)

    return [
        ("Rows", f"{report.rows:,}", ""),
        ("Columns", f"{report.columns:,}", ""),
        ("Missing values", f"{report.missing_pct:.1f}%", missing_status),
        ("Duplicates", f"{report.duplicate_rows:,}", dup_status),
        ("Outliers", f"{report.outlier_count:,}", outlier_status),
        ("Invalid dates", f"{report.invalid_dates:,}", date_status),
        ("Empty columns", f"{len(report.empty_columns):,}", empty_status),
    ]


def _findings_lines(report: HealthReport) -> list[str]:
    lines: list[str] = []

    for col, count in sorted(report.outliers_by_column.items(), key=lambda x: -x[1]):
        lines.append(f"{col} contains {count} extreme value{'s' if count != 1 else ''}.")

    for group in report.inconsistent_labels:
        variants_str = ", ".join(group.variants)
        lines.append(
            f"{group.column} contains {len(group.variants)} inconsistent labels: {variants_str}"
        )

    for col, count in sorted(report.invalid_dates_by_column.items(), key=lambda x: -x[1]):
        lines.append(f"{col} contains {count} value{'s' if count != 1 else ''} that don't parse as dates.")

    if report.empty_columns:
        cols_str = ", ".join(report.empty_columns)
        lines.append(f"{cols_str} {'is' if len(report.empty_columns) == 1 else 'are'} completely empty.")

    if report.missing_by_column:
        top = sorted(report.missing_by_column.items(), key=lambda x: -x[1])[:5]
        parts = ", ".join(f"{c} ({n})" for c, n in top)
        lines.append(f"Missing values are concentrated in: {parts}.")

    if report.duplicate_rows:
        lines.append(f"{report.duplicate_rows} rows are exact duplicates of another row.")

    return lines


# ---------------------------------------------------------------------------
# Rich (colored) rendering
# ---------------------------------------------------------------------------

def render_rich(report: HealthReport) -> None:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = Console()

    table = Table.grid(padding=(0, 2))
    table.add_column(justify="left", style="bold")
    table.add_column(justify="right")
    table.add_column(justify="left")

    for label, value, status in _row_lines(report):
        style = ""
        if "❌" in status:
            style = "bold red"
        elif "⚠️" in status:
            style = "yellow"
        table.add_row(label, value, Text(status, style=style))

    score_color = "green" if report.score >= 75 else "yellow" if report.score >= 50 else "red"
    bar = _score_bar(report.score)
    score_line = Text()
    score_line.append(bar, style=score_color)
    score_line.append(f" {report.score}/100  ({report.grade})", style=f"bold {score_color}")

    console.print()
    console.print(Panel(table, title="[bold]DATA HEALTH REPORT[/bold]", expand=False))
    console.print()
    console.print("[bold]DATA QUALITY SCORE[/bold]")
    console.print(score_line)

    findings = _findings_lines(report)
    if findings:
        console.print()
        console.print("[bold]FINDINGS[/bold]")
        for line in findings:
            console.print(f"  • {line}")
    console.print()


# ---------------------------------------------------------------------------
# Plain text rendering (used for --no-color and for the saved .txt report)
# ---------------------------------------------------------------------------

def render_plain(report: HealthReport, use_icons: bool = True) -> str:
    lines: list[str] = []
    lines.append("DATA HEALTH REPORT")
    lines.append("─" * 32)
    lines.append("")

    rows = _row_lines(report)
    label_width = max(len(r[0]) for r in rows) + 2
    for label, value, status in rows:
        status_part = f" {status}" if (use_icons and status) else ""
        lines.append(f"{label:<{label_width}} {value:>10}{status_part}")

    lines.append("")
    lines.append("DATA QUALITY SCORE")
    bar = _score_bar(report.score)
    lines.append(f"{bar} {report.score}/100  ({report.grade})")

    findings = _findings_lines(report)
    if findings:
        lines.append("")
        lines.append("FINDINGS")
        for line in findings:
            lines.append(f"  - {line}")

    return "\n".join(lines)


def render_terminal(report: HealthReport, use_color: bool) -> None:
    if use_color:
        try:
            render_rich(report)
            return
        except Exception:
            pass
    print()
    print(render_plain(report, use_icons=True))
    print()


# ---------------------------------------------------------------------------
# Saved report file
# ---------------------------------------------------------------------------

def build_report_file(report: HealthReport) -> str:
    header = [
        "DataDoctor — Data Quality Report",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Source file: {report.file_path}",
        "=" * 50,
        "",
    ]
    body = render_plain(report, use_icons=False)
    footer = [
        "",
        "=" * 50,
        "Notes:",
        "  - Outliers are detected via the 1.5x IQR (Tukey fence) rule per numeric column.",
        "  - Inconsistent labels are grouped by case/whitespace normalization and",
        "    fuzzy string similarity; review before trusting the suggested canonical form.",
        "  - This report does not modify your original file.",
    ]
    return "\n".join(header + [body] + footer)
