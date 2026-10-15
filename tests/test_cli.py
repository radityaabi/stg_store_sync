"""Tests for store_sync.cli — registry-backed subcommands (spec §7)."""
from __future__ import annotations

import json
from datetime import datetime

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
from store_sync.core.state import State
from store_sync.core.sync import SyncResult


def _send_outcome(status):
    from store_sync.core.notify import SendOutcome

    return SendOutcome(status)


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch):
    """Keep the developer's local `.env` out of the CLI tests.

    `cli.main()` calls `load_dotenv()` unconditionally, which re-populates
    `STG_SERVER`/`STG_USER`/`STG_PASSWORD` from a real `.env` on disk and defeats
    a test's `clear_creds`. The tests set credentials explicitly, so neutralise
    the load for the whole module.
    """
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)


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


def set_enabled(db, name, *, schedule, enabled):
    reg = Registry(db)
    try:
        reg.set_schedule(name, schedule)
        reg.set_enabled(name, enabled)
    finally:
        reg.close()


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

    def test_run_reports_proposal_count(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a", dry_run=False)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)

        def fake_run_sync(job, **kw):
            res = _result("ok", dry_run=False, counts={"add": 1})
            res.proposals = 2
            return res

        monkeypatch.setattr(cli, "run_sync", fake_run_sync)
        assert main(["run", "a"]) == 0
        out = capsys.readouterr().out
        assert "2 update proposal(s)" in out

    def test_run_json_includes_proposals(self, tmp_path, capsys, monkeypatch):
        import json

        db = tmp_path / "s.db"
        add_job(db, "a", dry_run=False)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)

        def fake_run_sync(job, **kw):
            res = _result("ok", dry_run=False, counts={"add": 1})
            res.proposals = 3
            return res

        monkeypatch.setattr(cli, "run_sync", fake_run_sync)
        assert main(["run", "a", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["proposals"] == 3


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


class TestBlacklist:
    def test_blacklist_add_list_remove(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        add_job(db, "a")

        assert main(["blacklist-add", "a", "104"]) == 0
        capsys.readouterr()

        assert main(["blacklist", "a"]) == 0
        out = capsys.readouterr().out
        assert "104" in out and "manual" in out

        assert main(["blacklist-remove", "a", "104"]) == 0
        capsys.readouterr()
        assert main(["blacklist", "a"]) == 0
        assert "104" not in capsys.readouterr().out

    def test_blacklist_unknown_job_exits_3(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        assert main(["blacklist", "nope"]) == 3

    def test_blacklist_add_unknown_job_exits_3(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        assert main(["blacklist-add", "nope", "1"]) == 3

    def test_run_json_includes_suppressed(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)

        res = _result("ok")
        res.suppressed = 2
        monkeypatch.setattr(cli, "run_sync", lambda job, **kw: res)
        assert main(["run", "a", "--json"]) == 0
        import json

        payload = json.loads(capsys.readouterr().out)
        assert payload["suppressed"] == 2


class TestRunDue:
    def test_runs_only_due_jobs(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        ran = []
        monkeypatch.setattr(
            cli, "_run_one",
            lambda reg, st, c, name, dry, **kw: (ran.append(name), (0, None))[1],
        )
        monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 12, 10, 40))
        assert main(["run-due"]) == 0
        assert ran == ["a"]

    def test_not_due_does_not_run(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        ran = []
        monkeypatch.setattr(
            cli, "_run_one", lambda *a, **k: (ran.append(a[3]), (0, None))[1]
        )
        monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 12, 10, 41))
        assert main(["run-due"]) == 0
        assert ran == []

    def test_second_fire_same_minute_is_skipped(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        ran = []
        monkeypatch.setattr(
            cli, "_run_one", lambda *a, **k: (ran.append(a[3]), (0, None))[1]
        )
        monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 12, 10, 40))
        main(["run-due"])
        main(["run-due"])
        assert ran == ["a"]

    def test_job_filter(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        add_job(db, "b")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        set_enabled(db, "b", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        ran = []
        monkeypatch.setattr(
            cli, "_run_one", lambda *a, **k: (ran.append(a[3]), (0, None))[1]
        )
        monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 12, 10, 40))
        assert main(["run-due", "--job", "b"]) == 0
        assert ran == ["b"]

    def test_unknown_job_is_3(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        assert main(["run-due", "--job", "nope"]) == 3

    def test_missing_credentials_is_1(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        clear_creds(monkeypatch)
        monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 12, 10, 40))
        assert main(["run-due"]) == 1

    def test_env_resolves_from_foreign_cwd(self, tmp_path, monkeypatch):
        # A scheduled task starts in an arbitrary CWD; .env must still load.
        # NOTE: the module-level `_no_dotenv` fixture neutralises
        # `cli.load_dotenv`, so this test asserts the *path resolution*, not the
        # env values: `_load_env` must point at the project-root `.env`.
        monkeypatch.chdir(tmp_path)
        seen = {}
        monkeypatch.setattr(cli, "load_dotenv", lambda p=None, **k: seen.update(p=p))
        cli._load_env()
        assert seen["p"] is not None
        assert str(seen["p"]).endswith(".env")
        assert "stg_store_sync" in str(seen["p"])   # repo-root .env, not CWD


class TestScheduleCmd:
    def test_schedule_list_shows_status(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        monkeypatch.setattr(cli, "os_status", lambda entries: {"a": "missing"})
        assert main(["schedule", "list"]) == 0
        out = capsys.readouterr().out
        assert "a" in out and "missing" in out

    def test_schedule_add_unknown_job_is_3(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(tmp_path / "s.db"))
        assert main(["schedule", "add", "nope"]) == 3

    def test_schedule_add_manual_only_is_3(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")            # add_job leaves it manual-only/disabled
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        assert main(["schedule", "add", "a"]) == 3

    def test_schedule_add_calls_os(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        seen = []
        monkeypatch.setattr(cli, "os_add", lambda name, sched: seen.append(name) or "ok")
        assert main(["schedule", "add", "a"]) == 0
        assert seen == ["a"]

    def test_schedule_remove_unknown_job_is_3(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(tmp_path / "s.db"))
        assert main(["schedule", "remove", "nope"]) == 3

    def test_schedule_sync_calls_os(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        seen = []
        monkeypatch.setattr(cli, "os_sync", lambda entries: seen.append(1) or "synced")
        assert main(["schedule", "sync"]) == 0
        assert seen == [1]


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A registry with job `link1` and one finished run that planned one add."""
    path = tmp_path / "s.db"
    monkeypatch.setenv("STG_STORE_SYNC_DB", str(path))
    add_job(path, "link1")
    st = State(path)
    try:
        rid = st.start_run(job="link1", dry_run=False)
        st.finish_run(rid, status="ok", counts={"add": 1})
    finally:
        st.close()
    return path


def fake_result(status="ok", *, dry_run=False, counts=None):
    return SyncResult(plan=Plan(), dry_run=dry_run, status=status,
                      counts=counts if counts is not None else {"add": 1})


class TestSendReport:
    def test_send_report_dry_run_writes_html_without_sending(
        self, monkeypatch, tmp_path, db
    ):
        # The dry-run path goes through the domain (which writes the HTML and
        # never reaches the transport), so only the transport seam is guarded.
        from store_sync.core import notify as notify_mod

        monkeypatch.setattr(notify_mod, "_send",
                            lambda **k: pytest.fail("must not send"))
        code = cli.main(["send-report", "link1", "--dry-run", "--out", str(tmp_path)])
        assert code == 0 and (tmp_path / "run_1.html").exists()

    def test_send_report_unknown_job_is_3(self, db, capsys):
        assert cli.main(["send-report", "nope"]) == 3

    def test_send_report_without_a_run_is_3(self, tmp_path, monkeypatch):
        path = tmp_path / "s.db"
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(path))
        add_job(path, "link1")
        assert cli.main(["send-report", "link1", "--dry-run"]) == 3

    def test_send_report_force_is_passed_through(self, monkeypatch, db):
        seen = []
        monkeypatch.setattr(cli, "notify_run_report",
                            lambda *a, **k: seen.append(k) or _send_outcome("sent"))
        assert cli.main(["send-report", "link1", "--force"]) == 0
        assert seen[0]["force"] is True

    def test_send_report_failed_outcome_is_1(self, monkeypatch, db):
        monkeypatch.setattr(cli, "notify_run_report",
                            lambda *a, **k: _send_outcome("failed"))
        assert cli.main(["send-report", "link1"]) == 1


class TestNotifyFlag:
    def test_run_no_notify_propagates(self, monkeypatch, db):
        seen = []
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "run_sync",
                            lambda cfg, **kw: seen.append(kw.get("notify")) or fake_result())
        cli.main(["run", "link1", "--no-notify", "--dry-run"])
        assert seen == [False]

    def test_run_json_includes_report_status(self, monkeypatch, db, capsys):
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "run_sync", lambda cfg, **kw: fake_result())
        cli.main(["run", "link1", "--json"])
        assert "report_status" in json.loads(capsys.readouterr().out)
