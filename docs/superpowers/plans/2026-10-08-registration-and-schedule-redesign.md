# Registration without a Window + Per-List Schedule — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all time configuration from store registration (every sync pulls the survey's full history) and replace the cron-string schedule with a structured per-list interval (minute/hour/day/week) that is set independently for each registered list.

**Architecture:** `SourceConfig` shrinks to `{survey_id}`; `sync.run_sync` pulls unbounded. A frozen `Schedule{unit, every, at, weekday}` dataclass in `core/config.py` replaces the 5-field cron string; `scheduler.is_due(Schedule, now)` evaluates it and `Registry` persists it as four `jobs` columns, reconstructing a `Schedule` on every `JobEntry`. The GUI drives it with a unit combo + `every` spinbox + time/day pickers instead of a raw text field.

**Tech Stack:** Python 3.12, PySide6, SQLite (stdlib `sqlite3`, 3.45), pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-registration-and-schedule-redesign-design.md`

## Global Constraints

- Python 3.12; PySide6; `requests`; stdlib `sqlite3` only (repo runs SQLite 3.45.1 — `ALTER TABLE ... DROP COLUMN` is available).
- All tests run with `./.venv/bin/python -m pytest -q` **and** `./.venv/bin/python -m pytest -q -W error`; the suite (311) must stay green after every task.
- Qt tests set `QT_QPA_PLATFORM=offscreen` (see existing test modules).
- DESIGN.md is authoritative for widget object names/icons; do not introduce colors or fonts outside it.
- Do not commit, push, or rewrite history unless explicitly asked.
- Every pull is **full history**: `survey_interview_ids(survey_id)` is called with no `date_start` and no `date_type`.
- `unit == ""` means **manual only** (never scheduled).
- `Schedule` field bounds: `minute` every ∈ 1..59, `hour` ∈ 1..23, `day` ∈ 1..31 with required `at`, `week` ∈ 1..52 with required `at` and `weekday` ∈ 0..6. `0` = Monday (`datetime.weekday`).

## Review Focus

The spec names the behavior; these are the inputs it does not spell out, and the tests below pin each one in the task that owns the code (most likely first):

1. **An existing `state.db` from before this change** (`jobs` with `window_days`, `date_type`, cron `schedule`) must open without raising and without losing other config; the job becomes manual. — Task 2 (drop window columns) + Task 4 (drop cron column, migrate to `unit=''`).
2. **A YAML job config carrying stale `window_days`/`date_type` keys** must still load (unknown keys ignored), not raise. — Task 2.
3. **A `day`/`week` schedule with a missing/blank/malformed `at`** must be treated as *not due* and must never raise out of `Scheduler.tick`. — Task 4.
4. **An out-of-range or zero `every`** (e.g. 0, 90) must be rejected by `Schedule.validate` and treated as never-due by `is_due`, never as an always-fire. — Task 3 + Task 4.
5. **`Registry.list_entries` over a partially-populated/legacy row** (empty strings, default ints) must still return a usable `JobEntry` with a valid `Schedule`. — Task 4.

---

### Task 1: Registration stops asking for a time window (UI)

**Files:**
- Modify: `src/store_sync/ui/job_detail.py` (Config tab row3; `config_fields`; `load`)
- Modify: `src/store_sync/ui/dialogs.py` (`JobDialog._build_ui`, `job()`, `load`)
- Modify: `src/store_sync/gui.py` (`Controller._job_from_detail`)
- Test: `tests/ui/test_job_detail.py`, `tests/ui/test_run_detail.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `JobDetail.config_fields()` no longer contains `window_days`/`date_type`; `JobDialog` has no `window_days_edit`/`date_type_edit`; `Controller._job_from_detail` builds `SourceConfig(survey_id=...)`. (`SourceConfig` still carries the fields with defaults until Task 2.)

- [ ] **Step 1: Write the failing test** — in `tests/ui/test_job_detail.py`, replace `test_config_fields_round_trip` expected dict with one lacking `window_days`/`date_type`, and replace the body of `test_config_fields_tolerates_whitespace` to assert only `store_id`:

