"""Tests for e2epool.services.runner_service."""

import dataclasses
import json

import pytest
from sqlalchemy.exc import IntegrityError

from e2epool.inventory import RunnerConfig
from e2epool.models import Runner
from e2epool.services.runner_service import (
    config_to_runner,
    create_runner,
    deactivate_runner,
    get_runner_by_id,
    list_runners,
    runner_to_config,
    validate_runner_fields,
)


# ---------------------------------------------------------------------------
# validate_runner_fields
# ---------------------------------------------------------------------------


class TestValidateRunnerFields:
    def test_invalid_backend(self):
        with pytest.raises(ValueError, match="Invalid backend"):
            validate_runner_fields("docker", {})

    def test_empty_string_backend(self):
        with pytest.raises(ValueError, match="Invalid backend"):
            validate_runner_fields("", {})

    def test_bare_metal_missing_reset_cmd(self):
        with pytest.raises(ValueError, match="requires 'reset_cmd'"):
            validate_runner_fields("bare_metal", {})

    def test_bare_metal_empty_reset_cmd(self):
        with pytest.raises(ValueError, match="requires 'reset_cmd'"):
            validate_runner_fields("bare_metal", {"reset_cmd": ""})

    def test_bare_metal_valid(self):
        validate_runner_fields("bare_metal", {"reset_cmd": "/opt/reset.sh"})

    def test_proxmox_missing_all_fields(self):
        with pytest.raises(ValueError, match="missing required fields"):
            validate_runner_fields("proxmox", {})

    def test_proxmox_missing_one_field(self):
        data = {
            "proxmox_host": "10.0.0.1",
            "proxmox_user": "root@pam",
            "proxmox_token_name": "e2e",
            "proxmox_token_value": "secret",
            "proxmox_node": "pve1",
            # proxmox_vmid missing
        }
        with pytest.raises(ValueError, match="proxmox_vmid"):
            validate_runner_fields("proxmox", data)

    def test_proxmox_valid(self):
        validate_runner_fields(
            "proxmox",
            {
                "proxmox_host": "10.0.0.1",
                "proxmox_user": "root@pam",
                "proxmox_token_name": "e2e",
                "proxmox_token_value": "secret",
                "proxmox_node": "pve1",
                "proxmox_vmid": 100,
            },
        )


# ---------------------------------------------------------------------------
# create_runner
# ---------------------------------------------------------------------------


def _proxmox_data(**overrides):
    base = {
        "runner_id": "new-proxmox-01",
        "backend": "proxmox",
        "proxmox_host": "10.0.0.1",
        "proxmox_user": "root@pam",
        "proxmox_token_name": "e2e",
        "proxmox_token_value": "secret",
        "proxmox_node": "pve1",
        "proxmox_vmid": 100,
    }
    base.update(overrides)
    return base


def _bare_metal_data(**overrides):
    base = {
        "runner_id": "new-bare-01",
        "backend": "bare_metal",
        "reset_cmd": "/opt/reset.sh",
    }
    base.update(overrides)
    return base


