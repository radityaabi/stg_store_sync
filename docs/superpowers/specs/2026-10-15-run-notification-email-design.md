# Run Notification Email (OWA) — design

Date: 2026-10-15
Status: approved (design), spec for review
Supersedes: nothing. Extends `2026-10-08-stg-store-sync-design.md` §7 (run
pipeline / CLI) and `2026-10-12-os-scheduler-design.md` §5 (CLI surface).
Reference (never imported): `stg_automation_suite/src/lib/owa_mailer.py`,
`src/momo/core/{report,mailer,pic}.py`.

## 1. Problem

A scheduled run adds rows to a Subject Store silently. The operator who owns the
store — and the PIC who requested the data — only learns about a run by opening
the GUI and reading the run log. When the app is closed and the OS task
(`run-due`) does the work, nobody sees anything at all.

The sibling `momo` tool already solves this shape: after a validation run it
mails an HTML report to the project's PIC list. Leadership has asked this tool to
do the same for added rows.

## 2. Goals / Non-Goals

**Goals**

- After a run that **added at least one row**, mail an HTML summary to the job's
  PIC list.
- One implementation point that covers **every** run path: GUI *Run now*, CLI
  `run`, `run-all`, and `run-due` (OS task).
- Delivery must never change a run's outcome. A broken mailbox cannot turn a
  successful sync into a failed one.
- The tool must never send mail it did not intend to send: dry-run sends nothing,
  a clean run (0 adds) sends nothing, a job with no PIC sends nothing.
- An operator can re-send or rehearse a run's mail by hand, and can see what was
  sent, to whom, and when.
- No IT involvement: no SMTP relay, no app password, no firewall change.

**Non-Goals**

- SMTP transport. Explicitly rejected (see §8) — it needs IT-managed
  credentials; OWA reuses the operator's existing signed-in browser session.
- Attachments. The body carries the summary and the add list (momo attaches a
  workbook; that is out of scope here).
- Per-column/per-PIC routing, mailing lists, templates the operator can edit.
- Retrying a failed send automatically. A failed send is recorded and can be
  re-sent by hand (`send-report`).
- Changing anything about the pull → plan → write pipeline or the invariants in
  `AGENTS.md`. The mail is a side channel.

## 3. Verified findings

- `run_sync` (`core/sync.py`) is the **single** funnel for every run:
  `gui.py::_default_run_fn`, `cli.py::_run_one` (`run`, `run-all`, `run-due`).
  One call site therefore covers all four entry points.
- `run_sync` already ends with a one-shot `_finish(status, counts)` that is
  guaranteed to run (its `finally` is a last resort). A notification placed
  **after** `_finish` cannot affect the recorded run status.
- The sibling's OWA transport is a self-contained Playwright script
  (`src/lib/owa_mailer.py`, 442 lines) with exactly two non-stdlib imports
  (`lib.log`, `lib.paths`). Porting it needs only a local logger and a profile
  directory; no `stg_automation_suite` import is required (repo rule: reference
  only, never import).
- The transport launches `channel="msedge"` with `headless=False` and a
  persistent profile. `microsoft-edge` is present on this machine
  (`/usr/bin/microsoft-edge`); the sibling pins `playwright==1.58.0`.
- `Plan.counts` (`core/planner.py`) is the authoritative add count:
  `{"add", "update", "same", "conflict"}`. `write.apply_plan` re-counts applied
  actions, but `Plan.counts["add"]` is what the run *planned* and, on a
  non-cancelled run, what it wrote.
- Existing tests call `run_sync` with configs that have no PIC and mostly
  `dry_run=True`; a notification defaulting to "no PIC ⇒ skipped" keeps the
  current 489 tests untouched.

## 4. Architecture

