"""Legacy store adoption: backfill the match column (`SbjNum`) once.

When the target store predates the match column, it has no `SbjNum`. Adoption
matches each legacy row to a source record on the **content columns** and, only
when the match is 1:1 unique, writes that record's `SbjNum` into the row (full
payload via `UpdateRow`). Anything else is quarantined — never guessed (§4.5).

This is the highest-risk step: dry-run first, never run automatically.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from store_sync.core.config import ColumnBinding


@dataclass
class AdoptionAction:
    id_value: object
    sbjnum: str
    payload: dict


@dataclass
class AdoptionResult:
    actions: list[AdoptionAction] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)
    already_adopted: list[object] = field(default_factory=list)


def _s(value) -> str:
    return "" if value is None else str(value)


def _content_key(row: dict, columns: list[ColumnBinding], *, store_side: bool) -> tuple:
    out = []
    for c in columns:
        col = c.store_col if store_side else c.source_var
        out.append(_s(row.get(col)))
    return tuple(out)


def plan_adoption(
    *,
    store_rows: list[dict],
    source_records: list[dict],
    columns: list[ColumnBinding],
    match_store_col: str,
    match_source_var: str,
    id_column: str,
) -> AdoptionResult:
    """Plan (do not execute) a one-time `SbjNum` adoption."""
    result = AdoptionResult()

    # Index source by content signature; >1 source for a signature = ambiguous.
    by_content: dict[tuple, list[dict]] = defaultdict(list)
    for rec in source_records:
        by_content[_content_key(rec, columns, store_side=False)].append(rec)

    for row in store_rows:
        existing = _s(row.get(match_store_col))
        if existing:
            result.already_adopted.append(row.get(id_column))
            continue

        key = _content_key(row, columns, store_side=True)
        candidates = by_content.get(key, [])

        if len(candidates) != 1:
            result.quarantined.append(
                {
                    "id_value": row.get(id_column),
                    "row": row,
                    "reason": "no unique source match"
                    if not candidates
                    else f"{len(candidates)} source matches",
                }
            )
            continue

        rec = candidates[0]
        sbjnum = _s(rec.get(match_source_var))
        if not sbjnum:
            result.quarantined.append(
                {"id_value": row.get(id_column), "row": row, "reason": "source has no match value"}
            )
            continue

        payload = dict(row)
        payload[match_store_col] = sbjnum
        payload[id_column] = row.get(id_column)
        result.actions.append(
            AdoptionAction(id_value=row.get(id_column), sbjnum=sbjnum, payload=payload)
        )

    return result
