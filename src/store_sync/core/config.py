"""Job configuration: schema, YAML load/save, and validation.

A job is a single source survey → single target Subject Store sync. Config is
plain dataclasses with explicit `from_dict`/`to_dict` so the YAML shape stays a
documented, validated contract (see docs/superpowers/specs/.../design.md §7.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import re

import yaml

VALID_MODES = {"mapped", "strict"}
VALID_OPS = {"full_sync"}
VALID_KINDS = {"text", "number"}

# Per-list schedule units (spec §4.3); "" means the job never runs itself.
SCHEDULE_UNITS: tuple[str, ...] = ("", "minute", "hour", "day", "week")

# "HH:MM" 24h, zero-padded — required by day/week schedules.
_AT_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)


@dataclass
class StoreConfig:
    id: int
    id_column: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StoreConfig":
        return cls(id=int(d.get("id", 0)), id_column=str(d.get("id_column", "")))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "id_column": self.id_column}


@dataclass
class SourceConfig:
    survey_id: str
    survey_index: dict[str, str] = field(default_factory=dict)
    paired_survey_id: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SourceConfig":
        # Legacy `window_days`/`date_type` keys are deliberately ignored: the
        # pull is always the survey's full history.
        return cls(
            survey_id=str(d.get("survey_id", "")),
            survey_index=dict(d.get("survey_index") or {}),
            paired_survey_id=str(d.get("paired_survey_id", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        out = {"survey_id": self.survey_id}
        if self.survey_index:
            out["survey_index"] = dict(self.survey_index)
        if self.paired_survey_id:
            out["paired_survey_id"] = self.paired_survey_id
        return out


@dataclass
class ColumnBinding:
    """One column: which survey variable feeds which store column.

    `source_var` is the answer-level variable (e.g. T_QPM1_1); it is used for
    projection. `pull_term` is the question-level base name used by the export
    filter (e.g. QPM1); empty means derive it from `source_var`.
    """

    source_var: str
    store_col: str
    kind: str = "text"
    pull_term: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ColumnBinding":
        return cls(
            source_var=str(d["source_var"]),
            store_col=str(d["store_col"]),
            kind=str(d.get("kind", "text")),
            pull_term=str(d.get("pull_term", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        out = {
            "source_var": self.source_var,
            "store_col": self.store_col,
            "kind": self.kind,
        }
        if self.pull_term:
            out["pull_term"] = self.pull_term
        return out


@dataclass
class BindingConfig:
    match: ColumnBinding
    columns: list[ColumnBinding] = field(default_factory=list)
    mode: str = "mapped"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BindingConfig":
        match = d.get("match")
        return cls(
            match=ColumnBinding.from_dict(match) if match else None,
            columns=[ColumnBinding.from_dict(c) for c in d.get("columns", [])],
            mode=str(d.get("mode", "mapped")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "match": self.match.to_dict() if self.match else None,
            "columns": [c.to_dict() for c in self.columns],
        }


@dataclass
class WriteConfig:
    op: str = "full_sync"
    dry_run: bool = True
    normalize: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WriteConfig":
        # A legacy `delete_policy` key is deliberately ignored: the tool no
        # longer deletes (spec §6), so the value carries no meaning.
        return cls(
            op=str(d.get("op", "full_sync")),
            dry_run=bool(d.get("dry_run", True)),
            normalize=dict(d.get("normalize", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        out = {
            "op": self.op,
            "dry_run": self.dry_run,
        }
        if self.normalize:
            out["normalize"] = self.normalize
        return out


@dataclass(frozen=True)
class Schedule:
    """When a job runs itself (spec §4.3): every-N `unit`, optionally at `at`.

    `unit=""` means manual-only (the operator presses the button). `at` is a
    zero-padded "HH:MM" local time, required for day/week. `weekday` is 0-6,
    Monday=0, used only by week.
    """

    unit: str = ""
    every: int = 1
    at: str = ""
    weekday: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Schedule":
        if not d:
            return cls()
        return cls(
            unit=str(d.get("unit", "")),
            every=int(d.get("every", 1)),
            at=str(d.get("at", "")),
            weekday=int(d.get("weekday", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit": self.unit,
            "every": self.every,
            "at": self.at,
            "weekday": self.weekday,
        }

    def validate(self) -> list[str]:
        """Return every schedule error; empty list means usable.

        Non-int `every`/`weekday` (a corrupt stored row) report as errors
        rather than raising, so `human()` stays renderable.
        """
        errors: list[str] = []
        if self.unit not in SCHEDULE_UNITS:
            errors.append(
                f"schedule.unit must be one of {list(SCHEDULE_UNITS)}, "
                f"got {self.unit!r}"
            )
            return errors
        if self.unit == "":
            return errors
        every = self.every if isinstance(self.every, int) else None
        weekday = self.weekday if isinstance(self.weekday, int) else None
        if self.unit == "minute":
            if every is None or not 1 <= every <= 59:
                errors.append("schedule.every must be 1..59 for minute")
        elif self.unit == "hour":
            if every is None or not 1 <= every <= 23:
                errors.append("schedule.every must be 1..23 for hour")
        elif self.unit == "day":
            if every is None or not 1 <= every <= 31:
                errors.append("schedule.every must be 1..31 for day")
        elif self.unit == "week":
            if every is None or not 1 <= every <= 52:
                errors.append("schedule.every must be 1..52 for week")
            if weekday is None or not 0 <= weekday <= 6:
                errors.append("schedule.weekday must be 0..6 (0=Monday)")
        if self.unit in ("day", "week"):
            if not self.at:
                errors.append(f"schedule.at is required for {self.unit}")
            elif not _AT_RE.match(self.at):
                errors.append(
                    f"schedule.at must match HH:MM (00:00..23:59), got {self.at!r}"
                )
        return errors


def _plural(n: int, noun: str) -> str:
    return f"{noun}" if n == 1 else f"{n} {noun}s"


def human(schedule: Schedule) -> str:
    """One-line label for the dashboard's Schedule column (spec §4.3).

    Malformed values (unknown unit, day/week without `at`) fall back to
    "manual only" so a bad stored value never renders an empty cell.
    """
    if schedule.validate():
        return "manual only"
    if schedule.unit == "":
        return "manual only"
    if schedule.unit == "minute":
        return f"every {_plural(schedule.every, 'minute')}"
    if schedule.unit == "hour":
        return "hourly" if schedule.every == 1 else f"every {schedule.every} hours"
    if schedule.unit == "day":
        base = "daily" if schedule.every == 1 else f"every {schedule.every} days"
        return f"{base} at {schedule.at}"
    if schedule.unit == "week":
        base = "weekly" if schedule.every == 1 else f"every {schedule.every} weeks"
        return f"{base} on {_WEEKDAY_NAMES[schedule.weekday]} at {schedule.at}"
    return "manual only"


@dataclass
class JobConfig:
    job: str
    store: StoreConfig
    source: SourceConfig
    binding: BindingConfig
    write: WriteConfig
    pic: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any], do_validate: bool = True) -> "JobConfig":
        cfg = cls(
            job=str(d.get("job", "")),
            store=StoreConfig.from_dict(d.get("store", {})),
            source=SourceConfig.from_dict(d.get("source", {})),
            binding=BindingConfig.from_dict(d.get("binding", {})),
            write=WriteConfig.from_dict(d.get("write", {})),
            pic=str(d.get("pic", "")),
        )
        if do_validate:
            errors = validate(cfg)
            if errors:
                raise ValueError("invalid job config: " + "; ".join(errors))
        return cfg

    def to_dict(self) -> dict[str, Any]:
        out = {
            "job": self.job,
            "store": self.store.to_dict(),
            "source": self.source.to_dict(),
            "binding": self.binding.to_dict(),
            "write": self.write.to_dict(),
        }
        if self.pic:
            out["pic"] = self.pic
        return out


def validate(cfg: JobConfig) -> list[str]:
    """Return every validation error; empty list means the config is usable."""
    errors: list[str] = []

    if not cfg.job:
        errors.append("job name is required")
    if not cfg.store.id_column:
        errors.append("store.id_column is required (the API id/tag column)")
    if not cfg.source.survey_id:
        errors.append("source.survey_id is required")

    if cfg.binding.mode not in VALID_MODES:
        errors.append(
            f"binding.mode must be one of {sorted(VALID_MODES)}, got {cfg.binding.mode!r}"
        )

    if cfg.binding.match is None:
        errors.append("binding.match is required")
    else:
        if not cfg.binding.match.source_var:
            errors.append("binding.match.source_var is required")
        if not cfg.binding.match.store_col:
            errors.append("binding.match.store_col is required")

    for c in cfg.binding.columns:
        if c.kind not in VALID_KINDS:
            errors.append(
                f"column kind must be one of {sorted(VALID_KINDS)}, got {c.kind!r}"
            )

    store_cols = [c.store_col for c in cfg.binding.columns]
    dups = {c for c in store_cols if store_cols.count(c) > 1}
    if dups:
        errors.append(f"duplicate store_col: {sorted(dups)}")

    src_vars = [c.source_var for c in cfg.binding.columns]
    dups = {v for v in src_vars if src_vars.count(v) > 1}
    if dups:
        errors.append(f"duplicate source_var: {sorted(dups)}")

    if cfg.binding.match:
        identity_col = cfg.binding.match.store_col
        if identity_col in store_cols:
            errors.append(
                f"store_col {identity_col!r} collides with the identity column"
            )

    if cfg.write.op not in VALID_OPS:
        errors.append(
            f"write.op must be one of {sorted(VALID_OPS)}, got {cfg.write.op!r}"
        )

    return errors


def load_config(path: str | Path) -> JobConfig:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return JobConfig.from_dict(data)


def save_config(cfg: JobConfig, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg.to_dict(), fh, sort_keys=False, allow_unicode=True)
