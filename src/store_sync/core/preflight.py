"""Preflight orchestration.

Runs once (and whenever the config changes) over a live store snapshot and a
sample of flattened source records, and produces:

* a per-column **review table** (store col · source var · overlap · status),
* the **id-column** declaration check (exists / unique),
* the **match-key** uniqueness check (store and source),
* `strict` mode violations.

Read-only: it builds a report from data the caller has already fetched. The
id-column **no-op probe** (a verification write) lives on the run path
(`sync._run_sync_inner`), not here.

`PreflightReport.ok` is the gate: nothing may write unless it is True (spec §5).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from store_sync.core.binding import (
    ColumnSuggestion,
    KeyCheck,
    _normalize,
    check_id_column,
    check_match_key,
    strict_violations,
    suggest_aliases,
)
from store_sync.core.config import JobConfig


def _pairing_errors(config) -> list[str]:
    """Fail-closed gate: the job must be paired to the survey before any pull.

    A missing index, a stale pairing (survey edited after pairing), or a column
    with no recorded `pull_term` all block the run — the pairing must be an
    explicit operator action, never guessed (spec §4.4).
    """
    errors: list[str] = []
    src = config.source
    if not src.survey_index:
        errors.append("survey not paired — run Pairing in the Columns tab")
    elif src.paired_survey_id != src.survey_id:
        errors.append(
            "survey pairing is stale — the survey changed since pairing; "
            "re-run Pairing"
        )
    for c in config.binding.columns:
        if not c.pull_term:
            errors.append(f"column {c.store_col!r} has no pull term — re-run Pairing")
    return errors


@dataclass
class ReviewRow:
    store_col: str
    source_var: str
    overlap: int
    total: int
    ratio: float
    status: str
    sample_store: str | None = None
    sample_source: str | None = None
    suggestion: ColumnSuggestion | None = None


@dataclass
class PreflightReport:
    table: list[ReviewRow] = field(default_factory=list)
    id_check: KeyCheck = field(default_factory=KeyCheck)
    match_store: KeyCheck = field(default_factory=KeyCheck)
    match_source: KeyCheck = field(default_factory=KeyCheck)
    strict_violations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def preflight(
    *,
    store_rows: list[dict],
    source_records: list[dict],
    config: JobConfig,
) -> PreflightReport:
    rep = PreflightReport()

    if not store_rows:
        rep.errors.append("store has no rows")

    # Cached survey index: proves a `source_var` is real even when it is absent
    # from the (small) pull sample — an empty answer is ABSENT from the export
    # (probe F7), so sample-absence alone is not an error.
    index = dict(config.source.survey_index)

    # per-column review table ------------------------------------------------
    configured = config.binding.columns
    match = config.binding.match
    identity_store_col = match.store_col if match else None

    cols_for_suggest = [c.store_col for c in configured]
    suggestions_by_col: dict[str, ColumnSuggestion] = {}
    if store_rows and source_records:
        sugg = suggest_aliases(
            store_rows,
            source_records,
            exclude={identity_store_col} if identity_store_col else None,
            top=3,
        )
        for col in cols_for_suggest:
            best = sugg.get(col, [])
            if best:
                suggestions_by_col[col] = best[0]

    store_cols_present = {k for row in store_rows for k in row}
    source_vars_present = {k for rec in source_records for k in rec}

    for c in configured:
        if c.store_col == identity_store_col:
            rep.table.append(
                ReviewRow(c.store_col, c.source_var, 0, len(store_rows), 0.0, "identity_collision")
            )
            rep.errors.append(
                f"column {c.store_col!r} collides with the identity column"
            )
            continue
        if c.store_col not in store_cols_present:
            rep.table.append(
                ReviewRow(
                    c.store_col, c.source_var, 0, len(store_rows), 0.0, "missing_store_col"
                )
            )
            rep.errors.append(
                f"store column {c.store_col!r} not present on the store"
            )
            continue
        if c.source_var not in source_vars_present and c.source_var not in index:
            # Absent from the sample is normal: an empty answer is ABSENT from
            # the export (probe F7), so a valid var can be missing from a small
            # sample. Only a var the survey index does not know is an error.
            rep.table.append(
                ReviewRow(
                    c.store_col, c.source_var, 0, len(store_rows), 0.0, "missing_source_var"
                )
            )
            rep.errors.append(
                f"source var {c.source_var!r} not present in the pull sample "
                "or the survey index"
            )
            continue

        # Value comparison against the *declared* binding.
        store_vals = [str(r.get(c.store_col)) for r in store_rows if r.get(c.store_col) is not None]
        src_vals = [str(r.get(c.source_var)) for r in source_records if r.get(c.source_var) is not None]
        store_set = set(store_vals)
        src_set = set(src_vals)
        exact = len(store_set & src_set)
        norm = len({_normalize(v) for v in store_vals} & {_normalize(v) for v in src_vals})
        total = len(store_vals) or 1
        if exact == len(store_vals):
            status = "ok"
        elif norm == len(store_vals):
            status = "format_differs"
        else:
            status = "partial"

        sug = suggestions_by_col.get(c.store_col)
        rep.table.append(
            ReviewRow(
                store_col=c.store_col,
                source_var=c.source_var,
                overlap=exact,
                total=len(store_vals),
                ratio=round(exact / total, 4),
                status=status,
                sample_store=(sug.sample_store if sug else None),
                sample_source=(sug.sample_source if sug else None),
                suggestion=sug,
            )
        )

    # id column --------------------------------------------------------------
    rep.id_check = check_id_column(store_rows, config.store.id_column)
    if not rep.id_check.exists:
        rep.errors.append(
            f"id column {config.store.id_column!r} does not exist on the store"
        )
    elif not rep.id_check.unique:
        rep.errors.append(
            f"id column {config.store.id_column!r} is not unique "
            f"({len(rep.id_check.duplicates)} duplicated value(s))"
        )

    # match key --------------------------------------------------------------
    if match:
        rep.match_store = check_match_key(store_rows, match.store_col)
        rep.match_source = check_match_key(source_records, match.source_var)
        if not rep.match_store.unique:
            rep.errors.append(
                f"match key {match.store_col!r} is not unique in the store"
            )
        if not rep.match_source.unique:
            rep.errors.append(
                f"match key {match.source_var!r} is not unique in the pull"
            )

    # strict mode ------------------------------------------------------------
    if config.binding.mode == "strict":
        rep.strict_violations = strict_violations(configured)
        rep.errors.extend(rep.strict_violations)

    # pairing gate (fail closed: unpaired / stale / empty pull_term) ---------
    rep.errors.extend(_pairing_errors(config))

    return rep
