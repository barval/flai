"""
Конфигурация приложения из переменных окружения
"""
from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    # ==================== СИСТЕМНЫЕ ====================
    timezone: str = "Europe/Moscow"
    app_port: int = 8000
    secret_key: str = "dev_secret_key_change_me"
    
    # ==================== ИЗОБРАЖЕНИЯ ====================
    max_image_width: int = 3840
    max_image_height: int = 2160
    max_image_size_mb: int = 5
    
    @property
    def max_image_size_bytes(self) -> int:
        return self.max_image_size_mb * 1024 * 1024
    
    # ==================== OLLAMA ====================
    ollama_url: str = "http://host.docker.internal:11434"
    
    # Чат-модель (также используется как маршрутизатор)
    llm_chat_model: str = "qwen3:4b-instruct-2507-q4_K_M"
    llm_chat_context: int = 32768
    llm_chat_temperature: float = 0.1
    llm_chat_top_p: float = 0.1
    
    # Мультимодальная модель
    llm_multimodal_model: str = "qwen3-vl:8b-instruct-q4_K_M"
    llm_multimodal_context: int = 32768
    llm_multimodal_temperature: float = 0.7
    llm_multimodal_top_p: float = 0.9
    
    # Reasoning модель
    llm_reasoning_model: str = "qwen3:8b-q4_K_M"
    llm_reasoning_context: int = 40960
    llm_reasoning_temperature: float = 0.7
    llm_reasoning_top_p: float = 0.9
    
    # ==================== AUTOMATIC1111 ====================
    automatic1111_url: str = "http://host.docker.internal:7860"
    a1111_model: str = "cyberrealisticXL_v90.safetensors"
    a1111_steps: int = 30
    a1111_cfg_scale: float = 7.0
    a1111_width: int = 1024
    a1111_height: int = 1024
    a1111_sampler: str = "Euler a"
    
    # ==================== QDRANT (опционально) ====================
    qdrant_url: Optional[str] = None
    qdrant_collection: str = "ai_local_docs"
    
    # ==================== ПУТИ ====================
    storage_path: str = "/app/storage"
    
    @property
    def uploads_path(self) -> str:
        return os.path.join(self.storage_path, "uploads")
    
    @property
    def sessions_path(self) -> str:
        return os.path.join(self.storage_path, "sessions")
    
    @property
    def cache_path(self) -> str:
        return os.path.join(self.storage_path, "cache")
    
    # ==================== ПОДВАЛ ====================
    footer_text: str = "ИИ Локальный v3.3 (с) 2026 Барсуков Валерий"
    
    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


# Глобальный экземпляр настроек
settings = Settings()