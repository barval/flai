"""
Утилиты для пост-обработки текста от ИИ-моделей
"""
import re
from loguru import logger

def clean_thinking_blocks(content: str) -> str:
    """
    Удаляет блоки рассуждений <think>...</think> и всё, что после <|endoftext|> из ответа модели.
    Сохраняет только финальный ответ после закрывающего тега.
    
    Args:
        content: исходный текст ответа модели
        
    Returns:
        Очищенный текст без блоков рассуждений
    """
    if not content:
        return content
    
        # Удаляем блок <think>...</think> И всё после <|endoftext|>
    cleaned = re.sub(
        r'(?:<think>[\s\S]*?</think>)|<\|endoftext\|>[\s\S]*$',
        '',
        content,
        flags=re.IGNORECASE
    )

    # Удаляем возможные пустые строки в начале/конце после очистки
    cleaned = cleaned.strip()
    
    # Если после удаления блокa остался пустой контент — возвращаем безопасный ответ
    if not cleaned:
        logger.warning("Контент полностью состоял из блока рассуждений")
        return "✓ Запрос обработан."
    
    return cleaned