```
     GUI Run now ─┐
     CLI run ─────┤
     CLI run-all ─┼─▶ run_sync(config, *, client, state, notify=True, …)
     CLI run-due ─┘        │
                           ├─ _run_sync_inner(…)  ── unchanged pipeline ──▶ SyncResult
                           │      (the current body of run_sync)
                           └─ _notify_safely(config, state, result, …)   # never raises
                                    │
                                    ▼
                            core/notify.py
                              should_notify(result)      # add count > 0, not dry-run
                              subject_line / render_report
                              notify_run_report(…) ──▶ core/owa_mailer.py
                                                          │
                                                          ▼
                                                   Playwright → OWA (msedge)
                                    │
                                    ▼
                            State.record_notification(…)   # sent|skipped|failed|dry_run
```

**Containment is the design.** `_notify_safely` wraps the whole notification in
`try/except Exception`: any failure — a bad PIC string, a Playwright crash, an
`ImportError` when Playwright is not installed — is logged, recorded as
`failed`, and swallowed. `run_sync` then returns the `SyncResult` exactly as
`_run_sync_inner` produced it. The run status is already committed by the time
the mail is attempted.

### 4.1 Components

- **`core/owa_mailer.py`** (new) — a verbatim port of the sibling's OWA
  transport, with two substitutions: `lib.log.get_logger` → a module
  `logging.getLogger`, and `lib.paths.DATA_DIR` → the tool's own state
  directory (`~/.stg_store_sync/`). Public API unchanged:
  `OwaOutcome(status, detail)` and
  `send_mail_via_owa_async(to, cc, subject, html, attachments, *, profile_dir)`.
  `status` ∈ `sent | skipped | failed`. Imported **lazily** inside the function
  so the app and the test suite never need Playwright installed.
- **`core/notify.py`** (new) — the domain layer, and the only module that knows
  what a "run report" is:
  - `should_notify(result) -> bool` — `not result.dry_run and
    result.counts.get("add", 0) > 0`. A *predicate*, not the gate: it answers
    "would this run mail?" for the GUI/CLI preview. `notify_run_report` is still
    invoked for every run (when `notify=True`) so that a non-send is recorded
    with its reason rather than silently omitted (§6).
  - `subject_line(job_cfg, result) -> str`.
  - `render_report(job_cfg, result) -> str` — pure, HTML,
    inline-styled, summary only, every interpolated value escaped.
  - `SendOutcome(status, reason, to, cc)` — `sent | skipped | failed | dry_run`.
  - `notify_run_report(job_cfg, state, result, *, force=False, out_dir=None,
    profile_dir=None) -> SendOutcome` — resolves recipients, applies the skip
    rules, calls the transport, records the notification.
- **`core/pic.py`** (new) — `parse_pics`, `format_pics`, `is_valid_email`,
  `split_pic_emails`, `pic_email_warning`, ported from `momo/core/pic.py`
  (~30 lines). Single source of truth for the `;`-separated PIC string, shared
  by the GUI field, the registry, and the mailer.
- **`core/registry.py`** — new `jobs.pic` column + migration; `JobEntry.pic`.
- **`core/state.py`** — new `notifications` table + three accessors.
- **`core/sync.py`** — `run_sync` becomes a thin wrapper; the current body moves
  to `_run_sync_inner` unchanged.
- **`cli.py`** — new `send-report` subcommand; `--no-notify` on the run
  commands; `report=<status>` in the text and JSON output.
- **`gui.py` / `ui/job_detail.py` / `ui/run_detail.py`** — the PIC field, its
  warning label, and the per-run notification line.

## 5. Data model

### 5.1 `jobs.pic` (registry)

```sql
ALTER TABLE jobs ADD COLUMN pic TEXT NOT NULL DEFAULT ''
```

A `;`-separated list of addresses (canonical stored form is `"; "`, via
`format_pics`). Migration follows the existing `_add_pairing_columns` pattern:
`PRAGMA table_info` → `ALTER TABLE … ADD COLUMN` when absent, so an existing DB
reads back `pic=''` (no recipients → notifications skipped, never an error).

