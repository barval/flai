    def call_ollama(self, messages, model_type='chat', stream=False, lang='ru'):
        """Call Ollama API with configurable timeout"""
        if not self.available:
            self.check_availability()
            if not self.available:
                return self._('Ollama service unavailable', lang)
        
        # Determine model config - use dynamic if available
        model_config = None
        if current_app and current_app.config.get('MODEL_CONFIGS'):
            db_config = current_app.config['MODEL_CONFIGS'].get(model_type)
            if db_config:
                model_config = {
                    'model': db_config.get('model_name') or self.models_config.get(model_type, {}).get('model'),
                    'context': db_config.get('context_length') or self.models_config.get(model_type, {}).get('context', 32768),
                    'temperature': db_config.get('temperature') or self.models_config.get(model_type, {}).get('temperature', 0.1),
                    'top_p': db_config.get('top_p') or self.models_config.get(model_type, {}).get('top_p', 0.1),
                    'timeout': db_config.get('timeout') or self.models_config.get(model_type, {}).get('timeout', 60),
                }
        if not model_config:
            model_config = self.models_config.get(model_type, self.models_config['chat'])
        
        model = model_config['model']
        timeout = model_config.get('timeout', 60)
        
        if not model:
            template = self._('Model for {model_type} not configured', lang)
            return template.format(model_type=model_type)
        
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
                return f"{self._('Error', lang)}: {response.status_code}"
                
        except requests.exceptions.Timeout:
            self.logger.error(f"Timeout ({timeout}s) when calling Ollama. Model: {model}")
            template = self._('Timeout ({timeout}s) when calling the model. Try increasing timeout in .env or simplify your request.', lang)
            return template.format(timeout=timeout)
        except requests.exceptions.ConnectionError:
            self.logger.error(f"Connection error to Ollama at {self.ollama_url}")
            return self._('Could not connect to Ollama', lang)
        except Exception as e:
            self.logger.error(f"Error calling Ollama: {str(e)}")
            return f"{self._('Error', lang)}: {str(e)}"