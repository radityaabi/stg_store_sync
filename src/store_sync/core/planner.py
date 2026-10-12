"""Planner: diff pulled records against store rows by match key.

Produces a `Plan` of `Action`s (add / update / same / conflict) plus
quarantined records, following the fail-closed rules in spec §4.4 and the value
rules in §5.3 (empty is a value; comparison is byte-for-byte after str()).

Only `add` is applied automatically; an `update` is a **proposal** carrying the
per-column diffs and is written only on operator approval. There is no delete.

Lookup is by key-set difference, never by position or timestamp (§4.2).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from store_sync.core.binding import project
from store_sync.core.config import ColumnBinding


@dataclass
class Action:
    kind: str  # "add" | "update" | "same" | "conflict"
    match_value: str
    id_column: str = ""
    id_value: object = None
    payload: dict = field(default_factory=dict)
    reason: str = ""
    side: str = ""  # "store" | "source" on conflicts; "" otherwise
    diffs: list = field(default_factory=list)  # [{"col","old","new"}] for update


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)
    next_tag: int = 1
    suppressed: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        c = {"add": 0, "update": 0, "same": 0, "conflict": 0}
        for a in self.actions:
            c[a.kind] = c.get(a.kind, 0) + 1
        return c

    @property
    def blocked(self) -> bool:
        """True when a conflict must halt the job (store-side duplicates)."""
        return any(a.kind == "conflict" and a.side == "store" for a in self.actions)

    @property
    def ok(self) -> bool:
        return not self.blocked


def _s(value) -> str:
    return "" if value is None else str(value)


def _dups(values: list[str]) -> set[str]:
    counts = Counter(values)
    return {v for v, n in counts.items() if n > 1}


def _next_tag(store_rows: list[dict], id_column: str) -> int:
    nums = []
    for r in store_rows:
        v = r.get(id_column)
        if v is None:
            continue
        sv = str(v)
        if sv.isdigit():
            nums.append(int(sv))
    return (max(nums) + 1) if nums else 1


def plan(
    *,
    store_rows: list[dict],
    source_records: list[dict],
    match: ColumnBinding,
    columns: list[ColumnBinding],
    id_column: str,
    store_columns: list[str] | None = None,
    blacklisted: set[str] | None = None,
) -> Plan:
    """Classify every source record and store row into a write plan.

    Only ``add`` and ``update`` are write-bearing; ``same`` is a no-op and
    ``conflict`` halts (store side) or skips (source side). Store rows absent
    from the source are **left untouched** — the tool never deletes (spec §6).

    ``store_columns`` is the store's full column set. STG ``AddRow``/``UpdateRow``
    reset any omitted column to its default, so payloads must carry every column:
    ADD blanks unmapped columns, UPDATE retains the existing row's values.

    ``blacklisted`` is the set of match keys the job suppresses (hand-deleted
    rows). A key in it is skipped and recorded in ``Plan.suppressed``; it produces
    no ``add`` action and is not counted.

    An ``update`` action carries a per-column ``diffs`` list
    (``[{"col", "old", "new"}]``) computed byte-for-byte on ``str()``; a row with
    no diffs is classified ``same`` and produces no write proposal.
    """
    p = Plan()
    p.next_tag = _next_tag(store_rows, id_column)
    blacklisted = {str(k) for k in (blacklisted or set())}
    if store_columns is None:
        store_columns = sorted({k for r in store_rows for k in r})

    # -- index the store by match key; duplicates are a hard conflict ---------
    store_by_key: dict[str, dict] = {}
    store_missing_match_col = False
    for row in store_rows:
        if match.store_col not in row:
            store_missing_match_col = True
            continue
        key = _s(row.get(match.store_col))
        store_by_key.setdefault(key, row)

    store_keys = [_s(r.get(match.store_col)) for r in store_rows if match.store_col in r]
    store_dups = _dups(store_keys)
    for key in sorted(store_dups):
        p.actions.append(
            Action("conflict", key, id_column,
                   reason="duplicate match key in store", side="store")
        )
    if store_missing_match_col:
        p.actions.append(
            Action("conflict", "", id_column,
                   reason="store row missing match column", side="store")
        )

    # -- index the source by match key ---------------------------------------
    src_by_key: dict[str, dict] = {}
    for rec in source_records:
        if match.source_var not in rec or _s(rec.get(match.source_var)) == "":
            p.quarantined.append(rec)
            continue
        key = _s(rec.get(match.source_var))
        src_by_key.setdefault(key, rec)

    src_keys = [_s(r.get(match.source_var)) for r in source_records if match.source_var in r and _s(r.get(match.source_var)) != ""]
    src_dups = _dups(src_keys)
    for key in sorted(src_dups):
        p.actions.append(
            Action("conflict", key, id_column,
                   reason="duplicate match key in source", side="source")
        )

    # -- classify ------------------------------------------------------------
    def _add_payload(rec: dict, key: str, id_value) -> dict:
        payload = {c: "" for c in store_columns}
        payload.update(project(columns, rec))
        payload[match.store_col] = key
        payload[id_column] = id_value
        return payload

    next_tag = p.next_tag
    for key in sorted(set(src_keys) - set(store_keys), key=lambda k: src_by_key[k].get(match.source_var, k)):
        if key in src_dups:
            continue
        if key in blacklisted:
            p.suppressed.append(key)
            continue
        p.actions.append(
            Action("add", key, id_column, next_tag, _add_payload(src_by_key[key], key, next_tag))
        )
        next_tag += 1

    for key in sorted(set(src_keys) & set(store_keys)):
        if key in src_dups or key in store_dups:
            continue
        rec = src_by_key[key]
        row = store_by_key[key]
        desired = project(columns, rec)
        id_value = row.get(id_column)
        payload = dict(row)  # full existing store row retains unmapped values
        payload.update(desired)
        payload[match.store_col] = key
        payload[id_column] = id_value
        diffs = [
            {"col": c.store_col, "old": _s(row.get(c.store_col)), "new": _s(desired[c.store_col])}
            for c in columns
            if _s(desired[c.store_col]) != _s(row.get(c.store_col))
        ]
        if diffs:
            p.actions.append(Action("update", key, id_column, id_value, payload, diffs=diffs))
        else:
            p.actions.append(Action("same", key, id_column, id_value, payload))

    # Store rows absent from the source are deliberately left untouched: the
    # tool never deletes (spec §6). A short/failed pull must not imply deletion.

    return p
