import multiprocessing

# Количество рабочих процессов
workers = multiprocessing.cpu_count() * 2 + 1

# Адрес и порт для прослушивания
bind = "0.0.0.0:5000"

# Таймауты
timeout = 30

# Логирование
accesslog = "-"
errorlog = "-"
loglevel = "info"

# Для начала уберем gevent, можно добавить позже
# worker_class = "gevent"