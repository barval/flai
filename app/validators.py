"""Input validation helpers for API endpoints."""
import re
from typing import Any, Dict, List, Optional, Tuple


# Whitelist patterns
LOGIN_PATTERN = re.compile(r'^[a-zA-Z0-9_\-]{3,50}$')
NAME_PATTERN = re.compile(r'^[a-zA-Zа-яА-ЯёЁ0-9_\s\-]{2,100}$')
MODULE_TYPES = {'chat', 'reasoning', 'multimodal', 'embedding'}


class ValidationError(Exception):
    """Raised when input validation fails."""
    pass


def validate_user_input(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate user creation/update input.

    Args:
        data: JSON data from request

    Returns:
        Validated data dict

    Raises:
        ValidationError: If validation fails
    """
    if not data or not isinstance(data, dict):
        raise ValidationError("Invalid JSON data")

    errors = []

    # Login validation
    login = data.get('login')
    if login:
        if not LOGIN_PATTERN.match(login):
            errors.append("Login must be 3-50 chars, alphanumeric, underscores, or hyphens")
    elif 'login' in data:
        errors.append("Login is required")

    # Name validation
    name = data.get('name')
    if name:
        if not NAME_PATTERN.match(name):
            errors.append("Name must be 2-100 chars, letters, digits, spaces, hyphens")
    elif 'name' in data:
        errors.append("Name is required")

    # Password validation (only for creation)
    password = data.get('password')
    if password and not isinstance(password, str):
        errors.append("Password must be a string")
    if password and len(password) < 4:
        errors.append("Password must be at least 4 characters")

    # Service class validation
    service_class = data.get('service_class')
    if service_class is not None:
        if not isinstance(service_class, int) or service_class not in (0, 1, 2):
            errors.append("Service class must be 0, 1, or 2")

    # is_active validation
    is_active = data.get('is_active')
    if is_active is not None and not isinstance(is_active, bool):
        errors.append("is_active must be a boolean")

    if errors:
        raise ValidationError("; ".join(errors))

    return data


def validate_model_config_update(data: Optional[Dict[str, Any]], module: str) -> Dict[str, Any]:
    """Validate model configuration update input.

    Args:
        data: JSON data from request
        module: Module type (chat, reasoning, multimodal, embedding)

    Returns:
        Validated updates dict

    Raises:
        ValidationError: If validation fails
    """
    if not data or not isinstance(data, dict):
        raise ValidationError("Invalid JSON data")

    if module not in MODULE_TYPES:
        raise ValidationError(f"Invalid module type: {module}. Must be one of {MODULE_TYPES}")

    allowed_fields = {'model_name', 'service_url', 'context_length', 'temperature', 'top_p', 'timeout'}
    updates = {k: v for k, v in data.items() if k in allowed_fields}

    if not updates:
        raise ValidationError("No valid fields to update")

    # Validate individual fields
    errors = []

    if 'context_length' in updates and updates['context_length'] is not None:
        val = updates['context_length']
        if not isinstance(val, int) or val < 512:
            errors.append("Context length must be at least 512")

    if 'temperature' in updates and updates['temperature'] is not None:
        val = updates['temperature']
        if not isinstance(val, (int, float)) or val < 0.0 or val > 2.0:
            errors.append("Temperature must be between 0.0 and 2.0")

    if 'top_p' in updates and updates['top_p'] is not None:
        val = updates['top_p']
        if not isinstance(val, (int, float)) or val < 0.0 or val > 1.0:
            errors.append("Top P must be between 0.0 and 1.0")

    if 'timeout' in updates and updates['timeout'] is not None:
        val = updates['timeout']
        if not isinstance(val, int) or val < 0 or val > 1200:
            errors.append("Timeout must be between 0 and 1200 seconds")

    if 'service_url' in updates and updates['service_url'] is not None:
        url = updates['service_url']
        if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
            errors.append("Service URL must start with http:// or https://")

    if errors:
        raise ValidationError("; ".join(errors))

    return updates
