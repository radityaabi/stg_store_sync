"""Tests for store_sync.cli — registry-backed subcommands (spec §7)."""
from __future__ import annotations

import pytest

from store_sync import cli
from store_sync.cli import _exit_for, build_parser, main
from store_sync.core.config import (
    BindingConfig,
    ColumnBinding,
    JobConfig,
    Schedule,
    SourceConfig,
    StoreConfig,
    WriteConfig,
)
from store_sync.core.planner import Plan
from store_sync.core.registry import Registry
from store_sync.core.sync import SyncResult


def a_job(name="a", *, dry_run=True, store_id=17620):
    return JobConfig(
        name,
        StoreConfig(store_id, "No"),
        SourceConfig("SURV"),
        BindingConfig(
            ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")],
        ),
        WriteConfig(dry_run=dry_run),
    )


def add_job(db, name="a", *, dry_run=True, **kw):
    r = Registry(db)
    r.add(a_job(name, dry_run=dry_run), **kw)
    r.close()


def set_creds(monkeypatch):
    monkeypatch.setenv("STG_SERVER", "https://example.test")
    monkeypatch.setenv("STG_USER", "u")
    monkeypatch.setenv("STG_PASSWORD", "p")


def clear_creds(monkeypatch):
    for var in ("STG_SERVER", "STG_USER", "STG_PASSWORD"):
        monkeypatch.delenv(var, raising=False)


class _DummyClient:
    """Stand-in for STGClient so no network is touched."""

    def __init__(self, **kw):
        self.kwargs = kw
        self.session = _DummySession()


class _DummySession:
    def close(self):
        pass


def _result(status, *, dry_run=True, counts=None, errors=None):
    return SyncResult(
        plan=Plan(),
        dry_run=dry_run,
        status=status,
        counts=counts or {},
        errors=errors or [],
    )


class TestParser:
    def test_parser_subcommands(self):
        p = build_parser()
        assert p.parse_args(["list"]).command == "list"
        assert p.parse_args(["run", "a"]).name == "a"
        ns = p.parse_args(["run-all", "--enabled-only"])
        assert ns.command == "run-all" and ns.enabled_only is True
        assert p.parse_args(["stop", "a"]).name == "a"

    def test_run_dry_run_override_parses(self):
        p = build_parser()
        assert p.parse_args(["run", "a"]).dry_run is None
        assert p.parse_args(["run", "a", "--no-dry-run"]).dry_run is False
        assert p.parse_args(["run", "a", "--dry-run"]).dry_run is True


class TestExitCodes:
    def test_exit_codes(self):
        assert _exit_for("ok") == 0
        assert _exit_for("blocked") == 2
        assert _exit_for("partial") == 3
        assert _exit_for("error") == 3

    def test_usage_error_is_1(self):
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 1


class TestList:
    def test_list_subcommand(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a", schedule=Schedule("minute", 5), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        rc = main(["list"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "a" in out

    def test_list_empty_registry_is_0(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(tmp_path / "empty.db"))
        assert main(["list"]) == 0


class TestRun:
    def test_run_unknown_job_is_3(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(tmp_path / "s.db"))
        assert main(["run", "nope"]) == 3

    def test_run_missing_creds_is_1(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        clear_creds(monkeypatch)
        assert main(["run", "a"]) == 1

    def test_run_blocked_is_2(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        monkeypatch.setattr(
            cli, "run_sync", lambda job, **kw: _result("blocked", dry_run=False)
        )
        assert main(["run", "a"]) == 2

    def test_run_ok_is_0(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a", dry_run=False)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        monkeypatch.setattr(cli, "run_sync", lambda job, **kw: _result("ok"))
        assert main(["run", "a", "--dry-run"]) == 0

    def test_run_no_dry_run_override(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a", dry_run=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        seen = {}

        def fake_run_sync(job, **kw):
            seen["dry_run"] = job.write.dry_run
            return _result("ok", dry_run=job.write.dry_run)

        monkeypatch.setattr(cli, "run_sync", fake_run_sync)
        assert main(["run", "a", "--no-dry-run"]) == 0
        assert seen["dry_run"] is False


class TestRunAll:
    def test_run_all_worst_code(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        add_job(db, "b")
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)

        def fake_run_sync(job, **kw):
            return _result("ok" if job.job == "a" else "blocked")

        monkeypatch.setattr(cli, "run_sync", fake_run_sync)
        assert main(["run-all"]) == 2

    def test_run_all_enabled_only_none(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a", enabled=False)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        assert main(["run-all", "--enabled-only"]) == 0


class TestStop:
    def test_stop_unknown_is_3(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(tmp_path / "s.db"))
        assert main(["stop", "nope"]) == 3

    def test_stop_known_is_0(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        assert main(["stop", "a"]) == 0


class TestLocks:
    def test_acquire_lock_holds(self, tmp_path):
        import filelock

        lock = cli._acquire_lock(str(tmp_path / "x.lock"))
        lock.acquire()
        try:
            holder = filelock.FileLock(str(tmp_path / "x.lock"))
            with pytest.raises(filelock.Timeout):
                holder.acquire(timeout=0.1)
        finally:
            lock.release()