class TestCreateRunner:
    def test_creates_runner_with_auto_token(self, db):
        runner = create_runner(db, _proxmox_data(tags=["e2e", "proxmox"]))
        assert runner.runner_id == "new-proxmox-01"
        assert runner.token is not None
        assert len(runner.token) > 20
        assert runner.is_active is True
        assert runner.tags == '["e2e", "proxmox"]'

    def test_each_runner_gets_unique_token(self, db):
        r1 = create_runner(db, _bare_metal_data(runner_id="tok-1"))
        r2 = create_runner(db, _bare_metal_data(runner_id="tok-2"))
        db.flush()
        assert r1.token != r2.token

    def test_creates_bare_metal_runner(self, db):
        runner = create_runner(db, _bare_metal_data())
        assert runner.runner_id == "new-bare-01"
        assert runner.backend == "bare_metal"
        assert runner.reset_cmd == "/opt/reset.sh"

    def test_empty_tags_stored_as_null(self, db):
        runner = create_runner(db, _bare_metal_data(tags=[]))
        assert runner.tags is None

    def test_no_tags_key_stored_as_null(self, db):
        data = _bare_metal_data()
        data.pop("tags", None)
        runner = create_runner(db, data)
        assert runner.tags is None

    def test_created_at_and_updated_at_populated(self, db):
        runner = create_runner(db, _bare_metal_data())
        db.flush()
        assert runner.created_at is not None
        assert runner.updated_at is not None

    def test_optional_fields_default_to_none(self, db):
        runner = create_runner(db, _bare_metal_data())
        db.flush()
        assert runner.proxmox_host is None
        assert runner.proxmox_vmid is None
        assert runner.gitlab_runner_id is None
        assert runner.cleanup_cmd is None
        assert runner.readiness_cmd is None

    def test_duplicate_runner_id_raises(self, db):
        create_runner(db, _bare_metal_data())
        db.flush()
        with pytest.raises(IntegrityError):
            create_runner(db, _bare_metal_data())
            db.flush()

    def test_validation_error_propagates(self, db):
        with pytest.raises(ValueError, match="Invalid backend"):
            create_runner(db, {"runner_id": "x", "backend": "docker"})


# ---------------------------------------------------------------------------
# list_runners
# ---------------------------------------------------------------------------


class TestListRunners:
    def test_lists_active_runners(self, db):
        create_runner(db, _bare_metal_data(runner_id="list-01"))
        create_runner(db, _bare_metal_data(runner_id="list-02"))
        db.flush()

        runners = list_runners(db)
        ids = [r.runner_id for r in runners]
        assert "list-01" in ids
        assert "list-02" in ids

    def test_returns_empty_list_when_no_runners(self, db):
        assert list_runners(db) == []

    def test_results_ordered_by_runner_id(self, db):
        create_runner(db, _bare_metal_data(runner_id="z-runner"))
        create_runner(db, _bare_metal_data(runner_id="a-runner"))
        create_runner(db, _bare_metal_data(runner_id="m-runner"))
        db.flush()

        ids = [r.runner_id for r in list_runners(db)]
        assert ids == sorted(ids)

    def test_excludes_inactive_by_default(self, db):
        create_runner(db, _bare_metal_data(runner_id="inactive-01"))
        db.flush()
        deactivate_runner(db, "inactive-01")
        db.flush()

        ids = [r.runner_id for r in list_runners(db)]
        assert "inactive-01" not in ids

    def test_includes_inactive_when_requested(self, db):
        create_runner(db, _bare_metal_data(runner_id="inactive-02"))
        db.flush()
        deactivate_runner(db, "inactive-02")
        db.flush()

        ids = [r.runner_id for r in list_runners(db, include_inactive=True)]
        assert "inactive-02" in ids


# ---------------------------------------------------------------------------
# get_runner_by_id
# ---------------------------------------------------------------------------


class TestGetRunnerById:
    def test_returns_active_runner(self, db):
        create_runner(db, _bare_metal_data(runner_id="get-01"))
        db.flush()

        runner = get_runner_by_id(db, "get-01")
        assert runner is not None
        assert runner.runner_id == "get-01"

    def test_returns_none_for_inactive(self, db):
        create_runner(db, _bare_metal_data(runner_id="get-02"))
        db.flush()
        deactivate_runner(db, "get-02")
        db.flush()

        assert get_runner_by_id(db, "get-02") is None

    def test_returns_none_for_unknown(self, db):
        assert get_runner_by_id(db, "nonexistent") is None


# ---------------------------------------------------------------------------
# deactivate_runner
# ---------------------------------------------------------------------------


class TestDeactivateRunner:
    def test_deactivates_runner(self, db):
        create_runner(db, _bare_metal_data(runner_id="deact-01"))
        db.flush()

        runner = deactivate_runner(db, "deact-01")
        assert runner is not None
        assert runner.is_active is False

    def test_returns_none_for_unknown(self, db):
        assert deactivate_runner(db, "nonexistent") is None

    def test_double_deactivate_returns_none(self, db):
        """Deactivating an already-deactivated runner returns None."""
        create_runner(db, _bare_metal_data(runner_id="deact-twice"))
        db.flush()
        deactivate_runner(db, "deact-twice")
        db.flush()

        assert deactivate_runner(db, "deact-twice") is None


