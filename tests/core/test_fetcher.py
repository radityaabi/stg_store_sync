"""Tests for store_sync.core.fetcher — survey index, pull-term derivation,
EAV flattening, filtered pull with fallback, and projection.

All against a fake client; no live calls.
"""
from __future__ import annotations

import pytest

from store_sync.core.fetcher import (
    PullResult,
    derive_pull_terms,
    flatten_subject,
    parse_survey_index,
    pull,
)

# A small survey XML: one question QPM1 (id 762) with three answers, plus a
# free question Q32 with an others-specify child.
SURVEY_XML = """<?xml version="1.0"?>
<Survey>
  <SQ>
    <QuestionID>762</QuestionID>
    <VariableName>QPM1</VariableName>
  </SQ>
  <STo>
    <QuestionID>762</QuestionID>
    <VariableName>T_QPM1_1</VariableName>
  </STo>
  <STo>
    <QuestionID>762</QuestionID>
    <VariableName>T_QPM1_2</VariableName>
  </STo>
  <STo>
    <QuestionID>762</QuestionID>
    <VariableName>T_QPM1_3</VariableName>
  </STo>
  <SQ>
    <QuestionID>900</QuestionID>
    <VariableName>Q32</VariableName>
  </SQ>
</Survey>
"""

# A grid/matrix survey: a question Q2 (id 603) whose answers are `<SAN>`
# blocks (not `<STo>`), each optionally carrying an `OtherSpecVarName`
# (`Q2_2_TEXT`). STG emits `<SAN>` for grid answers — a real survey uses tens
# of thousands of them — so a parser that reads only `<STo>` resolves none.
GRID_XML = """<?xml version="1.0"?>
<Survey>
  <SQ>
    <QuestionID>603</QuestionID>
    <VariableName>Q2</VariableName>
    <OtherSpecVarName>Q2_OTHER</OtherSpecVarName>
  </SQ>
  <SAN>
    <QuestionID>603</QuestionID>
    <VariableName>Q2_1</VariableName>
  </SAN>
  <SAN>
    <QuestionID>603</QuestionID>
    <VariableName>Q2_2</VariableName>
    <OtherSpecVarName>Q2_2_TEXT</OtherSpecVarName>
  </SAN>
</Survey>
"""


def subject(sid, pairs):
    return {"SubjectID": sid, "Columns": [{"Var": v, "Value": x} for v, x in pairs]}


# --- survey index -----------------------------------------------------------

class TestParseSurveyIndex:
    def test_answer_vars_map_to_question_base(self):
        idx = parse_survey_index(SURVEY_XML)
        assert idx["T_QPM1_1"] == "QPM1"
        assert idx["T_QPM1_2"] == "QPM1"
        assert idx["T_QPM1_3"] == "QPM1"

    def test_question_var_maps_to_itself(self):
        idx = parse_survey_index(SURVEY_XML)
        assert idx["Q32"] == "Q32"

    def test_accepts_bytes(self):
        assert parse_survey_index(SURVEY_XML.encode())["T_QPM1_1"] == "QPM1"

    def test_san_grid_answers_map_to_question_base(self):
        # Grid/matrix answers live in <SAN>, not <STo>.
        idx = parse_survey_index(GRID_XML)
        assert idx["Q2_1"] == "Q2"
        assert idx["Q2_2"] == "Q2"

    def test_otherspec_var_maps_to_owning_question_base(self):
        # The "other, specify" text var is exportable and must resolve to the
        # question it belongs to, not stay unresolved.
        idx = parse_survey_index(GRID_XML)
        assert idx["Q2_2_TEXT"] == "Q2"
        assert idx["Q2_OTHER"] == "Q2"


# --- derivation -------------------------------------------------------------

class TestDerivePullTerms:
    def test_resolves_answer_vars_to_base_terms(self):
        idx = parse_survey_index(SURVEY_XML)
        terms, unresolved = derive_pull_terms(
            ["T_QPM1_1", "T_QPM1_3", "Q32"], idx
        )
        assert terms == ["QPM1", "Q32"]
        assert unresolved == []

    def test_dedupes_terms_preserving_order(self):
        idx = parse_survey_index(SURVEY_XML)
        terms, _ = derive_pull_terms(["T_QPM1_3", "T_QPM1_1"], idx)
        assert terms == ["QPM1"]

    def test_unresolved_var_reported(self):
        idx = parse_survey_index(SURVEY_XML)
        terms, unresolved = derive_pull_terms(["T_QPM1_1", "I_351_Q46a"], idx)
        assert terms == ["QPM1"]
        assert unresolved == ["I_351_Q46a"]

    def test_always_includes_match_key(self):
        idx = parse_survey_index(SURVEY_XML)
        terms, _ = derive_pull_terms(["T_QPM1_1"], idx, match_var="SbjNum")
        assert "SbjNum" in terms


# --- flatten ----------------------------------------------------------------

class TestFlattenSubject:
    def test_eav_to_flat_dict(self):
        s = subject("1", [("SbjNum", 1), ("Q32", 4), ("T_QPM1_1", "A")])
        assert flatten_subject(s) == {"SbjNum": 1, "Q32": 4, "T_QPM1_1": "A"}

    def test_missing_vars_are_absent(self):
        s = subject("1", [("SbjNum", 1)])
        assert flatten_subject(s) == {"SbjNum": 1}

    def test_empty_value_kept(self):
        s = subject("1", [("Q42_TEXT", "")])
        assert flatten_subject(s) == {"Q42_TEXT": ""}


