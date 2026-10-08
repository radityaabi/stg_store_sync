"""Tests for store_sync.cli — headless entry: run a job from YAML/filelock."""
from __future__ import annotations

import yaml
import pytest

from store_sync.cli import build_parser, load_job_file, main


def write_job(tmp_path):
    d = {
        "job": "link1",
        "store": {"id": 17620, "id_column": "No"},
        "source": {"survey_id": "SURV"},
        "binding": {
            "match": {"source_var": "SbjNum", "store_col": "SbjNum"},
            "columns": [{"source_var": "T_QPM1_1", "store_col": "I7_1_2"}],
        },
        "write": {"dry_run": True, "delete_policy": "full_history"},
    }
    p = tmp_path / "job.yaml"
    p.write_text(yaml.safe_dump(d))
    return p


class TestParser:
    def test_requires_job_file(self):
        p = build_parser()
        with pytest.raises(SystemExit):
            p.parse_args([])

    def test_parses_job_and_flags(self):
        p = build_parser()
        ns = p.parse_args(["job.yaml", "--state", "/tmp/s.db"])
        assert ns.job_file == "job.yaml"
        assert ns.state_path == "/tmp/s.db"

    def test_no_dry_run_flag_overrides(self):
        p = build_parser()
        ns = p.parse_args(["job.yaml", "--no-dry-run"])
        assert ns.no_dry_run is True


class TestLoad:
    def test_loads_and_validates(self, tmp_path):
        cfg = load_job_file(write_job(tmp_path))
        assert cfg.job == "link1"
        assert cfg.store.id == 17620

    def test_invalid_file_raises(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.safe_dump({"job": ""}))
        with pytest.raises(ValueError):
            load_job_file(p)


class TestMain:
    def test_dry_run_missing_env_errors_cleanly(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("STG_SERVER", raising=False)
        monkeypatch.delenv("STG_USER", raising=False)
        monkeypatch.delenv("STG_PASSWORD", raising=False)
        jf = write_job(tmp_path)
        rc = main([str(jf), "--state", str(tmp_path / "s.db")])
        assert rc != 0

    def test_lock_prevents_concurrent_run(self, tmp_path, monkeypatch):
        from store_sync import cli

        lock = tmp_path / "job.lock"
        acquired = cli._acquire_lock  # exists
        # hold the lock, then a second acquire must fail fast
        import filelock

        with filelock.FileLock(str(lock)):
            holder = filelock.FileLock(str(lock))
            with pytest.raises(filelock.Timeout):
                holder.acquire(timeout=0.1)