```python
def test_config_fields_round_trip():
    d = JobDetail()
    d.load(E(), a_job())
    assert d.config_fields() == {
        "store_id": 17620, "id_column": "No", "survey_id": "SURV",
        "match_source_var": "SbjNum", "match_store_col": "SbjNum",
        "delete_policy": "full_history", "dry_run": True,
    }

def test_config_fields_tolerates_whitespace():
    d = JobDetail()
    d.load(E(), a_job())
    d.store_id_edit.setText("   ")
    assert d.config_fields()["store_id"] == 0
    assert not hasattr(d, "window_days_edit")
    assert not hasattr(d, "date_type_edit")
```

- [ ] **Step 2: Run test to verify it fails** — `./.venv/bin/python -m pytest tests/ui/test_job_detail.py -q` → FAIL (`KeyError`/`AttributeError` on `window_days`).

- [ ] **Step 3: Implement** — in `job_detail._config_tab` delete the `row3` block (`window_days_edit`, `date_type_edit`); in `config_fields()` delete the `window_days`/`date_type` entries; in `load()` delete the two `.setText(...)` lines. In `dialogs._build_ui` delete the two `form.addRow("Window days"/"Date type", ...)` rows; in `JobDialog.job()` build `SourceConfig(survey_id=self.survey_id_edit.text().strip())`; in `JobDialog.load()` delete the two pre-fill lines. In `gui._job_from_detail` build `SourceConfig(fields["survey_id"])`.

- [ ] **Step 4: Run tests to verify they pass** — `./.venv/bin/python -m pytest tests/ui/test_job_detail.py tests/ui/test_run_detail.py -q` → PASS. Then full suite `./.venv/bin/python -m pytest -q`.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "refactor(ui): stop collecting a time window at registration"` (only with explicit approval).

---

### Task 2: Drop the time window from the config model + registry + pull

**Files:**
- Modify: `src/store_sync/core/config.py` (`SourceConfig`)
- Modify: `src/store_sync/core/sync.py` (`_window_start`, `run_sync`)
- Modify: `src/store_sync/core/registry.py` (`_SCHEMA`, `_ENTRY_FIELDS`, `JobEntry`, `_config_values`, `get`, `_entry`, `__init__` migration)
- Test: `tests/core/test_config.py`, `tests/core/test_registry.py`, `tests/core/test_sync.py`, `tests/ui/test_run_detail.py`, `tests/ui/test_controller.py`

**Interfaces:**
- Consumes: Task 1's UI (no longer reads the fields).
- Produces:
  - `SourceConfig(survey_id: str)` — no `window_days`, no `date_type`.
  - `JobEntry` without `window_days`/`date_type`.
  - `run_sync` calls `client.survey_interview_ids(config.source.survey_id)`.

- [ ] **Step 1: Write the failing tests.**
  - `tests/core/test_config.py`: `sample_dict()["source"]` becomes `{"survey_id": "..."}`; `test_from_dict_builds_typed_config` drops the `window_days` assertion; `test_to_dict_is_stable` enforces the trimmed dict; `test_minimal_config_fills_documented_defaults` drops the two source assertions. Add:

```python
def test_stale_source_keys_are_ignored():
    d = sample_dict()
    d["source"]["window_days"] = 3          # legacy key in the wild
    d["source"]["date_type"] = "Upload"
    cfg = JobConfig.from_dict(d)
    assert cfg.source == SourceConfig("60824d46-9b2e-41e5-8b47-ae11410ee24d")
```
  - `tests/core/test_sync.py`: replace `TestReadiness::test_window_and_date_type_passed` with:

```python
def test_pull_is_unbounded(self, st):
    c = FakeClient(store=store_rows(), subjects=subjects())
    run_sync(config(dry_run=True), client=c, state=st)
    assert c.id_kwargs == {}          # no date_start / date_type
```
  - `tests/core/test_registry.py`: `a_job()` uses `SourceConfig("SURV")`; `test_get_roundtrips_stored_fields` uses `SourceConfig("SURV")`; `test_add_defaults` drops `e.window_days`/`e.date_type`; add a migration test:

```python
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
    r.close()
