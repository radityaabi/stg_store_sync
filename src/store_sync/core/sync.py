"""End-to-end orchestrator: run one sync job (spec §7.2).

    cached survey index -> derive pull terms -> pull (filter/fallback)
    store GetStore -> plan (diff by match key)
    id-column no-op probe (live adds only) -> writer.apply (adds only) -> proposals
    state.record_run

The `state` run row and conflicts are written regardless of dry-run, so the GUI
run log is complete. A store-side duplicate key is a hard conflict: the job is
marked `blocked` and **no write happens** (fail-closed, §4.4).

**Automation writes ADD only.** `update` actions are recorded as pending
proposals for operator review (never written here); there is no delete.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from store_sync.core.binding import noop_probe
from store_sync.core.config import JobConfig
from store_sync.core.fetcher import pull
from store_sync.core.notify import notify_run_report
from store_sync.core.planner import Plan, _s, plan
from store_sync.core.preflight import _pairing_errors, preflight
from store_sync.core.stg_client import Cancelled
from store_sync.core.writer import apply_plan

logger = logging.getLogger(__name__)


def _readiness_errors(config, store_rows, source_records) -> list[str]:
    """Full preflight parity for the in-run fail-closed gate (spec §3.3/§4.4).

    The CLI never runs a separate preflight pass (that would double-fetch), so
    this gate is its entire safety story. It must therefore evaluate the *same*
    error set as `preflight`, reusing the already-fetched store snapshot and
    pulled source records.

    One deliberate exception: `preflight` also flags a match key non-unique *in
    the pull*. Spec §3.2 says source-side duplicates are **skipped** (status
    `partial`), not a hard halt, so that single message is excluded here; the
    planner records those as source-side conflicts instead.
    """
    report = preflight(
        store_rows=store_rows,
        source_records=source_records,
        config=config,
    )
    m = config.binding.match
    source_dup = (
        f"match key {m.source_var!r} is not unique in the pull" if m else None
    )
    return [e for e in report.errors if e != source_dup]


@dataclass
class SyncResult:
    plan: Plan
    dry_run: bool
    status: str  # "ok" | "blocked" | "partial" | "error"
    counts: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    run_id: int | None = None
    proposals: int = 0  # update proposals recorded for operator review
    suppressed: int = 0  # blacklisted keys skipped (not re-added)
    finished_at: str | None = None  # stored UTC finish stamp (report display)


def run_sync(
    config: JobConfig,
    *,
    client,
    state,
    throttle: float = 0.0,
    max_retries: int = 3,
    backoff: float = 1.0,
    should_stop=None,
    progress=None,
    notify: bool = True,
    notify_out_dir=None,
) -> SyncResult:
    """Run one full sync, then notify the job's PIC when it added rows.

    The notification is a **side channel**: it runs after the run row is already
    finished, is wrapped so it can never raise, and cannot change the returned
    result. It is sent only when `notify` is true (the per-invocation flag, e.g.
    `--no-notify`) **and** the job has notifications enabled
    (`config.notify_enabled`, the per-job GUI toggle).
    """
    res = _run_sync_inner(
        config,
        client=client,
        state=state,
        throttle=throttle,
        max_retries=max_retries,
        backoff=backoff,
        should_stop=should_stop,
        progress=progress,
    )
    _stamp_finished(res, state)
    if notify and config.notify_enabled:
        _notify_safely(config, state, res, out_dir=notify_out_dir)
    return res


def _stamp_finished(res, state) -> None:
    """Copy the stored UTC finish stamp onto the result, for report display.

    The notification renders *after* the run row is finished, and the run row is
    the only place the finish time exists; reading it back keeps one source of
    truth (and `send-report` reconstructs the same way).
    """
    if res.run_id is None or res.finished_at is not None:
        return
    row = state.get_run(res.run_id) or {}
    res.finished_at = row.get("finished_at")


def _notify_safely(config, state, res, *, out_dir=None) -> None:
    """Attempt the run's notification; swallow every failure.

    A mailbox problem must never turn a successful sync into a failed one, so
    any exception here is logged and dropped. `notify_run_report` already
    records the attempt (including a `failed` outcome) in the state DB.
    """
    try:
        notify_run_report(config, state, res, out_dir=out_dir)
    except Exception:  # noqa: BLE001 — containment is the point
        logger.exception("run notification failed for %r", config.job)


def _run_sync_inner(
    config: JobConfig,
    *,
    client,
    state,
    throttle: float = 0.0,
    max_retries: int = 3,
    backoff: float = 1.0,
    should_stop=None,
    progress=None,
) -> SyncResult:
    """Run one full sync (dry-run by default per `config.write.dry_run`).

    `should_stop` is an optional zero-arg predicate polled between pull chunks
    and between write actions; when it trips the run is finished `cancelled`
    and any writes already applied stay tracked.

    `progress` is an optional callable receiving one dict per pipeline stage
    (``{"stage": "ids"|"pull"|"store"|"preflight"|"plan"|"apply"|"done", ...}``;
    extra keys carry stage payloads like ``total`` or ``counts``). It is
    called synchronously from the worker thread — the GUI marshals it through a
    queued signal (see ``Controller._ProgressBridge``).
    """

    def _emit(stage: str, **payload) -> None:
        if progress is not None:
            # Every event carries its job so the GUI can keep one log per job
            # and never render job A's run into job B's panel.
            payload.setdefault("job", config.job)
            try:
                progress({"stage": stage, **payload})
            except Exception:  # noqa: BLE001 - progress must never break a run
                logger.debug("progress callback failed", exc_info=True)

    dry_run = config.write.dry_run
    run_id = state.start_run(job=config.job, dry_run=dry_run)
    _emit("start", job=config.job, run_id=run_id, dry_run=dry_run)

    # The run row must never be left with a null status, so every terminal path
    # funnels through this one-shot finisher. The `finally` is a last resort
    # for exceptions that bypass the `except` below.
    finished = False

    def _finish(status_: str, counts_: dict) -> None:
        nonlocal finished
        if not finished:
            state.finish_run(run_id, status=status_, counts=counts_)
            finished = True

    errors: list[str] = []
    p = Plan()

    try:
        # 0) pairing gate (fail closed before any pull/write) ---------------
        pairing_errs = _pairing_errors(config)
        if pairing_errs:
            for err in pairing_errs:
                state.record_conflict(run_id, match_value=None, reason=err)
            _finish("blocked", {})
            _emit("done", status="blocked", counts={}, dry_run=dry_run)
            return SyncResult(
                plan=p, dry_run=dry_run, status="blocked", counts={},
                errors=list(pairing_errs), run_id=run_id,
            )

        # 1) survey index + pull ---------------------------------------------
        index = dict(config.source.survey_index)
        subject_ids = client.survey_interview_ids(config.source.survey_id)
        _emit("ids", total=len(subject_ids))
        match = config.binding.match
        try:
            pulled = pull(
                client,
                config.source.survey_id,
                subject_ids,
                source_vars=[c.source_var for c in config.binding.columns],
                match_var=match.source_var if match else None,
                survey_index=index,
                should_stop=should_stop,
            )
        except Cancelled:
            _finish("cancelled", {})
            _emit("done", status="cancelled", counts={}, dry_run=dry_run)
            return SyncResult(
                plan=p, dry_run=dry_run, status="cancelled", counts={},
                errors=errors, run_id=run_id,
            )
        _emit("pull", total=len(pulled.records))

        # 2) load the store --------------------------------------------------
        store_rows = client.get_store(config.store.id)
        _emit("store", total=len(store_rows))
        store_columns = sorted({k for r in store_rows for k in r})

        # Reconcile the blacklist against the store snapshot: a tracked key gone
        # from the store was deleted by hand (blacklist it); a blacklisted key
        # back in the store was re-added by hand (clear it). Runs on dry-run too
        # — it mutates only SQLite, never the store. An empty snapshot is
        # *unknown*, not a mass deletion (see `State.reconcile_blacklist`).
        store_keys = {
            _s(r.get(match.store_col))
            for r in store_rows
            if match.store_col in r
        }
        state.reconcile_blacklist(config.job, store_keys)
        blacklisted = state.blacklisted_keys(config.job)

        # readiness preconditions (computed early; acted on before any write).
        # We defer the block until after conflict recording so the run log stays
        # complete for store-side duplicates the planner already flags.
        readiness_errs = _readiness_errors(config, store_rows, pulled.records)
        _emit("preflight", errors=list(readiness_errs))

        # 3) plan ------------------------------------------------------------
        p = plan(
            store_rows=store_rows,
            source_records=pulled.records,
            match=match,
            columns=config.binding.columns,
            id_column=config.store.id_column,
            store_columns=store_columns,
            blacklisted=blacklisted,
        )
        _emit("plan", counts=dict(p.counts), suppressed=len(p.suppressed))

        # record conflicts/quarantine (always, dry-run included)
        for action in p.actions:
            if action.kind == "conflict":
                state.record_conflict(run_id, match_value=action.match_value, reason=action.reason)
        for rec in p.quarantined:
            state.record_conflict(
                run_id,
                match_value=rec.get(match.source_var) if match else None,
                reason="missing match key in source",
            )

        # 4) fail-closed readiness gate (before any write) -------------------
        if readiness_errs:
            for err in readiness_errs:
                state.record_conflict(run_id, match_value=None, reason=err)
            _finish("blocked", {})
            _emit("done", status="blocked", counts={}, dry_run=dry_run,
                  suppressed=len(p.suppressed))
            return SyncResult(
                plan=p, dry_run=dry_run, status="blocked", counts={},
                errors=list(readiness_errs), run_id=run_id,
                suppressed=len(p.suppressed),
            )

        # 4b) fail-closed on store-side conflicts ----------------------------
        if p.blocked:
            errors.extend(a.reason for a in p.actions if a.kind == "conflict")
            _finish("blocked", p.counts)
            _emit("done", status="blocked", counts=dict(p.counts), dry_run=dry_run,
                  suppressed=len(p.suppressed))
            return SyncResult(plan=p, dry_run=dry_run, status="blocked",
                              counts=p.counts, errors=errors, run_id=run_id,
                              suppressed=len(p.suppressed))

        # source-side duplicates are skipped but the run continues; when any
        # non-conflicting writes were applied the run is `partial`, not `ok`.
        skipped_source = any(
            a.kind == "conflict" and a.side == "source" for a in p.actions
        )

        # 4c) id-column verification probe (spec §4.2.1) ----------------------
        # A live run with at least one add re-writes one store row unchanged to
        # prove the declared id column really is the store's key: a wrong
        # declaration answers `500 id column data not found`. The probe is a
        # no-op (identical payload), so it is exempt from the dry-run guard —
        # but a dry-run must stay a pure simulation and never touches the store.
        if not dry_run and any(a.kind == "add" for a in p.actions):
            probe = noop_probe(client, config.store.id, store_rows[0])
            if not probe.ok:
                msg = f"id-column probe failed: {probe.message}"
                state.record_conflict(run_id, match_value=None, reason=msg)
                _finish("blocked", {})
                _emit("done", status="blocked", counts={}, dry_run=dry_run,
                      suppressed=len(p.suppressed))
                return SyncResult(
                    plan=p, dry_run=dry_run, status="blocked", counts={},
                    errors=[msg], run_id=run_id,
                    suppressed=len(p.suppressed),
                )

        # 5) apply adds (updates are proposals, never auto-written) ----------
        todo_kinds = [a.kind for a in p.actions if a.kind == "add"]
        _emit("apply", dry_run=dry_run, planned=len(todo_kinds))
        wres = apply_plan(
            client,
            config.store.id,
            p,
            dry_run=dry_run,
            throttle=throttle,
            max_retries=max_retries,
            backoff=backoff,
            id_column=config.store.id_column,
            should_stop=should_stop,
            on_add=lambda done, total: _emit("add", done=done, total=total),
        )
        errors.extend(wres.errors)
        _emit("applied", counts=dict(wres.counts), dry_run=dry_run)

        # 5b) record update proposals (pending operator approval) ------------
        proposals = 0
        if not dry_run:
            for action in p.actions:
                if action.kind == "update":
                    state.record_proposal(
                        run_id,
                        job=config.job,
                        match_value=action.match_value,
                        id_value=action.id_value,
                        payload=action.payload,
                        diff=action.diffs,
                    )
                    proposals += 1
        _emit("proposals", count=proposals)

        # 6) tracking (only real writes; adds) -------------------------------
        if not dry_run:
            for action in wres.applied:
                state.track_row(
                    job=config.job,
                    match_value=action.match_value,
                    row_hash=state.row_hash(action.payload),
                )

        # A cooperative stop mid-apply: writes already applied stay tracked,
        # and the run is finished exactly once as `cancelled`.
        if wres.cancelled:
            _finish("cancelled", wres.counts)
            _emit("done", status="cancelled", counts=dict(wres.counts), dry_run=dry_run,
                  suppressed=len(p.suppressed))
            return SyncResult(
                plan=p, dry_run=dry_run, status="cancelled", counts=wres.counts,
                errors=errors, run_id=run_id, proposals=proposals,
                suppressed=len(p.suppressed),
            )

        if errors:
            status = "partial" if wres.applied else "error"
        elif skipped_source and wres.applied:
            status = "partial"
        else:
            status = "ok"

        _finish(status, wres.counts)
        _emit("done", status=status, counts=dict(wres.counts), errors=list(errors),
              dry_run=dry_run, suppressed=len(p.suppressed))
        return SyncResult(
            plan=p, dry_run=dry_run, status=status, counts=wres.counts,
            errors=errors, run_id=run_id, proposals=proposals,
            suppressed=len(p.suppressed),
        )
    except Exception as e:  # noqa: BLE001 - guarantee a finished run
        logger.exception("unexpected error during run_sync")
        errors.append(f"unexpected error: {e}")
        _finish("error", {})
        _emit("done", status="error", counts={}, dry_run=dry_run)
        return SyncResult(
            plan=p, dry_run=dry_run, status="error", counts={},
            errors=errors, run_id=run_id,
        )
    finally:
        _finish("error", {})
