"""Tests for store_sync.core.sync — the end-to-end orchestrator.

Wires §7.2: survey index -> pull -> load store -> plan -> apply -> state.
"""
from __future__ import annotations

import pytest

from store_sync.core.config import (
    BindingConfig,
    ColumnBinding,
    JobConfig,
    SourceConfig,
    StoreConfig,
    WriteConfig,
)
from store_sync.core.state import State
from store_sync.core.stg_client import STGError
from store_sync.core.sync import run_sync

SURVEY_XML = """<?xml version="1.0"?>
<Survey>
  <SQ><QuestionID>762</QuestionID><VariableName>QPM1</VariableName></SQ>
  <STo><QuestionID>762</QuestionID><VariableName>T_QPM1_1</VariableName></STo>
  <SQ><QuestionID>900</QuestionID><VariableName>Q32</VariableName></SQ>
</Survey>
"""


def config(*, dry_run=True, id_column="No", delete_policy="full_history"):
    return JobConfig(
        job="link1",
        store=StoreConfig(id=17620, id_column=id_column),
        source=SourceConfig(survey_id="SURV"),
        binding=BindingConfig(
            match=ColumnBinding("SbjNum", "SbjNum"),
            columns=[
                ColumnBinding("T_QPM1_1", "I7_1_2"),
                ColumnBinding("Q32", "Q32"),
            ],
        ),
        write=WriteConfig(dry_run=dry_run, delete_policy=delete_policy),
    )


class FakeClient:
    def __init__(self, *, store=None, subjects=None):
        self.store = list(store or [])
        self.subjects = list(subjects or [])
        self.calls = []
        self.id_kwargs = {}

    def get_survey_xml(self, survey_id):
        return SURVEY_XML

    def survey_interview_ids(self, survey_id, **kw):
        self.id_kwargs = kw
        return [s["SubjectID"] for s in self.subjects]

    def simple_export(self, survey_id, ids, *, variable_names=None, **kw):
        self.calls.append(("export", variable_names))
        return [s for s in self.subjects if s["SubjectID"] in ids]

    def get_store(self, store_id):
        return list(self.store)

    def add_row(self, store_id, values):
        self.calls.append(("add", values))
        self.store.append(dict(values))
        return {"Done": True}

    def update_row(self, store_id, values):
        self.calls.append(("update", values))
        return {"Done": True}

    def delete_row(self, store_id, tag):
        self.calls.append(("delete", tag))
        self.store = [r for r in self.store if str(r.get("No")) != str(tag)]
        return {"Done": True}


def subj(sid, pairs):
    return {"SubjectID": sid, "Columns": [{"Var": v, "Value": x} for v, x in pairs]}


def subjects():
    return [
        subj("1", [("SbjNum", 101), ("T_QPM1_1", "Alice"), ("Q32", 11)]),
        subj("2", [("SbjNum", 102), ("T_QPM1_1", "BOB"), ("Q32", 99)]),
        subj("3", [("SbjNum", 104), ("T_QPM1_1", "Dave"), ("Q32", 44)]),
    ]


def store_rows():
    return [
        {"No": 1, "SbjNum": "101", "I7_1_2": "Alice", "Q32": 11},
        {"No": 2, "SbjNum": "102", "I7_1_2": "Bob", "Q32": 22},
        {"No": 3, "SbjNum": "103", "I7_1_2": "Carol", "Q32": 33},
    ]


@pytest.fixture
def st(tmp_path):
    s = State(tmp_path / "state.db")
    yield s
    s.close()


class TestDryRun:
    def test_dry_run_writes_nothing_and_records_run(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=True), client=c, state=st)
        assert [x for x in c.calls if x[0] in ("add", "update", "delete")] == []
        assert res.dry_run is True
        assert res.counts["add"] == 1
        assert res.counts["update"] == 1
        assert res.counts["delete"] == 1
        runs = st.recent_runs("link1")
        assert len(runs) == 1
        assert runs[0]["dry_run"] is True

    def test_dry_run_plan_summary(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=True), client=c, state=st)
        assert res.plan.counts == {"add": 1, "update": 1, "same": 1, "delete": 1, "conflict": 0}


