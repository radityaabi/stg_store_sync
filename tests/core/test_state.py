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

    def test_recording_same_key_supersedes_previous_pending(self, st):
        # A later run re-proposing the same key must leave exactly one pending
        # row (the freshest), so the GUI never shows the same id twice.
        r1 = st.start_run(job="j", dry_run=False)
        old = self._record(st, run_id=r1, id_value=2)
        r2 = st.start_run(job="j", dry_run=False)
        new = st.record_proposal(
            r2, job="j", match_value="102", id_value=2,
            payload={"No": 2, "SbjNum": "102", "Q32": "77"},
            diff=[{"col": "Q32", "old": "22", "new": "77"}],
        )
        pending = st.pending_proposals("j")
        assert len(pending) == 1
        assert pending[0]["id"] == new
        assert pending[0]["diff"] == [{"col": "Q32", "old": "22", "new": "77"}]
        assert st.get_proposal(old)["status"] == "superseded"

    def test_superseding_keeps_other_keys_and_decided_rows(self, st):
        rid = st.start_run(job="j", dry_run=False)
        other = self._record(st, run_id=rid, match="999", id_value=9)
        decided = self._record(st, run_id=rid, match="888", id_value=8)
        st.set_proposal_status(decided, "applied")
        self._record(st, run_id=rid)  # "102"
        self._record(st, run_id=rid)  # "102" again → supersedes the first
        pending = [p["match_value"] for p in st.pending_proposals("j")]
        assert sorted(pending) == ["102", "999"]
        assert st.get_proposal(other)["status"] == "pending"
        assert st.get_proposal(decided)["status"] == "applied"


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

    def test_reconcile_empty_store_keys_is_a_noop(self, st):
        # An empty store snapshot is *unknown*, not a mass deletion.
        st.track_row(job="j", match_value="104", row_hash="h")
        added, removed = st.reconcile_blacklist("j", set())
        assert added == set()
        assert st.blacklisted_keys("j") == set()

    def test_unblacklist_forgets_tracking_so_it_is_not_rediscovered(self, st):
        # The operator removing a key means "re-sync it". Without forgetting the
        # tracking record the next reconcile re-derives the same deletion and
        # puts the key straight back into the blacklist.
        st.track_row(job="j", match_value="104", row_hash="h")
        assert st.reconcile_blacklist("j", {"101", "102"})[0] == {"104"}
        st.unblacklist_key("j", "104")                 # operator decision
        assert st.tracked_keys("j") == set()
        assert st.reconcile_blacklist("j", {"101", "102"})[0] == set()
        assert st.blacklisted_keys("j") == set()

    def test_auto_clear_keeps_tracking_so_a_second_deletion_is_detected(self, st):
        # Auto-clear is not an operator decision: a key re-added by hand stays
        # tracked, so deleting it again by hand is still detected.
        st.track_row(job="j", match_value="104", row_hash="h")
        st.blacklist_key("j", "104", reason="deleted")
        assert st.reconcile_blacklist("j", {"101", "104"})[1] == {"104"}
        assert st.tracked_keys("j") == {"104"}
        assert st.reconcile_blacklist("j", {"101"})[0] == {"104"}

    def test_reblacklist_preserves_added_at(self, st):
        st.blacklist_key("j", "104", reason="deleted")
        st.conn.execute(
            "UPDATE blacklist SET added_at = '2020-01-01 00:00:00' "
            "WHERE job = 'j' AND match_value = '104'"
        )
        st.conn.commit()
        st.blacklist_key("j", "104", reason="deleted")  # re-detected
        assert st.list_blacklist("j")[0]["added_at"] == "2020-01-01 00:00:00"

    def test_reblacklist_preserves_manual_reason(self, st):
        st.blacklist_key("j", "104", reason="manual")
        st.blacklist_key("j", "104", reason="deleted")  # auto re-detect
        assert st.list_blacklist("j")[0]["reason"] == "manual"

    def test_manual_reason_upgrades_a_deleted_entry(self, st):
        st.blacklist_key("j", "104", reason="deleted")
        st.blacklist_key("j", "104", reason="manual")  # operator confirms
        assert st.list_blacklist("j")[0]["reason"] == "manual"


