import os
import io
import logging
import tempfile
from flask import Flask, request, jsonify, send_file
from piper import PiperVoice
from pydub import AudioSegment

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Directory where voice models are mounted (set via environment variable or default)
MODEL_DIR = os.environ.get('PIPER_MODEL_DIR', '/app/models')
os.makedirs(MODEL_DIR, exist_ok=True)

# Cache for loaded voices (dictionary: voice_id -> PiperVoice instance)
voices = {}

def get_voice_path(language):
    """
    Return the full path to the .onnx model file for the given language.
    Expected naming convention: <lang_code>-<speaker>-<quality>.onnx
    (e.g., ru_RU-ruslan-medium.onnx)
    The corresponding .json file must be in the same directory.
    """
    # Mapping from language code to expected model file prefix
    # You can customize this mapping or make it configurable.
    lang_to_model = {
        'ru': 'ru_RU-ruslan-medium',
        'en': 'en_US-bryce-medium'
    }
    if language not in lang_to_model:
        app.logger.warning(f"Language '{language}' not found in mapping, falling back to English.")
        language = 'en'
    model_prefix = lang_to_model[language]
    onnx_path = os.path.join(MODEL_DIR, model_prefix + '.onnx')
    json_path = os.path.join(MODEL_DIR, model_prefix + '.onnx.json')
    
    if not os.path.exists(onnx_path):
        raise FileNotFoundError(f"Model file not found: {onnx_path}")
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"Model config file not found: {json_path}")
    
    return onnx_path

@app.route('/tts', methods=['POST'])
def synthesize():
    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': 'Missing text'}), 400

    text = data['text']
    language = data.get('language', 'en')

    try:
        # Determine the full path to the model file
        model_path = get_voice_path(language)
        
        # Load voice (cached by model path)
        if model_path not in voices:
            voices[model_path] = PiperVoice.load(model_path, use_cuda=False)
        voice = voices[model_path]

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
    except FileNotFoundError as e:
        app.logger.error(f"Model not found: {str(e)}")
        return jsonify({'error': f'Voice model for language {language} not found. Please ensure the model files are placed in the mounted directory.'}), 404
    except Exception as e:
        app.logger.error(f"TTS synthesis error: {str(e)}")
        return jsonify({'error': 'TTS synthesis failed'}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888, debug=False)