# Run Notification Email (OWA) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After any run that added at least one store row, mail an HTML summary of
those adds to the job's PIC list over OWA (Playwright), without ever changing the
run's recorded outcome.

**Architecture:** A new `core/notify.py` domain layer (should-notify rules, HTML
report, recipient resolution) sits on top of a ported `core/owa_mailer.py`
transport. `run_sync` is split into a thin wrapper plus `_run_sync_inner`; the
wrapper calls `_notify_safely` **after** `_finish`, so every run path (GUI, `run`,
`run-all`, `run-due`) is covered by one call site and a mail failure is contained.

**Tech Stack:** Python 3.12, PySide6, SQLite (stdlib `sqlite3`), `playwright`
(optional extra), `pytest`.

**Spec:** `docs/superpowers/specs/2026-10-15-run-notification-email-design.md`

## Global Constraints

- Python 3.12; `src/` layout, package `store_sync`. Tests via `./.venv/bin/python -m pytest -q`.
- Standalone repo: **never import from `stg_automation_suite`** — reference only, port the code.
- `playwright` is an **optional** extra (`[owa]`); every import of it is lazy/inside a function. The suite and the app must run without Playwright installed.
- A notification is a **side channel**: it never changes a run's `status` or `counts`, and `run_sync` must not raise because of it.
- Dry-run sends nothing; a run with 0 adds sends nothing; a job with no PIC sends nothing. Each non-send is **recorded** with its reason and is never an error.
- Escape every interpolated value in the HTML body; inline styles only; no script, no remote image, no external URL.
- Do not commit secrets. Do not read/print `.env`.
- Every task: run `./.venv/bin/python -m pytest -q` before and after, keep the suite green, and commit only the files that task touches.
- No commits to `main` beyond the task commits below; never rewrite history.

## Review Focus

1. **A PIC entry that is a name, not an address** (e.g. `Budi`) — must be warned in
   the GUI, excluded from the recipients, and must never by itself make a run fail
   or block saving the job.
2. **A run that added 0 rows but hit conflicts/errors** — must record `skipped`,
   send nothing, and leave the run's `status`/`counts` exactly as the pipeline set
   them.
3. **Playwright absent, or the OWA window never signed in** — must record `failed`
   with an actionable reason, must not raise out of `run_sync`, and must leave the
   run re-sendable via `send-report`.
4. **A store value containing `<`, `>`, or `"`** — must be HTML-escaped in the body
   so the layout survives and no markup is injected.
5. **A database created before this change** (no `jobs.pic`, no `notifications`) —
   must migrate on open, read back `pic=''`, and keep running normally.

---

### Task 1: PIC list helpers (`core/pic.py`)

**Files:**
- Create: `src/store_sync/core/pic.py`
- Test: `tests/core/test_pic.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_pics(raw: str | None) -> list[str]`, `format_pics(pics: list[str]) -> str`, `is_valid_email(value: str | None) -> bool`, `split_pic_emails(raw: str | None) -> tuple[list[str], list[str]]`, `pic_email_warning(raw: str | None) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
from store_sync.core.pic import (
    format_pics, is_valid_email, parse_pics, pic_email_warning, split_pic_emails,
)

def test_parse_trims_drops_empty_and_dedupes_case_insensitively():
    assert parse_pics("alice@x.com; Bob ;;ALICE@X.COM") == ["alice@x.com", "Bob"]
    assert parse_pics("") == [] and parse_pics(None) == []

def test_format_round_trips_to_canonical_semicolon_space():
    assert format_pics(["a@x.com", "b@y.com"]) == "a@x.com; b@y.com"
    assert format_pics([]) == ""

def test_is_valid_email_requires_a_dotted_domain():
    assert is_valid_email("citra@nielseniq.com")
    assert not is_valid_email("Citra")
    assert not is_valid_email("citra@nielseniq")

def test_split_groups_addresses_and_everything_else():
    assert split_pic_emails("a@x.com; Budi") == (["a@x.com"], ["Budi"])

def test_warning_names_non_addresses_and_is_empty_when_all_valid():
    assert pic_email_warning("a@x.com; Budi") == (
        "1 entry is not an email address and will not receive reports: Budi"
    )
    assert pic_email_warning("a@x.com") == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_pic.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'store_sync.core.pic'`

- [ ] **Step 3: Implement `core/pic.py`**

