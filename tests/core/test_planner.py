"""Tests for store_sync.core.planner — diff store vs source by match key.

Classifications: ADD / UPDATE / SAME / DELETE / CONFLICT. Fail-closed rules
from spec §4.4; empty is a value (spec §5.3).
"""
from __future__ import annotations

import pytest

from store_sync.core.config import ColumnBinding
from store_sync.core.planner import Action, Plan, plan


MATCH = ColumnBinding("SbjNum", "SbjNum")
COLUMNS = [
    ColumnBinding("T_QPM1_1", "I7_1_2"),
    ColumnBinding("Q32", "Q32"),
]


def store_rows():
    return [
        {"No": 1, "SbjNum": "101", "I7_1_2": "Alice", "Q32": 11},
        {"No": 2, "SbjNum": "102", "I7_1_2": "Bob", "Q32": 22},
        {"No": 3, "SbjNum": "103", "I7_1_2": "Carol", "Q32": 33},
    ]


def source_records():
    # 101 same, 102 changed, 104 new; 103 absent
    return [
        {"SbjNum": "101", "T_QPM1_1": "Alice", "Q32": 11},
        {"SbjNum": "102", "T_QPM1_1": "BOB", "Q32": 99},
        {"SbjNum": "104", "T_QPM1_1": "Dave", "Q32": 44},
    ]


def make_plan(**kw):
    args = dict(
        store_rows=store_rows(),
        source_records=source_records(),
        match=MATCH,
        columns=COLUMNS,
        id_column="No",
        delete_policy="full_history",
    )
    args.update(kw)
    return plan(**args)


class TestClassification:
    def test_adds_new_keys(self):
        p = make_plan()
        adds = [a for a in p.actions if a.kind == "add"]
        assert len(adds) == 1
        assert adds[0].match_value == "104"
        assert adds[0].id_value == 4  # max(No)+1

    def test_updates_changed_keys(self):
        p = make_plan()
        ups = [a for a in p.actions if a.kind == "update"]
        assert len(ups) == 1
        assert ups[0].match_value == "102"
        assert ups[0].id_value == 2  # existing id preserved

    def test_skips_unchanged_keys(self):
        p = make_plan()
        same = [a for a in p.actions if a.kind == "same"]
        assert [a.match_value for a in same] == ["101"]

    def test_deletes_absent_keys_full_history(self):
        p = make_plan()
        dels = [a for a in p.actions if a.kind == "delete"]
        assert len(dels) == 1
        assert dels[0].match_value == "103"
        assert dels[0].id_value == 3

    def test_action_carries_full_payload(self):
        p = make_plan()
        add = next(a for a in p.actions if a.kind == "add")
        assert add.payload == {"SbjNum": "104", "I7_1_2": "Dave", "Q32": 44, "No": 4}
        upd = next(a for a in p.actions if a.kind == "update")
        assert upd.payload == {"SbjNum": "102", "I7_1_2": "BOB", "Q32": 99, "No": 2}


class TestFullColumnPayload:
    def test_add_payload_includes_unmapped_store_column(self):
        rows = [{"No": 1, "SbjNum": "101", "I7_1_2": "x", "Q32": 1, "Extra": "keep"}]
        src = [{"SbjNum": "104", "T_QPM1_1": "Dave", "Q32": 44}]
        p = make_plan(store_rows=rows, source_records=src)
        add = next(a for a in p.actions if a.kind == "add")
        assert add.payload["Extra"] == ""       # unmapped col present, blank
        assert set(add.payload) == {"No", "SbjNum", "I7_1_2", "Q32", "Extra"}

    def test_update_payload_preserves_unmapped_store_column(self):
        rows = [{"No": 2, "SbjNum": "102", "I7_1_2": "Bob", "Q32": 22, "Extra": "keep"}]
        src = [{"SbjNum": "102", "T_QPM1_1": "BOB", "Q32": 99}]
        p = make_plan(store_rows=rows, source_records=src)
        upd = next(a for a in p.actions if a.kind == "update")
        assert upd.payload["Extra"] == "keep"   # untouched store value retained
        assert upd.payload["Q32"] == 99
        assert upd.payload["No"] == 2


