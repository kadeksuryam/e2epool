import pytest

from e2epool.inventory import Inventory, RunnerConfig, load_inventory


class TestRunnerConfig:
    """Tests for RunnerConfig dataclass."""

    def test_runner_config_proxmox(self):
        """Verify RunnerConfig correctly stores Proxmox-specific fields."""
        config = RunnerConfig(
            runner_id="runner-01",
            backend="proxmox",
            token="secret",
            ci_adapter="gitlab",
            proxmox_host="10.0.0.10",
            proxmox_user="root@pam",
            proxmox_token_name="e2epool",
            proxmox_token_value="token-value",
            proxmox_node="pve1",
            proxmox_vmid=100,
            gitlab_url="https://gitlab.example.com",
            gitlab_token="glpat-test",
            gitlab_runner_id=42,
            tags=["e2e", "proxmox"],
        )

        assert config.runner_id == "runner-01"
        assert config.backend == "proxmox"
        assert config.token == "secret"
        assert config.ci_adapter == "gitlab"
        assert config.proxmox_host == "10.0.0.10"
        assert config.proxmox_user == "root@pam"
        assert config.proxmox_token_name == "e2epool"
        assert config.proxmox_token_value == "token-value"
        assert config.proxmox_node == "pve1"
        assert config.proxmox_vmid == 100
        assert config.gitlab_url == "https://gitlab.example.com"
        assert config.gitlab_token == "glpat-test"
        assert config.gitlab_runner_id == 42
        assert config.tags == ["e2e", "proxmox"]

    def test_runner_config_bare_metal(self):
        """Verify RunnerConfig correctly stores bare-metal specific fields."""
        config = RunnerConfig(
            runner_id="runner-bare-01",
            backend="bare_metal",
            token="secret-bare",
            reset_cmd="sudo /opt/e2e/reset.sh",
            cleanup_cmd="sudo /opt/e2e/cleanup.sh",
            readiness_cmd="/opt/e2e/check-ready.sh",
            tags=["e2e", "bare-metal"],
        )

        assert config.runner_id == "runner-bare-01"
        assert config.backend == "bare_metal"
        assert config.token == "secret-bare"
        assert config.reset_cmd == "sudo /opt/e2e/reset.sh"
        assert config.cleanup_cmd == "sudo /opt/e2e/cleanup.sh"
        assert config.readiness_cmd == "/opt/e2e/check-ready.sh"
        assert config.tags == ["e2e", "bare-metal"]

    def test_runner_config_default_ci_adapter(self):
        """Verify ci_adapter defaults to gitlab."""
        config = RunnerConfig(
            runner_id="runner-01",
            backend="proxmox",
            token="secret",
        )

        assert config.ci_adapter == "gitlab"

    def test_runner_config_default_tags(self):
        """Verify tags defaults to empty list."""
        config = RunnerConfig(
            runner_id="runner-01",
            backend="proxmox",
            token="secret",
        )

        assert config.tags == []