Port the module from `../stg_automation_suite/src/momo/core/pic.py` verbatim
(same regex `^[^@\s]+@[^@\s.]+(?:\.[^@\s.]+)+$`, same parse/format/warning
contracts), with one docstring change: drop the momo-specific wording and say
the PIC column is the per-job recipient list for run notifications. Keep
`__all__` as the five names above. Pluralisation: `"1 entry is"` vs
`f"{n} entries are"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_pic.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/pic.py tests/core/test_pic.py
git commit -m "feat(core): add PIC list helpers"
```

---

### Task 2: OWA transport (`core/owa_mailer.py`)

**Files:**
- Create: `src/store_sync/core/owa_mailer.py`
- Test: `tests/core/test_owa_mailer.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `OwaOutcome(status: str, detail: str = "")` (frozen dataclass, `status` ∈ `sent | skipped | failed`); `async send_mail_via_owa_async(to: Sequence[str], cc: Sequence[str] = (), subject: str = "", html: str = "", attachments: Iterable[tuple[str, bytes]] = (), *, profile_dir: Path | str | None = None) -> OwaOutcome`.

- [ ] **Step 1: Write the failing tests**

Fakes mirror `../stg_automation_suite/tests/compare/test_owa_compose_ready.py`
(`FakeLocator`, `FakePage` with `locator`, `wait_for_timeout`, `goto`,
`keyboard`, `evaluate`, `get_by_text`). Add a `FakeContext`/`FakeChromium`/
`FakePlaywright` returning a page whose editor is **not** visible, and install it
by monkeypatching `owa_mailer._playwright_factory`.

**No new test dependency:** drive the coroutine with `asyncio.run(...)` inside
plain `def` tests — do **not** use `pytest.mark.asyncio` (the `pytest-asyncio`
plugin is not a dependency of this repo).

```python
import asyncio
import pytest
from store_sync.core import owa_mailer
from store_sync.core.owa_mailer import OwaOutcome, send_mail_via_owa_async

def test_empty_recipients_skip_without_launching(monkeypatch):
    monkeypatch.setattr(owa_mailer, "_playwright_factory",
                        lambda: pytest.fail("must not launch a browser"))
    out = asyncio.run(send_mail_via_owa_async(to=[], subject="s", html="h"))
    assert out.status == "skipped"

def test_deep_link_carries_to_and_subject(fake_pw, monkeypatch):
    monkeypatch.setattr(owa_mailer, "_playwright_factory", lambda: fake_pw)
    asyncio.run(send_mail_via_owa_async(
        to=["a@x.com", "b@y.com"], subject="Hi there", html="<p>x</p>"))
    url = fake_pw.page.goto_url
    assert "outlook.office.com/mail/deeplink/compose" in url
    assert "to=a%40x.com%3Bb%40y.com" in url
    assert "subject=Hi+there" in url

def test_editor_never_ready_reports_failed(fake_pw_never_ready, monkeypatch):
    monkeypatch.setattr(owa_mailer, "_playwright_factory", lambda: fake_pw_never_ready)
    out = asyncio.run(send_mail_via_owa_async(to=["a@x.com"], subject="s", html="h"))
    assert out.status == "failed" and "OWA" in out.detail
```

(`fake_pw` / `fake_pw_never_ready` are fixtures in the same file building the
`FakePlaywright` chain described above.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_owa_mailer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'store_sync.core.owa_mailer'`

- [ ] **Step 3: Implement `core/owa_mailer.py`**

Port `../stg_automation_suite/src/lib/owa_mailer.py` (442 lines) verbatim — the
whole send sequence (`_handle_account_picker`, `_wait_compose_ready`,
`_wait_attachment_settled`, `_copy_html_to_clipboard`, the send retry loop, the
top-level `except` returning `failed`) — with exactly these substitutions:

- Replace `from lib.log import get_logger` with
  `logger = logging.getLogger(__name__)`.
- Replace `from lib.paths import DATA_DIR` /
  `_DEFAULT_PROFILE_DIR = DATA_DIR / "playwright_profile_owa"` with a helper
  `_default_profile_dir() -> Path` returning
  `Path(os.environ.get("STG_OWA_PROFILE_DIR") or Path.home() / ".stg_store_sync" / "playwright_profile_owa")`.