class TestDeletePolicy:
    def test_tracked_only_deletes_none_when_untracked(self):
        p = make_plan(delete_policy="tracked_only")
        assert [a for a in p.actions if a.kind == "delete"] == []
        # but the candidate is still reported
        assert "103" in p.delete_candidates

    def test_tracked_only_deletes_tracked_keys(self):
        p = make_plan(delete_policy="tracked_only", tracked={"103"})
        dels = [a for a in p.actions if a.kind == "delete"]
        assert [a.match_value for a in dels] == ["103"]

    def test_manual_reports_but_does_not_delete(self):
        p = make_plan(delete_policy="manual")
        assert [a for a in p.actions if a.kind == "delete"] == []
        assert "103" in p.delete_candidates


class TestFailClosed:
    def test_duplicate_match_key_in_source_conflicts(self):
        src = source_records()
        src.append({"SbjNum": "104", "T_QPM1_1": "Dup", "Q32": 1})
        p = make_plan(source_records=src)
        kinds = {a.kind for a in p.actions if a.match_value == "104"}
        assert "conflict" in kinds
        # no add/update emitted for the conflicted key
        assert not any(
            a.kind in ("add", "update") and a.match_value == "104" for a in p.actions
        )

    def test_duplicate_match_key_in_store_conflicts(self):
        rows = store_rows()
        rows.append({"No": 9, "SbjNum": "101", "I7_1_2": "Other", "Q32": 1})
        p = make_plan(store_rows=rows)
        assert any(a.kind == "conflict" for a in p.actions)
        assert p.ok is False

    def test_missing_match_key_in_source_is_quarantined(self):
        src = source_records()
        src.append({"T_QPM1_1": "NoKey", "Q32": 1})
        p = make_plan(source_records=src)
        assert len(p.quarantined) == 1
        assert p.quarantined[0]["T_QPM1_1"] == "NoKey"

    def test_conflicted_key_not_written(self):
        rows = store_rows()
        rows.append({"No": 9, "SbjNum": "101", "I7_1_2": "Other", "Q32": 1})
        p = make_plan(store_rows=rows)
        assert not any(
            a.kind in ("add", "update", "delete") and a.match_value == "101"
            for a in p.actions
        )

    def test_empty_value_is_a_value_not_skip(self):
        rows = [{"No": 1, "SbjNum": "1", "I7_1_2": "x", "Q32": 5}]
        src = [{"SbjNum": "1", "T_QPM1_1": "x", "Q32": ""}]
        p = make_plan(store_rows=rows, source_records=src)
        ups = [a for a in p.actions if a.kind == "update"]
        assert len(ups) == 1
        assert ups[0].payload["Q32"] == ""

    def test_empty_matches_empty_is_same(self):
        rows = [{"No": 1, "SbjNum": "1", "I7_1_2": "", "Q32": 5}]
        src = [{"SbjNum": "1", "T_QPM1_1": "", "Q32": "5"}]
        p = make_plan(store_rows=rows, source_records=src)
        assert [a.kind for a in p.actions if a.kind != "same"] == []
        # Q32 "5" == store 5 (string compare)
        assert any(a.kind == "same" for a in p.actions)


class TestNextTag:
    def test_next_tag_is_max_plus_one(self):
        p = make_plan()
        assert p.next_tag == 4

    def test_non_numeric_ids_ignored_for_next_tag(self):
        rows = [
            {"No": "a", "SbjNum": "1", "I7_1_2": "", "Q32": 0},
            {"No": "7", "SbjNum": "2", "I7_1_2": "", "Q32": 0},
        ]
        p = make_plan(store_rows=rows, source_records=[])
        assert p.next_tag == 8

    def test_empty_store_starts_at_one(self):
        p = make_plan(store_rows=[], source_records=source_records())
        adds = [a for a in p.actions if a.kind == "add"]
        assert sorted(a.id_value for a in adds) == [1, 2, 3]


class TestCounts:
    def test_summary_counts(self):
        p = make_plan()
        assert p.counts == {"add": 1, "update": 1, "same": 1, "delete": 1, "conflict": 0}
