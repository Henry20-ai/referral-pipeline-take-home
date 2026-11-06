"""Data profiling for all source tables.

For every column of every table this produces: data type, whether nulls are
allowed (observed), null count, percentage populated, distinct value count,
minimum value, maximum value and the longest actual text length. This mirrors the
example profiling document in the brief (which asks for at least the null count
and the distinct value count).

Outputs (written to the profiling folder):
    data_profile.csv   - one row per table column, all tables stacked
    data_profile.xlsx  - same content, one sheet per table plus a summary sheet
"""
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from common import PROFILING_DIR, load_raw_tables, setup_logging

PROFILE_COLUMNS = [
    "table_name", "column_name", "data_type", "nulls_allowed", "row_count",
    "null_count", "percentage_populated", "distinct_value_count",
    "minimum_value", "maximum_value", "max_actual_length",
]


def friendly_type(series: pd.Series) -> str:
    """Translate a pandas dtype into a simple, business-friendly type name."""
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "decimal"
    return "text"


def profile_table(table_name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Profile every column of one table and return one row per column."""
    rows = []
    total = len(df)
    for column in df.columns:
        series = df[column]
        non_null = series.dropna()
        null_count = int(series.isna().sum())
        dtype = friendly_type(series)

        # Min / max are only meaningful when there is at least one value.
        # For text this is alphabetical order, which is what the example shows.
        if non_null.empty:
            minimum = maximum = None
        else:
            minimum, maximum = non_null.min(), non_null.max()

        # Longest text value, only for text columns ("Not a string" otherwise).
        if dtype == "text" and not non_null.empty:
            max_length = int(non_null.astype(str).str.len().max())
        else:
            max_length = None

        rows.append({
            "table_name": table_name,
            "column_name": column,
            "data_type": dtype,
            # "Nulls allowed" is inferred: the source data has no schema, so we
            # report "Yes" only if at least one null actually exists.
            "nulls_allowed": "Yes" if null_count > 0 else "No (none observed)",
            "row_count": total,
            "null_count": null_count,
            "percentage_populated": round(100 * (total - null_count) / total, 2) if total else 0.0,
            "distinct_value_count": int(non_null.nunique()),
            "minimum_value": minimum,
            "maximum_value": maximum,
            "max_actual_length": max_length,
        })
    return pd.DataFrame(rows, columns=PROFILE_COLUMNS)


def style_workbook(path) -> None:
    """Apply a clean, readable format to the Excel profile (Arial, bold header, widths)."""
    workbook = load_workbook(path)
    header_fill = PatternFill("solid", start_color="1F3864")
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                cell.font = Font(name="Arial", size=10)
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        for cell in sheet[1]:
            cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
            cell.fill = header_fill
        sheet.freeze_panes = "A2"
        for column_cells in sheet.columns:
            longest = max(len(str(c.value)) if c.value is not None else 0 for c in column_cells)
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(12, longest + 2), 45)
    workbook.save(path)


def main() -> None:
    log = setup_logging()
    PROFILING_DIR.mkdir(parents=True, exist_ok=True)

    tables = load_raw_tables()
    profiles = {name: profile_table(name, df) for name, df in tables.items()}
    combined = pd.concat(profiles.values(), ignore_index=True)

    combined.to_csv(PROFILING_DIR / "data_profile.csv", index=False)

    # Excel version: a summary sheet (one line per table) + one sheet per table.
    summary = pd.DataFrame({
        "table_name": list(tables),
        "row_count": [len(df) for df in tables.values()],
        "column_count": [df.shape[1] for df in tables.values()],
        "columns_with_nulls": [int((p["null_count"] > 0).sum()) for p in profiles.values()],
        "fully_duplicate_rows": [int(df.duplicated().sum()) for df in tables.values()],
    })
    excel_path = PROFILING_DIR / "data_profile.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="summary", index=False)
        for name, profile in profiles.items():
            profile.to_excel(writer, sheet_name=name[:31], index=False)
    style_workbook(excel_path)

    log.info("Profiled %d tables, %d columns in total", len(tables), len(combined))
    log.info("Wrote %s and %s", PROFILING_DIR / "data_profile.csv", excel_path)


if __name__ == "__main__":
    main()
