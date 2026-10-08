"""Tests for store_sync.core.binding — match-key/id-column checks, alias
suggestions, strict mode, and the no-op id-column probe.

Fixtures mirror the real Link1/Gran_Turismo_Link2 shapes (spec §5.2).
"""
from __future__ import annotations

import pytest

from store_sync.core.binding import (
    ColumnSuggestion,
    KeyCheck,
    ProbeResult,
    check_id_column,
    check_match_key,
    noop_probe,
    project,
    strict_violations,
    suggest_aliases,
)
from store_sync.core.config import ColumnBinding
from store_sync.core.stg_client import STGError


# --- fixtures ---------------------------------------------------------------

def store_rows():
    return [
        {"No": 1, "uniqueID": "1001", "I7_1_2": "Shinta", "I8a_1": "8151883872", "Q32": 4},
        {"No": 2, "uniqueID": "1002", "I7_1_2": "Jefta", "I8a_1": "8123456789", "Q32": 7},
        {"No": 3, "uniqueID": "1003", "I7_1_2": "Budi", "I8a_1": "8111111111", "Q32": 9},
    ]


def source_records():
    # flattened {source_var: value}; phone has a leading zero (format differs)
    return [
        {"SbjNum": "1", "T_QPM1_1": "Shinta", "T_QPM1_2": "1001", "T_QPM1_3": "08151883872", "Q32": 4},
        {"SbjNum": "2", "T_QPM1_1": "Jefta", "T_QPM1_2": "1002", "T_QPM1_3": "08123456789", "Q32": 7},
        {"SbjNum": "3", "T_QPM1_1": "Budi", "T_QPM1_2": "1003", "T_QPM1_3": "08111111111", "Q32": 9},
    ]


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def update_row(self, store_id, values):
        self.calls.append((store_id, dict(values)))
        if self.error:
            raise self.error
        return {"Done": True}


# --- match key --------------------------------------------------------------

class TestCheckMatchKey:
    def test_unique_key_passes(self):
        recs = [{"SbjNum": str(i)} for i in range(5)]
        r = check_match_key(recs, "SbjNum")
        assert isinstance(r, KeyCheck)
        assert r.unique is True
        assert r.distinct == 5
        assert r.total == 5
        assert r.duplicates == []

    def test_duplicate_key_fails_and_lists_dups(self):
        recs = [{"SbjNum": "1"}, {"SbjNum": "1"}, {"SbjNum": "2"}]
        r = check_match_key(recs, "SbjNum")
        assert r.unique is False
        assert r.distinct == 2
        assert r.total == 3
        assert ("1", 2) in r.duplicates

    def test_missing_key_column_marks_empty(self):
        r = check_match_key([{"a": 1}], "SbjNum")
        assert r.unique is False
        assert r.distinct == 0

    def test_value_coerced_to_str(self):
        r = check_match_key([{"SbjNum": 1}, {"SbjNum": "1"}], "SbjNum")
        assert r.unique is False  # 1 and "1" collide as strings

    def test_real_world_248_of_296(self):
        recs = [{"QRelID_RVid": str(i)} for i in range(248)]
        recs += [{"QRelID_RVid": "0"}] * (296 - 248)
        r = check_match_key(recs, "QRelID_RVid")
        assert r.unique is False
        assert r.total == 296


# --- id column --------------------------------------------------------------

class TestCheckIdColumn:
    def test_existing_unique_column(self):
        r = check_id_column(store_rows(), "uniqueID")
        assert r.exists is True
        assert r.unique is True

    def test_missing_column(self):
        r = check_id_column(store_rows(), "not_there")
        assert r.exists is False
        assert r.unique is False

    def test_duplicate_id_values(self):
        rows = store_rows()
        rows[1]["uniqueID"] = "1001"
        r = check_id_column(rows, "uniqueID")
        assert r.exists is True
        assert r.unique is False
        assert ("1001", 2) in r.duplicates


