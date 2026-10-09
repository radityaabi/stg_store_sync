"""Tests for store_sync.core.registry — persisted store configs."""
from __future__ import annotations

import pytest

from store_sync.core.config import (BindingConfig, ColumnBinding, JobConfig,
    Schedule, SourceConfig, StoreConfig, WriteConfig)
from store_sync.core.registry import Registry

def a_job(name="link1", store_id=17620):
    return JobConfig(name, StoreConfig(store_id, "No"), SourceConfig("SURV"),
        BindingConfig(ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")]), WriteConfig(dry_run=True))

def test_add_get_roundtrip(tmp_path):
    r = Registry(tmp_path / "reg.db")
    r.add(a_job(), schedule=Schedule("minute", 5), enabled=True)
    job = r.get("link1")
    assert job.store.id == 17620
    assert job.binding.columns[0].store_col == "I7_1_2"
    e = r.get_entry("link1")
    assert e.enabled is True
    assert e.schedule == Schedule("minute", 5)
    assert e.column_count == 1
    r.close()

def test_unique_name(tmp_path):
    import pytest
    r = Registry(tmp_path / "reg.db")
    r.add(a_job())
    with pytest.raises(Exception):
        r.add(a_job())
    r.close()

def test_update_replaces_columns(tmp_path):
    r = Registry(tmp_path / "reg.db")
    r.add(a_job())
    r.update("link1", a_job(store_id=9), schedule=Schedule("day", 1, "02:00"),
             enabled=False)
    assert r.get("link1").store.id == 9
    assert r.get_entry("link1").schedule == Schedule("day", 1, "02:00")
    r.close()

def test_delete_removes(tmp_path):
    r = Registry(tmp_path / "reg.db")
    r.add(a_job())
    r.delete("link1")
    assert r.names() == []
    r.close()


# --- additional coverage for the full method list -------------------------


@pytest.fixture
def reg(tmp_path):
    r = Registry(tmp_path / "reg.db")
    yield r
    r.close()


def test_get_roundtrips_stored_fields(reg):
    # The spec §4 `jobs` schema stores a subset of JobConfig; `update`/`get`
    # round-trip every column that exists.
    job = JobConfig(
        "link2",
        StoreConfig(17620, "No"),
        SourceConfig("SURV"),
        BindingConfig(
            ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2", "number")],
        ),
        WriteConfig(op="full_sync", dry_run=False, normalize={"x": "trim"}),
    )
    reg.add(job, schedule=Schedule(""), enabled=False)
    got = reg.get("link2")
    assert got.store == StoreConfig(17620, "No")
    assert got.source == SourceConfig("SURV")
    assert got.binding.match == ColumnBinding("SbjNum", "SbjNum")
    assert got.binding.columns == [ColumnBinding("T_QPM1_1", "I7_1_2", "number")]
    assert got.write.dry_run is False
    # Not in the §4 schema → reconstructed from JobConfig defaults.
    assert got.binding.mode == "mapped"
    assert got.write.op == "full_sync"
    assert got.write.normalize == {}


def test_get_missing_raises(reg):
    with pytest.raises(KeyError):
        reg.get("nope")


def test_add_defaults(reg):
    reg.add(a_job())
    e = reg.get_entry("link1")
    assert e.enabled is False
    assert e.schedule == Schedule("")
    assert e.dry_run is True
    assert e.column_count == 1
    assert e.last_status is None and e.last_finished_at is None


def test_get_entry_full_fields(reg):
    reg.add(a_job(), schedule=Schedule("hour", 3), enabled=True)
    e = reg.get_entry("link1")
    assert e.name == "link1"
    assert e.store_id == 17620
    assert e.id_column == "No"
    assert e.survey_id == "SURV"
    assert e.match_source_var == "SbjNum"
    assert e.match_store_col == "SbjNum"
    assert e.schedule == Schedule("hour", 3)
    assert e.enabled is True


def test_names_sorted(reg):
    reg.add(a_job(name="zebra"))
    reg.add(a_job(name="alpha"))
    assert reg.names() == ["alpha", "zebra"]


def test_list_entries_sorted_with_column_count(reg):
    reg.add(a_job(name="b"))
    reg.add(a_job(name="a"))
    entries = reg.list_entries()
    assert [e.name for e in entries] == ["a", "b"]
    assert all(e.column_count == 1 for e in entries)


def test_list_entries_join_last_run(reg, tmp_path):
    from store_sync.core.state import State

    st = State(tmp_path / "reg.db")
    rid = st.start_run(job="link1", dry_run=True)
    st.finish_run(rid, status="ok", counts={})
    st.close()

    reg.add(a_job())
    e = reg.get_entry("link1")
    assert e.last_status == "ok"
    assert e.last_finished_at is not None


def test_set_enabled_and_schedule(reg):
    reg.add(a_job())
    reg.set_enabled("link1", True)
    assert reg.get_entry("link1").enabled is True
    reg.set_schedule("link1", Schedule("minute", 15))
    assert reg.get_entry("link1").schedule == Schedule("minute", 15)


def test_set_enabled_missing_raises(reg):
    with pytest.raises(KeyError):
        reg.set_enabled("nope", True)


def test_update_only_schedule_and_enabled(reg):
    reg.add(a_job(), schedule=Schedule("hour", 1), enabled=True)
    reg.update("link1", a_job(store_id=9), schedule=Schedule("week", 2, "02:00", 0),
               enabled=False)
    job = reg.get("link1")
    assert job.store.id == 9
    assert job.binding.columns[0].store_col == "I7_1_2"
    e = reg.get_entry("link1")
    assert e.schedule == Schedule("week", 2, "02:00", 0)
    assert e.enabled is False


def test_update_keeps_schedule_when_omitted(reg):
    reg.add(a_job(), schedule=Schedule("day", 2, "06:30"), enabled=True)
    reg.update("link1", a_job(store_id=9))
    e = reg.get_entry("link1")
    assert e.schedule == Schedule("day", 2, "06:30")
    assert e.enabled is True


def test_delete_missing_raises(reg):
    with pytest.raises(KeyError):
        reg.delete("nope")


def test_delete_cascades_columns(reg):
    reg.add(a_job())
    reg.delete("link1")
    count = reg.conn.execute("SELECT COUNT(*) AS n FROM job_columns").fetchone()["n"]
    assert count == 0


def test_migration_drops_window_columns(tmp_path):
    import sqlite3
    db = tmp_path / "reg.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,"
        " store_id INTEGER, id_column TEXT, survey_id TEXT, match_source_var TEXT,"
        " match_store_col TEXT, window_days INTEGER DEFAULT 0,"
        " date_type TEXT DEFAULT 'Upload', delete_policy TEXT DEFAULT 'full_history',"
        " dry_run INTEGER DEFAULT 1, enabled INTEGER DEFAULT 1, schedule TEXT DEFAULT '');"
    )
    con.execute("INSERT INTO jobs (name, store_id, id_column, survey_id, match_source_var,"
                " match_store_col, window_days, date_type, schedule)"
                " VALUES ('old', 1, 'No', 'S', 'SbjNum', 'SbjNum', 5, 'Upload', '*/5 * * * *')")
    con.commit(); con.close()
    r = Registry(db)
    cols = {row["name"] for row in r.conn.execute("PRAGMA table_info(jobs)")}
    assert "window_days" not in cols and "date_type" not in cols
    assert "delete_policy" not in cols
    r.close()


def test_schedule_migration_from_cron_becomes_manual(tmp_path):
    import sqlite3
    db = tmp_path / "reg.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,"
        " store_id INTEGER, id_column TEXT, survey_id TEXT, match_source_var TEXT,"
        " match_store_col TEXT, delete_policy TEXT DEFAULT 'full_history',"
        " dry_run INTEGER DEFAULT 1, enabled INTEGER DEFAULT 1, schedule TEXT DEFAULT '');"
    )
    con.execute("INSERT INTO jobs (name, store_id, id_column, survey_id, match_source_var,"
                " match_store_col, enabled, schedule) VALUES ('old', 1, 'No', 'S', 'SbjNum', 'SbjNum', 1, '*/5 * * * *')")
    con.commit(); con.close()
    r = Registry(db)
    e = r.get_entry("old")
    assert e.schedule == Schedule("") and e.enabled is True
    assert "schedule" not in {row["name"] for row in r.conn.execute("PRAGMA table_info(jobs)")}
    r.close()


def test_corrupt_schedule_row_degrades_to_manual(tmp_path):
    """A TEXT value in an INTEGER schedule column must not break list_entries."""
    import sqlite3

    from store_sync.core.config import human

    r = Registry(tmp_path / "reg.db")
    r.add(a_job(), schedule=Schedule("minute", 5), enabled=True)
    r.conn.execute(
        "UPDATE jobs SET schedule_every = 'five' WHERE name = 'link1'"
    )
    r.conn.commit()
    entries = r.list_entries()  # must not raise
    assert entries[0].schedule.every == "five"  # passed through unvalidated
    assert human(entries[0].schedule) == "manual only"  # renderable
    r.close()
