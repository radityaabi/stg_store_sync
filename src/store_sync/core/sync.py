"""End-to-end orchestrator: run one sync job (spec §7.2).

    survey XML -> index -> derive pull terms -> pull (filter/fallback)
    store GetStore -> plan (diff by match key)
    writer.apply (or dry-run)
    state.record_run

The `state` run row and conflicts are written regardless of dry-run, so the GUI
run log is complete. A store-side duplicate key is a hard conflict: the job is
marked `blocked` and **no write happens** (fail-closed, §4.4).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from store_sync.core.config import JobConfig
from store_sync.core.fetcher import derive_pull_terms, parse_survey_index, pull
from store_sync.core.planner import Plan, plan
from store_sync.core.preflight import preflight
from store_sync.core.writer import apply_plan


@dataclass
class SyncResult:
    plan: Plan
    dry_run: bool
    status: str  # "ok" | "blocked" | "partial" | "error"
    counts: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    run_id: int | None = None


def run_preflight(config: JobConfig, *, client, sample_size: int = 50):
    """Fetch a store snapshot + a small pull sample and run the binding gate.

    Returns `(report, pull_terms)` where `pull_terms` maps `source_var → base`
    for the GUI's read-only pull-term column.
    """
    index = parse_survey_index(client.get_survey_xml(config.source.survey_id))
    columns = config.binding.columns
    terms, _ = derive_pull_terms(
        [c.source_var for c in columns],
        index,
        match_var=config.binding.match.source_var if config.binding.match else None,
    )
    _ = terms  # terms reserved for the preflight review message

    store_rows = client.get_store(config.store.id)
    ids = client.survey_interview_ids(config.source.survey_id)[:sample_size]
    pulled = pull(
        client,
        config.source.survey_id,
        ids,
        source_vars=[c.source_var for c in columns],
        match_var=config.binding.match.source_var if config.binding.match else None,
        survey_index=index,
    )

    report = preflight(
        store_rows=store_rows,
        source_records=pulled.records,
        config=config,
        client=client,
        store_id=config.store.id,
    )
    pull_terms = {
        c.source_var: index.get(c.source_var, "") for c in columns
    }
    return report, pull_terms


def run_sync(
    config: JobConfig,
    *,
    client,
    state,
    throttle: float = 0.0,
    max_retries: int = 3,
    backoff: float = 1.0,
) -> SyncResult:
    """Run one full sync (dry-run by default per `config.write.dry_run`)."""
    dry_run = config.write.dry_run
    run_id = state.start_run(job=config.job, dry_run=dry_run)

    # 1) survey index + pull -------------------------------------------------
    index = parse_survey_index(client.get_survey_xml(config.source.survey_id))
    subject_ids = client.survey_interview_ids(config.source.survey_id)
    match = config.binding.match
    pulled = pull(
        client,
        config.source.survey_id,
        subject_ids,
        source_vars=[c.source_var for c in config.binding.columns],
        match_var=match.source_var if match else None,
        survey_index=index,
    )

    # 2) load the store ------------------------------------------------------
    store_rows = client.get_store(config.store.id)
    store_columns = sorted({k for r in store_rows for k in r})

    # 3) plan ----------------------------------------------------------------
    tracked = state.tracked_keys(config.job)
    p = plan(
        store_rows=store_rows,
        source_records=pulled.records,
        match=match,
        columns=config.binding.columns,
        id_column=config.store.id_column,
        store_columns=store_columns,
        delete_policy=config.write.delete_policy,
        tracked=tracked,
    )

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

    errors: list[str] = []

    # 4) fail-closed on store-side conflicts ---------------------------------
    if p.blocked:
        errors.extend(a.reason for a in p.actions if a.kind == "conflict")
        state.finish_run(run_id, status="blocked", counts=p.counts)
        return SyncResult(plan=p, dry_run=dry_run, status="blocked",
                          counts=p.counts, errors=errors, run_id=run_id)

    # 5) apply ---------------------------------------------------------------
    wres = apply_plan(
        client,
        config.store.id,
        p,
        dry_run=dry_run,
        throttle=throttle,
        max_retries=max_retries,
        backoff=backoff,
        id_column=config.store.id_column,
    )
    errors.extend(wres.errors)

    # 6) tracking (only real writes) -----------------------------------------
    if not dry_run:
        for action in wres.applied:
            if action.kind == "delete":
                state.untrack_row(job=config.job, match_value=action.match_value)
            else:
                state.track_row(
                    job=config.job,
                    match_value=action.match_value,
                    row_hash=state.row_hash(action.payload),
                )

    if errors:
        status = "partial" if wres.applied else "error"
    else:
        status = "ok"

    state.finish_run(run_id, status=status, counts=wres.counts)
    return SyncResult(
        plan=p, dry_run=dry_run, status=status, counts=wres.counts,
        errors=errors, run_id=run_id,
    )
