# STG Store Sync — Registration without a Window + Per-List Schedule

> **Status:** Draft for review
> **Date:** 2026-10-08
> **Repo:** `stg-store-sync`
> **Supersedes (in part):** §3.4 A4 (`window_days`/`date_type`) and §5.3 / Deferred
> "Cron format — decided" of `2026-10-08-gui-registry-redesign-design.md`; and §7.1
> `source:` shape of `2026-10-08-stg-store-sync-design.md`. All other domain rules
> (pull, projection, planning, fail-closed, empty-is-a-value) remain authoritative.
> **Author:** design session

---

## 1. Problem

Two mismatches between the tool and how it is actually used.

1. **Registration asks for a time window that is never wanted.** `SourceConfig`
   carries `window_days` + `date_type`, the Config tab exposes both, and
   `run_sync` uses them to bound the pull (`_window_start`). In practice every
   pull must be the survey's **full data** — a sync is a full re-read, not a
   delta over a moving window. The window is dead configuration: it invites an
   operator to accidentally pull a partial history.

2. **Schedule granularity is a raw cron string.** `jobs.schedule` stores a
   5-field cron and the UI is a bare text field. Operators think in
   "every 5 minutes / hourly / daily at 02:00 / weekly on Monday", not in
   `*/5 * * * *`. (The per-job *scoping* is already correct — `Scheduler.tick()`
   evaluates each entry's own schedule; the global single `run_time` lives in
   `momo`, not here. What is wrong is the *representation* and the UI.)

## 2. Intent (design brief)

**For:** the survey-ops operator who owns a set of STG Subject Stores.
**Outcome:** register a store with **no time configuration**; every run pulls the
full survey; set each list's cadence by **minute / hour / day / week** with a
human-readable preview, independently per list.
**Success:** an operator can add store #2..#N, choose "every 2 weeks on Monday at
02:00" from dropdowns (never typing cron), and each list fires on its own cadence.

**Constraints (agreed in this session):**

1. Pulls are always **full history** — no window, no `date_type`.
2. Schedule is **per-list** (already true in the engine) with a structured
   interval + anchor model, not a cron string.
3. No distributed scheduler; in-process while the GUI is open (unchanged).
4. Existing installs must migrate without guessing semantics.

## 3. Registration without a window (full data)

### 3.1 Config

`SourceConfig` shrinks to a single field:

```python
@dataclass
class SourceConfig:
    survey_id: str
```

`window_days` and `date_type` are **removed**. `JobConfig.validate` no longer
looks at them (it never required them).

### 3.2 Orchestrator

`core/sync.py`:

- Delete `_window_start`.
- Delete both `test_window_and_date_type_passed` concerns: the pull becomes

  ```python
  subject_ids = client.survey_interview_ids(config.source.survey_id)
  ```

  with no `date_start` and no `date_type` (the client's own default
  `date_type="Submit"` and full history apply). The client method keeps its
  `date_type`/`date_start` parameters — they are a transport capability, just no
  longer driven by job config.

### 3.3 Registry

Drop the `window_days` and `date_type` columns from `jobs` and from `JobEntry`.
`_config_values`, `get`, `_entry`, and `_ENTRY_FIELDS` lose them.

### 3.4 UI

- **Config tab** (`ui/job_detail.py`): remove the *Window days* and *Date type*
  fields; `config_fields()` no longer returns `window_days`/`date_type`.
- **Job dialog** (`ui/dialogs.py`): remove the two form rows; `job()` builds
  `SourceConfig(survey_id=...)` only.
- **Controller** (`gui.py`): `_job_from_detail` builds `SourceConfig(fields["survey_id"])`.

## 4. Per-list schedule (interval + anchor)

### 4.1 Model

A structured schedule, one per job. `unit == ""` means **manual only**.

```python
@dataclass(frozen=True)
class Schedule:
    unit: str = ""        # "" | "minute" | "hour" | "day" | "week"
    every: int = 1        # N in "every N <unit>"
    at: str = ""          # "HH:MM" — required for day/week
    weekday: int = 0      # 0=Mon .. 6=Sun (datetime.weekday) — used by week
```

Field bounds (validated; out-of-range is clamped or rejected at the UI edge):

