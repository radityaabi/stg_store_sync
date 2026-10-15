"""Run-notification domain: decide, render, and send one run's report.

This module is the only place that knows what a "run report" is. It sits on top
of the transport (:mod:`store_sync.core.owa_mailer`) and the PIC helpers
(:mod:`store_sync.core.pic`), and it records every attempt through
:class:`store_sync.core.state.State` so the GUI and an operator can see what was
mailed, to whom, and why nothing was mailed when nothing was.

**A notification is a side channel.** :func:`notify_run_report` never raises:
any failure — a bad PIC string, a Playwright crash, an ``ImportError`` when
Playwright is not installed — is recorded as ``failed`` and returned. The caller
(:func:`store_sync.core.sync.run_sync`) therefore cannot have a run's outcome
changed by a mailbox.

Trigger rules (spec §6), applied in order; every non-send is recorded and is
never an error:

1. dry run                          -> ``dry_run`` (write the .html when asked)
2. zero adds planned                -> ``skipped``
3. no valid address in the PIC list -> ``skipped``
4. a ``sent`` row already exists    -> ``skipped`` (unless ``force``)
5. otherwise                        -> ``sent`` / ``failed``
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from store_sync.core import owa_mailer
from store_sync.core.owa_mailer import OwaOutcome
from store_sync.core.pic import split_pic_emails
from store_sync.core.timefmt import local_time

logger = logging.getLogger(__name__)

__all__ = [
    "SendOutcome",
    "should_notify",
    "subject_line",
    "render_report",
    "notify_run_report",
]

# Inline styles only: Outlook strips <style> blocks and external stylesheets.
_WRAP = "font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937"
_H2 = "font-size:18px;margin:0 0 8px"
_MUTED = "color:#6b7280;font-size:12px;margin:8px 0"
_TABLE = "border-collapse:collapse;margin:6px 0;width:auto"
_LABEL = "padding:3px 10px 3px 0;color:#6b7280;font-size:12px;vertical-align:top"
_VALUE = "padding:3px 0;font-size:13px"


@dataclass(frozen=True)
class SendOutcome:
    """What happened when a run's report was attempted.

    ``status`` is one of ``sent`` · ``skipped`` · ``failed`` · ``dry_run``.
    """

    status: str
    reason: str = ""
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()


def should_notify(result) -> bool:
    """Would this run mail? A live run that planned at least one add.

    A *predicate* for previews — not the gate. :func:`notify_run_report` is still
    called for every run so a non-send is recorded with its reason.
    """
    return not result.dry_run and result.counts.get("add", 0) > 0


def _add_count(result) -> int:
    return int(result.counts.get("add", 0) or 0)


def subject_line(job_cfg, result) -> str:
    """Build the mail subject.

    The job name leads because a supervisor's mailbox holds several jobs'
    reports: a subject reading only "3 rows added" is unactionable.
    """
    n = _add_count(result)
    when = _run_when(result) or datetime.now()
    return f"[StoreSync] {job_cfg.job} — {n} row(s) added · {when.strftime('%d %b %Y')}"


def _run_when(result) -> datetime | None:
    """Best-effort local time of the run; None when unavailable."""
    raw = getattr(result, "finished_at", None)
    if not raw:
        return None
    try:
        return datetime.strptime(local_time(raw), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _fmt_time(result) -> str:
    raw = getattr(result, "finished_at", None)
    return local_time(raw) if raw else "—"


def render_report(job_cfg, result) -> str:
    """Render the mail body: self-contained, inline-styled, English.

    A **summary only** — the run's facts, no table of the added rows. The row
    detail lives in the run log; the mailbox gets the headline (job, counts,
    status), not a data dump. Every interpolated value is escaped — store and
    survey values are operator- and API-supplied and must not be able to break
    the layout. No script, no remote image, no external URL.
    """
    e = _html.escape

    facts = [
        ("Job", e(str(job_cfg.job))),
        ("Store", e(f"{job_cfg.store.id} · {job_cfg.store.id_column}")),
        ("Survey", e(str(job_cfg.source.survey_id))),
        ("Run", e(f"#{result.run_id}")),
        ("Time", e(_fmt_time(result))),
        ("Added", e(f"{_add_count(result):,}")),
        ("Update proposals", e(str(getattr(result, "proposals", 0) or 0))),
        ("Suppressed", e(str(getattr(result, "suppressed", 0) or 0))),
        ("Status", e(str(result.status))),
    ]
    fact_rows = "".join(
        f'<tr><td style="{_LABEL}">{label}</td>'
        f'<td style="{_VALUE}">{value}</td></tr>'
        for label, value in facts
    )

    return (
        f'<div style="{_WRAP}">'
        f'<h2 style="{_H2}">StoreSync — {_add_count(result):,} row(s) added</h2>'
        f'<p style="{_MUTED}">Automated report from the scheduled run.</p>'
        f'<table style="{_TABLE}" cellpadding="0" cellspacing="0">{fact_rows}</table>'
        f"</div>"
    )


def _send(*, to, cc, subject, html, profile_dir=None) -> OwaOutcome:
    """Run the async OWA transport on a fresh event loop.

    The one seam tests patch — no other code in this module touches Playwright.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(
            owa_mailer.send_mail_via_owa_async(
                to=list(to),
                cc=list(cc),
                subject=subject,
                html=html,
                attachments=[],
                profile_dir=profile_dir,
            )
        )
    finally:
        loop.close()


