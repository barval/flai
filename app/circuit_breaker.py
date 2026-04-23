"""Circuit Breaker pattern implementation for service resilience."""
import time
import threading
from typing import Optional


class CircuitBreaker:
    """Circuit Breaker to prevent cascading failures.

    States:
        CLOSED:   Normal operation, requests pass through
        OPEN:     Service is failing, requests are blocked (fail fast)
        HALF_OPEN: Testing if service recovered, one request allowed

    Usage:
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
        try:
            with cb:
                result = risky_call()
        except CircuitBreakerOpen:
            return fallback()
    """

    CLOSED = 'closed'
    OPEN = 'open'
    HALF_OPEN = 'half_open'

    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 30):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.state = self.CLOSED
        self._lock = threading.Lock()

    def record_success(self):
        """Record a successful call."""
        with self._lock:
            self.failure_count = 0
            self.state = self.CLOSED

    def record_failure(self):
        """Record a failed call."""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = self.OPEN

    def can_execute(self) -> bool:
        """Check if a request can be executed."""
        with self._lock:
            if self.state == self.CLOSED:
                return True
            if self.state == self.OPEN:
                elapsed = time.time() - self.last_failure_time
                if elapsed >= self.recovery_timeout:
                    self.state = self.HALF_OPEN
                    return True
                return False
            # HALF_OPEN: allow one request
            return True

    def get_state(self) -> dict:
        """Get circuit breaker state info."""
        with self._lock:
            return {
                'state': self.state,
                'failure_count': self.failure_count,
                'failure_threshold': self.failure_threshold,
                'recovery_timeout': self.recovery_timeout,
                'last_failure_time': self.last_failure_time,
            }

    def __enter__(self):
        if not self.can_execute():
            raise CircuitBreakerOpen(
                f"Circuit breaker is {self.state}. "
                f"Failures: {self.failure_count}/{self.failure_threshold}. "
                f"Retry after {self.recovery_timeout}s."
            )

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.record_failure()
        else:
            self.record_success()


class CircuitBreakerOpen(Exception):
    """Raised when circuit breaker is open and request is blocked."""
    pass
