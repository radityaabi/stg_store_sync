"""Writer: execute a `Plan` against the store.

Rules (spec §6; probe F2/F4/F8):

* Dry-run writes **nothing** — it only reports what would happen.
* **Automation writes ADD only.** `apply_plan` executes `add` actions
  (`AddRow` with the full payload incl. the assigned id/tag value). `update`
  actions are *proposals* — they are written by `apply_updates` only on operator
  approval. `same` / `conflict` actions are skipped.
* `apply_updates` is **compare-and-swap**: it re-reads the store and, for each
  selected proposal, verifies every diffed column still holds its recorded `old`
  value before writing. A proposal whose row changed underneath is reported
  `stale` and skipped (so a manual store revision is never clobbered).
* There is no delete.
* Writes are throttled and retried on transient errors.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from requests.exceptions import (
    ChunkedEncodingError,
    ConnectionError as ReqConnectionError,
    Timeout,
)

from store_sync.core.planner import Action, Plan
from store_sync.core.stg_client import STGError

# Transient failures worth retrying: STG API errors plus transport-level
# failures (dropped connection, timeout, truncated body). Anything else must
# propagate rather than be silently swallowed.
_WRITE_RETRYABLE = (STGError, ReqConnectionError, Timeout, ChunkedEncodingError)


@dataclass
class WriteResult:
    planned: list = field(default_factory=list)
    applied: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    cancelled: bool = False


@dataclass
class UpdateResult:
    applied: list = field(default_factory=list)
    stale: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)


def apply_plan(
    client,
    store_id,
    plan: Plan,
    *,
    dry_run: bool = True,
    throttle: float = 0.0,
    max_retries: int = 3,
    backoff: float = 1.0,
    id_column: str | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> WriteResult:
    """Apply (or dry-run) the **add** actions in `plan`.

    Only `add` is automated; `update`/`same`/`conflict` are ignored here.
    When `should_stop()` returns true, stop before the next action and mark
    `WriteResult.cancelled`; already-applied actions stay applied.
    """
    res = WriteResult()
    id_col = id_column or (plan.actions[0].id_column if plan.actions else None)

    todo = [a for a in plan.actions if a.kind == "add"]
    res.planned = list(todo)

    if dry_run:
        for a in todo:
            res.counts[a.kind] = res.counts.get(a.kind, 0) + 1
        return res

    wrote_since_sleep = False
    for action in todo:
        if should_stop and should_stop():
            res.cancelled = True
            break
        if wrote_since_sleep and throttle:
            time.sleep(throttle)
        wrote_since_sleep = False

        try:
            _retry(
                client.add_row, store_id, _full_payload(action, id_col),
                max_retries=max_retries, backoff=backoff,
            )
        except _WRITE_RETRYABLE as e:
            res.errors.append(f"{action.kind} {action.match_value}: {e}")
            continue

        wrote_since_sleep = True
        res.applied.append(action)
        res.counts[action.kind] = res.counts.get(action.kind, 0) + 1

    return res


def apply_updates(
    client,
    store_id,
    actions: list[Action],
    *,
    id_column: str,
    throttle: float = 0.0,
    max_retries: int = 3,
    backoff: float = 1.0,
    should_stop: Callable[[], bool] | None = None,
) -> UpdateResult:
    """Apply approved `update` proposals with compare-and-swap.

    The store is re-read once. For each proposal the current row is located by
    its id value; every diffed column must still equal its recorded `old` value,
    else the proposal is `stale` and skipped (a manual store revision wins). The
    written payload is the **current** row overlaid with the proposal's new
    values, so non-diffed columns keep whatever the operator set in the store.
    """
    res = UpdateResult()
    if not actions:
        return res

    try:
        current = _retry(client.get_store, store_id, max_retries=max_retries, backoff=backoff)
    except _WRITE_RETRYABLE as e:
        res.errors.append(f"update verification failed: {e}")
        return res

    by_id = {str(r.get(id_column)): r for r in current}

    wrote_since_sleep = False
    for action in actions:
        if should_stop and should_stop():
            break
        row = by_id.get(str(action.id_value))
        if row is None:
            res.stale.append(action)
            res.errors.append(f"update {action.match_value}: row no longer present")
            continue
        if any(str(row.get(d["col"])) != d["old"] for d in action.diffs):
            res.stale.append(action)
            res.errors.append(f"update {action.match_value}: row changed since proposal")
            continue

        if wrote_since_sleep and throttle:
            time.sleep(throttle)
        wrote_since_sleep = False

        payload = dict(row)
        for d in action.diffs:
            payload[d["col"]] = d["new"]
        payload[id_column] = action.id_value
        try:
            _retry(
                client.update_row, store_id, payload,
                max_retries=max_retries, backoff=backoff,
            )
        except _WRITE_RETRYABLE as e:
            res.errors.append(f"update {action.match_value}: {e}")
            continue

        wrote_since_sleep = True
        res.applied.append(action)
        res.counts["update"] = res.counts.get("update", 0) + 1

    return res


def _full_payload(action, id_column: str | None) -> dict:
    payload = dict(action.payload)
    if id_column is not None:
        payload[id_column] = action.id_value
    return payload


def _retry(fn, *args, max_retries, backoff):
    last: Exception | None = None
    for attempt in range(1, max(1, max_retries) + 1):
        try:
            return fn(*args)
        except _WRITE_RETRYABLE as e:  # transient server/transport error
            last = e
            if attempt < max_retries:
                time.sleep(backoff * attempt)
    if last is not None:
        raise last