| unit | `every` range | `at` | `weekday` |
|---|---|---|---|
| `""` (manual) | ignored | ignored | ignored |
| `minute` | 1..59 | ignored | ignored |
| `hour` | 1..23 | ignored | ignored |
| `day` | 1..31 | required `HH:MM` | ignored |
| `week` | 1..52 | required `HH:MM` | 0..6 |

Rationale for the caps: `minute`/`hour` are evaluated with modulo arithmetic on
the current minute/hour (`now.minute % every == 0`), which is only well-defined
for `every` within the clock field's range. A larger interval has no stable
anchor, so it is not offered.

### 4.2 Due rule

`is_due(schedule: Schedule, now: datetime) -> bool` replaces the cron evaluator:

| unit | due when |
|---|---|
| `""` | never |
| `minute` | `now.minute % every == 0` |
| `hour` | `now.minute == 0 and now.hour % every == 0` |
| `day` | `now.hour == H and now.minute == M and now.date().toordinal() % every == 0` |
| `week` | `now.weekday() == weekday and now.hour == H and now.minute == M and iso_week_index(now) % every == 0` |

`H`/`M` parse from `at`. The `day`/`week` anchors give a fixed wall-clock fire
time; `minute`/`hour` fire on the interval boundary. Example cadences:

- `minute, every=5` → every 5 minutes.
- `hour, every=2` → 02:00, 04:00, … (minute 0).
- `day, every=1, at=02:00` → daily at 02:00.
- `week, every=2, weekday=0, at=02:00` → every other Monday 02:00.

### 4.3 Human preview

`human(schedule: Schedule) -> str` produces the label used in the Schedule tab,
the dashboard column, `ScheduleOverview`, and the CLI:

- `""` → `manual only`
- `minute,1` → `every minute`
- `minute,5` → `every 5 minutes`
- `hour,1` → `hourly`
- `hour,2` → `every 2 hours`
- `day,1,02:00` → `daily at 02:00`
- `day,3,06:30` → `every 3 days at 06:30`
- `week,1,0,02:00` → `weekly on Monday at 02:00`
- `week,2,0,02:00` → `every 2 weeks on Monday at 02:00`

### 4.4 Scheduler engine

`core/scheduler.py` keeps its shape (`Scheduler` holds the registry + enqueue
callback; `tick()` iterates entries, dedupes once per minute, enqueues due jobs)
but:

- `is_due` takes a `Schedule` (built from the entry) instead of a cron string.
- `_parse_field` (the cron field parser) is deleted.
- A malformed schedule is caught and skipped, as today.

`SchedulerThread` is unchanged. `RunQueue` dedupe/single-active invariants are
unchanged.

### 4.5 Storage + migration

`jobs` changes:

```sql
-- dropped:  window_days, date_type, schedule (cron TEXT)
schedule_unit    TEXT    NOT NULL DEFAULT ''    -- '' | minute | hour | day | week
schedule_every   INTEGER NOT NULL DEFAULT 1
schedule_at      TEXT    NOT NULL DEFAULT ''    -- 'HH:MM' for day/week
schedule_weekday INTEGER NOT NULL DEFAULT 0     -- 0=Mon..6=Sun for week
```

**Migration** (`Registry.__init__`, defensive, idempotent): read
`PRAGMA table_info(jobs)`; `ADD COLUMN` each new column when absent; `DROP COLUMN`
`window_days`, `date_type`, and the legacy `schedule` when present. SQLite
3.45 supports `ALTER TABLE ... DROP COLUMN` (repo runs 3.45.1).

**Legacy cron jobs become manual.** An existing job whose `schedule` held a cron
string cannot be losslessly mapped to the interval model, so it is migrated to
`unit=''` (manual only) rather than guessed. This is deliberate and documented in
the migration; the operator re-picks a cadence once. `enabled` is preserved.

Registry API adjustments:

- `add(job, *, schedule: Schedule, enabled)`, `update(..., schedule: Schedule | None, ...)`.
- `set_schedule(name, schedule: Schedule)`.
- `JobEntry` carries `schedule: Schedule` (and `human(schedule)` where a label is
  needed); `column_count`, `enabled`, last-run fields unchanged.

### 4.6 UI