- Replace every `MOMO_SURVEY_EMAIL` / `MOMO_SURVEY_EMAIl` read with `STG_OWA_EMAIL`.
- Add a module-level indirection so tests never touch Playwright:
  `def _playwright_factory(): from playwright.async_api import async_playwright; return async_playwright()`
  and use it in place of the direct import. Keep `from playwright.async_api import async_playwright` **inside** the factory only.

`to` filtering, the `";"`-joined `to` param, the `;`-suffixed CC typing, and the
`skipped`-on-empty-recipients early return stay identical to the source.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_owa_mailer.py -q`
Expected: PASS

- [ ] **Step 5: Verify the suite still imports without Playwright**

Run: `./.venv/bin/python -c "import store_sync.core.owa_mailer"`
Expected: no output, exit 0 (the module must not import `playwright` at import time).

- [ ] **Step 6: Commit**

```bash
git add src/store_sync/core/owa_mailer.py tests/core/test_owa_mailer.py
git commit -m "feat(core): port OWA (Playwright) mail transport"
```

---

### Task 3: `notifications` table (`core/state.py`)

**Files:**
- Modify: `src/store_sync/core/state.py` (add table to `_SCHEMA`; add three methods)
- Test: `tests/core/test_state.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `State.record_notification(run_id: int, *, job: str, status: str, recipients: str = "", subject: str = "", body_html: str = "", reason: str | None = None) -> int`; `State.notification_for_run(run_id: int) -> dict | None`; `State.notifications_for_run(run_id: int) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_record_notification_round_trips(state):
    rid = state.start_run(job="j", dry_run=False)
    nid = state.record_notification(rid, job="j", status="sent",
                                    recipients="a@x.com", subject="S", body_html="<p>")
    assert nid > 0
    row = state.notification_for_run(rid)
    assert row["status"] == "sent" and row["recipients"] == "a@x.com"

def test_notification_for_run_returns_most_recent(state):
    rid = state.start_run(job="j", dry_run=False)
    state.record_notification(rid, job="j", status="skipped", reason="no adds")
    state.record_notification(rid, job="j", status="sent")
    assert state.notification_for_run(rid)["status"] == "sent"
    assert [r["status"] for r in state.notifications_for_run(rid)] == ["skipped", "sent"]

def test_notification_for_run_is_none_when_absent(state):
    rid = state.start_run(job="j", dry_run=False)
    assert state.notification_for_run(rid) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_state.py -q -k notification`
Expected: FAIL — `AttributeError: 'State' object has no attribute 'record_notification'`

- [ ] **Step 3: Implement the table and accessors in `core/state.py`**

