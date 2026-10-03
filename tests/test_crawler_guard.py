"""SSRF guard tests: only globally routable http(s) URLs pass."""

import socket
from unittest.mock import patch

import pytest

from app.crawler_guard import BlockedUrlError, validate_url


def _resolve(*_args, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


@pytest.mark.unit
class TestValidateUrl:
    def test_public_https_url_passes_and_is_normalized(self):
        with patch("app.crawler_guard.socket.getaddrinfo", side_effect=_resolve):
            assert validate_url("https://example.com/docs?a=1") == "https://example.com/docs?a=1"

    def test_non_http_schemes_are_rejected(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "data:text/html,x"):
            with pytest.raises(BlockedUrlError):
                validate_url(url)

    def test_private_ranges_are_rejected(self):
        blocked = [
            "http://192.168.1.1/",
            "http://10.0.0.5/",
            "http://172.16.0.9/",
            "http://127.0.0.1/",
            "http://169.254.169.254/latest/meta-data/",
            "http://100.64.0.1/",
            "http://0.0.0.0/",
            "http://[::1]/",
            "http://[fe80::1]/",
            "http://[fc00::1]/",
            "http://224.0.0.1/",
        ]
        for url in blocked:
            with pytest.raises(BlockedUrlError):
                validate_url(url)

    def test_dns_resolving_to_private_ip_is_rejected(self):
        def _private(*_a, **_k):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.0.10", 0))]

        with patch("app.crawler_guard.socket.getaddrinfo", side_effect=_private), pytest.raises(BlockedUrlError):
            validate_url("http://internal.example.com/")

    def test_every_resolved_address_must_pass(self):
        def _mixed(*_a, **_k):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
            ]

        with patch("app.crawler_guard.socket.getaddrinfo", side_effect=_mixed), pytest.raises(BlockedUrlError):
            validate_url("http://dual.example.com/")

    def test_resolution_failure_is_rejected(self):
        with patch("app.crawler_guard.socket.getaddrinfo", side_effect=socket.gaierror), pytest.raises(BlockedUrlError):
            validate_url("http://nonexistent.example/")

    def test_empty_and_malformed_urls_are_rejected(self):
        for url in ("", "not-a-url", "http://", "https:// "):
            with pytest.raises(BlockedUrlError):
                validate_url(url)
