"""Tests for store_sync.core.preflight — the preflight review table + gate.

Combines the binding checks (id column, match key, strict) with a per-column
review table over live store rows and a sample of flattened source records.
"""
from __future__ import annotations

from store_sync.core.config import (
    BindingConfig,
    ColumnBinding,
    JobConfig,
    SourceConfig,
    StoreConfig,
    WriteConfig,
)
from store_sync.core.preflight import ReviewRow, preflight


def store_rows():
    return [
        {"No": 1, "uniqueID": "1001", "SbjNum": "1", "I7_1_2": "Shinta", "I8a_1": "8151883872", "Q32": 4},
        {"No": 2, "uniqueID": "1002", "SbjNum": "2", "I7_1_2": "Jefta", "I8a_1": "8123456789", "Q32": 7},
        {"No": 3, "uniqueID": "1003", "SbjNum": "3", "I7_1_2": "Budi", "I8a_1": "8111111111", "Q32": 9},
    ]


def source_records():
    return [
        {"SbjNum": "1", "T_QPM1_1": "Shinta", "T_QPM1_2": "1001", "T_QPM1_3": "08151883872", "Q32": 4},
        {"SbjNum": "2", "T_QPM1_1": "Jefta", "T_QPM1_2": "1002", "T_QPM1_3": "08123456789", "Q32": 7},
        {"SbjNum": "3", "T_QPM1_1": "Budi", "T_QPM1_2": "1003", "T_QPM1_3": "08111111111", "Q32": 9},
    ]


def make_config(*, id_column="uniqueID", mode="mapped", columns=None):
    return JobConfig(
        job="link1",
        store=StoreConfig(id=17417, id_column=id_column),
        source=SourceConfig(survey_id="S"),
        binding=BindingConfig(
            mode=mode,
            match=ColumnBinding("SbjNum", "SbjNum"),
            columns=columns
            if columns is not None
            else [
                ColumnBinding("T_QPM1_1", "I7_1_2"),
                ColumnBinding("T_QPM1_3", "I8a_1"),
                ColumnBinding("Q32", "Q32"),
            ],
        ),
        write=WriteConfig(),
    )


class FakeClient:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def update_row(self, store_id, values):
        self.calls.append((store_id, dict(values)))
        if self.error:
            raise self.error
        return {"Done": True}


class TestTable:
    def test_builds_review_row_per_configured_column(self):
        rep = preflight(
            store_rows=store_rows(),
            source_records=source_records(),
            config=make_config(),
        )
        assert [r.store_col for r in rep.table] == ["I7_1_2", "I8a_1", "Q32"]
        assert all(isinstance(r, ReviewRow) for r in rep.table)

    def test_exact_column_status_ok(self):
        rep = preflight(
            store_rows=store_rows(),
            source_records=source_records(),
            config=make_config(),
        )
        by = {r.store_col: r for r in rep.table}
        assert by["Q32"].status == "ok"
        assert by["Q32"].overlap == 3

    def test_leading_zero_column_flagged_format_differs(self):
        rep = preflight(
            store_rows=store_rows(),
            source_records=source_records(),
            config=make_config(),
        )
        by = {r.store_col: r for r in rep.table}
        assert by["I8a_1"].status == "format_differs"

    def test_missing_source_var_flagged(self):
        cfg = make_config(columns=[ColumnBinding("NOPE", "Q32")])
        rep = preflight(
            store_rows=store_rows(), source_records=source_records(), config=cfg
        )
        assert rep.table[0].status == "missing_source_var"

    def test_missing_store_col_flagged(self):
        cfg = make_config(columns=[ColumnBinding("Q32", "NOT_A_COL")])
        rep = preflight(
            store_rows=store_rows(), source_records=source_records(), config=cfg
        )
        assert rep.table[0].status == "missing_store_col"

    def test_identity_collision_flagged(self):
        cfg = make_config(columns=[ColumnBinding("X", "SbjNum")])
        rep = preflight(
            store_rows=store_rows(), source_records=source_records(), config=cfg
        )
        assert rep.table[0].status == "identity_collision"


class TestChecks:
    def test_id_column_ok(self):
        rep = preflight(
            store_rows=store_rows(), source_records=source_records(), config=make_config()
        )
        assert rep.id_check.exists is True
        assert rep.id_check.unique is True
        assert rep.ok is True

    def test_wrong_id_column_blocks(self):
        rep = preflight(
            store_rows=store_rows(),
            source_records=source_records(),
            config=make_config(id_column="not_there"),
        )
        assert rep.ok is False
        assert any("id" in e.lower() for e in rep.errors)

    def test_duplicate_id_values_block(self):
        rows = store_rows()
        rows[1]["uniqueID"] = "1001"
        rep = preflight(
            store_rows=rows, source_records=source_records(), config=make_config()
        )
        assert rep.ok is False

    def test_match_key_unique_in_store_and_source(self):
        rep = preflight(
            store_rows=store_rows(), source_records=source_records(), config=make_config()
        )
        assert rep.match_store.unique is True
        assert rep.match_source.unique is True

    def test_duplicate_match_key_in_source_blocks(self):
        src = source_records()
        src.append(dict(src[0]))
        rep = preflight(
            store_rows=store_rows(), source_records=src, config=make_config()
        )
        assert rep.match_source.unique is False
        assert rep.ok is False

    def test_duplicate_match_key_in_store_blocks(self):
        rows = store_rows()
        rows.append(dict(rows[0]))
        rep = preflight(
            store_rows=rows, source_records=source_records(), config=make_config()
        )
        assert rep.match_store.unique is False
        assert rep.ok is False


class TestStrict:
    def test_strict_mismatch_blocks(self):
        rep = preflight(
            store_rows=store_rows(),
            source_records=source_records(),
            config=make_config(mode="strict"),
        )
        assert rep.ok is False
        assert rep.strict_violations

    def test_strict_identical_names_passes(self):
        cfg = make_config(
            mode="strict",
            columns=[ColumnBinding("Q32", "Q32")],
        )
        rep = preflight(
            store_rows=store_rows(), source_records=source_records(), config=cfg
        )
        assert rep.strict_violations == []
        assert rep.ok is True


class TestProbe:
    def test_probe_runs_when_client_given(self):
        c = FakeClient()
        rep = preflight(
            store_rows=store_rows(),
            source_records=source_records(),
            config=make_config(),
            client=c,
            store_id=17417,
        )
        assert rep.probe is not None and rep.probe.ok is True
        assert len(c.calls) == 1

    def test_probe_failure_blocks(self):
        from store_sync.core.stg_client import STGError

        c = FakeClient(STGError("HTTP 500: Error: id column data not found"))
        rep = preflight(
            store_rows=store_rows(),
            source_records=source_records(),
            config=make_config(),
            client=c,
            store_id=17417,
        )
        assert rep.probe.ok is False
        assert rep.ok is False

    def test_no_probe_without_client(self):
        rep = preflight(
            store_rows=store_rows(), source_records=source_records(), config=make_config()
        )
        assert rep.probe is None
