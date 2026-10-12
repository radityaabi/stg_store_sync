"""Tests for store_sync.core.planner — diff store vs source by match key.

Classifications: ADD / UPDATE / SAME / CONFLICT. Fail-closed rules from spec
§4.4; empty is a value (spec §5.3). No delete: absent store rows are untouched.
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

    def test_absent_store_key_is_not_deleted(self):
        p = make_plan()
        assert [a for a in p.actions if a.kind == "delete"] == []
        assert not hasattr(p, "delete_candidates")

    def test_action_carries_full_payload(self):
        p = make_plan()
        add = next(a for a in p.actions if a.kind == "add")
        assert add.payload == {"SbjNum": "104", "I7_1_2": "Dave", "Q32": 44, "No": 4}
        upd = next(a for a in p.actions if a.kind == "update")
        assert upd.payload == {"SbjNum": "102", "I7_1_2": "BOB", "Q32": 99, "No": 2}


class TestDiffs:
    def test_update_carries_per_column_diffs(self):
        p = make_plan()
        upd = next(a for a in p.actions if a.kind == "update")
        assert upd.diffs == [
            {"col": "I7_1_2", "old": "Bob", "new": "BOB"},
            {"col": "Q32", "old": "22", "new": "99"},
        ]

    def test_same_action_has_no_diffs(self):
        p = make_plan()
        same = next(a for a in p.actions if a.kind == "same")
        assert same.diffs == []

    def test_no_false_positive_when_only_string_form_differs(self):
        # store Q32 is int 11, source Q32 is "11" → equal after str(), no diff
        p = make_plan()
        same_keys = [a.match_value for a in p.actions if a.kind == "same"]
        assert "101" in same_keys
        assert "101" not in [a.match_value for a in p.actions if a.kind == "update"]


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
            a.kind in ("add", "update") and a.match_value == "101"
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


class TestConflictOrigin:
    def test_source_dup_does_not_block(self):
        src = source_records() + [{"SbjNum": "104", "T_QPM1_1": "Dup", "Q32": 1}]
        p = make_plan(source_records=src)
        assert p.blocked is False
        assert any(a.kind == "conflict" and a.side == "source" for a in p.actions)

    def test_store_dup_blocks(self):
        rows = store_rows() + [{"No": 9, "SbjNum": "101", "I7_1_2": "x", "Q32": 1}]
        p = make_plan(store_rows=rows)
        assert p.blocked is True
        assert any(a.kind == "conflict" and a.side == "store" for a in p.actions)

    def test_store_row_missing_match_col_is_store_conflict(self):
        rows = store_rows() + [{"No": 9, "I7_1_2": "x", "Q32": 1}]
        p = make_plan(store_rows=rows)
        assert p.blocked is True


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
        assert p.counts == {"add": 1, "update": 1, "same": 1, "conflict": 0}


class TestBlacklist:
    def test_blacklisted_key_is_not_added(self):
        p = make_plan(blacklisted={"104"})
        assert [a.match_value for a in p.actions if a.kind == "add"] == []
        assert p.suppressed == ["104"]

    def test_suppressed_not_counted_as_action(self):
        p = make_plan(blacklisted={"104"})
        assert p.counts["add"] == 0

    def test_blacklisted_key_in_store_is_planned_normally(self):
        # 101 is in the store and in the blacklist → auto-clear is upstream, but
        # the planner itself must not suppress an in-store key.
        p = make_plan(blacklisted={"101"})
        assert [a.match_value for a in p.actions if a.kind == "same"] == ["101"]
        assert p.suppressed == []

    def test_none_blacklist_is_unchanged_behaviour(self):
        p = make_plan(blacklisted=None)
        assert [a.match_value for a in p.actions if a.kind == "add"] == ["104"]
        assert p.suppressed == []
