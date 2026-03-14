    def _call_multimodal(self, messages, lang='ru'):
        """Call multimodal model with configurable timeout"""
        if not self.available:
            return self._('Multimodal model unavailable', lang)
        
        # Determine model config - use dynamic if available
        model_config = None
        if current_app and current_app.config.get('MODEL_CONFIGS'):
            db_config = current_app.config['MODEL_CONFIGS'].get('multimodal')
            if db_config:
                model_config = {
                    'model': db_config.get('model_name') or self.models_config['multimodal']['model'],
                    'context': db_config.get('context_length') or self.models_config['multimodal']['context'],
                    'temperature': db_config.get('temperature') or self.models_config['multimodal']['temperature'],
                    'top_p': db_config.get('top_p') or self.models_config['multimodal']['top_p'],
                    'timeout': db_config.get('timeout') or self.models_config['multimodal']['timeout'],
                }
        if not model_config:
            model_config = self.models_config['multimodal']
        
        model = model_config['model']
        timeout = model_config.get('timeout', 120)
        
        try:
            payload = {
                'model': model,
                'messages': messages,
                'stream': False,
                'options': {
                    'num_ctx': model_config['context'],
                    'temperature': model_config['temperature'],
                    'top_p': model_config['top_p'],
                }
            }
            
            self.logger.info(f"Sending request to multimodal model: {model}, timeout: {timeout}s")
            
            response = requests.post(
                f"{self.ollama_url}/api/chat",
                json=payload,
                timeout=timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                return result['message']['content'].strip()
            else:
                self.logger.error(f"Multimodal model error: {response.status_code}")
                return f"{self._('Error', lang)}: {response.status_code}"
                
        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout ({timeout}s) for multimodal model")
            template = self._('Timeout ({timeout}s) when calling multimodal model', lang)
            return template.format(timeout=timeout)
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to Ollama at {self.ollama_url}")
            return self._('Could not connect to Ollama', lang)
        except Exception as e:
            self.logger.error(f"Error calling multimodal model: {str(e)}")
            return f"{self._('Error', lang)}: {str(e)}"