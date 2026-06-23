# ── Worker configuration ──
# Single worker with multiple threads is optimal for I/O-bound
# operations (waiting for AI model responses).
# Multiple workers cause GPU race conditions — _gpu_lock (threading.Lock)
# only works within one process, not between gunicorn workers.
workers = 1
worker_class = "gevent"

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
