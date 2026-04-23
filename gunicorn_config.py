import multiprocessing
import logging
from logging import Formatter

# ── Worker configuration ──
# Single worker with multiple threads is optimal for I/O-bound
# operations (waiting for AI model responses).
# Adding more workers increases RAM usage without improving throughput
# since all requests ultimately wait for the same llama.cpp server.
workers = 1
threads = 4
worker_class = "gthread"

# ── Network ──
bind = "0.0.0.0:5000"

# ── Timeouts ──
# 900s (15 min) to accommodate the longest operation:
#   - LLM timeout: 300s (5 min)
#   - SD editing timeout: 900s (15 min)
#   - Reindex operations: variable
# This prevents Gunicorn from killing workers during legitimate long tasks.
timeout = 900
graceful_timeout = 30
keepalive = 5

# ── Logging ──
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'