Append to `_SCHEMA` (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL,
    job        TEXT    NOT NULL,
    status     TEXT    NOT NULL,
    recipients TEXT    NOT NULL DEFAULT '',
    subject    TEXT    NOT NULL DEFAULT '',
    body_html  TEXT    NOT NULL DEFAULT '',
    reason     TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS notifications_run ON notifications(run_id);
```

Add a `# -- notifications --` section with the three methods. `record_notification`
inserts and commits, returning `cur.lastrowid`. `notification_for_run` selects
`WHERE run_id = ? ORDER BY id DESC LIMIT 1`, returning `dict(row)` or `None`.
`notifications_for_run` selects `WHERE run_id = ? ORDER BY id`. `_SCHEMA` is run
through `executescript` on every open, so an existing DB gains the table.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_state.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/state.py tests/core/test_state.py
git commit -m "feat(state): add notifications table and accessors"
```

---

### Task 4: `pic` on the job (`core/config.py`, `core/registry.py`)

**Files:**
- Modify: `src/store_sync/core/config.py` (`JobConfig`), `src/store_sync/core/registry.py`
- Test: `tests/core/test_config.py`, `tests/core/test_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `JobConfig.pic: str = ""` (with `to_dict`/`from_dict` round-trip);
  `JobEntry.pic: str = ""`; `Registry.add`/`update` persist it; `Registry.get`
  returns it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_config.py
def test_pic_round_trips_and_is_omitted_when_empty():
    d = sample_dict(); d["pic"] = "a@x.com; b@y.com"
    assert JobConfig.from_dict(d).pic == "a@x.com; b@y.com"
    assert JobConfig.from_dict(d).to_dict()["pic"] == "a@x.com; b@y.com"
    assert "pic" not in JobConfig.from_dict(sample_dict()).to_dict()

# tests/core/test_registry.py
def test_pic_round_trips_through_registry(reg):
    job = a_job(); job.pic = "a@x.com"
    reg.add(job)
    assert reg.get("link1").pic == "a@x.com"
    assert reg.get_entry("link1").pic == "a@x.com"

def test_registry_migrates_old_db_without_pic(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL UNIQUE, store_id INTEGER NOT NULL, "
                "id_column TEXT NOT NULL, survey_id TEXT NOT NULL, "
                "match_source_var TEXT NOT NULL, match_store_col TEXT NOT NULL, "
                "dry_run INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 0)")
    con.execute("INSERT INTO jobs (name, store_id, id_column, survey_id, "
                "match_source_var, match_store_col) VALUES ('old', 1, 'No', 'S', 'M', 'M')")
    con.commit(); con.close()
    from store_sync.core.registry import Registry
    r = Registry(p)
    assert r.get_entry("old").pic == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_config.py tests/core/test_registry.py -q -k pic`
Expected: FAIL — `TypeError`/`AttributeError` on `pic`.

- [ ] **Step 3: Implement**

`core/config.py` — add `pic: str = ""` as the last field of `JobConfig`;
`from_dict` reads `str(d.get("pic", ""))`; `to_dict` includes `"pic"` only when
non-empty (mirrors how `SourceConfig.to_dict` omits `survey_index`/`paired_survey_id`).

`core/registry.py`:
- `_PAIRING_COLUMNS` sibling: add `_PIC_COLUMNS = (("pic", "TEXT NOT NULL DEFAULT ''"),)`
  and a `_add_pic_column()` called from `__init__` next to `_add_pairing_columns()`.
- `jobs` DDL: add `pic TEXT NOT NULL DEFAULT ''`.
- `JobEntry`: add `pic: str = ""` (keyword-defaulted so the four existing
  test/`registry.py` constructions keep working).
- `_ENTRY_FIELDS`: add `jobs.pic,` and `_entry()`: `pic=row["pic"]`.
- `_config_values`: append `str(job.pic)`; extend the `INSERT` column list and
  the `UPDATE` `sets`/params accordingly.
- `get()`: `pic=row["pic"]`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_config.py tests/core/test_registry.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/config.py src/store_sync/core/registry.py tests/core/test_config.py tests/core/test_registry.py
git commit -m "feat(registry): persist a per-job PIC list"
```

---

### Task 5: Notification domain (`core/notify.py`)

**Files:**
- Create: `src/store_sync/core/notify.py`
- Test: `tests/core/test_notify.py`

**Interfaces:**
- Consumes: `core.pic` (Task 1), `core.owa_mailer` (Task 2), `State.record_notification` (Task 3), `JobConfig.pic` (Task 4).
- Produces: `SendOutcome(status: str, reason: str = "", to: tuple[str, ...] = (), cc: tuple[str, ...] = ())` (frozen); `should_notify(result) -> bool`; `subject_line(job_cfg: JobConfig, result) -> str`; `render_report(job_cfg: JobConfig, result, *, max_rows: int = 50) -> str`; `notify_run_report(job_cfg, state, result, *, force: bool = False, out_dir=None, profile_dir=None) -> SendOutcome`.

- [ ] **Step 1: Write the failing tests**

Build a real `SyncResult` from `store_sync.core.planner` (`Plan` with `Action`
objects). Define these file-local helpers first:

```python
from store_sync.core import notify
from store_sync.core.owa_mailer import OwaOutcome
from store_sync.core.sync import SyncResult
from store_sync.core.planner import Action, Plan

def a_job(pic=""):
    return JobConfig("link1", StoreConfig(17620, "No"), SourceConfig("SURV"),
        BindingConfig(ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")]), WriteConfig(), pic=pic)

def a_result(adds=1, dry_run=False, status="ok", values=None):
    acts = [Action("add", f"k{i}", payload={"SbjNum": f"k{i}", "I7_1_2": "v"})
            for i in range(adds)]
    if values:  # one action carrying hostile column values
        acts = [Action("add", "kx", payload={"SbjNum": "kx", **values})]
    return SyncResult(plan=Plan(actions=acts), dry_run=dry_run, status=status,
                      counts={"add": len(acts), "update": 0, "same": 0, "conflict": 0},
                      run_id=1)

@pytest.fixture
def state():
    s = State(":memory:"); s.start_run(job="link1", dry_run=False); return s
```

`a_result(values={"Nama": "<script>x</script>"})` yields exactly one add whose
payload carries the hostile value, for the escaping test.

```python
def test_should_notify_only_when_live_and_adds():
    assert should_notify(a_result(adds=1))
    assert not should_notify(a_result(adds=0))
    assert not should_notify(a_result(adds=1, dry_run=True))

def test_subject_names_job_and_add_count():
    s = subject_line(a_job(), a_result(adds=3))
    assert s.startswith("[StoreSync] link1 — 3 row(s) added")

def test_render_escapes_store_values():
    html = render_report(a_job(), a_result(values={"Nama": "<script>x</script>"}))
    assert "<script>" not in html and "&lt;script&gt;" in html

def test_render_caps_the_add_list():
    html = render_report(a_job(), a_result(adds=60), max_rows=50)
    assert "and 10 more" in html

def test_notify_sends_when_adds_and_records_sent(monkeypatch, state):
    calls = []
    monkeypatch.setattr(notify, "_send", lambda **kw: calls.append(kw) or OwaOutcome("sent", ""))
    out = notify_run_report(a_job(pic="a@x.com"), state, a_result(adds=1))
    assert out.status == "sent" and calls and calls[0]["to"] == ["a@x.com"]
    assert state.notification_for_run(1)["status"] == "sent"

def test_notify_skips_zero_adds_and_no_pic(monkeypatch, state):
    monkeypatch.setattr(notify, "_send", lambda **kw: pytest.fail("must not send"))
    assert notify_run_report(a_job(pic="a@x.com"), state, a_result(adds=0)).status == "skipped"
    assert notify_run_report(a_job(pic=""), state, a_result(adds=1)).status == "skipped"

def test_notify_dry_run_writes_html_and_does_not_send(monkeypatch, state, tmp_path):
    monkeypatch.setattr(notify, "_send", lambda **kw: pytest.fail("must not send"))
    out = notify_run_report(a_job(pic="a@x.com"), state, a_result(adds=1, dry_run=True),
                            out_dir=tmp_path)
    assert out.status == "dry_run" and (tmp_path / "run_1.html").exists()

def test_notify_transport_exception_is_recorded_failed_not_raised(monkeypatch, state):
    def boom(**kw): raise RuntimeError("no playwright")
    monkeypatch.setattr(notify, "_send", boom)
    out = notify_run_report(a_job(pic="a@x.com"), state, a_result(adds=1))
    assert out.status == "failed" and "no playwright" in out.reason
    assert state.notification_for_run(1)["status"] == "failed"

def test_notify_skips_a_run_already_sent_unless_forced(monkeypatch, state):
    monkeypatch.setattr(notify, "_send", lambda **kw: OwaOutcome("sent", ""))
    notify_run_report(a_job(pic="a@x.com"), state, a_result(adds=1))
    assert notify_run_report(a_job(pic="a@x.com"), state, a_result(adds=1)).status == "skipped"
    assert notify_run_report(a_job(pic="a@x.com"), state, a_result(adds=1), force=True).status == "sent"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_notify.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'store_sync.core.notify'`

- [ ] **Step 3: Implement `core/notify.py`**

- `_send(*, to, cc, subject, html, profile_dir=None) -> OwaOutcome` — the one
  seam tests patch. It runs the async transport on a fresh event loop
  (Windows: set `WindowsProactorEventLoopPolicy` first) and returns the outcome.
  Uses `core.owa_mailer.send_mail_via_owa_async`.
- `should_notify(result)` — `not result.dry_run and result.counts.get("add", 0) > 0`.
- `subject_line(job_cfg, result)` —
  `f"[StoreSync] {job_cfg.job} — {n} row(s) added · {date}"` where `n` is the add
  count and `date` is `%d %b %Y` of the run's finish time (fall back to today when
  unavailable).