class TestInventory:
    """Tests for Inventory class."""

    def test_get_runner_existing(self):
        """Verify get_runner returns correct RunnerConfig for known runner_id."""
        runner1 = RunnerConfig(
            runner_id="runner-01",
            backend="proxmox",
            token="secret-1",
        )
        runner2 = RunnerConfig(
            runner_id="runner-02",
            backend="bare_metal",
            token="secret-2",
            reset_cmd="reset",
        )
        inventory = Inventory({"runner-01": runner1, "runner-02": runner2})

        result = inventory.get_runner("runner-01")

        assert result is runner1
        assert result.runner_id == "runner-01"
        assert result.backend == "proxmox"

    def test_get_runner_unknown_id(self):
        """Verify get_runner returns None for unknown runner_id."""
        runner = RunnerConfig(
            runner_id="runner-01",
            backend="proxmox",
            token="secret",
        )
        inventory = Inventory({"runner-01": runner})

        result = inventory.get_runner("unknown-runner")

        assert result is None

    def test_get_all_runners(self):
        """Verify get_all_runners returns dict of all runners."""
        runner1 = RunnerConfig(
            runner_id="runner-01",
            backend="proxmox",
            token="secret-1",
        )
        runner2 = RunnerConfig(
            runner_id="runner-02",
            backend="bare_metal",
            token="secret-2",
            reset_cmd="reset",
        )
        inventory = Inventory({"runner-01": runner1, "runner-02": runner2})

        result = inventory.get_all_runners()

        assert result == {"runner-01": runner1, "runner-02": runner2}
        assert result is not inventory._runners  # Should be a copy

    def test_runner_ids_property(self):
        """Verify runner_ids property returns list of all runner IDs."""
        runner1 = RunnerConfig(
            runner_id="runner-01",
            backend="proxmox",
            token="secret-1",
        )
        runner2 = RunnerConfig(
            runner_id="runner-02",
            backend="bare_metal",
            token="secret-2",
            reset_cmd="reset",
        )
        inventory = Inventory({"runner-01": runner1, "runner-02": runner2})

        result = inventory.runner_ids

        assert set(result) == {"runner-01", "runner-02"}
        assert len(result) == 2

    def test_runner_ids_empty_inventory(self):
        """Verify runner_ids returns empty list for empty inventory."""
        inventory = Inventory({})

        result = inventory.runner_ids

        assert result == []


class TestLoadInventory:
    """Tests for load_inventory function."""

    def test_load_inventory_proxmox_runner(self, tmp_path):
        """Load and verify valid Proxmox runner configuration from YAML."""
        yaml_content = """
runners:
  - runner_id: runner-proxmox-01
    backend: proxmox
    token: secret-token-proxmox-01
    ci_adapter: gitlab
    proxmox_host: "10.0.0.10"
    proxmox_user: "root@pam"
    proxmox_token_name: "e2epool"
    proxmox_token_value: "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    proxmox_node: "pve1"
    proxmox_vmid: 100
    cleanup_cmd: "sudo /opt/e2e/cleanup.sh"
    gitlab_url: "https://gitlab.example.com"
    gitlab_token: "glpat-xxxxxxxxxxxxxxxxxxxx"
    gitlab_runner_id: 42
    tags:
      - e2e
      - proxmox
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        inventory = load_inventory(inventory_file)

        assert inventory is not None
        assert inventory.runner_ids == ["runner-proxmox-01"]

        runner = inventory.get_runner("runner-proxmox-01")
        assert runner.runner_id == "runner-proxmox-01"
        assert runner.backend == "proxmox"
        assert runner.token == "secret-token-proxmox-01"
        assert runner.ci_adapter == "gitlab"
        assert runner.proxmox_host == "10.0.0.10"
        assert runner.proxmox_user == "root@pam"
        assert runner.proxmox_token_name == "e2epool"
        assert runner.proxmox_token_value == "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        assert runner.proxmox_node == "pve1"
        assert runner.proxmox_vmid == 100
        assert runner.cleanup_cmd == "sudo /opt/e2e/cleanup.sh"
        assert runner.gitlab_url == "https://gitlab.example.com"
        assert runner.gitlab_token == "glpat-xxxxxxxxxxxxxxxxxxxx"
        assert runner.gitlab_runner_id == 42
        assert runner.tags == ["e2e", "proxmox"]

    def test_load_inventory_bare_metal_runner(self, tmp_path):
        """Load and verify valid bare-metal runner with reset_cmd."""
        yaml_content = """
