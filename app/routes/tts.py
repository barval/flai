# app/routes/tts.py
import io

from flask import Blueprint, current_app, jsonify, request, send_file, session
from flask_babel import gettext as _

bp = Blueprint("tts", __name__, url_prefix="/api/tts")


@bp.route("/synthesize", methods=["POST"])
def synthesize():
    if "login" not in session:
        return jsonify({"error": _("Not authorized")}), 401

    data = request.get_json()
    if not data or "text" not in data:
        return jsonify({"error": _("Missing text")}), 400

    text = data["text"]
    lang = data.get("lang") or session.get("language", "ru")
    gender = data.get("gender") or session.get("voice_gender", "male")
    voice = data.get("voice")

    tts_module = current_app.modules.get("tts")
    if not tts_module or not tts_module.available:
        return jsonify({"error": _("TTS service unavailable")}), 503

    audio_bytes, mime_type = tts_module.synthesize(text, lang, gender, voice=voice)
    if audio_bytes is None:
        return jsonify({"error": _("TTS synthesis failed")}), 500

    return send_file(io.BytesIO(audio_bytes), mimetype=mime_type, as_attachment=False, download_name="speech.mp3")
