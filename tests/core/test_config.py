"""Tests for store_sync.core.config — job schema, YAML round-trip, validation."""
from pathlib import Path

import pytest
import yaml

from store_sync.core.config import (
    BindingConfig,
    ColumnBinding,
    JobConfig,
    SourceConfig,
    StoreConfig,
    WriteConfig,
    load_config,
    save_config,
    validate,
)


def sample_dict():
    return {
        "job": "gran_turismo_link1",
        "store": {"id": 17417, "id_column": "uniqueID"},
        "source": {
            "survey_id": "60824d46-9b2e-41e5-8b47-ae11410ee24d",
        },
        "binding": {
            "mode": "mapped",
            "match": {"source_var": "SbjNum", "store_col": "SbjNum", "kind": "text"},
            "columns": [
                {"source_var": "T_QPM1_1", "store_col": "I7_1_2", "kind": "text"},
                {"source_var": "T_QPM1_3", "store_col": "I8a_1", "kind": "text"},
                {"source_var": "Q32", "store_col": "Q32", "kind": "text"},
            ],
        },
        "write": {
            "op": "full_sync",
            "dry_run": True,
        },
    }


class TestRoundTrip:
    def test_from_dict_builds_typed_config(self):
        cfg = JobConfig.from_dict(sample_dict())
        assert cfg.job == "gran_turismo_link1"
        assert cfg.store.id == 17417
        assert cfg.store.id_column == "uniqueID"
        assert cfg.source.survey_id == "60824d46-9b2e-41e5-8b47-ae11410ee24d"
        assert cfg.binding.mode == "mapped"
        assert cfg.binding.match.source_var == "SbjNum"
        assert cfg.binding.match.store_col == "SbjNum"
        assert len(cfg.binding.columns) == 3
        assert cfg.binding.columns[0].source_var == "T_QPM1_1"
        assert cfg.binding.columns[0].store_col == "I7_1_2"
        assert cfg.write.op == "full_sync"
        assert cfg.write.dry_run is True

    def test_to_dict_is_stable(self):
        cfg = JobConfig.from_dict(sample_dict())
        assert cfg.to_dict() == sample_dict()

    def test_save_then_load_round_trips(self, tmp_path):
        cfg = JobConfig.from_dict(sample_dict())
        p = tmp_path / "job.yaml"
        save_config(cfg, p)
        assert p.exists()
        loaded = load_config(p)
        assert loaded == cfg

    def test_stale_source_keys_are_ignored(self):
        d = sample_dict()
        d["source"]["window_days"] = 3          # legacy key in the wild
        d["source"]["date_type"] = "Upload"
        cfg = JobConfig.from_dict(d)
        assert cfg.source == SourceConfig("60824d46-9b2e-41e5-8b47-ae11410ee24d")


class TestDefaults:
    def test_minimal_config_fills_documented_defaults(self):
        cfg = JobConfig.from_dict(
            {
                "job": "x",
                "store": {"id": 1, "id_column": "No"},
                "source": {"survey_id": "s"},
                "binding": {
                    "match": {"source_var": "SbjNum", "store_col": "SbjNum"},
                    "columns": [],
                },
                "write": {},
            }
        )
        assert cfg.binding.mode == "mapped"
        assert cfg.write.op == "full_sync"
        assert cfg.write.dry_run is True

    def test_column_kind_defaults_to_text(self):
        col = ColumnBinding.from_dict({"source_var": "A", "store_col": "B"})
        assert col.kind == "text"


class TestValidation:
    def test_valid_config_has_no_errors(self):
        assert validate(JobConfig.from_dict(sample_dict())) == []

    def test_missing_job_name(self):
        d = sample_dict()
        d["job"] = ""
        with pytest.raises(ValueError, match="job"):
            JobConfig.from_dict(d)

    def test_missing_id_column(self):
        d = sample_dict()
        d["store"]["id_column"] = ""
        with pytest.raises(ValueError, match="id_column"):
            JobConfig.from_dict(d)

    def test_bad_binding_mode(self):
        d = sample_dict()
        d["binding"]["mode"] = "guess"
        with pytest.raises(ValueError, match="mode"):
            JobConfig.from_dict(d)

    def test_legacy_delete_policy_key_is_ignored(self):
        d = sample_dict()
        d["write"]["delete_policy"] = "nuke"  # legacy key in the wild
        cfg = JobConfig.from_dict(d)
        assert not hasattr(cfg.write, "delete_policy")
        assert "delete_policy" not in cfg.to_dict()["write"]

    def test_bad_kind(self):
        d = sample_dict()
        d["binding"]["columns"][0]["kind"] = "blob"
        with pytest.raises(ValueError, match="kind"):
            JobConfig.from_dict(d)

    def test_duplicate_store_column_rejected(self):
        d = sample_dict()
        d["binding"]["columns"].append(
            {"source_var": "OTHER", "store_col": "Q32", "kind": "text"}
        )
        with pytest.raises(ValueError, match="duplicate store_col"):
            JobConfig.from_dict(d)

    def test_duplicate_source_var_rejected(self):
        d = sample_dict()
        d["binding"]["columns"].append(
            {"source_var": "Q32", "store_col": "OTHER", "kind": "text"}
        )
        with pytest.raises(ValueError, match="duplicate source_var"):
            JobConfig.from_dict(d)

    def test_store_column_cannot_collide_with_identity(self):
        d = sample_dict()
        d["binding"]["columns"].append(
            {"source_var": "K", "store_col": "SbjNum", "kind": "text"}
        )
        with pytest.raises(ValueError, match="identity"):
            JobConfig.from_dict(d)

    def test_validation_collects_all_errors_as_list(self):
        d = sample_dict()
        d["binding"]["mode"] = "nope"
        d["store"]["id_column"] = ""
        errors = validate(JobConfig.from_dict(d, do_validate=False))
        assert any("mode" in e for e in errors)
        assert any("id_column" in e for e in errors)


class TestEnums:
    def test_enums_construct_from_string(self):
        assert StoreConfig(id=1, id_column="No").id_column == "No"
        assert SourceConfig(survey_id="s").survey_id == "s"
        assert BindingConfig(match=None, columns=[]).mode == "mapped"
        assert WriteConfig().dry_run is True
