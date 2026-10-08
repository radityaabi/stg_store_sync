"""Job configuration: schema, YAML load/save, and validation.

A job is a single source survey → single target Subject Store sync. Config is
plain dataclasses with explicit `from_dict`/`to_dict` so the YAML shape stays a
documented, validated contract (see docs/superpowers/specs/.../design.md §7.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

VALID_MODES = {"mapped", "strict"}
VALID_OPS = {"full_sync"}
VALID_KINDS = {"text", "number"}


class DeletePolicy(str, Enum):
    """How rows absent from the pull are treated (spec §6)."""

    FULL_HISTORY = "full_history"
    TRACKED_ONLY = "tracked_only"
    MANUAL = "manual"


@dataclass
class StoreConfig:
    id: int
    id_column: str

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StoreConfig":
        return cls(id=int(d["id"]), id_column=str(d.get("id_column", "")))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "id_column": self.id_column}


@dataclass
class SourceConfig:
    survey_id: str
    window_days: int = 0
    date_type: str = "Upload"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SourceConfig":
        return cls(
            survey_id=str(d["survey_id"]),
            window_days=int(d.get("window_days", 0)),
            date_type=str(d.get("date_type", "Upload")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "survey_id": self.survey_id,
            "window_days": self.window_days,
            "date_type": self.date_type,
        }


@dataclass
class ColumnBinding:
    """One column: which survey variable feeds which store column.

    `source_var` is the answer-level variable (e.g. T_QPM1_1); it is used for
    projection. The pull term is derived, not stored here.
    """

    source_var: str
    store_col: str
    kind: str = "text"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ColumnBinding":
        return cls(
            source_var=str(d["source_var"]),
            store_col=str(d["store_col"]),
            kind=str(d.get("kind", "text")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_var": self.source_var,
            "store_col": self.store_col,
            "kind": self.kind,
        }


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
    delete_policy: str = DeletePolicy.FULL_HISTORY.value
    normalize: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WriteConfig":
        return cls(
            op=str(d.get("op", "full_sync")),
            dry_run=bool(d.get("dry_run", True)),
            delete_policy=str(d.get("delete_policy", DeletePolicy.FULL_HISTORY.value)),
            normalize=dict(d.get("normalize", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        out = {
            "op": self.op,
            "dry_run": self.dry_run,
            "delete_policy": self.delete_policy,
        }
        if self.normalize:
            out["normalize"] = self.normalize
        return out


@dataclass
class JobConfig:
    job: str
    store: StoreConfig
    source: SourceConfig
    binding: BindingConfig
    write: WriteConfig

    @classmethod
    def from_dict(cls, d: dict[str, Any], do_validate: bool = True) -> "JobConfig":
        cfg = cls(
            job=str(d.get("job", "")),
            store=StoreConfig.from_dict(d.get("store", {})),
            source=SourceConfig.from_dict(d.get("source", {})),
            binding=BindingConfig.from_dict(d.get("binding", {})),
            write=WriteConfig.from_dict(d.get("write", {})),
        )
        if do_validate:
            errors = validate(cfg)
            if errors:
                raise ValueError("invalid job config: " + "; ".join(errors))
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.job,
            "store": self.store.to_dict(),
            "source": self.source.to_dict(),
            "binding": self.binding.to_dict(),
            "write": self.write.to_dict(),
        }


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

    valid_policies = {p.value for p in DeletePolicy}
    if cfg.write.delete_policy not in valid_policies:
        errors.append(
            f"write.delete_policy must be one of {sorted(valid_policies)}, "
            f"got {cfg.write.delete_policy!r}"
        )

    return errors


def load_config(path: str | Path) -> JobConfig:
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return JobConfig.from_dict(data)


def save_config(cfg: JobConfig, path: str | Path) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(cfg.to_dict(), fh, sort_keys=False, allow_unicode=True)
