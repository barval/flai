#!/usr/bin/env python3
"""Health check for SuperLocalMemory HTTP proxy."""

import sys
import urllib.request

try:
    resp = urllib.request.urlopen("http://localhost:8766/health", timeout=10)
    if resp.status == 200:
        sys.exit(0)
    sys.exit(1)
except Exception:
    sys.exit(1)
