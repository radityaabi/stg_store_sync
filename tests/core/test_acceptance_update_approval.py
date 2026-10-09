"""End-to-end acceptance: add-only automation + manual update approval (spec §6).

Exercises the whole loop against an in-memory fake store:

  run_sync (adds automated, updates proposed) -> apply_updates (CAS) -> re-read

and confirms that a short pull deletes nothing.
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
from store_sync.core.fetcher import parse_survey_index
from store_sync.core.sync import run_sync
from store_sync.core.writer import apply_updates
from store_sync.core.planner import Action


SURVEY_XML = """<?xml version="1.0"?>
<Survey>
  <SQ><QuestionID>762</QuestionID><VariableName>QPM1</VariableName></SQ>
  <STo><QuestionID>762</QuestionID><VariableName>T_QPM1_1</VariableName></STo>
  <SQ><QuestionID>900</QuestionID><VariableName>Q32</VariableName></SQ>
</Survey>
"""

_PAIRED_INDEX = parse_survey_index(SURVEY_XML)  # {"T_QPM1_1": "QPM1", "Q32": "Q32"}


def subj(sid, pairs):
    return {"SubjectID": sid, "Columns": [{"Var": v, "Value": x} for v, x in pairs]}


class FakeStore:
    """Minimal STG client whose store is a mutable list of rows."""

    def __init__(self, store, subjects):
        self.store = [dict(r) for r in store]
        self.subjects = list(subjects)

    def get_survey_xml(self, survey_id):
        return SURVEY_XML

    def survey_interview_ids(self, survey_id, **kw):
        return [s["SubjectID"] for s in self.subjects]

    def simple_export(self, survey_id, ids, *, variable_names=None, **kw):
        return [s for s in self.subjects if s["SubjectID"] in ids]

    def get_store(self, store_id):
        return [dict(r) for r in self.store]

    def add_row(self, store_id, values):
        self.store.append(dict(values))
        return {"Done": True}

    def update_row(self, store_id, values):
        self.store = [
            dict(values) if str(r.get("No")) == str(values.get("No")) else r
            for r in self.store
        ]
        return {"Done": True}


def config(*, dry_run):
    return JobConfig(
        job="acc",
        store=StoreConfig(17620, "No"),
        source=SourceConfig(
            "SURV", survey_index=_PAIRED_INDEX, paired_survey_id="SURV",
        ),
        binding=BindingConfig(
            match=ColumnBinding("SbjNum", "SbjNum"),
            columns=[
                ColumnBinding("T_QPM1_1", "I7_1_2", pull_term="QPM1"),
                ColumnBinding("Q32", "Q32", pull_term="Q32"),
            ],
        ),
        write=WriteConfig(dry_run=dry_run),
    )


@pytest.fixture
def st(tmp_path):
    s = State(tmp_path / "state.db")
    yield s
    s.close()


def _action_from(p):
    return Action(
        "update", p["match_value"], "", p.get("id_value"),
        p.get("payload", {}), diffs=p.get("diff", []),
    )


def test_full_loop_add_proposed_applied_and_absent_untouched(st):
    store = [
        {"No": 1, "SbjNum": "101", "I7_1_2": "Alice", "Q32": 11},   # same
        {"No": 2, "SbjNum": "102", "I7_1_2": "Bob", "Q32": 22},     # changed
        {"No": 3, "SbjNum": "103", "I7_1_2": "Carol", "Q32": 33},   # absent from pull
    ]
    subjects = [
        subj("1", [("SbjNum", 101), ("T_QPM1_1", "Alice"), ("Q32", 11)]),
        subj("2", [("SbjNum", 102), ("T_QPM1_1", "BOB"), ("Q32", 99)]),
        subj("3", [("SbjNum", 104), ("T_QPM1_1", "Dave"), ("Q32", 44)]),  # new
    ]
    client = FakeStore(store, subjects)

    # 1) run: add 104 automatically, propose the 102 change, leave 103 alone.
    res = run_sync(config(dry_run=False), client=client, state=st,
                   throttle=0, backoff=0)
    assert res.status == "ok"
    assert res.counts == {"add": 1}
    assert res.proposals == 1
    keys = {str(r["SbjNum"]) for r in client.store}
    assert keys == {"101", "102", "103", "104"}     # 104 added, 103 untouched
    row_102 = next(r for r in client.store if r["SbjNum"] == "102")
    assert row_102["I7_1_2"] == "Bob"                # NOT written by the run

    # 2) apply the recorded proposal (compare-and-swap).
    pending = st.pending_proposals("acc")
    assert len(pending) == 1
    assert pending[0]["match_value"] == "102"
    applied = apply_updates(
        client, 17620, [_action_from(pending[0])], id_column="No",
    )
    assert applied.counts == {"update": 1}
    row_102 = next(r for r in client.store if r["SbjNum"] == "102")
    assert row_102["I7_1_2"] == "BOB" and str(row_102["Q32"]) == "99"
    st.set_proposal_status(pending[0]["id"], "applied")
    assert st.pending_proposals("acc") == []


def test_stale_when_store_manually_revised_before_apply(st):
    store = [{"No": 2, "SbjNum": "102", "I7_1_2": "Bob", "Q32": 22}]
    subjects = [subj("2", [("SbjNum", 102), ("T_QPM1_1", "BOB"), ("Q32", 99)])]
    client = FakeStore(store, subjects)

    run_sync(config(dry_run=False), client=client, state=st, throttle=0, backoff=0)
    pending = st.pending_proposals("acc")
    assert len(pending) == 1

    # Operator edits the store cell directly → the recorded `old` no longer holds.
    client.store[0]["I7_1_2"] = "manual-edit"
    res = apply_updates(client, 17620, [_action_from(pending[0])], id_column="No")
    assert res.counts.get("update", 0) == 0
    assert len(res.stale) == 1
    assert client.store[0]["I7_1_2"] == "manual-edit"   # manual revision preserved


def test_short_pull_deletes_nothing(st):
    # Store has 3 rows; the pull only returns 1 (a short window). No row is removed.
    store = [
        {"No": 1, "SbjNum": "101", "I7_1_2": "Alice", "Q32": 11},
        {"No": 2, "SbjNum": "102", "I7_1_2": "Bob", "Q32": 22},
        {"No": 3, "SbjNum": "103", "I7_1_2": "Carol", "Q32": 33},
    ]
    subjects = [subj("1", [("SbjNum", 101), ("T_QPM1_1", "Alice"), ("Q32", 11)])]
    client = FakeStore(store, subjects)

    run_sync(config(dry_run=False), client=client, state=st, throttle=0, backoff=0)
    assert len(client.store) == 3                       # nothing deleted
    assert not hasattr(client, "delete_row")


def test_dry_run_records_no_proposals(st):
    store = [{"No": 2, "SbjNum": "102", "I7_1_2": "Bob", "Q32": 22}]
    subjects = [subj("2", [("SbjNum", 102), ("T_QPM1_1", "BOB"), ("Q32", 99)])]
    client = FakeStore(store, subjects)

    run_sync(config(dry_run=True), client=client, state=st)
    assert st.pending_proposals("acc") == []
    assert client.store[0]["I7_1_2"] == "Bob"           # untouched