```
  - `tests/ui/test_run_detail.py` / `tests/ui/test_controller.py`: `a_job()` uses `SourceConfig("SURV")`; `JobEntry(...)` fixtures drop `window_days=`/`date_type=`.

- [ ] **Step 2: Run tests to verify they fail** — `./.venv/bin/python -m pytest tests/core/test_config.py tests/core/test_registry.py tests/core/test_sync.py -q` → FAIL.

- [ ] **Step 3: Implement.**
  - `config.SourceConfig` → single `survey_id` field; `from_dict`/`to_dict` handle only it.
  - `sync.py`: delete `_window_start`; in `run_sync` call `client.survey_interview_ids(config.source.survey_id)`.
  - `registry.py`: remove `window_days`/`date_type` from `_SCHEMA`, `_ENTRY_FIELDS`, `JobEntry`, `_config_values`, `_entry`, `get`. In `__init__`, after `executescript`, run a migration: read `PRAGMA table_info(jobs)`; `DROP COLUMN` each of `window_days`, `date_type` when present (guarded by presence).

- [ ] **Step 4: Run tests to verify they pass** — `./.venv/bin/python -m pytest -q` → PASS. Fix any remaining positional `SourceConfig(...)` fixtures.

- [ ] **Step 5: Commit** — `git commit -am "refactor(core): always pull full history; drop the time window"`.

---

### Task 3: `Schedule` model + `human()` label

**Files:**
- Modify: `src/store_sync/core/config.py` (append `SCHEDULE_UNITS`, `Schedule`, `human`)
- Test: `tests/core/test_schedule_model.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SCHEDULE_UNITS: tuple[str, ...] = ("", "minute", "hour", "day", "week")`
  - `@dataclass(frozen=True) class Schedule: unit: str = ""; every: int = 1; at: str = ""; weekday: int = 0`
  - `Schedule.from_dict(d: dict | None) -> Schedule`, `Schedule.to_dict() -> dict`
  - `Schedule.validate() -> list[str]` (empty = ok)
  - `human(schedule: Schedule) -> str`

- [ ] **Step 1: Write the failing test** — `tests/core/test_schedule_model.py`:

```python
from store_sync.core.config import Schedule, human

def test_human_labels():
    assert human(Schedule("")) == "manual only"
    assert human(Schedule("minute", 1)) == "every minute"
    assert human(Schedule("minute", 5)) == "every 5 minutes"
    assert human(Schedule("hour", 1)) == "hourly"
    assert human(Schedule("hour", 2)) == "every 2 hours"
    assert human(Schedule("day", 1, "02:00")) == "daily at 02:00"
    assert human(Schedule("day", 3, "06:30")) == "every 3 days at 06:30"
    assert human(Schedule("week", 1, "02:00", 0)) == "weekly on Monday at 02:00"
    assert human(Schedule("week", 2, "02:00", 0)) == "every 2 weeks on Monday at 02:00"

def test_validate_bounds():
    assert Schedule("minute", 60).validate()          # 60 out of 1..59
    assert Schedule("hour", 0).validate()             # 0 out of 1..23
    assert Schedule("day", 1, "").validate()          # day needs `at`
    assert Schedule("week", 1, "02:00", 9).validate() # weekday out of 0..6
    assert Schedule("nope").validate()                # unknown unit
    assert Schedule("day", 1, "02:00").validate() == []
    assert Schedule("week", 2, "06:30", 4).validate() == []
```

- [ ] **Step 2: Run test to verify it fails** — `./.venv/bin/python -m pytest tests/core/test_schedule_model.py -q` → FAIL (`ImportError`).

- [ ] **Step 3: Implement** in `core/config.py`. `human` maps per the assertions (`hour,1` → `"hourly"`; pluralization `_plural(n, "minute")`); unknown unit or missing `at` → `"manual only"` so a malformed stored value never renders an empty cell. `validate` checks `unit in SCHEDULE_UNITS` and the per-unit bounds from Global Constraints; `at` must match `^([01]\\d|2[0-3]):[0-5]\\d$`. `from_dict` coerces ints/strings; `to_dict` returns the four keys.

- [ ] **Step 4: Run tests to verify they pass** — `./.venv/bin/python -m pytest tests/core/test_schedule_model.py -q` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(schedule): structured Schedule model + human label"`.

