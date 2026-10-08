"""Tests for store_sync.core.writer — apply a Plan against a mocked client.

Verifies: full-row payloads, id never rewritten on update, add sends its tag,
delete by tag value + re-read confirmation, dry-run writes nothing, throttle +
retry.
"""
from __future__ import annotations

import pytest

from store_sync.core.planner import Action, Plan
from store_sync.core.stg_client import STGError
from store_sync.core.writer import apply_plan


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

    def delete_row(self, store_id, tag):
        return self._maybe_fail("delete_row", store_id, tag, tag=tag)

    def get_store(self, store_id):
        return list(self._rows)

    def _maybe_fail(self, name, store_id, values, tag=None):
        self.calls.append((name, store_id, values))
        if name in self.fail_on and self._failures < self.fail_times:
            self._failures += 1
            raise self._exception
        if name == "delete_row":
            self._rows = [r for r in self._rows if str(r.get("No")) != str(tag)]
        return {"Done": True}


def plan_with(*actions, next_tag=1):
    return Plan(actions=list(actions), next_tag=next_tag)


class StubbornClient:
    """A delete that silently does nothing — the row survives the re-read."""

    def __init__(self, rows):
        self.rows = rows

    def delete_row(self, store_id, tag):
        return {"Done": True}

    def get_store(self, store_id):
        return list(self.rows)


class TestDryRun:
    def test_dry_run_writes_nothing(self):
        c = FakeClient()
        p = plan_with(Action("add", "1", "No", 5, {"No": 5, "SbjNum": "1"}))
        res = apply_plan(c, 17620, p, dry_run=True)
        assert c.calls == []
        assert res.applied == []
        assert len(res.planned) == 1

    def test_dry_run_reports_planned(self):
        c = FakeClient()
        p = plan_with(
            Action("add", "1", "No", 5, {"No": 5, "SbjNum": "1"}),
            Action("update", "2", "No", 2, {"No": 2, "SbjNum": "2"}),
            Action("delete", "3", "No", 3),
        )
        res = apply_plan(c, 17620, p, dry_run=True)
        assert res.counts == {"add": 1, "update": 1, "delete": 1}


class TestApply:
    def test_add_uses_add_row_with_full_payload(self):
        c = FakeClient()
        p = plan_with(Action("add", "1", "No", 5, {"No": 5, "SbjNum": "1", "X": ""}))
        res = apply_plan(c, 17620, p, dry_run=False)
        assert c.calls[0] == ("add_row", 17620, {"No": 5, "SbjNum": "1", "X": ""})
        assert res.counts["add"] == 1

    def test_update_uses_update_row(self):
        c = FakeClient()
        p = plan_with(Action("update", "2", "No", 2, {"No": 2, "SbjNum": "2"}))
        apply_plan(c, 17620, p, dry_run=False)
        assert c.calls[0][0] == "update_row"

    def test_update_preserves_id_value(self):
        c = FakeClient()
        action = Action("update", "2", "No", 2, {"No": 2, "SbjNum": "2"})
        apply_plan(c, 17620, plan_with(action), dry_run=False)
        sent = c.calls[0][2]
        assert sent["No"] == 2

    def test_delete_uses_tag_value_then_verifies(self):
        c = FakeClient(store_after=[{"No": 1}, {"No": 2}, {"No": 3}])
        p = plan_with(Action("delete", "3", "No", 3, {}))
        res = apply_plan(c, 17620, p, dry_run=False, id_column="No")
        deletes = [x for x in c.calls if x[0] == "delete_row"]
        assert deletes[0] == ("delete_row", 17620, 3)
        # tag 3 was present and is now gone -> verified
        assert res.unverified_deletes == []
        assert res.counts["delete"] == 1

    def test_delete_not_verified_when_row_survives(self):
        c = StubbornClient(rows=[{"No": 3}])
        p = plan_with(Action("delete", "3", "No", 3, {}))
        res = apply_plan(c, 17620, p, dry_run=False, id_column="No")
        assert 3 in res.unverified_deletes
        assert res.errors

    def test_unknown_id_column_does_not_verify_with_hardcoded_No(self):
        c = FakeClient(store_after=[{"No": 3}])
        p = plan_with(Action("delete", "3", "No", 3, {}))
        res = apply_plan(c, 17620, p, dry_run=False, id_column=None)
        assert any("id column unknown" in e for e in res.errors)

    def test_same_actions_skipped(self):
        c = FakeClient()
        p = plan_with(Action("same", "1", "No", 1, {}))
        res = apply_plan(c, 17620, p, dry_run=False)
        assert c.calls == []
        assert res.counts.get("same", 0) == 0

    def test_conflict_actions_not_written(self):
        c = FakeClient()
        p = plan_with(Action("conflict", "1", "No", None, {}))
        res = apply_plan(c, 17620, p, dry_run=False)
        assert c.calls == []