class TestOsFired:
    def test_first_claim_wins_second_in_same_minute_loses(self, st):
        assert st.claim_due("j", "2026-10-12T10:40") is True
        assert st.claim_due("j", "2026-10-12T10:40") is False

    def test_different_minute_claims_again(self, st):
        assert st.claim_due("j", "2026-10-12T10:40") is True
        assert st.claim_due("j", "2026-10-12T10:41") is True

    def test_claim_is_per_job(self, st):
        assert st.claim_due("a", "2026-10-12T10:40") is True
        assert st.claim_due("b", "2026-10-12T10:40") is True

    def test_prune_removes_old_keys(self, st):
        st.claim_due("j", "2026-10-11T00:00")
        st.claim_due("j", "2026-10-12T10:40")
        removed = st.prune_os_fired("2026-10-12T00:00")
        assert removed == 1
        assert st.claim_due("j", "2026-10-11T00:00") is True  # pruned → reclaimable

class TestNotifications:
    def test_record_notification_round_trips(self, st):
        rid = st.start_run(job="j", dry_run=False)
        nid = st.record_notification(rid, job="j", status="sent",
                                     recipients="a@x.com", subject="S", body_html="<p>")
        assert nid > 0
        row = st.notification_for_run(rid)
        assert row["status"] == "sent" and row["recipients"] == "a@x.com"

    def test_notification_for_run_returns_most_recent(self, st):
        rid = st.start_run(job="j", dry_run=False)
        st.record_notification(rid, job="j", status="skipped", reason="no adds")
        st.record_notification(rid, job="j", status="sent")
        assert st.notification_for_run(rid)["status"] == "sent"
        assert [r["status"] for r in st.notifications_for_run(rid)] == ["skipped", "sent"]

    def test_notification_for_run_is_none_when_absent(self, st):
        rid = st.start_run(job="j", dry_run=False)
        assert st.notification_for_run(rid) is None


def test_connection_busy_timeout_allows_concurrent_writers(tmp_path):
    """Concurrent runs hold write locks briefly; the wait must be 30 s, not 5."""
    from store_sync.core.state import State

    st = State(tmp_path / "s.db")
    try:
        busy_ms = st.conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert busy_ms >= 30_000
    finally:
        st.close()


def _traced_commits(st):
    """Collect the SQL statements SQLite actually commits."""
    seen: list[str] = []
    st.conn.set_trace_callback(lambda sql: seen.append(sql) if sql.strip().upper() == "COMMIT" else None)
    return seen


class TestTransaction:
    def test_bulk_writes_commit_once(self, tmp_path):
        # The run path tracks/adds thousands of rows; one fsync per row made
        # that ~6 s. Inside `transaction()` the whole batch is one commit.
        st = State(tmp_path / "s.db")
        try:
            commits = _traced_commits(st)
            with st.transaction():
                for i in range(50):
                    st.track_row(job="j", match_value=str(i), row_hash="h")
            assert len(commits) == 1
            assert st.tracked_keys("j") == {str(i) for i in range(50)}
        finally:
            st.close()

    def test_transaction_rolls_back_on_error(self, tmp_path):
        st = State(tmp_path / "s.db")
        try:
            with pytest.raises(RuntimeError):
                with st.transaction():
                    st.track_row(job="j", match_value="keep", row_hash="h")
                    raise RuntimeError("boom")
            assert st.tracked_keys("j") == set()
        finally:
            st.close()

    def test_transaction_is_reentrant(self, tmp_path):
        st = State(tmp_path / "s.db")
        try:
            commits = _traced_commits(st)
            with st.transaction():
                st.track_row(job="j", match_value="a", row_hash="h")
                with st.transaction():
                    st.track_row(job="j", match_value="b", row_hash="h")
            assert len(commits) == 1
            assert st.tracked_keys("j") == {"a", "b"}
        finally:
            st.close()

    def test_write_outside_transaction_still_commits(self, tmp_path):
        st = State(tmp_path / "s.db")
        try:
            st.track_row(job="j", match_value="a", row_hash="h")
            assert st.tracked_keys("j") == {"a"}
        finally:
            st.close()
