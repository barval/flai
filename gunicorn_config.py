import multiprocessing
import logging
from logging import Formatter

# Количество рабочих процессов
workers = multiprocessing.cpu_count() * 2 + 1

# Адрес и порт для прослушивания
bind = "0.0.0.0:5000"

# Таймауты
timeout = 30

# Логирование с временными метками
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Настройка форматирования логов
# Gunicorn использует свои форматы, но можно настроить через переменные окружения
# или через дополнительные параметры