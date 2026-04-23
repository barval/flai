# tests/test_validators.py
"""Tests for input validation module."""
import pytest
from unittest.mock import patch

from app.validators import (
    ValidationError,
    validate_user_input,
    validate_model_config_update,
    validate_session_create,
    LOGIN_PATTERN,
    NAME_PATTERN,
    MODULE_TYPES,
)


class TestValidationError:
    """Test cases for ValidationError exception."""

    def test_is_exception(self):
        """ValidationError should be an Exception."""
        exc = ValidationError("test")
        assert isinstance(exc, Exception)


class TestValidateUserInput:
    """Test cases for user input validation."""

    def test_valid_user_data(self):
        """Should pass valid user data."""
        data = {'login': 'testuser', 'name': 'Test User', 'password': 'pass123'}
        result = validate_user_input(data)
        assert result == data

    def test_missing_login(self):
        """Should raise error if login missing."""
        data = {'name': 'Test User'}
        with pytest.raises(ValidationError):
            validate_user_input(data)

    def test_invalid_login_chars(self):
        """Should reject login with invalid characters."""
        data = {'login': 'test@user', 'name': 'Test'}
        with pytest.raises(ValidationError):
            validate_user_input(data)

    def test_login_too_short(self):
        """Should reject login shorter than 3 chars."""
        data = {'login': 'ab', 'name': 'Test'}
        with pytest.raises(ValidationError):
            validate_user_input(data)

    def test_login_too_long(self):
        """Should reject login longer than 50 chars."""
        data = {'login': 'a' * 51, 'name': 'Test'}
        with pytest.raises(ValidationError):
            validate_user_input(data)

    def test_valid_login_with_hyphen(self):
        """Should accept login with hyphen."""
        data = {'login': 'test-user', 'name': 'Test'}
        result = validate_user_input(data)
        assert 'login' in result

    def test_valid_login_with_underscore(self):
        """Should accept login with underscore."""
        data = {'login': 'test_user', 'name': 'Test'}
        result = validate_user_input(data)
        assert 'login' in result

    def test_invalid_name_chars(self):
        """Should reject name with invalid characters."""
        data = {'login': 'test', 'name': 'Test@User'}
        with pytest.raises(ValidationError):
            validate_user_input(data)

    def test_name_too_short(self):
        """Should reject name shorter than 2 chars."""
        data = {'login': 'test', 'name': 'A'}
        with pytest.raises(ValidationError):
            validate_user_input(data)

    def test_empty_data(self):
        """Should reject empty data."""
        with pytest.raises(ValidationError):
            validate_user_input({})

    def test_none_data(self):
        """Should reject None data."""
        with pytest.raises(ValidationError):
            validate_user_input(None)

    def test_invalid_json_type(self):
        """Should reject non-dict data."""
        with pytest.raises(ValidationError):
            validate_user_input("not a dict")

    def test_password_not_string(self):
        """Should reject non-string password."""
        data = {'login': 'test', 'password': 123}
        with pytest.raises(ValidationError):
            validate_user_input(data)

    def test_password_too_short(self):
        """Should reject password shorter than 4 chars."""
        data = {'login': 'test', 'password': 'abc'}
        with pytest.raises(ValidationError):
            validate_user_input(data)

    def test_service_class_invalid(self):
        """Should reject invalid service class."""
        data = {'login': 'test', 'service_class': 5}
        with pytest.raises(ValidationError):
            validate_user_input(data)

    def test_service_class_valid(self):
        """Should accept valid service classes."""
        for sc in [0, 1, 2]:
            data = {'login': 'test', 'service_class': sc}
            result = validate_user_input(data)
            assert result['service_class'] == sc


class TestValidateModelConfigUpdate:
    """Test cases for model config validation."""

    def test_valid_config(self):
        """Should pass valid config."""
        data = {'model_name': 'qwen', 'temperature': 0.7}
        result = validate_model_config_update(data, 'chat')
        assert result == data

    def test_invalid_module_type(self):
        """Should reject invalid module type."""
        data = {'model_name': 'qwen'}
        with pytest.raises(ValidationError):
            validate_model_config_update(data, 'invalid')

    def test_invalid_temperature(self):
        """Should reject invalid temperature."""
        data = {'temperature': 3.0}
        with pytest.raises(ValidationError):
            validate_model_config_update(data, 'chat')

    def test_invalid_context_length(self):
        """Should reject invalid context length."""
        data = {'context_length': 100}
        with pytest.raises(ValidationError):
            validate_model_config_update(data, 'chat')

    def test_invalid_timeout(self):
        """Should reject invalid timeout."""
        data = {'timeout': 5000}
        with pytest.raises(ValidationError):
            validate_model_config_update(data, 'chat')

    def test_empty_fields(self):
        """Should allow empty fields for partial updates."""
        data = {}
        result = validate_model_config_update(data, 'chat')
        assert result == {}


class TestValidateSessionCreate:
    """Test cases for session creation validation."""

    def test_valid_title(self):
        """Should pass valid title."""
        data = {'title': 'Test Session'}
        result = validate_session_create(data)
        assert result == data

    def test_title_too_long(self):
        """Should reject title longer than 200 chars."""
        data = {'title': 'a' * 201}
        with pytest.raises(ValidationError):
            validate_session_create(data)

    def test_invalid_title_chars(self):
        """Should reject title with special chars."""
        data = {'title': 'Test<script>Session'}
        with pytest.raises(ValidationError):
            validate_session_create(data)

    def test_default_title(self):
        """Should generate default title if not provided."""
        result = validate_session_create({})
        assert 'title' in result


class TestPatterns:
    """Test regex patterns."""

    def test_login_pattern(self):
        """LOGIN_PATTERN should match valid logins."""
        assert LOGIN_PATTERN.match('testuser')
        assert LOGIN_PATTERN.match('test-user')
        assert LOGIN_PATTERN.match('test_user')
        assert LOGIN_PATTERN.match('user123')

    def test_login_pattern_rejects(self):
        """LOGIN_PATTERN should reject invalid logins."""
        assert not LOGIN_PATTERN.match('ab')
        assert not LOGIN_PATTERN.match('test@user')
        assert not LOGIN_PATTERN.match('test user')

    def test_name_pattern(self):
        """NAME_PATTERN should match valid names."""
        assert NAME_PATTERN.match('John')
        assert NAME_PATTERN.match('Иван')
        assert NAME_PATTERN.match('John Doe')

    def test_module_types(self):
        """MODULE_TYPES should contain all types."""
        assert MODULE_TYPES == {'chat', 'reasoning', 'multimodal', 'embedding'}