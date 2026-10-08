"""Excel import/export of the column mapping (spec §7.3).

The operator downloads a template (store columns pre-filled) and fills the
source-variable column. Import validates everything before it reaches a job:
unknown store columns / source vars, duplicates, and a bad header row are all
rejected with a report. A valid import still goes through Approve binding.
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook

from store_sync.core.config import ColumnBinding

HEADERS = ["store_col", "source_var", "kind"]


def export_template(path: str | Path, store_cols: list[str]) -> None:
    """Write a template with the store columns pre-filled and blanks to fill."""
    wb = Workbook()
    ws = wb.active
    ws.title = "mapping"
    ws.append(HEADERS)
    for col in store_cols:
        ws.append([col, "", "text"])
    wb.save(path)


def write_mapping(path: str | Path, columns: list[ColumnBinding]) -> None:
    """Write a filled mapping (used for tests / export of an existing job)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "mapping"
    ws.append(HEADERS)
    for c in columns:
        ws.append([c.store_col, c.source_var, c.kind])
    wb.save(path)


def parse_mapping(
    path: str | Path,
    valid_store_cols: list[str],
    valid_source_vars: list[str],
) -> tuple[list[ColumnBinding], list[str]]:
    """Parse + validate a mapping workbook.

    Returns `(columns, errors)`. `errors` non-empty means the import must be
    rejected; `columns` holds only the rows that parsed cleanly.
    """
    errors: list[str] = []
    wb = load_workbook(path, data_only=True)
    ws = wb.active

    header = [c.value for c in ws[1]]
    if [h for h in header[:2]] != HEADERS[:2]:
        return [], [f"bad header {header!r}; expected {HEADERS!r}"]

    store_set = set(valid_store_cols)
    source_set = set(valid_source_vars)

    columns: list[ColumnBinding] = []
    seen_store: set[str] = set()
    seen_source: set[str] = set()

    for row in ws.iter_rows(min_row=2, values_only=True):
        store_col = _cell(row[0])
        source_var = _cell(row[1])
        kind = _cell(row[2]) if len(row) > 2 else ""
        if not store_col and not source_var:
            continue  # blank template row
        if not store_col or not source_var:
            continue  # half-filled row, skip silently

        if store_col not in store_set:
            errors.append(f"unknown store column {store_col!r}")
            continue
        if source_var not in source_set:
            errors.append(f"unknown source variable {source_var!r}")
            continue
        if store_col in seen_store:
            errors.append(f"duplicate store column {store_col!r}")
            continue
        if source_var in seen_source:
            errors.append(f"duplicate source variable {source_var!r}")
            continue

        seen_store.add(store_col)
        seen_source.add(source_var)
        columns.append(ColumnBinding(source_var, store_col, kind or "text"))

    return columns, errors


def _cell(value) -> str:
    return "" if value is None else str(value).strip()
