# tests/load/locustfile.py
from locust import HttpUser, task, between
import random


class ChatUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        """Login before starting."""
        # Use a test user that exists
        self.client.post("/login", {
            "login": "testuser",
            "password": "testpass"
        })

    @task(3)
    def send_message(self):
        """Send a text message."""
        self.client.post("/send_message", json={"message": "Hello, this is a test message"})

    @task(1)
    def get_sessions(self):
        """Fetch sessions list."""
        self.client.get("/api/sessions")

    @task(1)
    def get_messages(self):
        """Get messages for a session (need to know session id)."""
        # For simplicity, assume there is a session
        self.client.get("/api/sessions/12345/messages")