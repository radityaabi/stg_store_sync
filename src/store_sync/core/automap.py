"""Auto-map: fill the column grid from the store's columns + the survey index.

One click should be enough to get a job's columns populated. The inputs are
already available without a sample pull:

* the store's column names (a single `GetStore` — fast),
* the survey index (`answer var -> question base name`, from the survey XML,
  already cached by the Pairing action).

Matching is deliberately conservative — a wrong binding silently writes the
wrong value into a store column, so an ambiguous column is left **unmapped**
and reported, never guessed:

1. the store column is itself a known variable (`Q32 -> Q32`);
2. the store column is a question base name with exactly one answer variable
   (`QPM1 -> T_QPM1_1`); more than one answer var is ambiguous → unmapped;
3. in `merge` mode an existing binding's `source_var` wins for that column
   (the operator's manual work is never overwritten);
4. otherwise unmapped — the row is still emitted with an empty `source_var` so
   the operator sees exactly which columns need attention.

The result is a grid the operator still reviews and saves; nothing is persisted
here. No network, no store writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from store_sync.core.config import ColumnBinding


@dataclass
class AutoMapResult:
    """Outcome of `auto_map`: the new grid plus what needs attention."""

    columns: list[ColumnBinding] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)   # store cols with no var
    dropped: list[str] = field(default_factory=list)    # old cols not in store
    mapped: int = 0

    @property
    def total(self) -> int:
        return len(self.columns)


def _answer_vars(index: dict[str, str]) -> dict[str, list[str]]:
    """`base -> [answer vars]`, excluding base-name-to-itself entries."""
    by_base: dict[str, list[str]] = {}
    for var, base in index.items():
        if var == base:
            continue
        by_base.setdefault(base, []).append(var)
    return by_base


def auto_map(
    store_cols: Iterable[str],
    index: dict[str, str],
    *,
    skip: Iterable[str] = (),
    existing: Iterable[ColumnBinding] = (),
    merge: bool = True,
) -> AutoMapResult:
    """Build a column grid for `store_cols` from the survey `index`.

    `skip` holds columns the planner owns (the id column and the match store
    column) — they are not bindable and never appear in the grid.

    `merge=True` keeps an existing binding's `source_var` for the same store
    column (manual work wins); pass `False` to rebuild the grid outright.
    """
    skipped = {str(c) for c in skip if c}
    by_col = {c.store_col: c for c in existing if c.store_col}
    by_base = _answer_vars(index)

    res = AutoMapResult()
    seen: set[str] = set()

    for col in store_cols:
        col = str(col)
        if col in skipped or col in seen:
            continue
        seen.add(col)

        existing_var = (by_col.get(col).source_var if col in by_col else "") or ""
        if merge and existing_var:
            var = existing_var
        elif col in index:
            var = col
        else:
            candidates = sorted(set(by_base.get(col, [])))
            var = candidates[0] if len(candidates) == 1 else ""

        res.columns.append(
            ColumnBinding(
                source_var=var,
                store_col=col,
                pull_term=index.get(var, "") if var else "",
            )
        )
        if var:
            res.mapped += 1
        else:
            res.unmapped.append(col)

    for col in by_col:
        if col not in seen and col not in skipped:
            res.dropped.append(col)

    return res
