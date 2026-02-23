"""
Обработка мультимодальных запросов
"""
import json
import re
import base64
from typing import Dict, List, Optional
from loguru import logger
from backend.utils.ollama_client import ollama
from backend.utils.config import settings
from backend.models.prompts import (
    MULTIMODAL_WITH_CAPTION_PROMPT,
    MULTIMODAL_NO_CAPTION_PROMPT,
    SD_PROMPT_PREPARATION,
    get_current_time_str
)
from backend.utils.text_utils import clean_thinking_blocks

async def analyze_image(
    image_base64: str,
    caption: Optional[str] = None,
    session_id: str = ""
) -> Dict:
    current_time = get_current_time_str(settings.timezone)
    
    if caption and caption.strip():
        prompt_text = MULTIMODAL_WITH_CAPTION_PROMPT.format(
            current_time_str=current_time,
            caption=caption.strip()
        )
    else:
        prompt_text = MULTIMODAL_NO_CAPTION_PROMPT.format(
            current_time_str=current_time
        )
    
    try:
        response = await ollama.chat(
            model=settings.llm_multimodal_model,
            messages=[{
                "role": "user",
                "content": prompt_text
            }],
            images=[image_base64],
            temperature=settings.llm_multimodal_temperature,
            top_p=settings.llm_multimodal_top_p
        )
        content = response.get("message", {}).get("content", "").strip()
        content = clean_thinking_blocks(content)
        tokens = response.get("eval_count", 0) + response.get("prompt_eval_count", 0)
        
        logger.info(
            f"Multimodal analysis: {content[:150]}{'...' if len(content) > 150 else ''}"
        )
        
        return {
            "content": content,
            "tokens": tokens
        }
    except Exception as e:
        logger.error(f"Ошибка мультимодального анализа: {e}", exc_info=True)
        return {
            "content": "❌ Не удалось проанализировать изображение. Попробуйте ещё раз.",
            "tokens": 0
        }

async def prepare_sd_prompt(user_query: str) -> Dict:
    try:
        prompt_text = SD_PROMPT_PREPARATION.format(image_query=user_query)
        
        response = await ollama.chat(
            model=settings.llm_multimodal_model,
            messages=[{"role": "user", "content": prompt_text}],
            temperature=0.1,
            top_p=0.1
        )
        content = response.get("message", {}).get("content", "").strip()
        content = clean_thinking_blocks(content)
        logger.debug(f"SD prompt preparation raw: {content[:300]}")
        
        json_str = content
        if "```" in content:
            match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content, re.DOTALL)
            if match:
                json_str = match.group(1)
        
        params = json.loads(json_str)
        
        result = {
            "prompt": str(params.get("prompt", "")),
            "negative_prompt": str(params.get("negative_prompt", "")),
            "steps": int(params.get("steps", 40)),
            "width": int(params.get("width", 512)),
            "height": int(params.get("height", 512)),
            "cfg_scale": float(params.get("cfg_scale", 7)),
            "sampler_name": str(params.get("sampler_name", "Euler a")),
            "batch_size": int(params.get("batch_size", 1)),
            "enable_hr": str(params.get("enable_hr", "true")).lower() == "true",
            "hr_scale": float(params.get("hr_scale", 2)),
            "hr_upscaler": str(params.get("hr_upscaler", "Latent")),
            "denoising_strength": float(params.get("denoising_strength", 0.7)),
            "hr_second_pass_steps": int(params.get("hr_second_pass_steps", 25))
        }
        
        logger.info(f"SD prompt prepared: {result['prompt'][:100]}...")
        return result
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON промпта: {e}, raw: {content[:200]}")
        return _fallback_sd_prompt(user_query)
    except Exception as e:
        logger.error(f"Ошибка подготовки SD-промпта: {e}", exc_info=True)
        return _fallback_sd_prompt(user_query)

def _fallback_sd_prompt(user_query: str) -> Dict:
    return {
        "prompt": f"masterpiece, best quality, ultra-detailed, photorealistic, 8k, {user_query}",
        "negative_prompt": "worst quality, low quality, jpeg artifacts, ugly, duplicate, morbid, mutilated, extra fingers, mutated hands, poorly drawn hands, poorly drawn face, mutation, deformed, blurry, dehydrated, bad anatomy, bad proportions, extra limbs, cloned face, disfigured, out of frame, malformed limbs, missing arms, missing legs, extra arms, extra legs, fused fingers, too many fingers, long neck",
        "steps": 30,
        "width": 1024,
        "height": 1024,
        "cfg_scale": 7.0,
        "sampler_name": "Euler a",
        "batch_size": 1,
        "enable_hr": True,
        "hr_scale": 2.0,
        "hr_upscaler": "Latent",
        "denoising_strength": 0.7,
        "hr_second_pass_steps": 25
    }