Non-address entries are **allowed to save** (a PIC column holding people's names
predates the decision to mail reports there) and are surfaced as a warning in the
job detail, exactly as momo does. `split_pic_emails` returns them so the caller
can warn; only real addresses are mailed.

### 5.2 `notifications` (state)

```sql
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL,
    job        TEXT    NOT NULL,
    status     TEXT    NOT NULL,   -- sent | skipped | failed | dry_run
    recipients TEXT    NOT NULL DEFAULT '',
    subject    TEXT    NOT NULL DEFAULT '',
    body_html  TEXT    NOT NULL DEFAULT '',
    reason     TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS notifications_run ON notifications(run_id);
```

- `record_notification(run_id, job, status, recipients, subject, body_html,
  reason) -> int`
- `notification_for_run(run_id) -> dict | None` — the most recent row for a run
  (drives the GUI line and the re-send guard).
- `notifications_for_run(run_id) -> list[dict]` — full history (a re-send adds a
  row).

**Why the body is stored, not re-rendered.** `send-report` must be able to
re-send a run whose payloads are no longer in memory. Re-rendering would require
persisting every added row per run; storing the rendered body is one column and
makes a re-send byte-identical to the original. The trade-off is explicit: a
template change does not retroactively alter an old run's mail, which is the
desired behaviour for an audit trail.

## 6. Trigger rules

Evaluated in `notify_run_report`, in this order. Each non-send is **recorded**
(so the GUI and the audit trail show *why* nothing was mailed) and is **never an
error**.

| # | Condition | Outcome | Recorded `status` |
|---|---|---|---|
| 1 | `result.dry_run` | write `run_<id>.html` into `out_dir` when given, else nothing; send nothing | `dry_run` |
| 2 | `result.counts["add"] == 0` | send nothing | `skipped` |
| 3 | `pic` yields no valid address | send nothing | `skipped` (reason names the non-address entries) |
| 4 | a `sent` row already exists for this run and `force=False` | send nothing | `skipped` |
| 5 | otherwise | send | `sent` / `failed` |

- Rules are checked against the **applied** add count (`result.counts["add"]`),
  which equals the planned count on any run that completed its apply. A run
  finished `cancelled` mid-apply still notifies: some adds landed, and the PIC
  should see the partial result. A run finished `blocked` or `error` before any
  apply has `add == 0` and is therefore skipped by rule 2.
- Rule 1 precedes rule 2: a dry-run that planned adds is a rehearsal, not a
  skip, and is recorded as `dry_run`.
- A `sent` row is the re-send guard. `--force` (CLI) overrides it. Rule 3 is
  deliberately *not* overridable — there is nobody to mail.

## 7. Email content

