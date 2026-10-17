"""Tests for store_sync.core.automap — name-based column auto-mapping."""
from __future__ import annotations

from store_sync.core.automap import auto_map
from store_sync.core.config import ColumnBinding

INDEX = {
    "Q32": "Q32",           # question base name (also a var)
    "QPM1": "QPM1",         # question base name
    "T_QPM1_1": "QPM1",     # its single answer
    "T_QPM1_2": "QPM1",
    "T_Q10_1": "Q10_1",     # one answer -> its own base
    "T_Q10_2": "Q10_1",
    "SbjNum": "SbjNum",
}


def test_exact_name_wins():
    res = auto_map(["Q32", "SbjNum"], INDEX)
    assert [(c.store_col, c.source_var) for c in res.columns] == [
        ("Q32", "Q32"),
        ("SbjNum", "SbjNum"),
    ]
    assert res.mapped == 2
    assert res.unmapped == []


def test_question_base_name_resolves_the_answer_var():
    # A base name that is not itself a var resolves to its single answer var.
    res = auto_map(["Q10_1"], {"T_Q10_1": "Q10_1"})
    assert res.columns[0].source_var == "T_Q10_1"
    assert res.columns[0].pull_term == "Q10_1"


def test_ambiguous_base_is_never_guessed():
    # Two answer vars under one base -> unmapped, no arbitrary pick.
    res = auto_map(["A"], {"T_A_1": "A", "T_A_2": "A"})
    assert res.columns[0].source_var == ""
    assert res.columns[0].store_col == "A"
    assert res.unmapped == ["A"]


def test_exact_var_wins_over_base_expansion():
    # QPM1 is a real variable; it binds as itself, not to an answer var.
    assert auto_map(["QPM1"], INDEX).columns[0].source_var == "QPM1"


def test_unknown_column_is_kept_but_unmapped():
    res = auto_map(["I7_1_2", "Q32"], INDEX)
    assert [c.store_col for c in res.columns] == ["I7_1_2", "Q32"]
    assert res.columns[0].source_var == ""
    assert res.unmapped == ["I7_1_2"]
    assert res.mapped == 1


def test_pull_term_comes_from_the_index():
    res = auto_map(["QPM2"], {"T_QPM2_1": "QPM2"})
    assert res.columns[0].source_var == "T_QPM2_1"
    assert res.columns[0].pull_term == "QPM2"


def test_skip_columns_are_never_bindable():
    res = auto_map(["No", "SbjNum", "Q32"], INDEX, skip={"No", "SbjNum"})
    assert [c.store_col for c in res.columns] == ["Q32"]


def test_store_order_is_preserved_and_duplicates_dropped():
    res = auto_map(["Q32", "Q32", "SbjNum"], INDEX)
    assert [c.store_col for c in res.columns] == ["Q32", "SbjNum"]


def test_merge_keeps_manual_source_var():
    existing = [ColumnBinding("T_Q32_SPECIAL", "Q32")]
    res = auto_map(["Q32"], INDEX, existing=existing, merge=True)
    assert res.columns[0].source_var == "T_Q32_SPECIAL"


def test_replace_mode_rebuilds_from_the_index():
    existing = [ColumnBinding("T_Q32_SPECIAL", "Q32")]
    res = auto_map(["Q32"], INDEX, existing=existing, merge=False)
    assert res.columns[0].source_var == "Q32"


def test_dropped_reports_old_columns_absent_from_the_store():
    existing = [ColumnBinding("OLD", "GONE"), ColumnBinding("Q32", "Q32")]
    res = auto_map(["Q32"], INDEX, existing=existing)
    assert res.dropped == ["GONE"]


def test_empty_store_yields_an_empty_grid():
    res = auto_map([], INDEX)
    assert res.columns == []
    assert res.total == 0


def test_empty_index_leaves_every_column_unmapped():
    res = auto_map(["Q32", "SbjNum"], {})
    assert res.mapped == 0
    assert res.unmapped == ["Q32", "SbjNum"]