- **Schedule tab** (`ui/job_detail.py`) and **Job dialog** (`ui/dialogs.py`):
  - `QComboBox` unit: Manual · Every minute · Every hour · Every day · Every week.
  - `QSpinBox` *every N* (range from the unit table §4.1).
  - `QTimeEdit` *at* — shown only for day/week (default 02:00).
  - `QComboBox` weekday (Mon..Sun) — shown only for week.
  - Live preview label (`human(...)`); controls show/hide per unit.
- **Dashboard** "Schedule" column and **`ScheduleOverview`** render `human(...)`,
  not a cron string.
- **CLI** `list` prints the human label.

## 5. Compatibility / affected surfaces

| File | Change |
|---|---|
| `core/config.py` | `SourceConfig` loses 2 fields; add `Schedule` + `human` (or a small `core/schedule.py`) |
| `core/scheduler.py` | `is_due(Schedule)`; drop cron parser |
| `core/sync.py` | drop `_window_start`; unbounded pull |
| `core/registry.py` | schema + migration; structured schedule; `JobEntry` |
| `ui/job_detail.py` | Config fields removed; Schedule tab rebuilt |
| `ui/dialogs.py` | form rows removed; schedule controls; `ScheduleOverview` label |
| `ui/scheduler_thread.py` | unchanged |
| `gui.py` | `_job_from_detail`; save paths pass `Schedule` |
| `cli.py` | schedule label |
| `docs/…/stg-store-sync-design.md`, `gui-registry-redesign-design.md`, `tasks/plan.md`, `tasks/todo.md` | note the supersession |

Existing tests that reference `window_days` / `date_type` / cron strings are
updated to the new shapes (see §7).

## 6. Sequencing

1. **Core config + schedule** — `SourceConfig` trim; `Schedule`/`human`; `is_due`.
   Unit tests green.
2. **Sync** — unbounded pull; `test_sync` updated.
3. **Registry** — schema, migration, structured API, `JobEntry`; registry tests.
4. **UI** — Config trim; Schedule tab + dialog; dashboard/overview labels.
5. **CLI** — label; exit codes unchanged.
6. Full suite green (`./.venv/bin/python -m pytest -q`), then a manual GUI smoke.

Each step commits separately (only with explicit approval).

## 7. Testing strategy

TDD; the existing suite (311) stays green after updates (`-q` and `-q -W error`).

- **`SourceConfig`** — round-trip is `{survey_id}` only; a YAML with stale
  `window_days`/`date_type` keys loads without error (extra keys ignored).
- **`is_due`** — one case per unit boundary: fire/no-fire at the exact minute;
  `every` modulo for minute/hour; day anchor (`toordinal % every`);
  week anchor (weekday + iso-week modulo); `""` never due.
- **`human`** — each label in §4.3.
- **Migration** — build a legacy `jobs` table (with `window_days`, `date_type`,
  cron `schedule`), open `Registry`, assert new columns exist, the dropped columns
  are gone, and a previously-`enabled` job is now `unit=''` + still enabled.
- **Registry** — structured schedule round-trips; `set_schedule`; `JobEntry.schedule`.
- **Sync** — the pull no longer sends `date_start`; the old
  `test_window_and_date_type_passed` is replaced by an assertion that
  `survey_interview_ids` is called with the survey id only.
- **UI** — Schedule tab: unit change toggles the visibility of *at*/*weekday* and
  updates the preview; `config_fields()` no longer has `window_days`/`date_type`;
  `ScheduleOverview` renders the human label.

## 8. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Legacy cron → manual surprises operators | Med | Documented in migration; `enabled` kept; one-time re-pick |
| `ALTER TABLE DROP COLUMN` unsupported on old SQLite | Med | Guard by version/`table_info`; repo runs 3.45.1 |
| Dropping the window pulls much more data | Med | Intended (full data); chunked pull + retry already in `fetcher` |
| `every` modulo drift (e.g. restart mid-hour) | Low | Wall-clock anchor for day/week; minute/hour are stateless |
| UI shows stale cron assumptions | Low | Dashboard/CLI/overview all switched to `human` |

## 9. Out of scope

- A distributed / OS-level scheduler. The in-process scheduler is unchanged in
  shape; external cron would drive `stg-store-sync-cli run-all --enabled-only`,
  which is out of scope here.
- Lossless cron→interval inference for existing jobs.
- Any change to pull / projection / planning / write semantics.
