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

    def test_recover_orphan_runs_marks_unfinished(self, st):
        done = st.start_run(job="j", dry_run=True)
        st.finish_run(done, status="ok", counts={})
        orphan = st.start_run(job="j", dry_run=False)  # crash mid-run
        assert st.recover_orphan_runs() == 1
        recovered = st.get_run(orphan)
        assert recovered["status"] == "interrupted"
        assert recovered["finished_at"] is not None
        assert st.get_run(done)["status"] == "ok"  # untouched

    def test_recover_orphan_runs_noop_when_clean(self, st):
        rid = st.start_run(job="j", dry_run=True)
        st.finish_run(rid, status="ok", counts={})
        assert st.recover_orphan_runs() == 0


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


class TestProposals:
    def _record(self, st, *, run_id, job="j", match="102", id_value=2):
        return st.record_proposal(
            run_id,
            job=job,
            match_value=match,
            id_value=id_value,
            payload={"No": id_value, "SbjNum": match, "Q32": "99"},
            diff=[{"col": "Q32", "old": "22", "new": "99"}],
        )

    def test_record_and_read_pending(self, st):
        rid = st.start_run(job="j", dry_run=False)
        pid = self._record(st, run_id=rid)
        pending = st.pending_proposals("j")
        assert len(pending) == 1
        assert pending[0]["id"] == pid
        assert pending[0]["match_value"] == "102"
        assert pending[0]["payload"]["Q32"] == "99"
        assert pending[0]["diff"] == [{"col": "Q32", "old": "22", "new": "99"}]
        assert pending[0]["status"] == "pending"

    def test_status_transition_removes_from_pending(self, st):
        rid = st.start_run(job="j", dry_run=False)
        pid = self._record(st, run_id=rid)
        st.set_proposal_status(pid, "applied")
        assert st.pending_proposals("j") == []
        got = st.get_proposal(pid)
        assert got["status"] == "applied"
        assert got["decided_at"] is not None

    def test_pending_scoped_to_job(self, st):
        rid = st.start_run(job="j", dry_run=False)
        self._record(st, run_id=rid, job="j")
        self._record(st, run_id=rid, job="other", match="9")
        assert [p["match_value"] for p in st.pending_proposals("j")] == ["102"]

    def test_proposals_for_run(self, st):
        rid = st.start_run(job="j", dry_run=False)
        self._record(st, run_id=rid)
        assert len(st.proposals_for_run(rid)) == 1


class TestBlacklist:
    def test_add_list_and_remove(self, st):
        st.blacklist_key("j", "104", reason="deleted")
        st.blacklist_key("j", "105")  # manual default
        rows = st.list_blacklist("j")
        assert [r["match_value"] for r in rows] == ["104", "105"]
        assert rows[0]["reason"] == "deleted"
        assert rows[1]["reason"] == "manual"
        assert st.blacklisted_keys("j") == {"104", "105"}
        st.unblacklist_key("j", "104")
        assert st.blacklisted_keys("j") == {"105"}

    def test_blacklist_is_per_job(self, st):
        st.blacklist_key("a", "1")
        assert st.blacklisted_keys("b") == set()

    def test_reconcile_blacklists_tracked_key_missing_from_store(self, st):
        st.track_row(job="j", match_value="104", row_hash="h")
        added, removed = st.reconcile_blacklist("j", {"101", "102"})
        assert added == {"104"}
        assert removed == set()
        assert st.blacklisted_keys("j") == {"104"}
        assert st.list_blacklist("j")[0]["reason"] == "deleted"

    def test_reconcile_clears_blacklisted_key_back_in_store(self, st):
        st.blacklist_key("j", "104", reason="deleted")
        added, removed = st.reconcile_blacklist("j", {"101", "104"})
        assert added == set()
        assert removed == {"104"}
        assert st.blacklisted_keys("j") == set()

    def test_reconcile_leaves_untracked_key_alone(self, st):
        # 103 was never tracked (never written by the system) → not blacklisted.
        added, removed = st.reconcile_blacklist("j", {"101", "102"})
        assert added == set()
        assert st.blacklisted_keys("j") == set()
