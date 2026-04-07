# tests/load/locustfile_public.py
"""Load testing for public endpoints only (no auth required).
Measures raw server capacity without authentication overhead.
"""
from locust import HttpUser, task, between
import random


class PublicUser(HttpUser):
    """Simulates anonymous users hitting public endpoints."""
    wait_time = between(0.5, 2)

    @task(10)
    def health_check(self):
        """Health endpoint - measures baseline server speed."""
        self.client.get("/health")

    @task(5)
    def login_page(self):
        """Login page - tests template rendering."""
        self.client.get("/login")

    @task(3)
    def static_css(self):
        """CSS files - tests static file serving."""
        self.client.get("/static/css/main.css")

    @task(3)
    def static_js(self):
        """JS files - tests static file serving."""
        files = [
            "/static/js/chat-init.js",
            "/static/js/chat-queue.js",
            "/static/js/chat-sessions.js",
            "/static/js/chat-messages.js",
            "/static/js/header.js",
        ]
        self.client.get(random.choice(files))

    @task(2)
    def static_images(self):
        """Image files."""
        self.client.get("/static/logo-header.png")