**Subject** (momo's shape — the job name leads, because a supervisor's mailbox
holds several jobs' reports):

```
[StoreSync] <job> — <n> row(s) added · <DD Mon YYYY>
```

**Body** — self-contained HTML, **inline styles only** (Outlook strips `<style>`
blocks and external stylesheets), no script, no remote image, no external URL
(the same mail-client constraints momo documents). Every interpolated value is
escaped: store/survey values and PIC entries are operator- and API-supplied and
must not be able to break the layout.

- A fact table: Job, Store (`id` + id column), Survey, Run `#id`, Time (local,
  via `core/timefmt.py`), Added, Update proposals, Suppressed, Status.
- Summary only — no table of the added rows. The per-row detail lives in the
  run log; the mailbox gets the headline.
- No attachment.

A run with no mapped columns beyond the match key still renders.

## 8. Transport — OWA via Playwright

The sibling's `send_mail_via_owa_async` is ported as-is. Its behaviour is the
contract:

- `playwright.chromium.launch_persistent_context(user_data_dir, headless=False,
  channel="msedge", args=["--disable-blink-features=AutomationControlled",
  "--no-sandbox"], permissions=["clipboard-read", "clipboard-write"])`.
- Navigate to
  `https://outlook.office.com/mail/deeplink/compose?to=…&subject=…`.
- Wait for the compose shell (auto-clicking the AAD account picker when it
  appears), inject CC, paste the HTML body from the clipboard, optionally attach
  files, then click Send / press `Ctrl+Enter` with retries, and confirm by the
  editor disappearing or a completion screen.
- Returns `OwaOutcome("sent"|"skipped"|"failed", detail)`; it **never raises**
  (its own top-level `except` returns `failed`).

**Configuration**

| Env var | Purpose |
|---|---|
| `STG_OWA_EMAIL` | the account to click when OWA shows the AAD account picker (replaces the sibling's `MOMO_SURVEY_EMAIL`) |
| `STG_OWA_PROFILE_DIR` | optional override for the persistent browser profile; default `~/.stg_store_sync/playwright_profile_owa` |

**Consequences accepted (operator-visible):**

- **A browser window opens.** `headless=False` means every email raises an Edge
  window, including on a scheduled `run-due` fired by an OS task. This is momo's
  behaviour and is kept verbatim; OWA's own anti-automation handling is why it is
  not headless.
- **The send blocks the run worker.** The send runs synchronously on the
  `RunQueue` worker after `_finish`, so the next queued run waits (worst case
  ~90 s for `page.goto` plus compose time). A separate daemon thread was
  considered and rejected: it would be a detached thread to join on shutdown
  (`AGENTS.md`), and the run is not "done" for the operator until the mail is
  out anyway.
- **Playwright is an optional extra.** `pip install -e ".[owa]"`. Absent →
  `ImportError` → `failed` with an actionable reason; the app and the suite run
  without it.

## 9. CLI surface

```
stg-store-sync-cli send-report <name> [--run-id N] [--dry-run --out DIR] [--force] [--json]
stg-store-sync-cli run <name>      … [--no-notify]
stg-store-sync-cli run-all         … [--no-notify]
stg-store-sync-cli run-due         … [--no-notify]
```

- **`send-report <name>`** — mail (or rehearse) one run's report, momo-style.
  Defaults to the job's most recent run; `--run-id` selects another. `--dry-run`
  requires `--out DIR` and writes `run_<id>.html` without sending. `--force`
  re-sends a run already reported. Exit `0` on `sent`/`skipped`/`dry_run`, `1`
  on `failed`, `3` on unknown job/run.
- **`--no-notify`** — suppresses the automatic send for that invocation. It is
  the escape hatch for an operator who wants a run without mail (e.g. a test
  against a live store).
- **Output** — `report=<status>` appended to the existing status line, and a
  `report_status` key in `--json`.

## 10. GUI

- **Job detail → Config tab**: a `PIC emails` `QLineEdit` bound to
  `jobs.pic`, with a live warning label underneath (`pic_email_warning`) naming
  any entry that is not an address. `config_fields()` gains `pic`;
  `Controller._job_from_detail` and `Controller.save` thread it through
  unchanged otherwise.
- **Run detail**: one notification line under the summary —
  `Mail: sent to a@x, b@y` / `Mail: skipped — no adds` / `Mail: failed — …` —
  read from `state.notification_for_run(run_id)`. Absent row → the line reads
  `Mail: —`.

The GUI never sends mail itself; it only displays what `run_sync` recorded. A
manual re-send is the CLI's job (`send-report`), keeping one send path.

## 11. Risks and failure modes

| Risk | Level | Mitigation |
|---|---|---|
| Mail failure corrupts a run | High | `_notify_safely` swallows every exception; the run is `_finish`ed first. Recorded `failed`, run status untouched. |
| Playwright not installed | Med | Lazy import → `failed` with an actionable message; `[owa]` extra documented. |
| OWA session not signed in / profile stale | Med | Transport returns `failed` with the sibling's guidance ("log in to Outlook Web in the Playwright window"); recorded, re-sendable. |
| Browser window appears on a scheduled run | Med | Accepted, documented (§8); `--no-notify` exists; a future headless option is out of scope. |
| Send blocks the run queue | Med | Accepted (§8). Bounded by the transport's own timeouts. |
| Duplicate mail for one run | Low | `sent` row per run is the guard; `--force` is explicit. |
| Mail on a run the operator did not expect | Low | `dry_run` sends nothing; `--no-notify` per invocation; `enabled`/schedule are the operator's own. |
| PIC holds names, not addresses | Low | Non-fatal; `split_pic_emails` mails only addresses and the GUI warns. |
| HTML injection via store/survey values | Low | Every interpolated value escaped in `render_report`. |
| An existing test starts sending mail | High | `notify=True` defaults are inert: no PIC ⇒ `skipped`, `dry_run` ⇒ `dry_run`. No test config carries a PIC. |
| `run_sync`'s `finally: _finish("error", {})` masked | Med | Notification is **outside** `_run_sync_inner`, after the wrapper returns; the inner control flow is not touched. |

## 12. Testing strategy

TDD throughout; the existing suite stays green.

- **`test_owa_mailer.py`** — a fake `playwright.async_api.async_playwright`
  (stubbed context/page) asserts: the deep-link URL carries `to` and `subject`;
  the body is pasted; a `failed` editor wait returns `failed`; a closed window is
  read as `sent`; an empty `to` returns `skipped` without launching. No browser
  is started.
- **`test_pic.py`** — parse/format round-trips, case-insensitive dedupe,
  `is_valid_email` accept/reject, `split_pic_emails` grouping,
  `pic_email_warning` singular/plural/empty.
- **`test_notify.py`** — `should_notify` truth table (dry-run, 0 adds, adds);
  `render_report` escaping (`<script>` in a store value) and the 50-row cap with
  the "and N more" line; `notify_run_report` with a **fake transport**: `sent` /
  `skipped` (0 adds, no PIC, no valid address, already sent) / `dry_run` writes
  the file; a transport that raises is recorded `failed` and does not propagate.
- **`test_state.py`** — `record_notification` / `notification_for_run`
  (most recent wins) / `notifications_for_run` ordering; the table is created on
  an existing DB.
- **`test_registry.py`** — `pic` round-trips through `add`/`update`/`get` and
  `JobEntry`; a DB created before the column migrates and reads back `''`.
- **`test_sync.py`** — with a monkeypatched `notify_run_report`: it is called
  once per run with the result; `notify=False` skips it; a `notify_run_report`
  that raises leaves the run's `status`/`counts` unchanged (the containment
  contract).
- **`test_job_detail.py`** — the PIC field loads/saves and the warning label
  updates.
- **CLI** — `send-report` argument handling and exit codes against a fake
  transport; `--no-notify` propagates `notify=False`; `report=<status>` appears
  in the text and JSON output.

## 13. Documentation

- `AGENTS.md`: a new invariant — **a notification is a side channel; it never
  changes a run's status, and a clean/dry run never mails.** Plus a pointer to
  `core/notify.py` / `core/owa_mailer.py` in the layout list.
- `CONTEXT.md`: vocabulary for "PIC", "notification", "OWA transport".
- `.env.example`: `STG_OWA_EMAIL`, optional `STG_OWA_PROFILE_DIR`, and a note
  that OWA needs a one-time interactive sign-in in the Playwright window.
- `README.md`: an "Email notifications" section (install the `owa` extra, set
  `STG_OWA_EMAIL`, sign in once, set PIC per job).
- `tasks/todo.md`: a phase ledger entry.

## 14. Out of scope

- SMTP transport, attachments, digest/daily batching, per-column routing,
  operator-editable templates, automatic retry/backoff for a failed send,
  headless OWA, and any change to the pull → plan → write pipeline or its
  invariants.
