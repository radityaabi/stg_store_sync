"""Tests for store_sync.core.writer — apply a Plan against a mocked client.

Verifies: add sends the full row + its assigned tag, dry-run writes nothing,
only adds are auto-applied, CAS update apply (fresh vs stale), throttle + retry.
"""
from __future__ import annotations

import pytest

from store_sync.core.planner import Action, Plan
from store_sync.core.stg_client import STGError
from store_sync.core.writer import apply_plan, apply_updates


class FakeClient:
    def __init__(self, *, store_after=None, fail_on=None, fail_times=0):
        self.calls = []
        self.store_after = store_after
        self.fail_on = fail_on or set()
        self.fail_times = fail_times
        self._failures = 0
        self._exception = STGError("transient")
        self._rows = list(store_after or [])

    def add_row(self, store_id, values):
        return self._maybe_fail("add_row", store_id, values)

    def update_row(self, store_id, values):
        return self._maybe_fail("update_row", store_id, values)

    def get_store(self, store_id):
        return list(self._rows)

    def _maybe_fail(self, name, store_id, values):
        self.calls.append((name, store_id, values))
        if name in self.fail_on and self._failures < self.fail_times:
            self._failures += 1
            raise self._exception
        return {"Done": True}


def plan_with(*actions, next_tag=1):
    return Plan(actions=list(actions), next_tag=next_tag)


def upd(match, id_value, old, new, col="Q32"):
    return Action(
        "update", match, "No", id_value,
        {"No": id_value, "SbjNum": match, col: new},
        diffs=[{"col": col, "old": old, "new": new}],
    )


class TestDryRun:
    def test_dry_run_writes_nothing(self):
        c = FakeClient()
        p = plan_with(Action("add", "1", "No", 5, {"No": 5, "SbjNum": "1"}))
        res = apply_plan(c, 17620, p, dry_run=True)
        assert c.calls == []
        assert res.applied == []
        assert len(res.planned) == 1

    def test_dry_run_reports_planned_adds_only(self):
        c = FakeClient()
        p = plan_with(
            Action("add", "1", "No", 5, {"No": 5, "SbjNum": "1"}),
            upd("2", 2, "a", "b"),
            Action("same", "3", "No", 3, {}),
        )
        res = apply_plan(c, 17620, p, dry_run=True)
        assert res.counts == {"add": 1}


class TestApplyAddsOnly:
    def test_add_uses_add_row_with_payload(self):
        c = FakeClient()
        p = plan_with(Action("add", "1", "No", 5, {"No": 5, "SbjNum": "1", "X": ""}))
        res = apply_plan(c, 17620, p, dry_run=False)
        # `X=""` is dropped: STG rejects "" for numeric columns and applies the
        # per-type default for an omitted column (see TestEmptyValueOmission).
        assert c.calls[0] == ("add_row", 17620, {"No": 5, "SbjNum": "1"})
        assert res.counts["add"] == 1

    def test_update_actions_are_not_applied(self):
        c = FakeClient()
        p = plan_with(upd("2", 2, "a", "b"))
        res = apply_plan(c, 17620, p, dry_run=False)
        assert c.calls == []
        assert res.applied == []

    def test_same_and_conflict_actions_skipped(self):
        c = FakeClient()
        p = plan_with(
            Action("same", "1", "No", 1, {}),
            Action("conflict", "2", "No", None, {}),
        )
        res = apply_plan(c, 17620, p, dry_run=False)
        assert c.calls == []
        assert res.counts.get("same", 0) == 0


class TestEmptyValueOmission:
    """`""` values are omitted from the wire payload.

    STG rejects `""` for numeric columns (`500 ... of type efyNumber`) and
    applies the per-type default for any column left out of the request: `""`
    for text, `0` for numeric. Sending every column with `""` therefore crashed
    on the first numeric column; omitting empties is the type-agnostic fix.
    """

    def test_add_omits_empty_values(self):
        c = FakeClient()
        p = plan_with(Action("add", "1", "No", 5, {"No": 5, "SbjNum": "1", "A": "", "B": "x"}))
        apply_plan(c, 17620, p, dry_run=False)
        assert c.calls[0] == ("add_row", 17620, {"No": 5, "SbjNum": "1", "B": "x"})

    def test_add_keeps_id_even_if_blank(self):
        # The id column must never be dropped, even when blank — a row without
        # its tag is unreachable by id/DeleteRow.
        c = FakeClient()
        p = plan_with(Action("add", "1", "No", 5, {"No": 5, "SbjNum": "", "A": ""}))
        apply_plan(c, 17620, p, dry_run=False)
        assert c.calls[0][2] == {"No": 5}

    def test_update_omits_empty_values(self):
        c = FakeClient(store_after=[{"No": 2, "SbjNum": "2", "Q32": "22", "Empty": ""}])
        res = apply_updates(c, 17620, [upd("2", 2, "22", "")], id_column="No")
        assert res.counts["update"] == 1
        sent = c.calls[0][2]
        assert "Q32" not in sent       # cleared value omitted
        assert "Empty" not in sent     # blank store column omitted
        assert sent["No"] == 2

    def test_update_keeps_id_even_if_blank(self):
        c = FakeClient(store_after=[{"No": 2, "SbjNum": "2", "Q32": "22"}])
        res = apply_updates(c, 17620, [upd("2", 2, "22", "")], id_column="No")
        assert c.calls[0][2]["No"] == 2