runners:
  - runner_id: runner-bare-01
    backend: bare_metal
    token: secret-token-bare-01
    ci_adapter: gitlab
    reset_cmd: "sudo /opt/e2e/reset.sh"
    cleanup_cmd: "sudo /opt/e2e/cleanup.sh"
    readiness_cmd: "/opt/e2e/check-ready.sh"
    gitlab_url: "https://gitlab.example.com"
    gitlab_token: "glpat-xxxxxxxxxxxxxxxxxxxx"
    gitlab_runner_id: 43
    tags:
      - e2e
      - bare-metal
      - mobile
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        inventory = load_inventory(inventory_file)

        assert inventory is not None
        assert inventory.runner_ids == ["runner-bare-01"]

        runner = inventory.get_runner("runner-bare-01")
        assert runner.runner_id == "runner-bare-01"
        assert runner.backend == "bare_metal"
        assert runner.token == "secret-token-bare-01"
        assert runner.ci_adapter == "gitlab"
        assert runner.reset_cmd == "sudo /opt/e2e/reset.sh"
        assert runner.cleanup_cmd == "sudo /opt/e2e/cleanup.sh"
        assert runner.readiness_cmd == "/opt/e2e/check-ready.sh"
        assert runner.gitlab_url == "https://gitlab.example.com"
        assert runner.gitlab_token == "glpat-xxxxxxxxxxxxxxxxxxxx"
        assert runner.gitlab_runner_id == 43
        assert runner.tags == ["e2e", "bare-metal", "mobile"]

    def test_load_inventory_multiple_runners(self, tmp_path):
        """Load inventory with multiple runners of different backends."""
        yaml_content = """
runners:
  - runner_id: runner-proxmox-01
    backend: proxmox
    token: secret-proxmox
    proxmox_host: "10.0.0.10"
    proxmox_user: "root@pam"
    proxmox_token_name: "e2epool"
    proxmox_token_value: "token-value"
    proxmox_node: "pve1"
    proxmox_vmid: 100
    tags:
      - proxmox

  - runner_id: runner-bare-01
    backend: bare_metal
    token: secret-bare
    reset_cmd: "reset"
    tags:
      - bare-metal
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        inventory = load_inventory(inventory_file)

        assert set(inventory.runner_ids) == {"runner-proxmox-01", "runner-bare-01"}
        assert inventory.get_runner("runner-proxmox-01").backend == "proxmox"
        assert inventory.get_runner("runner-bare-01").backend == "bare_metal"

    def test_load_inventory_missing_file_raises(self, tmp_path):
        """Verify FileNotFoundError when inventory file does not exist."""
        nonexistent_path = tmp_path / "nonexistent.yml"

        with pytest.raises(FileNotFoundError) as exc_info:
            load_inventory(nonexistent_path)

        assert "Inventory file not found" in str(exc_info.value)
        assert str(nonexistent_path) in str(exc_info.value)

    def test_load_inventory_invalid_backend_raises(self, tmp_path):
        """Verify ValueError when backend is not 'proxmox' or 'bare_metal'."""
        yaml_content = """
runners:
  - runner_id: runner-docker-01
    backend: docker
    token: secret
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        with pytest.raises(ValueError) as exc_info:
            load_inventory(inventory_file)

        error_msg = str(exc_info.value)
        assert "Invalid backend 'docker'" in error_msg
        assert "runner-docker-01" in error_msg
        assert "Must be 'proxmox' or 'bare_metal'" in error_msg

    def test_bare_metal_requires_reset_cmd(self, tmp_path):
        """Verify ValueError when bare_metal runner lacks reset_cmd."""
        yaml_content = """
runners:
  - runner_id: runner-bare-01
    backend: bare_metal
    token: secret
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        with pytest.raises(ValueError) as exc_info:
            load_inventory(inventory_file)

        error_msg = str(exc_info.value)
        assert "runner-bare-01" in error_msg
        assert "bare_metal backend requires 'reset_cmd'" in error_msg

    def test_proxmox_runner_without_reset_cmd_allowed(self, tmp_path):
        """Verify Proxmox runners do not require reset_cmd."""
        yaml_content = """
runners:
  - runner_id: runner-proxmox-01
    backend: proxmox
    token: secret
    proxmox_host: "10.0.0.10"
    proxmox_user: "root@pam"
    proxmox_token_name: "e2epool"
    proxmox_token_value: "token-value"
    proxmox_node: "pve1"
    proxmox_vmid: 100
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        inventory = load_inventory(inventory_file)

        runner = inventory.get_runner("runner-proxmox-01")
        assert runner.backend == "proxmox"
        assert runner.reset_cmd is None

    def test_load_inventory_empty_runners_list(self, tmp_path):
        """Verify loading inventory with no runners returns empty Inventory."""
        yaml_content = """
runners: []
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        inventory = load_inventory(inventory_file)

        assert inventory.runner_ids == []
        assert inventory.get_all_runners() == {}

    def test_load_inventory_missing_runners_key(self, tmp_path):
        """Verify loading YAML without 'runners' key returns empty Inventory."""
        yaml_content = """
