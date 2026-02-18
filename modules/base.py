# modules/base.py
import logging
import requests
from datetime import datetime
import os
from .ollama_base import OllamaBaseModule

# Условный импорт для избежания циклических зависимостей
try:
    from app import format_prompt
except ImportError:
    # Будет импортировано позже
    pass

class BaseModule(OllamaBaseModule):
    """Базовый модуль для работы с чатом и рассуждающей моделью"""
    
    def __init__(self, app=None, ollama_url=None, models_config=None):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.ollama_url = ollama_url
        self.models_config = models_config or {}
        self.available = False
        
        if app:
            self.init_app(app)
        elif ollama_url:
            self.check_availability()
    
    def init_app(self, app):
        """Инициализация модуля с приложением Flask"""
        self.ollama_url = app.config.get('OLLAMA_URL')
        self.models_config = {
            'chat': {
                'model': app.config.get('LLM_CHAT_MODEL'),
                'context': app.config.get('LLM_CHAT_MODEL_CONTEXT_WINDOW', 32768),
                'temperature': app.config.get('LLM_CHAT_TEMPERATURE', 0.1),
                'top_p': app.config.get('LLM_CHAT_TOP_P', 0.1)
            },
            'reasoning': {
                'model': app.config.get('LLM_REASONING_MODEL'),
                'context': app.config.get('LLM_REASONING_MODEL_CONTEXT_WINDOW', 40960),
                'temperature': app.config.get('LLM_REASONING_TEMPERATURE', 0.7),
                'top_p': app.config.get('LLM_REASONING_TOP_P', 0.9)
            }
        }
        
        self.check_availability()
        
        if self.available:
            self.logger.info("BaseModule инициализирован и доступен")
        else:
            self.logger.warning("BaseModule инициализирован, но Ollama недоступна")
    
    def check_availability(self):
        """Проверка доступности модуля"""
        if not self.ollama_url:
            self.logger.error("OLLAMA_URL не настроен")
            return False
        
        try:
            self.logger.info(f"Проверка подключения к Ollama по адресу: {self.ollama_url}")
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get('models', [])
                available_models = [m['name'] for m in models]
                
                chat_model = self.models_config['chat']['model']
                reasoning_model = self.models_config['reasoning']['model']
                
                self.logger.info(f"Доступные модели в Ollama: {available_models}")
                
                if chat_model not in available_models:
                    self.logger.warning(f"Модель чата {chat_model} не найдена в Ollama")
                
                if reasoning_model not in available_models:
                    self.logger.warning(f"Рассуждающая модель {reasoning_model} не найдена в Ollama")
                
                self.available = True
                return True
            else:
                self.logger.error(f"Ollama вернула статус {response.status_code}")
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Ошибка подключения к Ollama по адресу {self.ollama_url}")
        except Exception as e:
            self.logger.error(f"Ошибка подключения к Ollama: {str(e)}")
        
        self.available = False
        return False
    
    def call_ollama(self, messages, model_type='chat', stream=False):
        """Вызов Ollama API с поддержкой повтора при ошибках"""
        return self.call_with_retry(self._call_ollama_impl, messages, model_type, stream)
    
    def _call_ollama_impl(self, messages, model_type='chat', stream=False):
        """Внутренняя реализация вызова Ollama API"""
        if not self.available:
            self.check_availability()
            if not self.available:
                return "⚠️ Сервис Ollama недоступен"
        
        model_config = self.models_config.get(model_type, self.models_config['chat'])
        model = model_config['model']
        
        if not model:
            return f"⚠️ Модель для {model_type} не настроена"
        
        try:
            payload = {
                'model': model,
                'messages': messages,
                'stream': stream,
                'options': {
                    'num_ctx': model_config['context'],
                    'temperature': model_config['temperature'],
                    'top_p': model_config['top_p'],
                    'stop': ['<|im_end|>', '<|endoftext|>', '\n\n\n'],
                }
            }
            
            # Оптимизация памяти: выгружаем модель сразу после использования
            # Если это не частая интерактивная беседа
            if model_type != 'chat' or self.consecutive_errors > 2:
                payload['keep_alive'] = '0'  # Выгрузить сразу
            else:
                payload['keep_alive'] = '30s'  # Держать 30 секунд для чата
            
            self.logger.info(f"Отправка запроса к Ollama. Модель: {model}")
            
            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=120
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result['message']['content']
                
                for stop_token in ['<|endoftext|>', '<|im_end|>']:
                    if stop_token in content:
                        content = content[:content.index(stop_token)]
                
                if model_type == 'chat' and model_config['temperature'] < 0.3:
                    content = content.split('\n')[0].strip()
                
                return content.strip()
            else:
                error_msg = f"Ollama error: {response.status_code}"
                self.logger.error(error_msg)
                return f"⚠️ Ошибка Ollama: {response.status_code}"
                
        except requests.exceptions.ConnectionError:
            self.logger.error("Ошибка подключения к Ollama")
            return "⚠️ Не удалось подключиться к Ollama"
        except Exception as e:
            self.logger.error(f"Error calling Ollama: {str(e)}")
            return f"⚠️ Ошибка: {str(e)}"
    
    def process_message(self, message_text, current_time_str):
        """Обработка текстового сообщения через модель-маршрутизатор"""
        from app import format_prompt
        
        prompt = format_prompt('base_text.template', {
            'current_time_str': current_time_str,
            'user_query': message_text
        })
        
        if not prompt:
            self.logger.error("Ошибка загрузки шаблона промпта")
            return {'error': 'Ошибка загрузки шаблона промпта'}
        
        router_messages = [
            {
                'role': 'system',
                'content': 'Ты - маршрутизатор запросов. Отвечай ТОЛЬКО одной строкой на русском языке. Никаких пояснений.'
            },
            {'role': 'user', 'content': prompt}
        ]
        
        self.logger.info(f"Отправка запроса к маршрутизатору: {message_text}")
        router_response = self.call_ollama(router_messages, model_type='chat')
        self.logger.info(f"Ответ маршрутизатора: {router_response}")
        
        return self._parse_router_response(router_response, message_text, current_time_str)
    
    def _parse_router_response(self, response, original_query, current_time_str):
        """Парсинг ответа маршрутизатора"""
        response = response.strip()
        
        markers = {
            '[-IMAGE-]': 'image',
            '[-CAMERA-]': 'camera',
            '[-REASONING-]': 'reasoning'
        }
        
        for marker, action in markers.items():
            if marker in response:
                parts = response.split(marker, 1)
                processed = parts[1].strip() if len(parts) > 1 else ""
                
                if action == 'reasoning':
                    return {
                        'action': action,
                        'query': processed,
                        'needs_reasoning': True
                    }
                else:
                    return {
                        'action': action,
                        'query': processed,
                        'needs_reasoning': False
                    }
        
        return {
            'action': 'none',
            'query': response,
            'needs_reasoning': False
        }
    
    def process_reasoning(self, query, current_time_str):
        """Обработка сложного запроса через reasoning модель"""
        from app import format_prompt
        
        reasoning_prompt = format_prompt('reasoning.template', {
            'current_time_str': current_time_str,
            'reasoning_query': query
        })
        
        if not reasoning_prompt:
            return "⚠️ Ошибка загрузки шаблона для сложного запроса"
        
        self.logger.info(f"Отправка запроса к reasoning модели: {query}")
        response = self.call_ollama(
            [{'role': 'user', 'content': reasoning_prompt}],
            model_type='reasoning'
        )
        self.logger.info(f"Ответ reasoning модели: {response[:100]}...")
        
        return response