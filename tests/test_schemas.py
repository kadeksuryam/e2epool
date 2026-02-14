"""Tests for e2epool schemas validation and serialization."""

import datetime
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from e2epool.schemas import (
    CheckpointCreateRequest,
    CheckpointFinalizeRequest,
    CheckpointResponse,
    FinalizeStatus,
)


class TestCheckpointCreateRequest:
    """Tests for CheckpointCreateRequest schema."""

    def test_checkpoint_create_request_valid(self):
        """Valid request with runner_id and job_id should serialize."""
        request = CheckpointCreateRequest(
            runner_id="test-runner-01",
            job_id="job-123-456",
        )
        assert request.runner_id == "test-runner-01"
        assert request.job_id == "job-123-456"

    def test_checkpoint_create_request_missing_runner_id(self):
        """Missing runner_id should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointCreateRequest(job_id="job-123-456")

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("runner_id",)
        assert errors[0]["type"] == "missing"

    def test_checkpoint_create_request_missing_job_id(self):
        """Missing job_id should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointCreateRequest(runner_id="test-runner-01")

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("job_id",)
        assert errors[0]["type"] == "missing"

    def test_checkpoint_create_request_missing_both_fields(self):
        """Missing both runner_id and job_id should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointCreateRequest()

        errors = exc_info.value.errors()
        assert len(errors) == 2
        error_fields = {error["loc"][0] for error in errors}
        assert error_fields == {"runner_id", "job_id"}

    def test_checkpoint_create_request_empty_runner_id_rejected(self):
        """Empty runner_id should raise ValidationError (min_length=1)."""
        with pytest.raises(ValidationError):
            CheckpointCreateRequest(runner_id="", job_id="job-123")

    def test_checkpoint_create_request_empty_job_id_rejected(self):
        """Empty job_id should raise ValidationError (min_length=1)."""
        with pytest.raises(ValidationError):
            CheckpointCreateRequest(runner_id="test-runner-01", job_id="")

    def test_checkpoint_create_request_long_runner_id_rejected(self):
        """runner_id > 255 chars should raise ValidationError."""
        with pytest.raises(ValidationError):
            CheckpointCreateRequest(runner_id="a" * 256, job_id="job-123")

    def test_checkpoint_create_request_long_job_id_rejected(self):
        """job_id > 255 chars should raise ValidationError."""
        with pytest.raises(ValidationError):
            CheckpointCreateRequest(runner_id="runner-01", job_id="j" * 256)

    def test_checkpoint_create_request_invalid_runner_id_pattern(self):
        """runner_id with special chars should raise ValidationError."""
        with pytest.raises(ValidationError):
            CheckpointCreateRequest(runner_id="runner@bad!", job_id="job-123")

    def test_checkpoint_create_request_invalid_job_id_pattern(self):
        """job_id with special chars should raise ValidationError."""
        with pytest.raises(ValidationError):
            CheckpointCreateRequest(runner_id="runner-01", job_id="job id 123")


class TestCheckpointFinalizeRequest:
    """Tests for CheckpointFinalizeRequest schema."""

    def test_checkpoint_finalize_request_valid_success(self):
        """Valid request with success status should serialize."""
        request = CheckpointFinalizeRequest(
            checkpoint_name="job-my-app-123-abcd1234",
            status=FinalizeStatus.success,
        )
        assert request.checkpoint_name == "job-my-app-123-abcd1234"
        assert request.status == FinalizeStatus.success
        assert request.source == "hook"

    def test_checkpoint_finalize_request_valid_failure(self):
        """Valid request with failure status should serialize."""
        request = CheckpointFinalizeRequest(
            checkpoint_name="job-build-456-00112233",
            status=FinalizeStatus.failure,
        )
        assert request.checkpoint_name == "job-build-456-00112233"
        assert request.status == FinalizeStatus.failure
        assert request.source == "hook"

    def test_checkpoint_finalize_request_valid_canceled(self):
        """Valid request with canceled status should serialize."""
        request = CheckpointFinalizeRequest(
            checkpoint_name="job-deploy-789-aabbccdd",
            status=FinalizeStatus.canceled,
        )
        assert request.checkpoint_name == "job-deploy-789-aabbccdd"
        assert request.status == FinalizeStatus.canceled
        assert request.source == "hook"

    def test_checkpoint_finalize_request_custom_source(self):
        """Valid request with custom source should serialize."""
        request = CheckpointFinalizeRequest(
            checkpoint_name="job-test-999-11223344",
            status=FinalizeStatus.success,
            source="manual",
        )
        assert request.source == "manual"

    def test_checkpoint_finalize_request_invalid_status_string(self):
        """Invalid status string should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointFinalizeRequest(
                checkpoint_name="job-valid-123-abcd1234",
                status="invalid_status",  # type: ignore
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("status",)
        assert errors[0]["type"] == "enum"

    def test_checkpoint_finalize_request_invalid_status_number(self):
        """Invalid status type (number) should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointFinalizeRequest(
                checkpoint_name="job-valid-456-abcd1234",
                status=123,  # type: ignore
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("status",)

    def test_checkpoint_finalize_request_missing_checkpoint_name(self):
        """Missing checkpoint_name should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointFinalizeRequest(status=FinalizeStatus.success)  # type: ignore

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("checkpoint_name",)
        assert errors[0]["type"] == "missing"

    def test_checkpoint_finalize_request_missing_status(self):
        """Missing status should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointFinalizeRequest(checkpoint_name="job-valid-789-abcd1234")  # type: ignore

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("status",)
        assert errors[0]["type"] == "missing"

    def test_checkpoint_finalize_request_source_validation(self):
        """Source field with invalid pattern should raise ValidationError."""
        with pytest.raises(ValidationError):
            CheckpointFinalizeRequest(
                checkpoint_name="job-test-999-11223344",
                status=FinalizeStatus.success,
                source="bad source!",
            )


class TestCheckpointNamePatternValidation:
    """Tests for checkpoint_name pattern validation."""

    def test_valid_checkpoint_name_simple(self):
        """Simple valid checkpoint name with hex suffix should be accepted."""
        request = CheckpointFinalizeRequest(
            checkpoint_name="job-app-123-abcd1234",
            status=FinalizeStatus.success,
        )
        assert request.checkpoint_name == "job-app-123-abcd1234"

    def test_valid_checkpoint_name_with_dots(self):
        """Checkpoint name with dots should be accepted."""
        request = CheckpointFinalizeRequest(
            checkpoint_name="job-my.app-789-00112233",
            status=FinalizeStatus.success,
        )
        assert request.checkpoint_name == "job-my.app-789-00112233"

    def test_valid_checkpoint_name_with_underscores(self):
        """Checkpoint name with underscores should be accepted."""
        request = CheckpointFinalizeRequest(
            checkpoint_name="job-my_app-456-aabbccdd",
            status=FinalizeStatus.success,
        )
        assert request.checkpoint_name == "job-my_app-456-aabbccdd"

    def test_valid_checkpoint_name_with_mixed_chars(self):
        """
        Checkpoint name with mixed alphanumeric, dots, and underscores should
        be accepted.
        """
        request = CheckpointFinalizeRequest(
            checkpoint_name="job-my_app.v2-999-11223344",
            status=FinalizeStatus.success,
        )
        assert request.checkpoint_name == "job-my_app.v2-999-11223344"

    def test_invalid_checkpoint_name_missing_job_prefix(self):
        """Name without 'job-' prefix should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointFinalizeRequest(
                checkpoint_name="invalid-name",
                status=FinalizeStatus.success,
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("checkpoint_name",)
        assert errors[0]["type"] == "value_error"
        assert "checkpoint_name must match pattern" in str(errors[0]["msg"])

    def test_invalid_checkpoint_name_missing_hex_suffix(self):
        """Name without hex suffix should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointFinalizeRequest(
                checkpoint_name="job-app-123",
                status=FinalizeStatus.success,
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("checkpoint_name",)

    def test_invalid_checkpoint_name_short_hex_suffix(self):
        """Name with too-short hex suffix should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointFinalizeRequest(
                checkpoint_name="job-app-123-abcd",
                status=FinalizeStatus.success,
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("checkpoint_name",)

    def test_invalid_checkpoint_name_special_chars(self):
        """Name with special characters should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointFinalizeRequest(
                checkpoint_name="job-app@test-123-abcd1234",
                status=FinalizeStatus.success,
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1

    def test_invalid_checkpoint_name_spaces(self):
        """Name with spaces should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointFinalizeRequest(
                checkpoint_name="job app-123-abcd1234",
                status=FinalizeStatus.success,
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1


class TestCheckpointResponse:
    """Tests for CheckpointResponse schema with from_attributes."""

    def test_checkpoint_response_with_dict(self):
        """CheckpointResponse should deserialize from dict."""
        now = datetime.datetime.now()
        response = CheckpointResponse(
            name="job-app-123",
            runner_id="test-runner-01",
            job_id="job-123",
            state="active",
            finalize_status=None,
            finalize_source=None,
            created_at=now,
            finalized_at=None,
        )
        assert response.name == "job-app-123"
        assert response.runner_id == "test-runner-01"
        assert response.job_id == "job-123"
        assert response.state == "active"
        assert response.finalize_status is None
        assert response.created_at == now

    def test_checkpoint_response_with_finalize_status(self):
        """CheckpointResponse with finalize_status should serialize."""
        now = datetime.datetime.now()
        finalized = datetime.datetime.now()
        response = CheckpointResponse(
            name="job-build-456",
            runner_id="test-runner-02",
            job_id="job-456",
            state="finalized",
            finalize_status="success",
            finalize_source="hook",
            created_at=now,
            finalized_at=finalized,
        )
        assert response.finalize_status == "success"
        assert response.finalize_source == "hook"
        assert response.finalized_at == finalized

    def test_checkpoint_response_from_orm_attributes(self):
        """
        CheckpointResponse should deserialize from ORM object using
        from_attributes.
        """
        now = datetime.datetime.now()
        finalized = datetime.datetime.now()

        mock_orm = MagicMock()
        mock_orm.name = "job-deploy-789"
        mock_orm.runner_id = "test-runner-03"
        mock_orm.job_id = "job-789"
        mock_orm.state = "finalized"
        mock_orm.finalize_status = "success"
        mock_orm.finalize_source = "hook"
        mock_orm.created_at = now
        mock_orm.finalized_at = finalized

        response = CheckpointResponse.model_validate(mock_orm)
        assert response.name == "job-deploy-789"
        assert response.runner_id == "test-runner-03"
        assert response.job_id == "job-789"
        assert response.state == "finalized"
        assert response.finalize_status == "success"
        assert response.finalize_source == "hook"
        assert response.created_at == now
        assert response.finalized_at == finalized

    def test_checkpoint_response_from_orm_with_none_finalize_status(self):
        """CheckpointResponse should handle None finalize_status from ORM."""
        now = datetime.datetime.now()

        mock_orm = MagicMock()
        mock_orm.name = "job-test-111"
        mock_orm.runner_id = "test-runner-04"
        mock_orm.job_id = "job-111"
        mock_orm.state = "active"
        mock_orm.finalize_status = None
        mock_orm.finalize_source = None
        mock_orm.created_at = now
        mock_orm.finalized_at = None

        response = CheckpointResponse.model_validate(mock_orm)
        assert response.finalize_status is None
        assert response.finalize_source is None
        assert response.finalized_at is None

    def test_checkpoint_response_missing_required_fields(self):
        """
        CheckpointResponse with missing required fields should raise
        ValidationError.
        """
        with pytest.raises(ValidationError) as exc_info:
            CheckpointResponse(
                name="job-app-123",
                runner_id="test-runner-01",
                # Missing job_id, state, created_at
            )  # type: ignore

        errors = exc_info.value.errors()
        assert len(errors) == 3
        error_fields = {error["loc"][0] for error in errors}
        assert error_fields == {"job_id", "state", "created_at"}

    def test_checkpoint_response_invalid_datetime(self):
        """CheckpointResponse with invalid datetime should raise ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            CheckpointResponse(
                name="job-app-123",
                runner_id="test-runner-01",
                job_id="job-123",
                state="active",
                created_at="not-a-datetime",  # type: ignore
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("created_at",)


class TestFinalizeStatus:
    """Tests for FinalizeStatus enum."""

    def test_finalize_status_success_value(self):
        """FinalizeStatus.success should have correct value."""
        assert FinalizeStatus.success.value == "success"

    def test_finalize_status_failure_value(self):
        """FinalizeStatus.failure should have correct value."""
        assert FinalizeStatus.failure.value == "failure"

    def test_finalize_status_canceled_value(self):
        """FinalizeStatus.canceled should have correct value."""
        assert FinalizeStatus.canceled.value == "canceled"

    def test_finalize_status_from_string(self):
        """FinalizeStatus should be creatable from string value."""
        assert FinalizeStatus("success") == FinalizeStatus.success
        assert FinalizeStatus("failure") == FinalizeStatus.failure
        assert FinalizeStatus("canceled") == FinalizeStatus.canceled

    def test_finalize_status_all_members(self):
        """FinalizeStatus should have exactly three members."""
        members = list(FinalizeStatus)
        assert len(members) == 3
        assert FinalizeStatus.success in members
        assert FinalizeStatus.failure in members
        assert FinalizeStatus.canceled in members
