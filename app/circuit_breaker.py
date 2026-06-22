"""Circuit Breaker pattern implementation for service resilience."""

import threading
import time


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
        except CircuitBreakerOpenError:
            return fallback()
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

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

    def record_failure(self) -> bool:
        """Record a failed call.

        Returns True if the circuit just transitioned to OPEN (useful for triggering fallback logic).
        """
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                if self.state != self.OPEN:
                    self.state = self.OPEN
                    return True
                self.state = self.OPEN
            return False

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


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open and request is blocked."""

    pass