class TestRetry:
    def test_retries_failed_add(self):
        c = FakeClient(fail_on={"add_row"}, fail_times=1)
        p = plan_with(Action("add", "1", "No", 5, {"No": 5, "SbjNum": "1"}))
        res = apply_plan(c, 17620, p, dry_run=False, max_retries=3, backoff=0)
        assert res.counts["add"] == 1
        assert len([x for x in c.calls if x[0] == "add_row"]) == 2

    def test_gives_up_and_reports_error(self):
        c = FakeClient(fail_on={"add_row"}, fail_times=99)
        p = plan_with(Action("add", "1", "No", 5, {"No": 5}))
        res = apply_plan(c, 17620, p, dry_run=False, max_retries=2, backoff=0)
        assert res.counts.get("add", 0) == 0
        assert len(res.errors) == 1


def test_stop_before_second_action_keeps_first():
    c = FakeClient()
    p = plan_with(Action("add", "1", "No", 1, {"No": 1}),
                  Action("add", "2", "No", 2, {"No": 2}))
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 1   # allow the first, stop before the second

    res = apply_plan(c, 17620, p, dry_run=False, should_stop=stop)
    assert res.cancelled is True
    assert res.counts["add"] == 1


class TestThrottle:
    def test_throttle_called_between_adds(self, monkeypatch):
        slept = []
        import store_sync.core.writer as w

        monkeypatch.setattr(w.time, "sleep", lambda s: slept.append(s))
        c = FakeClient()
        p = plan_with(
            Action("add", "1", "No", 5, {"No": 5}),
            Action("add", "2", "No", 6, {"No": 6}),
        )
        apply_plan(c, 17620, p, dry_run=False, throttle=0.01)
        assert slept == [0.01]


class TestApplyUpdates:
    def test_fresh_proposal_is_applied(self):
        c = FakeClient(store_after=[{"No": 2, "SbjNum": "2", "Q32": "22", "Extra": "keep"}])
        res = apply_updates(c, 17620, [upd("2", 2, "22", "99")], id_column="No")
        assert res.counts["update"] == 1
        assert res.stale == []
        sent = c.calls[0][2]
        assert sent["Q32"] == "99"
        assert sent["Extra"] == "keep"   # non-diffed store value retained
        assert sent["No"] == 2

    def test_stale_proposal_is_skipped(self):
        # store row no longer matches the recorded old value
        c = FakeClient(store_after=[{"No": 2, "SbjNum": "2", "Q32": "manual-edit"}])
        res = apply_updates(c, 17620, [upd("2", 2, "22", "99")], id_column="No")
        assert c.calls == []
        assert len(res.stale) == 1
        assert res.counts.get("update", 0) == 0
        assert res.errors

    def test_missing_row_is_stale(self):
        c = FakeClient(store_after=[])
        res = apply_updates(c, 17620, [upd("2", 2, "22", "99")], id_column="No")
        assert c.calls == []
        assert len(res.stale) == 1

    def test_empty_actions_is_noop(self):
        c = FakeClient(store_after=[{"No": 2, "Q32": "22"}])
        res = apply_updates(c, 17620, [], id_column="No")
        assert c.calls == []
        assert res.counts == {}

    def test_transport_error_on_read_is_recorded(self):
        import requests

        class BadRead(FakeClient):
            def get_store(self, store_id):
                raise requests.ConnectionError("dropped")

        c = BadRead()
        res = apply_updates(c, 17620, [upd("2", 2, "22", "99")], id_column="No", max_retries=2, backoff=0)
        assert res.applied == []
        assert res.errors


class TestTransportRetry:
    def test_retries_transport_error(self):
        import requests
        c = FakeClient(fail_on={"add_row"}, fail_times=1)
        c._exception = requests.ConnectionError("dropped")
        p = plan_with(Action("add", "1", "No", 5, {"No": 5}))
        res = apply_plan(c, 17620, p, dry_run=False, max_retries=3, backoff=0)
        assert res.counts["add"] == 1
