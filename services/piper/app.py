# services/piper/app.py
#!/usr/bin/env python3
import os
import io
import wave
import logging
import tempfile
from flask import Flask, request, jsonify, send_file
from piper import PiperVoice
from pydub import AudioSegment

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

MODEL_DIR = os.environ.get('PIPER_MODEL_DIR', '/app/models')
os.makedirs(MODEL_DIR, exist_ok=True)

# Cache for loaded voices
voices = {}

# Voice mapping: (language, gender) -> model prefix (without extension)
voice_map = {
    ('ru', 'male'):   'ru_RU-dmitri-medium',
    ('ru', 'female'): 'ru_RU-irina-medium',
    ('en', 'male'):   'en_US-ryan-medium',
    ('en', 'female'): 'en_US-ljspeech-medium',
}

def get_voice_path(language, gender):
    """Return the full path to the .onnx model file for the given language and gender."""
    key = (language, gender)
    if key not in voice_map:
        # Fallback to male voice of the same language
        app.logger.warning(f"Voice for {language}/{gender} not found, falling back to male")
        key = (language, 'male')
        if key not in voice_map:
            raise ValueError(f"No voice available for language {language}")
    model_prefix = voice_map[key]
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
    gender = data.get('gender', 'male')
    
    try:
        model_path = get_voice_path(language, gender)
        
        # Load voice (cached by model path)
        if model_path not in voices:
            voices[model_path] = PiperVoice.load(model_path, use_cuda=False)
        voice = voices[model_path]
        
        # Create temporary WAV file path
        wav_fd, wav_path = tempfile.mkstemp(suffix='.wav')
        os.close(wav_fd)  # Close FD, we only need the path
        
        try:
            # Open WAV file with wave module and synthesize
            with wave.open(wav_path, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(voice.config.sample_rate)
                voice.synthesize(text, wav_file)
            
            # Convert WAV to MP3 using pydub
            audio = AudioSegment.from_wav(wav_path)
            mp3_buffer = io.BytesIO()
            audio.export(mp3_buffer, format='mp3')
            mp3_buffer.seek(0)
            
            return send_file(
                mp3_buffer,
                mimetype='audio/mpeg',
                as_attachment=False,
                download_name='speech.mp3'
            )
        finally:
            # Clean up temporary WAV file
            if os.path.exists(wav_path):
                os.remove(wav_path)
                
    except FileNotFoundError as e:
        app.logger.error(f"Model not found: {str(e)}")
        return jsonify({'error': f'Voice model for language {language} and gender {gender} not found'}), 404
    except Exception as e:
        app.logger.error(f"TTS synthesis error: {str(e)}", exc_info=True)
        return jsonify({'error': 'TTS synthesis failed'}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8888, debug=False)