# tests/test_circuit_breaker.py
"""Tests for Circuit Breaker pattern implementation."""
import pytest
import time
from unittest.mock import patch, MagicMock

from app.circuit_breaker import CircuitBreaker, CircuitBreakerOpen


class TestCircuitBreaker:
    """Test cases for CircuitBreaker class."""

    def test_initial_state_is_closed(self):
        """Circuit breaker should start in CLOSED state."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.failure_count == 0

    def test_record_success_resets_failure_count(self):
        """Recording success should reset failure count."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == CircuitBreaker.CLOSED

    def test_record_failure_increments_count(self):
        """Recording failure should increment failure count."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        cb.record_failure()
        assert cb.failure_count == 1

    def test_threshold_opens_circuit(self):
        """Circuit should open after threshold failures."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 3
        assert cb.state == CircuitBreaker.OPEN

    def test_can_execute_allows_in_closed_state(self):
        """can_execute should return True in CLOSED state."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        assert cb.can_execute() is True

    def test_can_execute_blocks_in_open_state(self):
        """can_execute should return False in OPEN state before timeout."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30)
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        assert cb.can_execute() is False

    def test_can_execute_transitions_to_half_open_after_timeout(self):
        """can_execute should transition to HALF_OPEN after recovery_timeout."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=1)
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        time.sleep(1.1)
        assert cb.can_execute() is True
        assert cb.state == CircuitBreaker.HALF_OPEN

    def test_context_manager_allows_execution_when_closed(self):
        """Context manager should allow execution in CLOSED state."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        with cb:
            result = "success"
        assert result == "success"

    def test_context_manager_raises_when_open(self):
        """Context manager should raise CircuitBreakerOpen when OPEN."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=30)
        cb.record_failure()

        with pytest.raises(CircuitBreakerOpen) as exc_info:
            with cb:
                pass

        assert "Circuit breaker is open" in str(exc_info.value)

    def test_context_manager_records_success_on_exit(self):
        """Context manager should record success on normal exit."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        with cb:
            pass

        assert cb.failure_count == 0
        assert cb.state == CircuitBreaker.CLOSED

    def test_context_manager_records_failure_on_exception(self):
        """Context manager should record failure on exception."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        try:
            with cb:
                raise ValueError("test error")
        except ValueError:
            pass

        assert cb.failure_count == 1

    def test_get_state_returns_dict(self):
        """get_state should return state information."""
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        state = cb.get_state()

        assert isinstance(state, dict)
        assert 'state' in state
        assert 'failure_count' in state
        assert 'failure_threshold' in state
        assert 'recovery_timeout' in state

    def test_half_open_state_allows_one_request(self):
        """HALF_OPEN should allow one request then return to OPEN."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=1)
        cb.record_failure()

        time.sleep(1.1)
        assert cb.can_execute() is True
        assert cb.state == CircuitBreaker.HALF_OPEN

        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

    def test_multiple_threads_safe(self):
        """Circuit breaker should be thread-safe."""
        cb = CircuitBreaker(failure_threshold=10, recovery_timeout=30)

        def record_failures():
            for _ in range(5):
                cb.record_failure()

        import threading
        threads = [threading.Thread(target=record_failures) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert cb.failure_count == 10
        assert cb.state == CircuitBreaker.OPEN


class TestCircuitBreakerOpen:
    """Test cases for CircuitBreakerOpen exception."""

    def test_is_exception(self):
        """CircuitBreakerOpen should be an Exception."""
        exc = CircuitBreakerOpen("test message")
        assert isinstance(exc, Exception)

    def test_can_be_caught(self):
        """CircuitBreakerOpen should be catchable."""
        with pytest.raises(CircuitBreakerOpen):
            raise CircuitBreakerOpen("test")