# ---------------------------------------------------------------------------
# runner_to_config
# ---------------------------------------------------------------------------


class TestRunnerToConfig:
    def test_converts_proxmox_runner(self, db):
        runner = create_runner(
            db, _proxmox_data(gitlab_runner_id=42, tags=["e2e"])
        )
        db.flush()

        config = runner_to_config(runner)
        assert config.runner_id == "new-proxmox-01"
        assert config.backend == "proxmox"
        assert config.token == runner.token
        assert config.proxmox_host == "10.0.0.1"
        assert config.proxmox_vmid == 100
        assert config.gitlab_runner_id == 42
        assert config.tags == ["e2e"]

    def test_converts_runner_without_tags(self, db):
        runner = create_runner(db, _bare_metal_data())
        db.flush()

        config = runner_to_config(runner)
        assert config.tags == []

    def test_roundtrip_preserves_all_fields(self, db):
        """runner_to_config → config_to_runner should preserve all data."""
        original = create_runner(
            db,
            _proxmox_data(
                runner_id="roundtrip",
                gitlab_runner_id=99,
                tags=["a", "b"],
            ),
        )
        db.flush()

        config = runner_to_config(original)
        restored = config_to_runner(config)

        for f in dataclasses.fields(RunnerConfig):
            orig_val = getattr(original, f.name) if f.name != "tags" else json.loads(original.tags or "[]")
            rest_val = getattr(restored, f.name) if f.name != "tags" else json.loads(restored.tags or "[]")
            assert orig_val == rest_val, f"Mismatch on field '{f.name}': {orig_val!r} != {rest_val!r}"


# ---------------------------------------------------------------------------
# config_to_runner
# ---------------------------------------------------------------------------


class TestConfigToRunner:
    def test_converts_proxmox_config(self):
        config = RunnerConfig(
            runner_id="cfg-01",
            backend="proxmox",
            token="secret-token",
            proxmox_host="10.0.0.1",
            proxmox_user="root@pam",
            proxmox_token_name="e2e",
            proxmox_token_value="tok-val",
            proxmox_node="pve1",
            proxmox_vmid=200,
            gitlab_runner_id=50,
            tags=["e2e", "test"],
        )
        row = config_to_runner(config)
        assert isinstance(row, Runner)
        assert row.runner_id == "cfg-01"
        assert row.backend == "proxmox"
        assert row.token == "secret-token"
        assert row.proxmox_vmid == 200
        assert json.loads(row.tags) == ["e2e", "test"]

    def test_converts_bare_metal_config(self):
        config = RunnerConfig(
            runner_id="cfg-02",
            backend="bare_metal",
            token="secret-token-2",
            reset_cmd="/opt/reset.sh",
            cleanup_cmd="/opt/cleanup.sh",
        )
        row = config_to_runner(config)
        assert row.backend == "bare_metal"
        assert row.reset_cmd == "/opt/reset.sh"
        assert row.proxmox_host is None

    def test_empty_tags_stored_as_null(self):
        config = RunnerConfig(
            runner_id="cfg-03",
            backend="bare_metal",
            token="tok",
            reset_cmd="/reset.sh",
            tags=[],
        )
        row = config_to_runner(config)
        assert row.tags is None

    def test_can_be_persisted(self, db):
        """config_to_runner output can be added to a DB session."""
        config = RunnerConfig(
            runner_id="cfg-persist",
            backend="bare_metal",
            token="persist-tok",
            reset_cmd="/reset.sh",
        )
        row = config_to_runner(config)
        db.add(row)
        db.flush()

        found = db.query(Runner).filter(Runner.runner_id == "cfg-persist").first()
        assert found is not None
        assert found.token == "persist-tok"