- `render_report(job_cfg, result, *, max_rows=50)` — pure. Escape every value with
  `html.escape`. Fact table (Job, Store `id`+`id_column`, Survey, Run `#id`, Time
  via `core.timefmt.local_time`, Added, Update proposals, Suppressed, Status) then
  a table of `add` actions (match key + mapped store columns) capped at `max_rows`,
  with `and {n} more row(s) — see the run log.` when truncated. Inline styles only.
- `notify_run_report(job_cfg, state, result, *, force=False, out_dir=None, profile_dir=None)`
  — applies the spec §6 rule order, calls `_send` only on the send path, and
  **always** records via `state.record_notification(...)`. It never raises: wrap
  the `_send` call in `try/except Exception` → `failed` + reason. Recipients come
  from `split_pic_emails(job_cfg.pic)[0]`; the `other` group goes into the skip
  `reason`. When `result.run_id` is `None`, record with `run_id=0` and skip the
  already-sent check.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_notify.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/notify.py tests/core/test_notify.py
git commit -m "feat(core): add run notification domain and HTML report"
```

---

### Task 6: Wire notification into `run_sync` (`core/sync.py`)

**Files:**
- Modify: `src/store_sync/core/sync.py`
- Test: `tests/core/test_sync.py`

**Interfaces:**
- Consumes: `core.notify.notify_run_report` (Task 5), `State` (Task 3).
- Produces: `run_sync(config, *, client, state, throttle=0.0, max_retries=3, backoff=1.0, should_stop=None, progress=None, notify: bool = True, notify_out_dir=None) -> SyncResult` — signature otherwise unchanged; `_run_sync_inner(...)` holds the current body.

- [ ] **Step 1: Write the failing tests**

Reuse the file's existing helpers: `config(...)`, `FakeClient`, and a
`State(":memory:")`. (`...` below stands for the store/subject arguments the
existing tests already pass to `FakeClient`.)

```python
import store_sync.core.sync as sync_mod
from store_sync.core.sync import run_sync

