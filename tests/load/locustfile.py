# tests/load/locustfile.py
"""Load testing script for FLAI application using Locust.
Tests performance of various API endpoints without authentication
to measure raw server capacity.
"""
from locust import HttpUser, task, between
import random


class APILoadUser(HttpUser):
    """Simulates users hitting various API endpoints."""
    wait_time = between(1, 3)

    @task(5)
    def health_check(self):
        """Check health endpoint - very fast."""
        self.client.get("/health")

    @task(3)
    def login_page(self):
        """Load login page - tests template rendering."""
        self.client.get("/login")

    @task(2)
    def static_files(self):
        """Load static files - tests static file serving."""
        files = [
            "/static/css/main.css",
            "/static/js/chat-init.js",
            "/static/js/chat-queue.js",
            "/static/js/chat-sessions.js",
        ]
        self.client.get(random.choice(files))


class AuthenticatedChatUser(HttpUser):
    """Simulates authenticated users with full workflow."""
    wait_time = between(2, 5)

    def on_start(self):
        """Login and create a session."""
        import re

        # Each user gets a random test account
        user_num = random.randint(1, 5)
        self.username = f"loaduser{user_num}"
        self.password = f"loadpass{user_num}"
        self.session_id = None
        self.csrf_token = ""

        # Step 1: Get login page and extract CSRF
        login_page = self.client.get("/login")
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', login_page.text)
        if match:
            self.csrf_token = match.group(1)

        # Step 2: Login
        self.client.post("/login", data={
            "login": self.username,
            "password": self.password,
            "csrf_token": self.csrf_token
        }, allow_redirects=True)

        # Step 3: Get chat page CSRF
        chat_page = self.client.get("/chat")
        match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', chat_page.text)
        if match:
            self.csrf_token = match.group(1)

        # Step 4: Create session
        resp = self.client.post("/api/sessions/new", json={}, headers={
            "X-CSRFToken": self.csrf_token
        })
        if resp.status_code == 200:
            self.session_id = resp.json().get("id")

    @task(3)
    def get_sessions(self):
        """Fetch sessions list."""
        self.client.get("/api/sessions")

    @task(2)
    def get_messages(self):
        """Get messages for session."""
        if self.session_id:
            self.client.get(f"/api/sessions/{self.session_id}/messages")