class TestApply:
    def test_real_run_writes(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        kinds = [x[0] for x in c.calls]
        assert "add" in kinds and "update" in kinds and "delete" in kinds
        assert res.counts["add"] == 1
        assert res.counts["update"] == 1
        assert res.counts["delete"] == 1

    def test_run_status_recorded(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        run = st.recent_runs("link1")[0]
        assert run["status"] == "ok"
        assert run["counts"]["add"] == 1

    def test_tracking_written_for_written_rows(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        # updated + added keys tracked, deleted untracked
        tracked = st.tracked_keys("link1")
        assert "104" in tracked
        assert "102" in tracked
        assert "103" not in tracked


class TestConflicts:
    def test_store_duplicate_key_halts_before_write(self, st):
        rows = store_rows() + [{"No": 9, "SbjNum": "101", "I7_1_2": "x", "Q32": 1}]
        c = FakeClient(store=rows, subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st)
        assert [x for x in c.calls if x[0] in ("add", "update", "delete")] == []
        assert res.status == "blocked"
        assert res.errors

    def test_source_duplicate_skips_and_status_partial(self, st):
        subs = subjects() + [
            subj("4", [("SbjNum", 104), ("T_QPM1_1", "Dup"), ("Q32", 1)])
        ]
        c = FakeClient(store=store_rows(), subjects=subs)
        res = run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        # source-side duplicate is skipped, but writes still happen -> partial
        assert res.status == "partial"
        kinds = [x[0] for x in c.calls]
        assert "update" in kinds and "delete" in kinds
        assert "add" not in kinds  # the duplicate key 104 is skipped
        run = st.recent_runs("link1")[0]
        assert run["status"] == "partial"

    def test_conflicts_persisted(self, st):
        rows = store_rows() + [{"No": 9, "SbjNum": "101", "I7_1_2": "x", "Q32": 1}]
        c = FakeClient(store=rows, subjects=subjects())
        run_sync(config(dry_run=False), client=c, state=st)
        run = st.recent_runs("link1")[0]
        conflicts = st.conflicts_for_run(run["id"])
        assert any(cf["match_value"] == "101" for cf in conflicts)

    def test_write_error_recorded(self, st):
        class Boom(FakeClient):
            def update_row(self, store_id, values):
                raise STGError("server exploded")

        c = Boom(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st, backoff=0)
        assert res.errors
        assert res.status in ("partial", "error")


def test_transport_error_leaves_run_finished(st):
    import requests
    class Boom(FakeClient):
        def update_row(self, store_id, values):
            raise requests.ConnectionError("dropped")
    c = Boom(store=store_rows(), subjects=subjects())
    run_sync(config(dry_run=False), client=c, state=st, backoff=0)
    run = st.recent_runs("link1")[0]
    assert run["status"] in ("partial", "error")
    assert run["finished_at"] is not None


class TestCancellation:
    def test_cancelled_run_status_and_tracking(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        state = {"n": 0}
        def stop():
            state["n"] += 1
            return state["n"] > 1
        res = run_sync(config(dry_run=False), client=c, state=st, should_stop=stop)
        assert res.status == "cancelled"
        assert st.recent_runs("link1")[0]["status"] == "cancelled"


class TestPull:
    def test_uses_derived_filter(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=True), client=c, state=st)
        exports = [x for x in c.calls if x[0] == "export"]
        assert exports and exports[0][1] is not None


class TestReadiness:
    def test_window_and_date_type_passed(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        cfg = config(dry_run=True)
        cfg.source.window_days = 3
        cfg.source.date_type = "Upload"
        run_sync(cfg, client=c, state=st)
        assert c.id_kwargs["date_type"] == "Upload"
        assert c.id_kwargs["date_start"] is not None

    def test_missing_id_column_blocks_before_write(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False, id_column="Nope"), client=c, state=st)
        assert res.status == "blocked"
        assert [x for x in c.calls if x[0] in ("add", "update", "delete")] == []

    def test_empty_store_blocks(self, st):
        c = FakeClient(store=[], subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st)
        assert res.status == "blocked"
