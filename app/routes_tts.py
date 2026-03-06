# app/routes_tts.py
from flask import Blueprint, request, jsonify, session, current_app, send_file
import io
from flask_babel import gettext as _

bp = Blueprint('tts', __name__, url_prefix='/api/tts')

@bp.route('/synthesize', methods=['POST'])
def synthesize():
    if 'login' not in session:
        return jsonify({'error': _('Not authorized')}), 401

    data = request.get_json()
    if not data or 'text' not in data:
        return jsonify({'error': _('Missing text')}), 400

    text = data['text']
    lang = data.get('lang', session.get('language', 'ru'))
    gender = data.get('gender', session.get('voice_gender', 'male'))

    tts_module = current_app.modules.get('tts')
    if not tts_module or not tts_module.available:
        return jsonify({'error': _('TTS service unavailable')}), 503

    audio_bytes = tts_module.synthesize(text, lang, gender)
    if audio_bytes is None:
        return jsonify({'error': _('TTS synthesis failed')}), 500

    return send_file(
        io.BytesIO(audio_bytes),
        mimetype='audio/mpeg',
        as_attachment=False,
        download_name='speech.mp3'
    )