"""
Клиент для взаимодействия с Ollama API
"""
import httpx
import json
from loguru import logger
from typing import List, Optional, AsyncGenerator, Dict, Any
from backend.utils.config import settings

class OllamaClient:
    def __init__(self):
        self.base_url = settings.ollama_url.rstrip('/')
        self.timeout = 300.0  # Увеличен таймаут для медленных моделей
    
    async def _post(self, endpoint: str, json_data: dict) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}{endpoint}",
                json=json_data,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            return response.json()
    
    async def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float,
        top_p: float,
        images: Optional[List[str]] = None,
        options: Optional[Dict] = None
    ) -> Dict[str, Any]:
        payload = {
            "model": model,
            "messages": messages,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_ctx": getattr(settings, 'llm_chat_context', 32768)
            },
            "stream": False
        }
        if options:
            payload["options"].update(options)
        if images:
            payload["images"] = images
        return await self._post("/api/chat", payload)
    
    async def generate(
        self,
        model: str,
        prompt: str,
        temperature: float,
        top_p: float,
        images: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        payload = {
            "model": model,
            "prompt": prompt,
            "options": {
                "temperature": temperature,
                "top_p": top_p
            },
            "stream": False
        }
        if images:
            payload["images"] = images
        return await self._post("/api/generate", payload)
    
    async def embed(self, model: str, prompt: str) -> List[float]:
        result = await self._post("/api/embeddings", {
            "model": model,
            "prompt": prompt
        })
        return result.get("embedding", [])
    
    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                return response.json().get("models", [])
        except Exception as e:
            logger.error(f"Ошибка получения списка моделей: {e}")
            return []
    
    async def is_model_available(self, model: str) -> bool:
        try:
            models = await self.list_models()
            model_names = [m["name"] for m in models]
            model_base = model.split(":")[0]
            return model in model_names or any(m.startswith(model_base) for m in model_names)
        except Exception as e:
            logger.error(f"Ошибка проверки модели {model}: {e}")
            return False
    
    async def pull_model(self, model: str) -> AsyncGenerator[Dict, None]:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/pull",
                json={"name": model, "stream": True}
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except json.JSONDecodeError:
                            continue

ollama = OllamaClient()