def notify_run_report(
    job_cfg,
    state,
    result,
    *,
    force: bool = False,
    out_dir: str | Path | None = None,
    profile_dir: str | Path | None = None,
    subject: str | None = None,
    body_html: str | None = None,
) -> SendOutcome:
    """Mail one run's report, or record why it was not mailed.

    Never raises for a delivery problem: the run already succeeded and its
    results are stored; a broken mailbox must not turn that into a failed run.
    Every path records a ``notifications`` row, including the non-sends.

    ``subject``/``body_html`` override the freshly rendered report; ``send-report``
    passes the *stored* body so a re-send is byte-identical to the original even
    though the run's payloads are long gone (spec §5.2).
    """
    run_id = result.run_id if result.run_id is not None else 0
    recipients, others = split_pic_emails(job_cfg.pic)
    # Rendering runs outside `_decide`'s try, so contain it here: a render bug
    # must still record a row rather than escape into `run_sync`.
    try:
        subject = subject or subject_line(job_cfg, result)
        html = body_html if body_html is not None else render_report(job_cfg, result)
    except Exception as exc:  # noqa: BLE001 — containment is the point
        logger.exception("Run %s: report render failed", run_id)
        outcome = SendOutcome("failed", reason=f"render failed: {exc}")
        state.record_notification(
            run_id, job=job_cfg.job, status=outcome.status,
            reason=outcome.reason,
        )
        return outcome

    outcome = _decide(state, result, run_id, job_cfg, recipients, others, force,
                      subject, html, out_dir, profile_dir)
    state.record_notification(
        run_id,
        job=job_cfg.job,
        status=outcome.status,
        recipients=", ".join(outcome.to),
        subject=subject,
        # Only a genuine send keeps its body: a `skipped`/`dry_run` row has
        # nothing to re-send, and a per-minute job would otherwise grow the DB
        # without bound.
        body_html=html if outcome.status == "sent" else "",
        reason=outcome.reason or None,
    )
    return outcome


def _decide(state, result, run_id, job_cfg, recipients, others, force,
            subject, html, out_dir, profile_dir) -> SendOutcome:
    """Apply the spec §6 rule order and act on the outcome (no recording)."""
    # 1) dry run: rehearse, never send.
    if result.dry_run:
        if out_dir is not None:
            target = Path(out_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / f"run_{run_id}.html").write_text(html, encoding="utf-8")
            logger.info("Run %s: dry run wrote %s", run_id, target)
        return SendOutcome("dry_run", reason="dry run — nothing sent")

    # 2) nothing was added: not this feature's mail to send.
    if _add_count(result) == 0:
        return SendOutcome("skipped", reason="no rows added")

    # 3) nobody to mail.
    if not recipients:
        reason = "no PIC recipient configured"
        if others:
            reason = (
                "PIC holds no email address — not mailed: " + ", ".join(others)
            )
        return SendOutcome("skipped", reason=reason)

    # 4) already reported (unless explicitly re-sent).
    if not force and run_id:
        existing = state.notification_for_run(run_id)
        if existing and existing.get("status") == "sent":
            return SendOutcome("skipped", reason="already sent for this run",
                               to=tuple(recipients))

    # 5) send. Contained: a transport failure is a recorded `failed`, not a raise.
    try:
        owa = _send(to=recipients, cc=[], subject=subject, html=html,
                    profile_dir=profile_dir)
    except Exception as exc:  # noqa: BLE001 — containment is the point
        logger.exception("Run %s: report send failed", run_id)
        return SendOutcome("failed", reason=str(exc), to=tuple(recipients))

    if owa.status == "sent":
        logger.info("Run %s: report sent to %s", run_id, ", ".join(recipients))
        return SendOutcome("sent", to=tuple(recipients))
    return SendOutcome("failed", reason=owa.detail, to=tuple(recipients))