---

### Task 4: Schedule storage, engine, and all consumers (coordinated migration)

> This is the atomic type migration: `schedule` moves from a cron `str` to a `Schedule` at the registry boundary, so registry + scheduler + every reader/writer move together.

**Files:**
- Modify: `src/store_sync/core/registry.py`
- Modify: `src/store_sync/core/scheduler.py`
- Modify: `src/store_sync/ui/models.py`, `src/store_sync/ui/dialogs.py` (`ScheduleOverview`), `src/store_sync/ui/job_detail.py` (schedule widgets, `load`, `schedule()`), `src/store_sync/gui.py`, `src/store_sync/cli.py`
- Test: `tests/core/test_scheduler.py`, `tests/core/test_registry.py` (schedule cases), `tests/test_cli.py`, `tests/ui/test_job_detail.py`, `tests/ui/test_run_detail.py`, `tests/ui/test_controller.py`

**Interfaces:**
- Consumes: `Schedule`, `human` (Task 3).
- Produces:
  - `scheduler.is_due(schedule: Schedule, now: datetime) -> bool`
  - `Registry.add(job, *, schedule: Schedule, enabled: bool = False) -> int`
  - `Registry.update(name, job, *, schedule: Schedule | None = None, enabled: bool | None = None)`
  - `Registry.set_schedule(name: str, schedule: Schedule) -> None`
  - `JobEntry.schedule: Schedule`
  - `JobDetail.schedule() -> Schedule` (plain widgets: unit combo, every spinbox, `QTimeEdit`, weekday combo — all visible)
  - `Controller.save(name, job, *, schedule: Schedule, enabled: bool) -> bool`

- [ ] **Step 1: Write the failing tests.**
  - `tests/core/test_scheduler.py` — rewrite to `Schedule`:

```python
from datetime import datetime
from store_sync.core.config import Schedule
from store_sync.core.scheduler import is_due, Scheduler

def test_minute_and_hour():
    assert is_due(Schedule("minute", 5), datetime(2026, 10, 8, 3, 10)) is True
    assert is_due(Schedule("minute", 5), datetime(2026, 10, 8, 3, 11)) is False
    assert is_due(Schedule("hour", 2), datetime(2026, 10, 8, 4, 0)) is True
    assert is_due(Schedule("hour", 2), datetime(2026, 10, 8, 4, 1)) is False

def test_day_anchor():
    assert is_due(Schedule("day", 3, "02:00"), datetime(2026, 10, 5, 2, 0)) is True
    assert is_due(Schedule("day", 3, "02:00"), datetime(2026, 10, 12, 2, 0)) is False  # ord%3 == 2

def test_week_anchor():
    assert is_due(Schedule("week", 2, "02:00", 0), datetime(2026, 10, 5, 2, 0)) is True
    assert is_due(Schedule("week", 2, "02:00", 0), datetime(2026, 10, 12, 2, 0)) is False

def test_blank_and_malformed_never_due():
    assert is_due(Schedule(""), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("day", 1, ""), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("minute", 0), datetime(2026, 10, 8, 2, 0)) is False
```
  - `tests/core/test_registry.py` — `schedule=` call sites pass `Schedule(...)`; assert round-trip: `reg.add(a_job(), schedule=Schedule("minute", 5), enabled=True)` → `reg.get_entry("link1").schedule == Schedule("minute", 5)`. Add:

```python
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
```
  - `tests/test_cli.py` — `add_job(db, "a", schedule=Schedule("minute", 5), enabled=True)`; `test_list_subcommand` still asserts `"a" in out`.
  - `tests/ui/test_job_detail.py` — `E()` gains `schedule=Schedule("")`; add `test_schedule_controls_round_trip` asserting `d.schedule_edit is None` and:

