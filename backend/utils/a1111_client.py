"""
Клиент для Automatic1111 Stable Diffusion WebUI API
"""
import httpx
import base64
from loguru import logger
from typing import Optional, Dict, Any, List
from backend.utils.config import settings

class A1111Client:
    def __init__(self):
        self.base_url = settings.automatic1111_url.rstrip('/')
        self.timeout = 300.0
    
    async def _post(self, endpoint: str, json_data: dict, timeout: Optional[float] = None) -> dict:
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
            response = await client.post(
                f"{self.base_url}{endpoint}",
                json=json_data,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            return response.json()
    
    async def _get(self, endpoint: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}{endpoint}")
            response.raise_for_status()
            return response.json()
    
    async def txt2img(
        self,
        prompt: str,
        negative_prompt: str = "",
        steps: Optional[int] = None,
        cfg_scale: Optional[float] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        sampler: Optional[str] = None,
        model: Optional[str] = None,
        seed: Optional[int] = -1
    ) -> Dict[str, Any]:
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt or "",
            "steps": steps or settings.a1111_steps,
            "cfg_scale": cfg_scale or settings.a1111_cfg_scale,
            "width": width or settings.a1111_width,
            "height": height or settings.a1111_height,
            "sampler_name": sampler or settings.a1111_sampler,
            "batch_size": 1,
            "n_iter": 1,
            "seed": seed,
            "send_images": False,
            "save_images": False,
            "alwayson_scripts": {}
        }
        
        if model and model != settings.a1111_model:
            await self._set_model(model)
        
        try:
            result = await self._post("/sdapi/v1/txt2img", payload)
            if not result.get("images"):
                raise ValueError("A1111 вернул пустой результат")
            return {
                "image": result["images"][0],
                "parameters": result.get("parameters", {}),
                "info": result.get("info", {}),
                "success": True
            }
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP ошибка A1111: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Ошибка генерации txt2img: {e}")
            raise
    
    async def img2img(
        self,
        prompt: str,
        init_images: List[str],
        negative_prompt: str = "",
        denoising_strength: float = 0.7,
        steps: Optional[int] = None,
        cfg_scale: Optional[float] = None,
        sampler: Optional[str] = None,
        model: Optional[str] = None
    ) -> Dict[str, Any]:
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt or "",
            "init_images": init_images,
            "denoising_strength": denoising_strength,
            "steps": steps or settings.a1111_steps,
            "cfg_scale": cfg_scale or settings.a1111_cfg_scale,
            "sampler_name": sampler or settings.a1111_sampler,
            "batch_size": 1,
            "n_iter": 1,
            "send_images": False,
            "save_images": False
        }
        
        if model:
            await self._set_model(model)
        
        result = await self._post("/sdapi/v1/img2img", payload)
        return {
            "image": result["images"][0] if result.get("images") else None,
            "parameters": result.get("parameters", {}),
            "info": result.get("info", {}),
            "success": bool(result.get("images"))
        }
    
    async def _set_model(self, model_name: str) -> bool:
        try:
            models = await self._get("/sdapi/v1/sd-models")
            target = next(
                (m for m in models if model_name in m.get("title", "")),
                None
            )
            if target:
                await self._post("/sdapi/v1/options", {
                    "sd_model_checkpoint": target["title"]
                })
                logger.info(f"Модель A1111 изменена на: {target['title']}")
                return True
            else:
                logger.warning(f"Модель '{model_name}' не найдена в A1111")
                available = [m.get("title", "") for m in models[:5]]
                logger.warning(f"Доступные модели (первые 5): {available}")
                return False
        except Exception as e:
            logger.error(f"Ошибка смены модели A1111: {e}")
            return False
    
    async def get_progress(self) -> Dict[str, Any]:
        return await self._get("/sdapi/v1/progress")
    
    async def interrupt(self) -> bool:
        try:
            await self._post("/sdapi/v1/interrupt", {})
            return True
        except:
            return False
    
    async def get_options(self) -> Dict[str, Any]:
        return await self._get("/sdapi/v1/options")
    
    async def list_models(self) -> List[Dict[str, str]]:
        models = await self._get("/sdapi/v1/sd-models")
        return [{"title": m.get("title"), "hash": m.get("model_hash")} for m in models]
    
    async def list_samplers(self) -> List[str]:
        samplers = await self._get("/sdapi/v1/samplers")
        return [s.get("name") for s in samplers]
    
    async def is_available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/sdapi/v1/options")
                return response.status_code == 200
        except Exception as e:
            logger.debug(f"A1111 недоступен: {e}")
            return False
    
    async def get_version(self) -> Optional[str]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/")
                if response.status_code == 200:
                    import re
                    match = re.search(r'version:\s*([^\s<]+)', response.text)
                    if match:
                        return match.group(1)
        except:
            pass
        return None

a1111 = A1111Client()