"""Writer: execute a `Plan` against the store.

Rules (spec §4.3, §6; probe F2/F4/F8):

* Dry-run writes **nothing** — it only reports what would happen.
* ADD → `AddRow` with the full payload incl. the assigned id/tag value.
* UPDATE → `UpdateRow` with the full payload; the id value is sent unchanged.
* DELETE → `DeleteRow/{id}/{tag}`, then **re-read the store** to confirm the row
  is gone (`DeleteRow` always returns `{Done: true}`).
* `same` / `conflict` actions are skipped.
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

from store_sync.core.planner import Plan
from store_sync.core.stg_client import STGError

_WRITE_KINDS = {"add", "update", "delete"}

# Transient failures worth retrying: STG API errors plus transport-level
# failures (dropped connection, timeout, truncated body). Anything else must
# propagate rather than be silently swallowed.
_WRITE_RETRYABLE = (STGError, ReqConnectionError, Timeout, ChunkedEncodingError)


@dataclass
class WriteResult:
    planned: list = field(default_factory=list)
    applied: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unverified_deletes: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    cancelled: bool = False


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
    """Apply (or dry-run) the write actions in `plan`.

    When `should_stop()` returns true, stop before the next action and mark
    `WriteResult.cancelled`; already-applied actions stay tracked.
    """
    res = WriteResult()
    id_col = id_column or (plan.actions[0].id_column if plan.actions else None)

    todo = [a for a in plan.actions if a.kind in _WRITE_KINDS]
    res.planned = list(todo)

    if dry_run:
        for a in todo:
            res.counts[a.kind] = res.counts.get(a.kind, 0) + 1
        return res

    deleted_tags: list = []
    wrote_since_sleep = False
    for action in todo:
        if should_stop and should_stop():
            res.cancelled = True
            break
        if wrote_since_sleep and throttle:
            time.sleep(throttle)
        wrote_since_sleep = False

        try:
            if action.kind == "add":
                _retry(client.add_row, store_id, _full_payload(action, id_col), max_retries=max_retries, backoff=backoff)
            elif action.kind == "update":
                _retry(client.update_row, store_id, _full_payload(action, id_col), max_retries=max_retries, backoff=backoff)
            elif action.kind == "delete":
                _retry(client.delete_row, store_id, action.id_value, max_retries=max_retries, backoff=backoff)
                deleted_tags.append((action.match_value, action.id_value))
            else:  # pragma: no cover - guarded above
                continue
        except _WRITE_RETRYABLE as e:
            res.errors.append(f"{action.kind} {action.match_value}: {e}")
            continue

        wrote_since_sleep = True
        res.applied.append(action)
        res.counts[action.kind] = res.counts.get(action.kind, 0) + 1

    # Verify deletes by re-reading the store (F4: DeleteRow always says Done).
    # With no declared id column we cannot map a tag back to a row: report it
    # explicitly rather than guessing a column name.
    if deleted_tags:
        if id_column is None:
            res.errors.append("id column unknown; delete verification skipped")
        else:
            try:
                actual = _retry(
                    client.get_store, store_id,
                    max_retries=max_retries, backoff=backoff,
                )
            except _WRITE_RETRYABLE as e:
                # A dropped re-read must not escape: the writes already applied
                # stand, and we simply cannot confirm the deletes this run.
                res.errors.append(f"delete verification failed: {e}")
                for _match_value, tag in deleted_tags:
                    res.unverified_deletes.append(tag)
            else:
                present = {str(r.get(id_column)) for r in actual}
                for match_value, tag in deleted_tags:
                    if str(tag) in present:
                        res.unverified_deletes.append(tag)
                        res.errors.append(f"delete {match_value!r} (tag {tag}) not verified")

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
