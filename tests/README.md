# FLAI Testing Guide

## Quick Start

```bash
# Run all tests
pytest

# Run unit tests only (fast, no external dependencies)
pytest -m unit -v

# Run integration tests (with mocked services)
pytest -m integration -v

# Run with coverage report
pytest --cov=app --cov=modules --cov-report=html

# Open coverage report
xdg-open htmlcov/index.html  # Linux
open htmlcov/index.html      # macOS
```

## Test Categories

| Category | Command | Time | Requires |
|----------|---------|------|----------|
| **Unit** | `pytest -m unit` | ~30s | Nothing |
| **Integration** | `pytest -m integration` | ~2min | Nothing (mocked) |
| **E2E** | `pytest -m e2e` | ~10min | Docker Compose |

### Unit Tests

Test individual functions and classes in isolation.

```bash
# Run all unit tests
pytest -m unit -v

# Run specific unit test file
pytest tests/test_utils.py -v

# Run specific test
pytest tests/test_utils.py::test_chunk_text -v
```

**Examples:**
- `test_utils.py` — Utility functions
- `test_audio_module.py` — Audio module (Whisper)
- `test_image_module.py` — Image module (Automatic1111)
- `test_queue.py` — Redis queue (mocked)

### Integration Tests

Test interactions between components with mocked external services.

```bash
# Run all integration tests
pytest -m integration -v

# Run admin routes tests
pytest tests/test_admin_routes.py -v

# Run security tests
pytest tests/test_security.py -v
```

**Examples:**
- `test_admin_routes.py` — Admin panel endpoints
- `test_documents_routes.py` — Document upload/RAG
- `test_security.py` — CSRF, rate limiting, etc.

### E2E Tests

End-to-end tests with real services (requires Docker Compose).

```bash
# Start all services first
docker-compose -f docker-compose.all.yml up -d

# Run E2E tests
pytest -m e2e -v

# Stop services
docker-compose -f docker-compose.all.yml down
```

---

## Writing Tests

### Unit Test Example

```python
# tests/unit/test_example.py
import pytest
from app.utils import some_function


@pytest.mark.unit
def test_some_function():
    """Test some_function with various inputs."""
    result = some_function("input")
    assert result == "expected_output"


@pytest.mark.unit
def test_some_function_edge_case():
    """Test edge case."""
    result = some_function("")
    assert result is None
```

### Integration Test Example

```python
# tests/integration/test_admin.py
import pytest


@pytest.mark.integration
def test_admin_panel_requires_auth(client):
    """Test that admin panel requires authentication."""
    response = client.get('/admin/')
    assert response.status_code == 302  # Redirect to login


@pytest.mark.integration
def test_admin_panel_requires_admin(client, test_user_admin):
    """Test that admin panel requires admin privileges."""
    # Login as admin
    client.post('/login', data={
        'login': 'admin',
        'password': 'adminpass'
    })
    
    response = client.get('/admin/')
    assert response.status_code == 200
```

### Using Fixtures

```python
# tests/test_example.py
import pytest


@pytest.mark.integration
def test_with_database(client):
    """Test that uses database."""
    # Database is automatically created and cleaned up
    response = client.get('/api/sessions')
    assert response.status_code == 200


@pytest.mark.integration
def test_with_mock_ollama(client, mock_ollama_client):
    """Test with mocked Ollama."""
    # Configure mock response
    mock_ollama_client.chat.return_value = {
        'message': {'content': 'Custom response'}
    }
    
    response = client.post('/api/send_message', 
                          json={'message': 'Hello'})
    assert response.status_code == 200


@pytest.mark.integration
def test_with_mock_redis(client, mock_redis_client):
    """Test with mocked Redis."""
    client.post('/api/endpoint')
    
    # Assert Redis was called
    mock_redis_client.rpush.assert_called()
```

---

## Available Fixtures

| Fixture | Description | Scope |
|---------|-------------|-------|
| `client` | Flask test client | function |
| `test_app` | Flask app instance | function |
| `runner` | CLI test runner | function |
| `mock_redis_client` | Mocked Redis client | function |
| `mock_ollama_client` | Mocked Ollama client | function |
| `mock_qdrant_client` | Mocked Qdrant client | function |

---

## Troubleshooting

### Test hangs

```bash
# Run with timeout
pytest --timeout=60
```

### Database conflicts

```bash
# Clean test databases
rm -rf /tmp/tmp*/test*.db
```

### Import errors

```bash
# Make sure you're in the project root
cd /home/GIT/GITEA/BARVAL-MY/flai

# Run from project root
pytest tests/test_utils.py
```

### Mock not working

```python
# Make sure to patch the correct import path
# If your code does: from modules.audio import AudioModule
# Then patch: @patch('modules.audio.requests.get')

# NOT: @patch('requests.get')  # This won't work!
```

---

## Coverage Requirements

Minimum coverage: **40%**

```bash
# Check coverage
pytest --cov=app --cov=modules --cov-report=term-missing

# Generate HTML report
pytest --cov=app --cov=modules --cov-report=html

# Fail if coverage below 40%
pytest --cov=app --cov=modules --cov-fail-under=40
```

---

## CI/CD Integration

### GitHub Actions

```yaml
# .github/workflows/tests.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v3
    
    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.9'
    
    - name: Install dependencies
      run: pip install -r requirements.txt
    
    - name: Run unit tests
      run: pytest -m unit --cov=app --cov=modules --cov-report=xml
    
    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
```

### GitLab CI

```yaml
# .gitlab-ci.yml
test:
  image: python:3.9
  script:
    - pip install -r requirements.txt
    - pytest -m unit --cov=app --cov=modules --cov-report=xml
  coverage: '/TOTAL.*\s+(\d+%)/'
```

---

## Best Practices

1. **Use appropriate markers** — Mark tests with `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.e2e`

2. **Keep tests isolated** — Each test should be independent and not rely on other tests

3. **Use fixtures** — Use provided fixtures instead of creating your own app instances

4. **Mock external services** — Never call real Ollama/Redis/Qdrant in unit/integration tests

5. **Descriptive names** — Use descriptive test names: `test_login_success` not `test1`

6. **Test edge cases** — Test empty inputs, None values, error conditions

7. **Keep tests fast** — Unit tests should run in <100ms each

---

## Running in Docker

```bash
# Run tests inside Docker container
docker exec -it flai-web pytest -v

# Run with coverage
docker exec -it flai-web pytest --cov=app --cov=modules

# Run specific test category
docker exec -it flai-web pytest -m unit -v
```

---

## Additional Resources

- [Pytest Documentation](https://docs.pytest.org/)
- [Pytest Fixtures](https://docs.pytest.org/en/latest/explanation/fixtures.html)
- [Pytest Mock](https://pytest-mock.readthedocs.io/)
- [Coverage.py](https://coverage.readthedocs.io/)
