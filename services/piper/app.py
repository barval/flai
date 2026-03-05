import os
import io
import logging
import tempfile
from flask import Flask, request, jsonify, send_file
from piper import PiperVoice
from pydub import AudioSegment

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Directory to store downloaded models
MODEL_DIR = "/app/models"
os.makedirs(MODEL_DIR, exist_ok=True)
os.environ['PIPER_CACHE_DIR'] = MODEL_DIR

# Cache for loaded voices
voices = {}

def get_model_path(language):
    """Return the Piper model name for the given language."""
    # Map language codes to Piper model names
    lang_map = {
        'ru': 'ru_RU',
        'en': 'en_US'
    }
    if language not in lang_map:
        language = 'en'  # fallback
    return lang_map[language]

@app.route('/tts', methods=['POST'])
def synthesize():
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'Missing text'}), 400

    text = data['text']
    language = data.get('language', 'en')

    try:
        model_name = get_model_path(language)
        # Load voice (cached)
        if model_name not in voices:
            # PiperVoice.load will download if not cached
            voices[model_name] = PiperVoice.load(model_name, use_cuda=False)
        voice = voices[model_name]

        # Synthesize to WAV in memory
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=True) as f:
            voice.synthesize(text, f)
            f.flush()
            # Convert WAV to MP3 using pydub
            audio = AudioSegment.from_wav(f.name)
            mp3_buffer = io.BytesIO()
            audio.export(mp3_buffer, format='mp3')
            mp3_buffer.seek(0)

        return send_file(
            mp3_buffer,
            mimetype='audio/mpeg',
            as_attachment=False,
            download_name='speech.mp3'
        )
    except Exception as e:
        app.logger.error(f"TTS synthesis error: {str(e)}")
        return jsonify({'error': 'TTS synthesis failed'}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888, debug=False)