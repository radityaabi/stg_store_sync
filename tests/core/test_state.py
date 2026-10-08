"""Tests for store_sync.core.state — SQLite persistence.

jobs, run log, conflicts, and per-row hashes for `tracked_only` + idempotency.
Stable hashes across runs.
"""
from __future__ import annotations

import pytest

from store_sync.core.state import State


@pytest.fixture
def st(tmp_path):
    s = State(tmp_path / "state.db")
    yield s
    s.close()


class TestRuns:
    def test_record_and_read_run(self, st):
        rid = st.start_run(job="link1", dry_run=True)
        st.finish_run(rid, status="ok", counts={"add": 1, "update": 2})
        run = st.get_run(rid)
        assert run["job"] == "link1"
        assert run["dry_run"] is True
        assert run["status"] == "ok"
        assert run["counts"] == {"add": 1, "update": 2}
        assert run["finished_at"] is not None

    def test_recent_runs_ordered(self, st):
        for i in range(3):
            rid = st.start_run(job="j", dry_run=True)
            st.finish_run(rid, status="ok", counts={})
        runs = st.recent_runs("j", limit=2)
        assert len(runs) == 2
        assert runs[0]["id"] > runs[1]["id"]


class TestConflicts:
    def test_record_conflict(self, st):
        rid = st.start_run(job="j", dry_run=True)
        st.record_conflict(rid, match_value="103", reason="dup key in store")
        st.finish_run(rid, status="blocked", counts={})
        c = st.conflicts_for_run(rid)
        assert len(c) == 1
        assert c[0]["match_value"] == "103"
        assert c[0]["reason"] == "dup key in store"


class TestHashes:
    def test_hash_stable_across_runs(self, st, tmp_path):
        h1 = st.row_hash({"a": 1, "b": "x"})
        st.close()
        st2 = State(tmp_path / "state.db")
        h2 = st2.row_hash({"b": "x", "a": 1})  # key order irrelevant
        assert h1 == h2
        st2.close()

    def test_hash_changes_with_value(self, st):
        assert st.row_hash({"a": 1}) != st.row_hash({"a": 2})

    def test_tracked_rows_persist(self, st):
        st.track_row(job="j", match_value="101", row_hash="h1")
        st.track_row(job="j", match_value="102", row_hash="h2")
        assert st.tracked_keys("j") == {"101", "102"}
        st.untrack_row(job="j", match_value="101")
        assert st.tracked_keys("j") == {"102"}

    def test_upsert_tracked_row_updates_hash(self, st):
        st.track_row(job="j", match_value="101", row_hash="h1")
        st.track_row(job="j", match_value="101", row_hash="h2")
        assert st.tracked_keys("j") == {"101"}
        assert st.tracked_hash("j", "101") == "h2"
