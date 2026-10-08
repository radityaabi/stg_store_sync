"""Tests for store_sync.core.adoption — one-time legacy SbjNum adoption.

Match legacy store rows (no SbjNum yet) to source records on content columns.
1:1 unique match -> adopt; ambiguous/none -> quarantine, never guess (spec §4.5).
"""
from __future__ import annotations

from store_sync.core.adoption import AdoptionResult, plan_adoption
from store_sync.core.config import ColumnBinding


COLUMNS = [
    ColumnBinding("T_QPM1_1", "I7_1_2"),
    ColumnBinding("Q32", "Q32"),
]


def store_rows():
    return [
        {"No": 1, "SbjNum": "", "I7_1_2": "Alice", "Q32": 11},
        {"No": 2, "SbjNum": "", "I7_1_2": "Bob", "Q32": 22},
        {"No": 3, "SbjNum": "", "I7_1_2": "Carol", "Q32": 33},
    ]


def source_records():
    return [
        {"SbjNum": "101", "T_QPM1_1": "Alice", "Q32": 11},
        {"SbjNum": "102", "T_QPM1_1": "Bob", "Q32": 22},
        {"SbjNum": "104", "T_QPM1_1": "Dave", "Q32": 44},
    ]


class TestAdoption:
    def test_unique_matches_adopted(self):
        r = plan_adoption(
            store_rows=store_rows(),
            source_records=source_records(),
            columns=COLUMNS,
            match_store_col="SbjNum",
            match_source_var="SbjNum",
            id_column="No",
        )
        assert isinstance(r, AdoptionResult)
        got = {a.id_value: a.sbjnum for a in r.actions}
        assert got == {1: "101", 2: "102"}

    def test_payload_is_full_row_with_adopted_sbjnum(self):
        r = plan_adoption(
            store_rows=store_rows(),
            source_records=source_records(),
            columns=COLUMNS,
            match_store_col="SbjNum",
            match_source_var="SbjNum",
            id_column="No",
        )
        a = next(a for a in r.actions if a.id_value == 1)
        assert a.payload == {"I7_1_2": "Alice", "Q32": 11, "SbjNum": "101", "No": 1}

    def test_no_match_quarantined(self):
        rows = store_rows() + [{"No": 4, "SbjNum": "", "I7_1_2": "Zed", "Q32": 99}]
        r = plan_adoption(
            store_rows=rows,
            source_records=source_records(),
            columns=COLUMNS,
            match_store_col="SbjNum",
            match_source_var="SbjNum",
            id_column="No",
        )
        # Carol (3) and Zed (4) have no source record -> both quarantined
        assert [q["id_value"] for q in r.quarantined] == [3, 4]

    def test_ambiguous_match_quarantined(self):
        src = source_records() + [{"SbjNum": "999", "T_QPM1_1": "Alice", "Q32": 11}]
        r = plan_adoption(
            store_rows=store_rows(),
            source_records=src,
            columns=COLUMNS,
            match_store_col="SbjNum",
            match_source_var="SbjNum",
            id_column="No",
        )
        # Alice (row 1) matches two source records -> ambiguous
        assert 1 in {q["id_value"] for q in r.quarantined}
        assert 1 not in {a.id_value for a in r.actions}

    def test_sbjnum_already_present_skipped(self):
        rows = store_rows()
        rows[0]["SbjNum"] = "101"
        r = plan_adoption(
            store_rows=rows,
            source_records=source_records(),
            columns=COLUMNS,
            match_store_col="SbjNum",
            match_source_var="SbjNum",
            id_column="No",
        )
        assert 1 not in {a.id_value for a in r.actions}
        assert 1 in r.already_adopted

    def test_no_writes_without_unique_content_columns(self):
        rows = [{"No": 1, "SbjNum": "", "I7_1_2": "", "Q32": 0}]
        src = [{"SbjNum": "1", "T_QPM1_1": "", "Q32": 0}]
        r = plan_adoption(
            store_rows=rows,
            source_records=src,
            columns=COLUMNS,
            match_store_col="SbjNum",
            match_source_var="SbjNum",
            id_column="No",
        )
        # empty content still matches uniquely by values -> adopt (values equal)
        assert len(r.actions) == 1