version: "1.0"
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        inventory = load_inventory(inventory_file)

        assert inventory.runner_ids == []
        assert inventory.get_all_runners() == {}

    def test_load_inventory_path_as_string(self, tmp_path):
        """Verify load_inventory accepts string paths."""
        yaml_content = """
runners:
  - runner_id: runner-01
    backend: proxmox
    token: secret
    proxmox_host: "10.0.0.10"
    proxmox_user: "root@pam"
    proxmox_token_name: "e2epool"
    proxmox_token_value: "token-value"
    proxmox_node: "pve1"
    proxmox_vmid: 100
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        inventory = load_inventory(str(inventory_file))

        assert inventory.runner_ids == ["runner-01"]

    def test_load_inventory_path_as_path_object(self, tmp_path):
        """Verify load_inventory accepts Path objects."""
        yaml_content = """
runners:
  - runner_id: runner-01
    backend: proxmox
    token: secret
    proxmox_host: "10.0.0.10"
    proxmox_user: "root@pam"
    proxmox_token_name: "e2epool"
    proxmox_token_value: "token-value"
    proxmox_node: "pve1"
    proxmox_vmid: 100
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        inventory = load_inventory(inventory_file)

        assert inventory.runner_ids == ["runner-01"]

    def test_load_inventory_optional_fields(self, tmp_path):
        """Verify optional fields can be omitted from YAML."""
        yaml_content = """
runners:
  - runner_id: runner-minimal
    backend: bare_metal
    token: secret
    reset_cmd: "reset"
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        inventory = load_inventory(inventory_file)

        runner = inventory.get_runner("runner-minimal")
        assert runner.runner_id == "runner-minimal"
        assert runner.backend == "bare_metal"
        assert runner.token == "secret"
        assert runner.ci_adapter == "gitlab"  # default
        assert runner.tags == []  # default
        assert runner.proxmox_host is None
        assert runner.cleanup_cmd is None
        assert runner.gitlab_url is None

    def test_proxmox_runner_missing_required_field_raises(self, tmp_path):
        """Verify ValueError when proxmox runner is missing required fields."""
        yaml_content = """
runners:
  - runner_id: runner-proxmox-01
    backend: proxmox
    token: secret
    proxmox_host: "10.0.0.10"
    proxmox_user: "root@pam"
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        with pytest.raises(ValueError) as exc_info:
            load_inventory(inventory_file)

        error_msg = str(exc_info.value)
        assert "runner-proxmox-01" in error_msg
        assert "proxmox backend" in error_msg
        assert "proxmox_token_name" in error_msg
        assert "proxmox_token_value" in error_msg
        assert "proxmox_node" in error_msg
        assert "proxmox_vmid" in error_msg

    def test_load_inventory_ignores_unknown_fields(self, tmp_path):
        """Verify old inventory files with unknown fields (e.g. SSH) still load."""
        yaml_content = """
runners:
  - runner_id: runner-01
    backend: bare_metal
    token: secret
    reset_cmd: "reset"
    ssh_host: "192.168.1.50"
    ssh_user: "ci"
    ssh_key_path: "/etc/keys/bare-01"
    some_future_field: "value"
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        inventory = load_inventory(inventory_file)

        runner = inventory.get_runner("runner-01")
        assert runner.runner_id == "runner-01"
        assert runner.backend == "bare_metal"
        assert not hasattr(runner, "ssh_host")

    def test_load_inventory_multiple_invalid_backends(self, tmp_path):
        """Verify error reported for first invalid backend encountered."""
        yaml_content = """
runners:
  - runner_id: runner-docker-01
    backend: docker
    token: secret
"""
        inventory_file = tmp_path / "inventory.yml"
        inventory_file.write_text(yaml_content)

        with pytest.raises(ValueError) as exc_info:
            load_inventory(inventory_file)

        error_msg = str(exc_info.value)
        assert "docker" in error_msg
        assert "runner-docker-01" in error_msg
