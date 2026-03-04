# modules/base.py
import logging
import requests
from datetime import datetime
import os

from app.utils import format_prompt

class BaseModule:
    """Base module for chat and reasoning model interactions"""
    
    def __init__(self, app=None, ollama_url=None, models_config=None):
        self.logger = logging.getLogger(__name__)
        self.ollama_url = ollama_url
        self.models_config = models_config or {}
        self.available = False
        self.timeouts = {}
        # Internal message translations
        self.messages = {
            'ru': {
                'ollama_unavailable': '⚠️ Сервис Ollama недоступен',
                'model_not_configured': '⚠️ Модель для {model_type} не настроена',
                'timeout': '⚠️ Превышено время ожидания ответа от модели ({timeout}с). Попробуйте увеличить таймаут в .env или упростите запрос.',
                'connection_error': '⚠️ Не удалось подключиться к Ollama',
                'error_prefix': '⚠️ Ошибка',
                'prompt_load_error': 'Ошибка загрузки шаблона промпта',
                'no_action': 'Не удалось определить действие'
            },
            'en': {
                'ollama_unavailable': '⚠️ Ollama service unavailable',
                'model_not_configured': '⚠️ Model for {model_type} not configured',
                'timeout': '⚠️ Timeout ({timeout}s) when calling the model. Try increasing timeout in .env or simplify your request.',
                'connection_error': '⚠️ Could not connect to Ollama',
                'error_prefix': '⚠️ Error',
                'prompt_load_error': 'Error loading prompt template',
                'no_action': 'Could not determine action'
            }
        }
        
        if app:
            self.init_app(app)
        elif ollama_url:
            self.check_availability()
    
    def get_message(self, key, lang='ru', **kwargs):
        """Get translated message with optional formatting."""
        msg_dict = self.messages.get(lang, self.messages['ru'])
        msg = msg_dict.get(key, key)
        if kwargs:
            try:
                return msg.format(**kwargs)
            except KeyError:
                return msg
        return msg
    
    def init_app(self, app):
        """Initialize module with Flask app"""
        self.ollama_url = app.config.get('OLLAMA_URL')
        
        self.timeouts = {
            'chat': app.config.get('LLM_CHAT_TIMEOUT', 60),
            'multimodal': app.config.get('LLM_MULTIMODAL_TIMEOUT', 120),
            'reasoning': app.config.get('LLM_REASONING_TIMEOUT', 300)
        }
        
        self.models_config = {
            'chat': {
                'model': app.config.get('LLM_CHAT_MODEL'),
                'context': app.config.get('LLM_CHAT_MODEL_CONTEXT_WINDOW', 32768),
                'temperature': app.config.get('LLM_CHAT_TEMPERATURE', 0.1),
                'top_p': app.config.get('LLM_CHAT_TOP_P', 0.1),
                'timeout': self.timeouts['chat']
            },
            'reasoning': {
                'model': app.config.get('LLM_REASONING_MODEL'),
                'context': app.config.get('LLM_REASONING_MODEL_CONTEXT_WINDOW', 40960),
                'temperature': app.config.get('LLM_REASONING_TEMPERATURE', 0.7),
                'top_p': app.config.get('LLM_REASONING_TOP_P', 0.9),
                'timeout': self.timeouts['reasoning']
            },
            'multimodal': {
                'model': app.config.get('LLM_MULTIMODAL_MODEL', app.config.get('LLM_MULTIMODAL_MODEL')),
                'context': app.config.get('LLM_MULTIMODAL_MODEL_CONTEXT_WINDOW', 32768),
                'temperature': app.config.get('LLM_MULTIMODAL_TEMPERATURE', 0.7),
                'top_p': app.config.get('LLM_MULTIMODAL_TOP_P', 0.9),
                'timeout': self.timeouts.get('multimodal', 120)
            }
        }
        
        self.check_availability()
        
        if self.available:
            self.logger.info(f"BaseModule initialized and available. Timeouts: {self.timeouts}")
        else:
            self.logger.warning("BaseModule initialized, but Ollama is unavailable")
    
    def check_availability(self):
        """Check module availability"""
        if not self.ollama_url:
            self.logger.error("OLLAMA_URL not configured")
            return False
        
        try:
            self.logger.info(f"Checking connection to Ollama at: {self.ollama_url}")
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get('models', [])
                available_models = [m['name'] for m in models]
                
                chat_model = self.models_config['chat']['model']
                reasoning_model = self.models_config['reasoning']['model']
                
                self.logger.info(f"Available models in Ollama: {available_models}")
                
                if chat_model not in available_models:
                    self.logger.warning(f"Chat model {chat_model} not found in Ollama")
                
                if reasoning_model not in available_models:
                    self.logger.warning(f"Reasoning model {reasoning_model} not found in Ollama")
                
                self.available = True
                return True
            else:
                self.logger.error(f"Ollama returned status {response.status_code}")
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to Ollama at {self.ollama_url}")
        except Exception as e:
            self.logger.error(f"Error connecting to Ollama: {str(e)}")
        
        self.available = False
        return False
    
    def call_ollama(self, messages, model_type='chat', stream=False, lang='ru'):
        """Call Ollama API with configurable timeout"""
        if not self.available:
            self.check_availability()
            if not self.available:
                return self.get_message('ollama_unavailable', lang)
        
        model_config = self.models_config.get(model_type, self.models_config['chat'])
        model = model_config['model']
        timeout = model_config.get('timeout', 60)
        
        if not model:
            return self.get_message('model_not_configured', lang, model_type=model_type)
        
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
            
            self.logger.info(f"Sending request to Ollama. Model: {model}, timeout: {timeout}s")
            
            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=timeout
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
                return f"{self.get_message('error_prefix', lang)}: {response.status_code}"
                
        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout ({timeout}s) when calling Ollama. Model: {model}")
            return self.get_message('timeout', lang, timeout=timeout)
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to Ollama at {self.ollama_url}")
            return self.get_message('connection_error', lang)
        except Exception as e:
            self.logger.error(f"Error calling Ollama: {str(e)}")
            return f"{self.get_message('error_prefix', lang)}: {str(e)}"
    
    def process_message(self, message_text, current_time_str, lang='ru'):
        """Process text message through router model"""
        response_language = 'Russian' if lang == 'ru' else 'English'
        prompt = format_prompt('base_text.template', {
            'current_time_str': current_time_str,
            'user_query': message_text,
            'response_language': response_language
        })
        
        if not prompt:
            self.logger.error("Error loading prompt template")
            return {'error': self.get_message('prompt_load_error', lang)}
        
        router_messages = [
            {
                'role': 'system',
                'content': 'You are a request router. Answer ONLY with one line in the specified language. No explanations.'
            },
            {'role': 'user', 'content': prompt}
        ]
        
        self.logger.info(f"Sending request to router: {message_text}")
        router_response = self.call_ollama(router_messages, model_type='chat', lang=lang)
        self.logger.info(f"Router response: {router_response}")
        
        return self._parse_router_response(router_response, message_text, current_time_str, lang)
    
    def _parse_router_response(self, response, original_query, current_time_str, lang='ru'):
        """Parse router response"""
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
    
    def process_reasoning(self, query, current_time_str, lang='ru'):
        """Process complex query via reasoning model"""
        response_language = 'Russian' if lang == 'ru' else 'English'
        reasoning_prompt = format_prompt('reasoning.template', {
            'current_time_str': current_time_str,
            'reasoning_query': query,
            'response_language': response_language
        })
        
        if not reasoning_prompt:
            return "⚠️ " + self.get_message('prompt_load_error', lang)
        
        self.logger.info(f"Sending request to reasoning model: {query}")
        response = self.call_ollama(
            [{'role': 'user', 'content': reasoning_prompt}],
            model_type='reasoning',
            lang=lang
        )
        self.logger.info(f"Reasoning model response: {response[:100]}...")
        
        return response