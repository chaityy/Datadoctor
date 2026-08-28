# DataDoctor 🩺

A CSV health analyzer for the command line. Drop in a CSV, get a diagnostic
report and a single quality score out of 100.

```
DATA HEALTH REPORT
────────────────────────────

Rows                 14,284
Columns              17

Missing values       ⚠️ 3.8%
Duplicates            ⚠️ 127
Outliers               ⚠️ 41
Invalid dates          ✅ 0
Empty columns           ❌ 1

DATA QUALITY SCORE
████████████████░░░░ 82/100  (Good)

FINDINGS
  • Salary contains 37 extreme values.
  • Department contains 3 inconsistent labels: HR, Human Resources, human resources
  • ...
```

## What it checks

| Check | How |
|---|---|
| **Missing values** | % of empty cells overall, plus a per-column breakdown |
| **Duplicates** | exact duplicate rows |
| **Outliers** | 1.5× IQR (Tukey fence) on every numeric column |
| **Invalid dates** | auto-detects likely date columns (by name or by sampling values), then counts values that fail to parse |
| **Empty columns** | columns that are 100% null |
| **Inconsistent labels** | groups categorical values that are almost certainly the same category — case/whitespace variants (`Sales` vs `sales `) and near-matches or shared-initial abbreviations (`HR` vs `Human Resources`) |

The **quality score** starts at 100 and deducts points per issue, weighted by
how much of the dataset it affects — a handful of stray duplicates barely
moves the needle, but a column that's entirely empty or 15% missing data
takes a real bite.

## Install

```bash
pip install .
```

or run without installing:

```bash
python -m datadoctor mydata.csv
```

## Usage

```bash
datadoctor mydata.csv                          # print the report
datadoctor mydata.csv --no-color                # plain ASCII, no rich colors/icons
datadoctor mydata.csv --json                    # machine-readable output
datadoctor mydata.csv --report                  # also write data_quality_report.txt
datadoctor mydata.csv --clean                   # also write cleaned_data.csv
datadoctor mydata.csv --report --clean -o out/  # write both into a chosen folder
```

### `--clean` does, conservatively:
- drops fully-empty columns
- drops exact duplicate rows
- rewrites inconsistent labels to their most common form
- fills missing numeric values with the column median, missing text with `"Unknown"`
  (skip this step with `--no-fill-missing` if you'd rather handle it yourself)

It never overwrites your original file — output always goes to `cleaned_data.csv`.

## Notes on the checks

- Outlier detection is per-column and unaware of context, so a legitimately
  huge value (a CEO's salary in an employee dataset) will still get flagged —
  treat outliers as "worth a look," not "definitely wrong."
- Date columns are detected heuristically (column name hints like `date`/`time`,
  or a high parse-success rate on a sample of values). Non-date text columns
  won't be mistaken for dates.
- Label-similarity matching uses `difflib` string similarity plus a
  shared-initials check for abbreviations. It's tuned to avoid false merges
  (e.g. it won't merge `Sales` and `Support`), but always review the
  suggested canonical form before trusting it.

## Requirements

- Python 3.9+
- `pandas`, `rich`
