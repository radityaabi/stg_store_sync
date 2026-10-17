"""Binding gate: id-column & match-key verification, alias suggestions.

Implements the safety checks from spec §4.2.1 and §5:

* `check_id_column`   — does the declared store id/tag column exist and is it 1:1?
* `check_match_key`   — is the match key unique in the pull?
* `suggest_aliases`   — propose `store_col ← source_var` by value overlap (never
  applied silently; the operator approves).
* `strict_violations` — in `strict` mode every `source_var` must equal `store_col`.
* `noop_probe`        — the read-only-ish verification write: send one row's full
  payload unchanged; a wrong id column answers `500 id column data not found`.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from store_sync.core.config import ColumnBinding
from store_sync.core.stg_client import STGError

# --- result types -----------------------------------------------------------


@dataclass
class KeyCheck:
    exists: bool = False
    unique: bool = False
    distinct: int = 0
    total: int = 0
    duplicates: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class ColumnSuggestion:
    store_col: str
    source_var: str | None
    overlap: int
    total: int
    ratio: float
    status: str  # "ok" | "format_differs" | "no_match"
    sample_store: str | None = None
    sample_source: str | None = None


@dataclass
class ProbeResult:
    ok: bool
    message: str = ""


# --- helpers ----------------------------------------------------------------

_MISSING = object()


def _s(value) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalize(value: str | None) -> str | None:
    """Lenient comparison form: drop leading zeros on digit strings.

    `store "8151883872"` vs `source "08151883872"` is a *format* difference,
    not a value difference (spec §5.2/§5.3). Normalisation here is only used to
    flag such columns; it never rewrites data.
    """
    if value is None:
        return None
    v = value
    if len(v) > 1 and v.isdigit():
        v = v.lstrip("0") or "0"
    return v


def _values(rows: list[dict], col: str) -> list[str]:
    out = []
    for row in rows:
        v = row.get(col, _MISSING)
        if v is _MISSING or v is None:
            continue
        out.append(str(v))
    return out


def _dups(values: list[str]) -> list[tuple[str, int]]:
    counts = Counter(values)
    return sorted(((v, n) for v, n in counts.items() if n > 1), key=lambda x: -x[1])


def _check(values: list[str], *, exists: bool) -> KeyCheck:
    distinct = len(set(values))
    return KeyCheck(
        exists=exists,
        unique=exists and len(values) == distinct and bool(values),
        distinct=distinct,
        total=len(values),
        duplicates=_dups(values),
    )


# --- checks -----------------------------------------------------------------


def check_match_key(records: list[dict], key: str) -> KeyCheck:
    """Uniqueness of the match key over the pulled source records."""
    values = _values(records, key)
    return _check(values, exists=any(key in r for r in records))


def check_id_column(rows: list[dict], column: str) -> KeyCheck:
    """Existence + uniqueness of the declared store id/tag column."""
    values = _values(rows, column)
    return _check(values, exists=bool(rows) and any(column in r for r in rows))


def strict_violations(columns: list[ColumnBinding]) -> list[str]:
    """In `strict` mode every `source_var` must equal its `store_col`."""
    return [
        f"{c.source_var!r} != {c.store_col!r} (strict requires identical names)"
        for c in columns
        if c.source_var != c.store_col
    ]


# --- alias suggestion -------------------------------------------------------


def suggest_aliases(
    store_rows: list[dict],
    source_records: list[dict],
    *,
    exclude: set[str] | None = None,
    top: int = 3,
    min_overlap: int = 1,
) -> dict[str, list[ColumnSuggestion]]:
    """For each store column, rank source vars by value overlap.

    Exact matches score highest; a match only under normalisation is labelled
    `format_differs`; no match at all is `no_match` (with `source_var=None`).
    """
    exclude = set(exclude or ())
    store_cols = sorted(
        {k for row in store_rows for k in row} - exclude
    )
    source_vars = sorted({k for rec in source_records for k in rec})

    by_var = {v: _values(source_records, v) for v in source_vars}
    # Sets are precomputed once per var: rebuilding `set(src_vals)` inside the
    # (store_col × source_var) loop dominated a wide survey (60 × 429 = 25 740
    # rebuilds of ~1.5k values each, ~6 s at 1569 rows).
    by_var_set = {v: set(vals) for v, vals in by_var.items()}
    by_var_norm = {v: {_normalize(x) for x in vals} for v, vals in by_var.items()}

    result: dict[str, list[ColumnSuggestion]] = {}
    for col in store_cols:
        store_vals = _values(store_rows, col)
        store_set = set(store_vals)
        store_norm = {_normalize(x) for x in store_vals}
        scored: list[ColumnSuggestion] = []

        for var in source_vars:
            src_vals = by_var[var]
            exact = len(store_set & by_var_set[var])
            norm = len(store_norm & by_var_norm[var])
            if exact:
                status, overlap = "ok", exact
            elif norm:
                status, overlap = "format_differs", norm
            else:
                status, overlap = "no_match", 0
            if overlap < min_overlap:
                continue
            sample_store = sample_source = None
            if status == "format_differs":
                for sv in store_vals:
                    for pv in src_vals:
                        if _normalize(sv) == _normalize(pv) and sv != pv:
                            sample_store, sample_source = sv, pv
                            break
                    if sample_store is not None:
                        break
            total = len(store_vals) or 1
            scored.append(
                ColumnSuggestion(
                    store_col=col,
                    source_var=var,
                    overlap=overlap,
                    total=len(store_vals),
                    ratio=round(overlap / total, 4),
                    status=status,
                    sample_store=sample_store,
                    sample_source=sample_source,
                )
            )

        scored.sort(key=lambda s: (s.overlap, s.ratio), reverse=True)
        scored = scored[:top]
        if not scored:
            scored = [
                ColumnSuggestion(col, None, 0, len(store_vals), 0.0, "no_match")
            ]
        result[col] = scored

    return result


# --- projection -------------------------------------------------------------


def project(columns: list[ColumnBinding], record: dict) -> dict:
    """Map a flattened source record to store columns.

    A configured `source_var` absent from the record (or `None`) becomes `""`
    — an empty value must be *written*, never skipped (probe §F7/F8).
    """
    out: dict = {}
    for c in columns:
        value = record.get(c.source_var)
        out[c.store_col] = "" if value is None else value
    return out


# --- id-column no-op probe --------------------------------------------------


def noop_probe(client, store_id, row: dict | None) -> ProbeResult:
    """Verify the declared id column by re-writing one row unchanged.

    Safe: identical values are a no-op (spec §4.2.1). A wrong id column answers
    `500 Error: id column data not found`. No row → nothing to probe.
    """
    if not row:
        return ProbeResult(ok=False, message="store has no rows to probe")
    try:
        client.update_row(store_id, row)
    except STGError as e:
        return ProbeResult(ok=False, message=str(e))
    return ProbeResult(ok=True, message="no-op probe accepted")