class TestRetry:
    def test_retries_failed_write(self):
        c = FakeClient(fail_on={"update_row"}, fail_times=1)
        p = plan_with(Action("update", "2", "No", 2, {"No": 2, "SbjNum": "2"}))
        res = apply_plan(c, 17620, p, dry_run=False, max_retries=3, backoff=0)
        assert res.counts["update"] == 1
        assert len([x for x in c.calls if x[0] == "update_row"]) == 2

    def test_gives_up_and_reports_error(self):
        c = FakeClient(fail_on={"add_row"}, fail_times=99)
        p = plan_with(Action("add", "1", "No", 5, {"No": 5}))
        res = apply_plan(c, 17620, p, dry_run=False, max_retries=2, backoff=0)
        assert res.counts.get("add", 0) == 0
        assert len(res.errors) == 1


class TestTransportRetry:
    def test_retries_transport_error(self):
        import requests
        c = FakeClient(fail_on={"update_row"}, fail_times=1)
        # make the failure a transport error instead of STGError
        c._exception = requests.ConnectionError("dropped")
        p = plan_with(Action("update", "2", "No", 2, {"No": 2}))
        res = apply_plan(c, 17620, p, dry_run=False, max_retries=3, backoff=0)
        assert res.counts["update"] == 1

    def test_delete_verify_transport_error_is_recorded(self):
        import requests

        class BadReadClient(FakeClient):
            def get_store(self, store_id):
                raise requests.ConnectionError("dropped")

        c = BadReadClient(store_after=[{"No": 3}])
        p = plan_with(
            Action("add", "1", "No", 5, {"No": 5}),
            Action("update", "2", "No", 2, {"No": 2}),
            Action("delete", "3", "No", 3, {}),
        )
        res = apply_plan(c, 17620, p, dry_run=False, max_retries=2, backoff=0, id_column="No")
        # must return normally, preserving the applied add/update counts
        assert res.counts["add"] == 1
        assert res.counts["update"] == 1
        assert res.errors
        assert 3 in res.unverified_deletes


def test_stop_before_second_action_keeps_first():
    c = FakeClient()
    p = plan_with(Action("add", "1", "No", 1, {"No": 1}),
                  Action("update", "2", "No", 2, {"No": 2}))
    calls = {"n": 0}
    def stop():
        calls["n"] += 1
        return calls["n"] > 1   # allow the first, stop before the second
    res = apply_plan(c, 17620, p, dry_run=False, should_stop=stop)
    assert res.cancelled is True
    assert res.counts["add"] == 1 and res.counts.get("update", 0) == 0


class TestThrottle:
    def test_throttle_called_between_writes(self, monkeypatch):
        slept = []
        import store_sync.core.writer as w

        monkeypatch.setattr(w.time, "sleep", lambda s: slept.append(s))
        c = FakeClient()
        p = plan_with(
            Action("add", "1", "No", 5, {"No": 5}),
            Action("update", "2", "No", 2, {"No": 2}),
        )
        apply_plan(c, 17620, p, dry_run=False, throttle=0.01)
        assert slept == [0.01]
