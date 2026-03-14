    def _get_embedding(self, text):
        """Get embedding vector from Ollama."""
        ollama_url = current_app.config.get('OLLAMA_URL')
        # Use dynamic embedding model if available
        embedding_model = self.embedding_model
        if current_app and current_app.config.get('MODEL_CONFIGS'):
            db_config = current_app.config['MODEL_CONFIGS'].get('embedding')
            if db_config and db_config.get('model_name'):
                embedding_model = db_config['model_name']
        try:
            response = requests.post(
                f"{ollama_url}/api/embeddings",
                json={"model": embedding_model, "prompt": text}
            )
            if response.status_code == 200:
                return response.json()["embedding"]
            else:
                self.logger.error(f"Ollama embedding error: {response.text}")
                return None
        except Exception as e:
            self.logger.error(f"Error getting embedding: {e}")
            return None