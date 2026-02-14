from unittest.mock import patch

from e2epool.backends.bare_metal import BareMetalBackend


def test_create_checkpoint_is_noop(mock_bare_metal_runner):
    """Verify create_checkpoint is a no-op for bare metal."""
    backend = BareMetalBackend()
    backend.create_checkpoint(mock_bare_metal_runner, "test-checkpoint")


@patch("e2epool.backends.bare_metal.run_on_agent")
def test_reset_runs_reset_cmd(mock_run, mock_bare_metal_runner):
    """Verify reset executes the reset_cmd via agent."""
    mock_run.return_value = "Reset complete"

    backend = BareMetalBackend()
    backend.reset(mock_bare_metal_runner, "test-checkpoint")

    mock_run.assert_called_once_with(
        mock_bare_metal_runner.runner_id, mock_bare_metal_runner.reset_cmd
    )


@patch("e2epool.backends.bare_metal.run_on_agent")
def test_cleanup_runs_cleanup_cmd(mock_run, mock_bare_metal_runner):
    """Verify cleanup executes the cleanup_cmd via agent."""
    mock_run.return_value = "Cleanup complete"

    backend = BareMetalBackend()
    backend.cleanup(mock_bare_metal_runner, "test-checkpoint")

    mock_run.assert_called_once_with(
        mock_bare_metal_runner.runner_id, mock_bare_metal_runner.cleanup_cmd
    )


@patch("e2epool.backends.bare_metal.run_on_agent")
def test_cleanup_no_cmd_is_noop(mock_run, mock_bare_metal_runner):
    """Verify cleanup is a no-op when cleanup_cmd is None."""
    mock_bare_metal_runner.cleanup_cmd = None

    backend = BareMetalBackend()
    backend.cleanup(mock_bare_metal_runner, "test-checkpoint")

    mock_run.assert_not_called()


@patch("e2epool.backends.bare_metal.run_on_agent")
def test_check_ready_runs_readiness_cmd(mock_run, mock_bare_metal_runner):
    """Verify check_ready executes readiness_cmd when configured."""
    mock_run.return_value = "Ready"

    backend = BareMetalBackend()
    result = backend.check_ready(mock_bare_metal_runner)

    assert result is True
    mock_run.assert_called_once_with(
        mock_bare_metal_runner.runner_id, mock_bare_metal_runner.readiness_cmd
    )


@patch("e2epool.backends.bare_metal.wait_for_agent")
def test_check_ready_no_cmd_waits_for_agent(mock_wait, mock_bare_metal_runner):
    """Verify check_ready falls back to agent connectivity."""
    mock_bare_metal_runner.readiness_cmd = None
    mock_wait.return_value = True

    backend = BareMetalBackend()
    result = backend.check_ready(mock_bare_metal_runner)

    assert result is True
    mock_wait.assert_called_once_with(mock_bare_metal_runner.runner_id, timeout=5)


@patch("e2epool.backends.bare_metal.wait_for_agent")
def test_check_ready_no_cmd_returns_false_on_timeout(mock_wait, mock_bare_metal_runner):
    """Verify check_ready returns False when agent not connected."""
    mock_bare_metal_runner.readiness_cmd = None
    mock_wait.side_effect = TimeoutError("not connected")

    backend = BareMetalBackend()
    result = backend.check_ready(mock_bare_metal_runner)

    assert result is False