def test_run_sync_notifies_with_the_result(monkeypatch, ...):
    seen = []
    monkeypatch.setattr(sync_mod, "notify_run_report",
                        lambda cfg, st, res, **kw: seen.append(res) or None)
    res = run_sync(config(dry_run=False), client=FakeClient(...), state=state)
    assert seen == [res]

def test_run_sync_notify_false_skips_notification(monkeypatch, ...):
    monkeypatch.setattr(sync_mod, "notify_run_report", lambda *a, **k: pytest.fail("no"))
    run_sync(config(dry_run=False), client=..., state=state, notify=False)

def test_notification_failure_does_not_change_run_status(monkeypatch, ...):
    def boom(*a, **k): raise RuntimeError("mailbox down")
    monkeypatch.setattr(sync_mod, "notify_run_report", boom)
    res = run_sync(config(dry_run=False), client=..., state=state)
    assert res.status == "ok" and res.counts["add"] == 1
    assert state.get_run(res.run_id)["status"] == "ok"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_sync.py -q -k notify`
Expected: FAIL — `notify_run_report` is not an attribute of `sync`.

- [ ] **Step 3: Implement the wrapper**

Rename the existing `run_sync` body to `_run_sync_inner` (same signature, minus
the two new parameters). Add at the top of the module:
`from store_sync.core.notify import notify_run_report` (module-level, so
`monkeypatch.setattr(sync_mod, "notify_run_report", …)` works). Then:

```python
def run_sync(config, *, client, state, throttle=0.0, max_retries=3, backoff=1.0,
             should_stop=None, progress=None, notify=True, notify_out_dir=None):
    res = _run_sync_inner(config, client=client, state=state, throttle=throttle,
                          max_retries=max_retries, backoff=backoff,
                          should_stop=should_stop, progress=progress)
    if notify:
        _notify_safely(config, state, res, out_dir=notify_out_dir)
    return res
```

`_notify_safely` calls `notify_run_report(config, state, res, out_dir=out_dir)`
inside `try/except Exception` and logs (`logger.exception`) on failure — the
exception is swallowed and `res` is returned untouched. `_run_sync_inner` keeps
its `finally: _finish("error", {})` exactly as-is.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_sync.py -q`
Expected: PASS — including the pre-existing tests, which have no PIC and so
record `skipped`.

- [ ] **Step 5: Run the whole suite**

Run: `./.venv/bin/python -m pytest -q && ./.venv/bin/python -m pytest -q -W error`
Expected: all green (489 pre-existing + new).

- [ ] **Step 6: Commit**

```bash
git add src/store_sync/core/sync.py tests/core/test_sync.py
git commit -m "feat(sync): notify PIC after a run that added rows"
```

---

### Task 7: CLI (`send-report`, `--no-notify`, report status)