```python
def test_schedule_controls_round_trip():
    d = JobDetail()
    d.load(E(), a_job())
    d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData("day"))
    d.schedule_every_spin.setValue(3)
    d.schedule_at_edit.setTime(__import__("PySide6.QtCore", fromlist=["QTime"]).QTime(2, 0))
    assert d.schedule() == Schedule("day", 3, "02:00")
```
  - `tests/ui/test_run_detail.py` — `JobDialog` schedule test uses the new widgets; `an_entry()` uses `schedule=Schedule("day", 1, "03:00")`; `test_job_dialog_entry_fields` asserts `dlg.schedule() == Schedule("day", 1, "03:00")`.
  - `tests/ui/test_controller.py` — `c.save("a", a_job(), schedule=Schedule(""), enabled=False)` throughout.

- [ ] **Step 2: Run tests to verify they fail** — `./.venv/bin/python -m pytest tests/core/test_scheduler.py tests/core/test_registry.py tests/test_cli.py -q` → FAIL.

- [ ] **Step 3: Implement.**
  - `scheduler.is_due(schedule: Schedule, now: datetime) -> bool` — implement the §4.2 table. `""`/unknown unit → `False`; `every <= 0` → `False`; parse `at` as `HH:MM` (missing/malformed → `False`). Minute: `now.minute % every == 0`. Hour: `now.minute == 0 and now.hour % every == 0`. Day: `now.hour==H and now.minute==M and now.date().toordinal() % every == 0`. Week: `now.weekday()==weekday and now.hour==H and now.minute==M and (now.isocalendar()[0]*53 + now.isocalendar()[1]) % every == 0`. Delete `_parse_field`. `Scheduler.tick` reads `entry.schedule` (already a `Schedule`) and calls `is_due`; keep the per-minute dedupe and the try/except that skips a bad entry.
  - `registry.py`: add `schedule_unit`/`schedule_every`/`schedule_at`/`schedule_weekday` to `_SCHEMA`; in `__init__` migration `ADD COLUMN` each when absent and `DROP COLUMN schedule` when present (a legacy cron job therefore lands as `unit=''`). Add `_schedule_values(schedule)`; `add`/`update`/`set_schedule` take a `Schedule`; `_entry` builds `Schedule(row["schedule_unit"], row["schedule_every"], row["schedule_at"], row["schedule_weekday"])`.
  - `ui/models.py`: dashboard "Schedule" column → `human(e.schedule)`.
  - `ui/dialogs.py`: `ScheduleOverview._FIELDS = ["name", "schedule", "enabled"]`, render `human(s.schedule)`; `JobDialog` schedule controls: `schedule_unit_combo` (data `""`/`minute`/`hour`/`day`/`week`), `schedule_every_spin`, `schedule_at_edit` (`QTimeEdit`), `schedule_weekday_combo`; `job()`/`entry_fields()` unchanged except `schedule` in `entry_fields` becomes `self.schedule()`; `load()` sets from `entry.schedule`.
  - `ui/job_detail.py`: replace `schedule_edit` with the four widgets; `load()` sets them from `entry.schedule`; add `schedule() -> Schedule`; keep `schedule_preview` showing `human(self.schedule())` (wire `valueChanged`/`currentIndexChanged`/`timeChanged` to refresh it).
  - `gui.py`: `save(name, job, *, schedule: Schedule, enabled)`; `_save_from_detail`/`_approve` pass `schedule=detail.schedule()`; `_new` passes `schedule=dlg.schedule()` into the `SimpleNamespace`; `_show_schedules` rows use `human(e.schedule)`.
  - `cli.py`: `_cmd_list` prints `human(e.schedule)`.

- [ ] **Step 4: Run tests to verify they pass** — `./.venv/bin/python -m pytest -q` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(schedule): structured per-list schedules (storage, engine, UI, CLI)"`.

---

### Task 5: Schedule tab UX — dynamic fields + preview

