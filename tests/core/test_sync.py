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
from store_sync.core.fetcher import parse_survey_index
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

_PAIRED_INDEX = parse_survey_index(SURVEY_XML)  # {"T_QPM1_1": "QPM1", "Q32": "Q32"}


def config(*, dry_run=True, id_column="No", mode="mapped", columns=None, paired=True):
    if columns is None:
        columns = [
            ColumnBinding("T_QPM1_1", "I7_1_2", pull_term="QPM1"),
            ColumnBinding("Q32", "Q32", pull_term="Q32"),
        ]
    source = SourceConfig(
        survey_id="SURV",
        survey_index=(_PAIRED_INDEX if paired else {}),
        paired_survey_id=("SURV" if paired else ""),
    )
    return JobConfig(
        job="link1",
        store=StoreConfig(id=17620, id_column=id_column),
        source=source,
        binding=BindingConfig(
            mode=mode,
            match=ColumnBinding("SbjNum", "SbjNum"),
            columns=columns,
        ),
        write=WriteConfig(dry_run=dry_run),
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
        assert [x for x in c.calls if x[0] in ("add", "update")] == []
        assert res.dry_run is True
        assert res.counts["add"] == 1
        assert "update" not in res.counts
        runs = st.recent_runs("link1")
        assert len(runs) == 1
        assert runs[0]["dry_run"] is True

    def test_dry_run_plan_summary(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=True), client=c, state=st)
        assert res.plan.counts == {"add": 1, "update": 1, "same": 1, "conflict": 0}

    def test_dry_run_records_no_proposals(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=True), client=c, state=st)
        assert st.pending_proposals("link1") == []