**Files:**
- Modify: `src/store_sync/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `core.notify.notify_run_report`, `SendOutcome` (Task 5); `State.notification_for_run` (Task 3); `run_sync(..., notify=…)` (Task 6).
- Produces: `stg-store-sync-cli send-report <name> [--run-id N] [--dry-run --out DIR] [--force] [--json]`; `--no-notify` on `run`/`run-all`/`run-due`; `report=<status>` in text output and `report_status` in JSON.

- [ ] **Step 1: Write the failing tests**

```python
def test_send_report_dry_run_writes_html_without_sending(monkeypatch, tmp_path, db):
    # `db` fixture: a registry with job "link1" + a finished run that planned one
    # add; monkeypatch.setenv("STG_STORE_SYNC_DB", str(db)) is applied by the fixture.
    code = cli.main(["send-report", "link1", "--dry-run", "--out", str(tmp_path)])
    assert code == 0 and (tmp_path / "run_1.html").exists()

def test_send_report_unknown_job_is_3(db, capsys):
    assert cli.main(["send-report", "nope"]) == 3

def test_run_no_notify_propagates(monkeypatch, db):
    seen = []
    monkeypatch.setattr(cli, "run_sync",
        lambda cfg, **kw: seen.append(kw.get("notify")) or fake_result())
    cli.main(["run", "link1", "--no-notify", "--dry-run"])
    assert seen == [False]

def test_run_json_includes_report_status(monkeypatch, db, capsys):
    cli.main(["run", "link1", "--json"])
    assert "report_status" in json.loads(capsys.readouterr().out)
```

Follow the existing `tests/test_cli.py` convention: point the CLI at a temp DB
with `monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))` (there is no `--db`
flag). Add a small `db` fixture that builds a `Registry` + `State` on a
`tmp_path` file, adds `link1`, and finishes one run with `counts={"add": 1}`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -q -k "send_report or no_notify"`
Expected: FAIL — unknown command / missing `--no-notify`.

- [ ] **Step 3: Implement**

- `build_parser`: add `--no-notify` (`action="store_true"`) to `run`, `run-all`,
  `run-due`; add a `send-report` subparser with `name`, `--run-id`, `--dry-run`,
  `--out`, `--force`, `--json`.
- `_run_one`: pass `notify=not args.no_notify` (add a keyword-only `notify=True`
  parameter to `_run_one`; `run-due`'s loop and `run-all` thread it through).
- `_emit`/`_status_line`: append `report=<status>` from
  `getattr(res, "report_status", "")` when set, and add `report_status` to the
  JSON object.
- `run_sync` records the notification but does not return its status; so in
  `_run_one`, after the call, set `res.report_status =
  (state.notification_for_run(res.run_id) or {}).get("status", "")` for display.
- New `_cmd_send_report(db, args)`: resolve the job (unknown → `3`), resolve the
  run (`--run-id` or the newest `state.recent_runs(name, limit=1)`; none → `3`),
  reconstruct a minimal `SyncResult` from the stored run row (`dry_run` from the
  row, `counts` from the row), call `notify_run_report(job, state, result,
  force=args.force, out_dir=args.out)` (with `dry_run` forced when `--dry-run`),
  then map the outcome to an exit code: `sent`/`skipped`/`dry_run` → `0`,
  `failed` → `1`.
- `main()`: dispatch `send-report`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): add send-report and --no-notify"
```

---

### Task 8: GUI (PIC field, notification line)

**Files:**
- Modify: `src/store_sync/ui/job_detail.py`, `src/store_sync/gui.py`, `src/store_sync/ui/run_detail.py`
- Test: `tests/ui/test_job_detail.py`, `tests/ui/test_run_detail.py`

**Interfaces:**
- Consumes: `core.pic.pic_email_warning` (Task 1); `JobConfig.pic` (Task 4); `State.notification_for_run` (Task 3).
- Produces: `JobDetail` gains `pic_edit` (a `QLineEdit`) and `pic_warning` (a `QLabel`); `config_fields()["pic"]`; `RunDetail.load(run, conflicts, notification=None)` sets a `notification_label`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/ui/test_job_detail.py
def test_pic_field_loads_saves_and_warns():
    # `a_job_with_pic(pic)` = the file's `a_job()` with `pic=pic`.
    d = JobDetail(); d.load(E(), a_job_with_pic("a@x.com; Budi"))
    assert d.pic_edit.text() == "a@x.com; Budi"
    assert "Budi" in d.pic_warning.text()
    assert d.config_fields()["pic"] == "a@x.com; Budi"
    d.pic_edit.setText("ok@y.com")
    assert d.pic_warning.text() == "" and d.config_fields()["pic"] == "ok@y.com"

# tests/ui/test_run_detail.py
def test_run_detail_shows_notification_line():
    d = RunDetail(); d.load(a_run(), [], notification={"status": "sent", "recipients": "a@x.com"})
    assert "a@x.com" in d.notification_label.text()
    d.load(a_run(), [], notification=None)
    assert "—" in d.notification_label.text()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/ui/test_job_detail.py tests/ui/test_run_detail.py -q -k "pic or notification"`