# --- pull -------------------------------------------------------------------

class FakeClient:
    def __init__(self, subjects, index=None):
        self.subjects = subjects
        self.index = index or {}
        self.calls = []

    def _keep(self, var, terms):
        return self.index.get(var, var) in terms

    def simple_export(self, survey_id, ids, *, variable_names=None, **kw):
        self.calls.append({"survey_id": survey_id, "ids": list(ids), "variable_names": variable_names})
        if variable_names is None:
            return [s for s in self.subjects if s["SubjectID"] in ids]
        # emulate STG: filter by question-level base name family
        terms = set(variable_names)
        out = []
        for s in self.subjects:
            if s["SubjectID"] not in ids:
                continue
            kept = [c for c in s["Columns"] if self._keep(c["Var"], terms)]
            if kept:
                out.append({"SubjectID": s["SubjectID"], "Columns": kept})
        return out


def full_subjects():
    return [
        subject("1", [("SbjNum", 1), ("T_QPM1_1", "A"), ("T_QPM1_3", "0811"), ("Q32", 4)]),
        subject("2", [("SbjNum", 2), ("T_QPM1_1", "B"), ("Q32", 7)]),
    ]


class TestPull:
    def test_filtered_pull_uses_derived_terms(self):
        idx = parse_survey_index(SURVEY_XML)
        c = FakeClient(full_subjects(), idx)
        res = pull(
            c, "SURV", ["1", "2"],
            source_vars=["T_QPM1_1", "T_QPM1_3", "Q32"],
            match_var="SbjNum",
            survey_index=idx,
        )
        assert isinstance(res, PullResult)
        assert res.fell_back is False
        assert set(res.pull_terms) == {"QPM1", "Q32", "SbjNum"}
        assert c.calls[0]["variable_names"] is not None
        assert len(res.records) == 2

    def test_records_are_flattened(self):
        idx = parse_survey_index(SURVEY_XML)
        c = FakeClient(full_subjects())
        res = pull(c, "S", ["1"], source_vars=["T_QPM1_1"], match_var="SbjNum",
                   survey_index=idx)
        assert res.records[0]["T_QPM1_1"] == "A"
        assert res.records[0]["SbjNum"] == 1

    def test_missing_needed_var_triggers_full_fallback(self):
        idx = parse_survey_index(SURVEY_XML)
        # T_QPM1_9 is absent from the index -> unresolved -> full export
        c = FakeClient(full_subjects())
        res = pull(
            c, "S", ["1", "2"],
            source_vars=["T_QPM1_9"],
            match_var="SbjNum",
            survey_index=idx,
        )
        assert res.fell_back is True
        assert res.unresolved == ["T_QPM1_9"]
        assert c.calls[-1]["variable_names"] is None
        assert len(res.records) == 2

    def test_filtered_pull_missing_var_also_falls_back(self):
        # Even a resolvable term can be dropped by the server -> verify + fallback.
        idx = parse_survey_index(SURVEY_XML)
        subjects = [
            subject("1", [("SbjNum", 1), ("Q32", 4)]),  # T_QPM1_* not returned
        ]
        c = FakeClient(subjects)
        res = pull(
            c, "S", ["1"],
            source_vars=["T_QPM1_1", "Q32"],
            match_var="SbjNum",
            survey_index=idx,
        )
        assert res.fell_back is True
        assert c.calls[-1]["variable_names"] is None

    def test_no_source_vars_skips_filter(self):
        c = FakeClient(full_subjects())
        res = pull(c, "S", ["1", "2"], source_vars=[], match_var="SbjNum")
        assert c.calls[-1]["variable_names"] is None
        assert len(res.records) == 2

    def test_projection_to_columns(self):
        from store_sync.core.config import ColumnBinding
        idx = parse_survey_index(SURVEY_XML)
        c = FakeClient(full_subjects())
        res = pull(
            c, "S", ["1"],
            source_vars=["T_QPM1_1", "T_QPM1_3"],
            match_var="SbjNum",
            survey_index=idx,
            columns=[
                ColumnBinding("T_QPM1_1", "I7_1_2"),
                ColumnBinding("T_QPM1_3", "I8a_1"),
                ColumnBinding("Q42_TEXT", "OTHER"),  # absent -> ""
            ],
        )
        rec = res.projected[0]
        assert rec == {"I7_1_2": "A", "I8a_1": "0811", "OTHER": ""}


# --- pairing ----------------------------------------------------------------

class TestPairColumns:
    def test_fills_terms_for_resolvable_vars(self):
        from store_sync.core.fetcher import pair_columns
        idx = parse_survey_index(SURVEY_XML)
        terms, unresolved = pair_columns(["T_QPM1_1", "T_QPM1_3", "Q32"], idx)
        assert terms == {"T_QPM1_1": "QPM1", "T_QPM1_3": "QPM1", "Q32": "Q32"}
        assert unresolved == []

    def test_unresolved_var_gets_empty_term_and_is_reported(self):
        from store_sync.core.fetcher import pair_columns
        idx = parse_survey_index(SURVEY_XML)
        terms, unresolved = pair_columns(["T_QPM1_1", "I_351_Q46a"], idx)
        assert terms == {"T_QPM1_1": "QPM1", "I_351_Q46a": ""}
        assert unresolved == ["I_351_Q46a"]