# --- alias suggestions ------------------------------------------------------

class TestSuggestAliases:
    def test_finds_best_source_var_per_store_col(self):
        sugg = suggest_aliases(store_rows(), source_records())
        # uniqueID <- T_QPM1_2 (panelist id), exact
        assert sugg["uniqueID"][0].source_var == "T_QPM1_2"
        assert sugg["uniqueID"][0].status == "ok"
        # Q32 <- Q32 exact
        assert sugg["Q32"][0].source_var == "Q32"
        assert sugg["Q32"][0].status == "ok"

    def test_phone_leads_zero_flagged_format_differs(self):
        sugg = suggest_aliases(store_rows(), source_records())
        phone = sugg["I8a_1"][0]
        assert phone.source_var == "T_QPM1_3"
        assert phone.status == "format_differs"
        assert phone.sample_store is not None and phone.sample_source is not None

    def test_overlap_counts_and_ratio(self):
        sugg = suggest_aliases(store_rows(), source_records())
        s = sugg["uniqueID"][0]
        assert s.overlap == 3
        assert s.total == 3
        assert s.ratio == 1.0

    def test_can_exclude_columns(self):
        sugg = suggest_aliases(
            store_rows(), source_records(), exclude={"No", "uniqueID"}
        )
        assert "No" not in sugg
        assert "uniqueID" not in sugg

    def test_no_match_when_no_values_shared(self):
        sugg = suggest_aliases(
            [{"I7_1_2": "only-store"}], [{"SbjNum": "x", "Z": "different"}]
        )
        assert sugg["I7_1_2"][0].source_var is None
        assert sugg["I7_1_2"][0].status == "no_match"

    def test_top_n(self):
        store = [{"X": "1"}, {"X": "2"}]
        src = [{"A": "1", "B": "1"}, {"A": "2", "B": "2"}]
        assert len(suggest_aliases(store, src, top=1)["X"]) == 1
        assert len(suggest_aliases(store, src, top=5)["X"]) == 2


# --- strict -----------------------------------------------------------------

class TestStrictViolations:
    def test_identical_names_pass(self):
        cols = [ColumnBinding("A", "A"), ColumnBinding("B", "B")]
        assert strict_violations(cols) == []

    def test_mismatch_reported(self):
        cols = [
            ColumnBinding("T_QPM1_1", "I7_1_2"),
            ColumnBinding("Q32", "Q32"),
        ]
        v = strict_violations(cols)
        assert len(v) == 1
        assert "T_QPM1_1" in v[0] and "I7_1_2" in v[0]


# --- projection -------------------------------------------------------------

class TestProject:
    def test_maps_source_to_store_and_defaults_missing_to_empty(self):
        cols = [
            ColumnBinding("Q32", "Q32"),
            ColumnBinding("Q42_TEXT", "OTHER"),  # absent from record -> ""
        ]
        rec = {"SbjNum": "1", "Q32": 4}
        assert project(cols, rec) == {"Q32": 4, "OTHER": ""}

    def test_empty_string_is_preserved(self):
        cols = [ColumnBinding("A", "B")]
        assert project(cols, {"A": ""}) == {"B": ""}


# --- no-op probe ------------------------------------------------------------

class TestNoopProbe:
    def test_success(self):
        c = FakeClient()
        row = store_rows()[0]
        r = noop_probe(c, 17417, row)
        assert isinstance(r, ProbeResult)
        assert r.ok is True
        assert c.calls == [(17417, row)]

    def test_wrong_id_column_fails(self):
        c = FakeClient(
            STGError("HTTP 500: Error: id column data not found")
        )
        r = noop_probe(c, 17620, {"SbjNum": 1})
        assert r.ok is False
        assert "id column data not found" in r.message

    def test_empty_store_is_not_probeable(self):
        c = FakeClient()
        r = noop_probe(c, 17620, None)
        assert r.ok is False
        assert c.calls == []