Expected: FAIL — missing attribute.

- [ ] **Step 3: Implement**

- `ui/job_detail.py` `_config_tab`: a row with `QLabel("PIC emails:")`,
  `self.pic_edit = QLineEdit()`, and `self.pic_warning = QLabel()` (object name
  for a muted style); connect `pic_edit.textChanged` to a `_update_pic_warning`
  slot that sets `self.pic_warning.setText(pic_email_warning(text))`. `load`:
  `self.pic_edit.setText(job.pic)`; `config_fields()`: `"pic": self.pic_edit.text().strip()`.
- `gui.py` `_job_from_detail`: pass `pic=fields.get("pic", "")` into `JobConfig`.
- `ui/run_detail.py`: add `self.notification_label` under the summary; change
  `load(self, run, conflicts, notification=None)` to set
  `Mail: sent to <recipients>` / `Mail: skipped — <reason>` / `Mail: failed — <reason>`
  / `Mail: —`. `gui.py::_show_run`: pass
  `notification=self.state.notification_for_run(int(run_id))`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/ui -q`
Expected: PASS

- [ ] **Step 5: Headless smoke check**

Run:
`QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c "from PySide6.QtWidgets import QApplication as A; from store_sync.ui.main_window import MainWindow as W; a=A.instance() or A([]); w=W(); w.show(); w.close()"`
Expected: exit 0, no traceback.

- [ ] **Step 6: Commit**

```bash
git add src/store_sync/ui/job_detail.py src/store_sync/ui/run_detail.py src/store_sync/gui.py tests/ui/test_job_detail.py tests/ui/test_run_detail.py
git commit -m "feat(gui): PIC field and per-run notification line"
```

---

### Task 9: Packaging and documentation

**Files:**
- Modify: `pyproject.toml`, `.env.example`, `AGENTS.md`, `CONTEXT.md`, `README.md`, `tasks/todo.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the `[owa]` extra, the env documentation, and the new invariant.

- [ ] **Step 1: Add the optional extra**

In `pyproject.toml`, under `[project.optional-dependencies]`, add
`owa = ["playwright"]` alongside `dev = ["pytest"]`.

- [ ] **Step 2: Document the environment**

In `.env.example`, add a block: `STG_OWA_EMAIL` (the account to click when OWA
shows the account picker), optional `STG_OWA_PROFILE_DIR` (default
`~/.stg_store_sync/playwright_profile_owa`), and a note that OWA needs a one-time
interactive sign-in in the Playwright window (the browser is deliberately not
headless). No secrets.

- [ ] **Step 3: Record the invariant**

In `AGENTS.md`, add to the Hard invariants list: **a notification is a side
channel — it never changes a run's recorded status, and a clean (0-add) or
dry-run run never mails.** Add `notify`, `owa_mailer`, `pic` to the `core/`
layout bullet. In `CONTEXT.md`, add **PIC** and **Notification** to the glossary.

- [ ] **Step 4: Add the README section**

An "Email notifications" section: `pip install -e ".[owa]"`, set `STG_OWA_EMAIL`,
sign in to Outlook Web once when the Playwright window opens, set the PIC list
per job in the job detail, and `stg-store-sync-cli send-report <job>` to rehearse
(`--dry-run --out DIR`) or re-send (`--force`). Note `--no-notify` on the run
commands.

- [ ] **Step 5: Ledger + full suite**

Add a phase entry to `tasks/todo.md`. Then run
`./.venv/bin/python -m pytest -q && ./.venv/bin/python -m pytest -q -W error`
and confirm green.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .env.example AGENTS.md CONTEXT.md README.md tasks/todo.md
git commit -m "docs: document OWA run notifications"
```