class TestApply:
    def test_real_run_writes_adds_only(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        kinds = [x[0] for x in c.calls]
        assert "add" in kinds
        assert "update" not in kinds       # updates are proposals, not writes
        assert res.counts["add"] == 1
        assert res.counts.get("update", 0) == 0

    def test_real_run_records_update_proposal(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        pending = st.pending_proposals("link1")
        assert len(pending) == 1
        assert pending[0]["match_value"] == "102"
        assert pending[0]["diff"] == [
            {"col": "I7_1_2", "old": "Bob", "new": "BOB"},
            {"col": "Q32", "old": "22", "new": "99"},
        ]

    def test_run_status_recorded(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        run = st.recent_runs("link1")[0]
        assert run["status"] == "ok"
        assert run["counts"]["add"] == 1

    def test_tracking_written_for_added_rows(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        tracked = st.tracked_keys("link1")
        assert "104" in tracked
        assert "102" not in tracked        # proposed, not written
        assert "103" not in tracked        # absent, never touched


class TestConflicts:
    def test_store_duplicate_key_halts_before_write(self, st):
        rows = store_rows() + [{"No": 9, "SbjNum": "101", "I7_1_2": "x", "Q32": 1}]
        c = FakeClient(store=rows, subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st)
        assert [x for x in c.calls if x[0] in ("add", "update")] == []
        assert res.status == "blocked"
        assert res.errors

    def test_source_duplicate_skips_and_status_partial(self, st):
        subs = subjects() + [
            subj("4", [("SbjNum", 104), ("T_QPM1_1", "Dup"), ("Q32", 1)]),
            subj("5", [("SbjNum", 105), ("T_QPM1_1", "Eve"), ("Q32", 55)]),
        ]
        c = FakeClient(store=store_rows(), subjects=subs)
        res = run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        # source-side duplicate (104) is skipped, but the unique new key 105 is
        # added -> partial
        assert res.status == "partial"
        kinds = [x[0] for x in c.calls]
        assert "add" in kinds
        assert "update" not in kinds
        tracked = st.tracked_keys("link1")
        assert "105" in tracked
        assert "104" not in tracked        # duplicate key skipped
        run = st.recent_runs("link1")[0]
        assert run["status"] == "partial"

    def test_conflicts_persisted(self, st):
        rows = store_rows() + [{"No": 9, "SbjNum": "101", "I7_1_2": "x", "Q32": 1}]
        c = FakeClient(store=rows, subjects=subjects())
        run_sync(config(dry_run=False), client=c, state=st)
        run = st.recent_runs("link1")[0]
        conflicts = st.conflicts_for_run(run["id"])
        assert any(cf["match_value"] == "101" for cf in conflicts)

    def test_add_error_recorded(self, st):
        class Boom(FakeClient):
            def add_row(self, store_id, values):
                raise STGError("server exploded")

        c = Boom(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st, backoff=0)
        assert res.errors
        assert res.status in ("partial", "error")


def test_transport_error_leaves_run_finished(st):
    import requests
    class Boom(FakeClient):
        def add_row(self, store_id, values):
            raise requests.ConnectionError("dropped")
    c = Boom(store=store_rows(), subjects=subjects())
    run_sync(config(dry_run=False), client=c, state=st, backoff=0)
    run = st.recent_runs("link1")[0]
    assert run["status"] in ("partial", "error")
    assert run["finished_at"] is not None


class TestProgress:
    """`run_sync(progress=...)` must emit the full pipeline stage sequence."""

    def test_stage_names_and_order_dry_run(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        events = []
        run_sync(config(dry_run=True), client=c, state=st, progress=events.append)
        stages = [e["stage"] for e in events]
        assert stages == [
            "start", "ids", "pull", "store",
            "preflight", "plan", "apply", "applied", "proposals", "done",
        ]

    def test_stage_names_and_order_live(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        events = []
        run_sync(
            config(dry_run=False), client=c, state=st,
            throttle=0, backoff=0, progress=events.append,
        )
        stages = [e["stage"] for e in events]
        assert stages == [
            "start", "ids", "pull", "store",
            "preflight", "plan", "apply", "applied", "proposals", "done",
        ]

    def test_events_carry_counts_where_known(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        events = []
        run_sync(config(dry_run=True), client=c, state=st, progress=events.append)
        by = {e["stage"]: e for e in events}
        assert by["ids"]["total"] == 3          # 3 interview ids
        assert by["pull"]["total"] == 3         # 3 pulled records
        assert by["store"]["total"] == 3        # 3 store rows
        assert by["plan"]["counts"] == {"add": 1, "update": 1, "same": 1, "conflict": 0}
        assert by["apply"]["planned"] == 1      # adds only
        assert by["applied"]["counts"] == {"add": 1}
        assert by["applied"]["dry_run"] is True

    def test_blocked_run_emits_done_with_status(self, st):
        c = FakeClient(store=[], subjects=subjects())
        events = []
        res = run_sync(
            config(dry_run=False), client=c, state=st,
            progress=events.append,
        )
        assert res.status == "blocked"
        done = [e for e in events if e["stage"] == "done"][0]
        assert done["status"] == "blocked"
        assert done["counts"] == {}
        assert done["dry_run"] is False

    def test_done_flags_dry_run(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        events = []
        run_sync(config(dry_run=True), client=c, state=st, progress=events.append)
        done = [e for e in events if e["stage"] == "done"][0]
        assert done["dry_run"] is True

    def test_no_progress_kwarg_still_works(self, st):
        # Backwards-compat: progress is optional.
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=True), client=c, state=st)
        assert res.status == "ok"
    def test_cancelled_run_status_and_tracking(self, st):
        subs = subjects() + [
            subj("5", [("SbjNum", 105), ("T_QPM1_1", "Eve"), ("Q32", 55)]),
        ]
        c = FakeClient(store=store_rows(), subjects=subs)
        state = {"n": 0}
        def stop():
            state["n"] += 1
            return state["n"] > 1
        res = run_sync(config(dry_run=False), client=c, state=st, should_stop=stop, throttle=0, backoff=0)
        assert res.status == "cancelled"
        assert st.recent_runs("link1")[0]["status"] == "cancelled"


class TestPull:
    def test_uses_derived_filter(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=True), client=c, state=st)
        exports = [x for x in c.calls if x[0] == "export"]
        assert exports and exports[0][1] is not None


class TestReadiness:
    def test_pull_is_unbounded(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=True), client=c, state=st)
        assert c.id_kwargs == {}          # no date_start / date_type

    def test_missing_id_column_blocks_before_write(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False, id_column="Nope"), client=c, state=st)
        assert res.status == "blocked"
        assert [x for x in c.calls if x[0] in ("add", "update")] == []

    def test_empty_store_blocks(self, st):
        c = FakeClient(store=[], subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st)
        assert res.status == "blocked"

    def test_missing_store_column_blocks_before_write(self, st):
        cfg = config(
            dry_run=False,
            columns=[
                ColumnBinding("T_QPM1_1", "I7_1_2", pull_term="QPM1"),
                ColumnBinding("Q32", "NOT_A_STORE_COL", pull_term="Q32"),
            ],
        )
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(cfg, client=c, state=st)
        assert res.status == "blocked"
        assert [x for x in c.calls if x[0] in ("add", "update")] == []
        assert any("not present on the store" in e for e in res.errors)

    def test_missing_source_var_blocks_before_write(self, st):
        cfg = config(
            dry_run=False,
            columns=[
                ColumnBinding("T_QPM1_1", "I7_1_2", pull_term="QPM1"),
                ColumnBinding("NOT_A_SOURCE_VAR", "Q32", pull_term="Q32"),
            ],
        )
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(cfg, client=c, state=st)
        assert res.status == "blocked"
        assert [x for x in c.calls if x[0] in ("add", "update")] == []
        assert any("not present in the pull" in e for e in res.errors)

    def test_strict_violation_blocks_before_write(self, st):
        # mapped names differ → strict mode reports every mismatch as an error
        cfg = config(dry_run=False, mode="strict")
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(cfg, client=c, state=st)
        assert res.status == "blocked"
        assert [x for x in c.calls if x[0] in ("add", "update")] == []
        assert any("strict" in e.lower() for e in res.errors)

    def test_format_differs_still_runs(self, st):
        # Only real errors gate — a per-column `format_differs` review status
        # (leading-zero phone numbers) must NOT block the run.
        rows = [
            {"No": 1, "SbjNum": "101", "I7_1_2": "Alice", "Phone": "8151883872"},
            {"No": 2, "SbjNum": "102", "I7_1_2": "Bob", "Phone": "8123456789"},
        ]
        subs = [
            subj("1", [("SbjNum", 101), ("T_QPM1_1", "Alice"), ("PHONE", "08151883872")]),
            subj("2", [("SbjNum", 102), ("T_QPM1_1", "Bob"), ("PHONE", "08123456789")]),
        ]
        cfg = config(
            dry_run=True,
            columns=[
                ColumnBinding("T_QPM1_1", "I7_1_2", pull_term="QPM1"),
                ColumnBinding("PHONE", "Phone", pull_term="PHONE"),
            ],
        )
        c = FakeClient(store=rows, subjects=subs)
        res = run_sync(cfg, client=c, state=st)
        assert res.status != "blocked"
        assert res.errors == []


class TestPairingGate:
    def test_unpaired_job_blocks_without_xml(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False, paired=False), client=c, state=st)
        assert res.status == "blocked"
        assert any("not paired" in e for e in res.errors)
        assert [x for x in c.calls if x[0] in ("add", "update")] == []

    def test_run_never_calls_get_survey_xml(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        called = []
        c.get_survey_xml = lambda sid: called.append(sid) or SURVEY_XML
        run_sync(config(dry_run=True), client=c, state=st)
        assert called == []

    def test_stale_pairing_blocks(self, st):
        cfg = config(dry_run=False)
        cfg.source.survey_id = "SURV2"  # edited after pairing
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(cfg, client=c, state=st)
        assert res.status == "blocked"
        assert any("stale" in e for e in res.errors)

    def test_column_without_pull_term_blocks(self, st):
        cfg = config(dry_run=False, columns=[
            ColumnBinding("T_QPM1_1", "I7_1_2", pull_term=""),  # never paired
        ])
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(cfg, client=c, state=st)
        assert res.status == "blocked"
        assert any("pull term" in e for e in res.errors)

    def test_paired_var_absent_from_pull_does_not_block(self, st):
        # A paired var that is empty across every sampled subject is ABSENT from
        # the export (probe F7), not an error. The cached index proves it real,
        # so the run must proceed (empty source values are still written).
        cfg = config(dry_run=True, columns=[
            ColumnBinding("T_QPM1_1", "I7_1_2", pull_term="QPM1"),
            ColumnBinding("T_QPM1_9", "Q32", pull_term="QPM1"),  # in index, absent from pull
        ])
        cfg.source.survey_index["T_QPM1_9"] = "QPM1"
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(cfg, client=c, state=st)
        assert res.status != "blocked"
        assert not any("not present in the pull" in e for e in res.errors)


def test_run_preflight_never_reads_xml(st):
    from store_sync.core.sync import run_preflight
    called = []
    c = FakeClient(store=store_rows(), subjects=subjects())
    c.get_survey_xml = lambda sid: called.append(sid) or SURVEY_XML
    report, terms = run_preflight(config(), client=c)
    assert called == []
    assert terms["T_QPM1_1"] == "QPM1"


class TestBlacklist:
    def test_tracked_then_deleted_key_is_not_readded(self, st):
        st.track_row(job="link1", match_value="104", row_hash="h")
        # 104 absent from the store this run -> deleted by hand.
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        kinds = [x[0] for x in c.calls]
        assert "add" not in kinds                    # 104 not re-added
        assert res.status == "ok"                    # suppression is not an error
        assert res.suppressed == 1
        assert st.blacklisted_keys("link1") == {"104"}

    def test_suppression_recorded_on_dry_run(self, st):
        st.track_row(job="link1", match_value="104", row_hash="h")
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=True), client=c, state=st)
        assert res.counts.get("add", 0) == 0
        assert res.suppressed == 1
        assert st.blacklisted_keys("link1") == {"104"}   # state written, store not

    def test_blacklisted_key_back_in_store_is_cleared_and_planned(self, st):
        st.track_row(job="link1", match_value="104", row_hash="h")
        st.blacklist_key("link1", "104", reason="deleted")
        rows = store_rows() + [{"No": 4, "SbjNum": "104", "I7_1_2": "Dave", "Q32": 44}]
        c = FakeClient(store=rows, subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        assert st.blacklisted_keys("link1") == set()     # auto-cleared
        assert res.suppressed == 0

    def test_untracked_absent_key_is_not_blacklisted(self, st):
        # 103 is in the store but not in the source; it was never tracked.
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        assert st.blacklisted_keys("link1") == set()

    def test_empty_store_snapshot_does_not_mass_blacklist(self, st):
        # An empty (or malformed) GetStore response is *unknown*, not a mass
        # deletion — a bad read must never blacklist every tracked key.
        st.track_row(job="link1", match_value="104", row_hash="h")
        st.track_row(job="link1", match_value="105", row_hash="h")
        c = FakeClient(store=[], subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        assert res.status == "blocked"
        assert st.blacklisted_keys("link1") == set()

    def test_store_missing_match_column_does_not_mass_blacklist(self, st):
        # Rows present but without the match column -> empty key set -> unknown.
        st.track_row(job="link1", match_value="104", row_hash="h")
        rows = [{"No": 1, "I7_1_2": "Alice"}]  # no SbjNum
        c = FakeClient(store=rows, subjects=subjects())
        run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        assert st.blacklisted_keys("link1") == set()



class TestNotificationWiring:
    def test_run_sync_notifies_with_the_result(self, monkeypatch, st):
        import store_sync.core.sync as sync_mod
        seen = []
        monkeypatch.setattr(sync_mod, "notify_run_report",
                            lambda cfg, s, res, **kw: seen.append(res) or None)
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st)
        assert seen == [res]

    def test_run_sync_notify_false_skips_notification(self, monkeypatch, st):
        import store_sync.core.sync as sync_mod
        monkeypatch.setattr(sync_mod, "notify_run_report",
                            lambda *a, **k: pytest.fail("must not notify"))
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=False), client=c, state=st, notify=False)

    def test_notification_failure_does_not_change_run_status(self, monkeypatch, st):
        import store_sync.core.sync as sync_mod

        def boom(*a, **k):
            raise RuntimeError("mailbox down")

        monkeypatch.setattr(sync_mod, "notify_run_report", boom)
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st)
        assert res.status == "ok" and res.counts["add"] == 1
        assert st.get_run(res.run_id)["status"] == "ok"

    def test_default_run_records_a_notification_row(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st)
        row = st.notification_for_run(res.run_id)
        assert row is not None and row["status"] == "skipped"
