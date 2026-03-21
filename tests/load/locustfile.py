# tests/load/locustfile.py
from locust import HttpUser, task, between
import random
import json


class ChatUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        """Login and create a session before starting the test."""
        # Login using the test user (must exist in the system)
        response = self.client.post("/login", data={
            "login": "testuser",
            "password": "testpass"
        })
        if response.status_code != 200:
            print(f"Login failed: {response.status_code}")
            self.interrupt()
        
        # Create a new session to use for message retrieval
        create_response = self.client.post("/api/sessions/new")
        if create_response.status_code == 200:
            data = create_response.json()
            self.session_id = data.get("id")
            print(f"Created new session: {self.session_id}")
        else:
            print("Failed to create session")
            self.interrupt()

    @task(3)
    def send_message(self):
        """Send a text message to the chat."""
        self.client.post("/api/send_message", json={
            "message": "Hello, this is a test message"
        })

    @task(1)
    def get_sessions(self):
        """Fetch the list of sessions."""
        self.client.get("/api/sessions")

    @task(1)
    def get_messages(self):
        """Get messages for the session created in on_start."""
        if hasattr(self, 'session_id'):
            self.client.get(f"/api/sessions/{self.session_id}/messages")
        else:
            # Fallback to a dummy ID if session creation failed
            self.client.get("/api/sessions/dummy/messages")