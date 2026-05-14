# app/routes/__init__.py
from . import admin, auth, backups, chat, documents, messages, queue, sessions, tts

__all__ = ["auth", "chat", "admin", "queue", "tts", "messages", "sessions", "documents", "backups"]
