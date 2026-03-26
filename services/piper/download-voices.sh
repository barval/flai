
### `download-voices.sh`

```bash
#!/bin/bash
# services/piper/download-voices.sh
# Download Piper TTS voice models

set -e

MODELS_DIR="./piper_models"
mkdir -p "$MODELS_DIR"

echo "Downloading Russian voices..."

# Russian male voice
echo "Downloading ru_RU-dmitri-medium..."
curl -L -o "$MODELS_DIR/ru_RU-dmitri-medium.onnx" \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx
curl -L -o "$MODELS_DIR/ru_RU-dmitri-medium.onnx.json" \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/dmitri/medium/ru_RU-dmitri-medium.onnx.json

# Russian female voice
echo "Downloading ru_RU-irina-medium..."
curl -L -o "$MODELS_DIR/ru_RU-irina-medium.onnx" \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx
curl -L -o "$MODELS_DIR/ru_RU-irina-medium.onnx.json" \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json

echo "Downloading English voices..."

# English male voice
echo "Downloading en_US-ryan-medium..."
curl -L -o "$MODELS_DIR/en_US-ryan-medium.onnx" \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx
curl -L -o "$MODELS_DIR/en_US-ryan-medium.onnx.json" \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json

# English female voice
echo "Downloading en_US-ljspeech-medium..."
curl -L -o "$MODELS_DIR/en_US-ljspeech-medium.onnx" \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx
curl -L -o "$MODELS_DIR/en_US-ljspeech-medium.onnx.json" \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx.json

echo "Download complete!"
ls -la "$MODELS_DIR"