**Files:**
- Modify: `src/store_sync/ui/job_detail.py`, `src/store_sync/ui/dialogs.py`
- Test: `tests/ui/test_job_detail.py`, `tests/ui/test_run_detail.py`

**Interfaces:**
- Consumes: Task 4's widgets + `human`.
- Produces: `at`/`weekday` controls are visible only for `day`/`week`; a live human preview (`#SchedulePreview`).

- [ ] **Step 1: Write the failing test** — in `tests/ui/test_job_detail.py`:

```python
def test_schedule_fields_follow_unit():
    d = JobDetail()
    d.load(E(), a_job())
    d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData("minute"))
    assert d.schedule_at_edit.isVisibleTo(d) is False
    assert d.schedule_weekday_combo.isVisibleTo(d) is False
    d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData("week"))
    assert d.schedule_weekday_combo.isVisibleTo(d) is True
    assert d.schedule_preview.text() == human(d.schedule())
```

- [ ] **Step 2: Run test to verify it fails** → FAIL.

- [ ] **Step 3: Implement** — a `_refresh_schedule_fields()` slot connected to unit changes toggles `setVisible` on the `at`/`weekday` labels+widgets per unit (`day` shows `at`; `week` shows `at` + `weekday`) and sets `schedule_preview` to `human(self.schedule())`. Apply the same to `JobDialog`. Keep the `SchedulePreview` object name.

- [ ] **Step 4: Run tests to verify they pass** — `./.venv/bin/python -m pytest tests/ui -q` → PASS.

- [ ] **Step 5: Commit** — `git commit -am "feat(ui): schedule tab shows only the fields each unit needs"`.

---

### Task 6: Record the supersession

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-stg-store-sync-design.md` (§7.1 `source:` example)
- Modify: `docs/superpowers/specs/2026-10-08-gui-registry-redesign-design.md` (§3.4 A4, §5.3, §10 Deferred "Cron format")
- Modify: `tasks/plan.md`, `tasks/todo.md`

**Interfaces:** none (documentation only).

- [ ] **Step 1:** In the base spec §7.1, change `source:` to `{ survey_id: ... }` and add a one-line note that `window_days`/`date_type` were removed (all pulls are full history).
- [ ] **Step 2:** In the redesign spec, mark §3.4 A4 superseded, rewrite §5.3 to the interval model, and replace the §10 "Cron format — decided" bullet with a pointer to this plan's spec (`2026-10-08-registration-and-schedule-redesign-design.md`).
- [ ] **Step 3:** Add a `Phase 7 — Registration & schedule redesign` block to `tasks/plan.md` / `tasks/todo.md` listing Tasks 1–5 and their checkboxes.
- [ ] **Step 4:** `./.venv/bin/python -m pytest -q` → still PASS (docs change cannot break tests).
- [ ] **Step 5: Commit** — `git commit -am "docs: supersede time-window + cron with registration/schedule redesign"`.

---

## Self-Review

- **Spec coverage:** §3 (registration) → Tasks 1–2; §4.1 model → Task 3; §4.2 due rule + §4.4 engine → Task 4; §4.3 `human` → Tasks 3/4; §4.5 storage + migration → Tasks 2/4; §4.6 UI → Tasks 4–5; §5 affected surfaces → Tasks 4–6; §7 testing → tests named per task. No spec section is unmapped.
- **Type consistency:** `Schedule{unit,every,at,weekday}` and `human(Schedule)` are used with the same field order and names in every task; `is_due(Schedule, datetime)`, `Registry.add/update/set_schedule`, `JobEntry.schedule: Schedule`, `JobDetail.schedule()`, `Controller.save(..., schedule: Schedule, ...)` match across tasks.
- **Review Focus:** each of the five inputs is pinned by a named test (Task 2 stale-YAML + window-column migration, Task 4 cron→manual migration + malformed `at` + `every<=0` + legacy row rendering).
- **Proportion:** the plan carries signatures and test assertions, not implementations; bodies appear only where the due-rule arithmetic is not obvious from the tests (Task 4 `is_due`).
