import multiprocessing
import logging
from logging import Formatter

# Number of worker processes
workers = multiprocessing.cpu_count() * 2 + 1

# Address and port to listen on
bind = "0.0.0.0:5000"

# Timeouts
timeout = 30

# Logging with timestamps
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Log format configuration
# Gunicorn uses its own formats, but can be configured via environment variables
# or via additional parameters