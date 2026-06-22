# tests/test_circuit_breaker.py
"""Tests for Circuit Breaker pattern implementation."""

import time

import pytest

from app.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError


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


class TestCircuitBreakerOpenError:
    """Test cases for CircuitBreakerOpenError exception."""

    def test_is_exception(self):
        """CircuitBreakerOpenError should be an Exception."""
        exc = CircuitBreakerOpenError("test message")
        assert isinstance(exc, Exception)

    def test_can_be_caught(self):
        """CircuitBreakerOpenError should be catchable."""
        with pytest.raises(CircuitBreakerOpenError):
            raise CircuitBreakerOpenError("test")
