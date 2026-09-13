import contextlib
import gc
import io
import logging
import multiprocessing as mp
import os
import queue
import signal
import sys
import threading
import time
import traceback

# Set cache dirs to writable locations before any imports
os.environ.setdefault("HF_HOME", "/app/hf-cache")
os.environ.setdefault("RUACCENT_CACHE_DIR", "/app/hf-cache/ruaccent")
os.environ.setdefault("XDG_CACHE_HOME", "/app/cache")

from flask import Flask, jsonify, request, send_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

MODELS_DIR = os.environ.get("KOKORO_MODEL_DIR", "/app/models")
VOICES_DIR = os.environ.get("KOKORO_VOICES_DIR", "/app/voices")

# G2P: en (misaki espeak) is light (~90 MiB) and cached in the Flask process.
# ru (RUAccent) holds ~3.1 GiB, so it lives in a dedicated worker and is fully
# unloaded (process terminated) after idle, returning the memory to the OS.
_g2p_cache = {}
_en_g2p_lock = threading.Lock()

_g2p_worker_lock = threading.RLock()
_g2p_queue = None
_g2p_proc = None
G2P_IDLE_TIMEOUT = 300  # seconds without a ru request before unloading RUAccent
_last_ru_g2p_use = 0.0


def _get_en_g2p():
    """misaki espeak for English — light, safe to keep in the Flask process."""
    with _en_g2p_lock:
        if "en" in _g2p_cache:
            return _g2p_cache["en"]
        from misaki import espeak as misaki_espeak

        g2p = misaki_espeak.EspeakG2P(language="en-us")
        _g2p_cache["en"] = g2p
        return g2p


# ---------- RUAccent worker (Russian G2P) ----------
# RUAccent's ONNX models + koziev + dictionaries weigh ~3.1 GiB. It loads once
# per ru request and is terminated after G2P_IDLE_TIMEOUT of TTS inactivity, so
# idle memory drops to the torch worker + Flask only. First ru phrase after a
# cold start pays the ~10 s init; subsequent ones render instantly.


def _g2p_worker_main(req_queue):
    """Long-lived child process holding RuG2P. Processes (conn, text) tasks."""
    g2p = None
    try:
        while True:
            task = req_queue.get()
            if task is None:
                break
            conn, text = task
            try:
                if g2p is None:
                    from ru_g2p import RuG2P

                    t0 = time.time()
                    g2p = RuG2P(espeak_data="/app/espeak-data")
                    logger.info(f"G2P worker loaded RuG2P in {time.time() - t0:.1f}s")
                ipa, oov = g2p(text)
                conn.send(("ok", ipa, oov))
            except Exception as e:
                logger.error(f"G2P worker task error: {e}\n{traceback.format_exc()}")
                with contextlib.suppress(Exception):
                    conn.send(("error", str(e)))
            finally:
                with contextlib.suppress(Exception):
                    conn.close()
    except Exception as e:
        logger.error(f"G2P worker crashed: {e}\n{traceback.format_exc()}")


def _drain_g2p_queue(q):
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break
        with contextlib.suppress(Exception):
            item[0].close()


def _start_g2p_worker():
    global _g2p_proc, _g2p_queue, _last_ru_g2p_use
    with _g2p_worker_lock:
        # A fresh worker needs time to load RuG2P (~10 s) before the first reply;
        # mark it active immediately so the idle sweeper does not kill it mid-boot.
        _last_ru_g2p_use = time.time()
        if _g2p_queue is not None:
            _drain_g2p_queue(_g2p_queue)
        _g2p_queue = mp.Queue()
        _g2p_proc = mp.Process(target=_g2p_worker_main, args=(_g2p_queue,))
        _g2p_proc.start()
        logger.info("G2P worker started")


def _ensure_g2p_worker():
    with _g2p_worker_lock:
        if _g2p_proc is None or not _g2p_proc.is_alive():
            _start_g2p_worker()
            return False
        return True


def _phonemize_ru(text, timeout=120):
    """Queue a ru text to the G2P worker and wait for (ipa, oov)."""
    global _last_ru_g2p_use
    _last_ru_g2p_use = time.time()
    _ensure_g2p_worker()
    parent_conn, child_conn = mp.Pipe(duplex=False)
    # Do NOT close child_conn before the reply — mp.Queue serializes in a
    # background feeder thread, racing the close (OSError: handle closed).
    with _g2p_worker_lock:
        _g2p_queue.put((child_conn, text))

    try:
        if parent_conn.poll(timeout):
            try:
                status, *data = parent_conn.recv()
            except EOFError:
                raise RuntimeError("G2P worker died during phonemization") from None
            if status == "ok":
                _last_ru_g2p_use = time.time()
                return data[0], data[1]
            raise RuntimeError(data[0])
        raise TimeoutError(f"Phonemization timed out after {timeout}s")
    finally:
        parent_conn.close()
        child_conn.close()


def _g2p_idle_sweeper():
    """Terminate the RUAccent worker after G2P_IDLE_TIMEOUT of TTS inactivity."""
    global _g2p_proc
    while True:
        time.sleep(30)
        with _g2p_worker_lock:
            if _g2p_proc is None or not _g2p_proc.is_alive():
                continue
        if time.time() - _last_ru_g2p_use < G2P_IDLE_TIMEOUT:
            continue
        logger.info(f"G2P worker idle; terminating to release {G2P_IDLE_TIMEOUT}s-cold RUAccent")
        with _g2p_worker_lock:
            proc, _g2p_proc = _g2p_proc, None
            _drain_g2p_queue(_g2p_queue)
            proc.terminate()
            proc.join(timeout=10)
        logger.info("G2P worker terminated")


# Voice configuration
# (lang, voice_name) -> (model_path, voice_pack_path, g2p_lang)
VOICE_CONFIG = {
    ("ru", "sveta"): ("kokoro-ru-v2-base.pth", "sveta.pt", "ru"),
    ("ru", "dima"): ("kokoro-ru-v2-dima.pth", "dima.pt", "ru"),
    ("en", "af_heart"): ("kokoro-v1_0.pth", "af_heart.pt", "en"),
    ("en", "am_liam"): ("kokoro-v1_0.pth", "am_liam.pt", "en"),
}


# ---------- Long-lived worker for torch inference ----------
# Torch can segfault during model inference (e.g. dima model). Generation runs
# in a dedicated child process so the Flask server stays alive. Unlike the old
# subprocess-per-request approach, the worker keeps its model loaded in memory
# between requests: model load (~1.5s) happens once per voice, not once per
# sentence. Switching voices unloads the current model and loads the new one.

_request_queue = None
_worker_proc = None
_worker_lock = threading.Lock()


def _load_model(model_name):
    """Load a KModel fresh in the worker process."""
    from kokoro import KModel

    model_path = os.path.join(MODELS_DIR, model_name)
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model not found: {model_path}")

    config_path = "/app/hexgrad-config.json" if model_name == "kokoro-v1_0.pth" else "/app/config.json"
    size_mb = os.path.getsize(model_path) // 1024 // 1024
    logger.info(f"Worker loading model: {model_name} ({size_mb} MB)")
    t0 = time.time()
    model = KModel(config=config_path, model=model_path).eval()
    logger.info(f"Worker model {model_name} loaded in {time.time() - t0:.2f}s")
    return model


def _worker_main(req_queue, preload_model=None):
    """Long-lived child process. Holds the current model and voice packs in
    memory; processes tasks from req_queue one at a time."""
    current_model = None
    current_model_name = None
    pack_cache = {}
    try:
        if preload_model:
            current_model = _load_model(preload_model)
            current_model_name = preload_model
            logger.info(f"Worker preloaded model: {preload_model}")

        while True:
            task = req_queue.get()
            if task is None:
                break
            conn, model_name, pack_path, ipa_str, speed = task
            try:
                import soundfile as sf
                import torch

                # Reload model on voice switch
                if model_name != current_model_name:
                    if current_model is not None:
                        del current_model
                        gc.collect()
                    current_model = _load_model(model_name)
                    current_model_name = model_name

                if pack_path not in pack_cache:
                    pack_cache[pack_path] = torch.load(pack_path, map_location="cpu", weights_only=True)
                pack = pack_cache[pack_path]
                style = pack[len(ipa_str) - 1]

                t0 = time.time()
                with torch.no_grad():
                    audio = current_model(ipa_str, style, speed, return_output=True).audio
                elapsed = time.time() - t0
                logger.info(
                    f"Worker generated {len(audio)} samples in {elapsed:.2f}s (RTF={elapsed / (len(audio) / 24000):.3f})"
                )

                buf = io.BytesIO()
                sf.write(buf, audio.cpu().numpy(), 24000, format="WAV")
                conn.send(("ok", buf.getvalue()))
            except Exception as e:
                logger.error(f"Worker task error: {e}\n{traceback.format_exc()}")
                with contextlib.suppress(Exception):
                    conn.send(("error", str(e)))
            finally:
                with contextlib.suppress(Exception):
                    conn.close()
    except Exception as e:
        logger.error(f"Worker crashed: {e}\n{traceback.format_exc()}")


def _drain_queue(q):
    """Drop stale tasks (conn objects) left after a worker crash."""
    while True:
        try:
            item = q.get_nowait()
        except queue.Empty:
            break
        with contextlib.suppress(Exception):
            item[0].close()


def _start_worker(preload_model=None):
    global _worker_proc, _request_queue
    with _worker_lock:
        if _request_queue is not None:
            _drain_queue(_request_queue)
        _request_queue = mp.Queue()
        _worker_proc = mp.Process(target=_worker_main, args=(_request_queue, preload_model))
        _worker_proc.start()
        logger.info(f"Kokoro worker started (preload={preload_model})")


def _ensure_worker():
    with _worker_lock:
        if _worker_proc is None or not _worker_proc.is_alive():
            _start_worker()
            return False
        return True


def _generate_in_worker(model_name, pack_path, ipa_str, speed, timeout=60):
    """Queue a task for the long-lived worker and wait for a WAV reply."""
    _ensure_worker()
    parent_conn, child_conn = mp.Pipe(duplex=False)
    # NOTE: child_conn must NOT be closed here — mp.Queue serializes items in a
    # background feeder thread, so closing it right after put() races the pickle
    # of the pipe fd (OSError: handle is closed). It is closed after the reply.
    with _worker_lock:
        _request_queue.put((child_conn, model_name, pack_path, ipa_str, speed))

    try:
        if parent_conn.poll(timeout):
            try:
                status, data = parent_conn.recv()
            except EOFError:
                raise RuntimeError("TTS worker died during generation") from None
            if status == "ok":
                return data
            raise RuntimeError(data)
        raise TimeoutError(f"Generation timed out after {timeout}s")
    finally:
        parent_conn.close()
        child_conn.close()


@app.route("/tts", methods=["POST"])
def synthesize():
    data = request.get_json()
    if not data or "text" not in data:
        return jsonify({"error": "Missing text"}), 400

    text = data["text"]
    language = data.get("language", "en")
    voice = data.get("voice", "sveta" if language == "ru" else "af_heart")
    speed = float(data.get("speed", 1.0))

    # Resolve voice config
    key = (language, voice)
    if key not in VOICE_CONFIG:
        # Fallback: try first voice for this language
        lang_voices = [k for k in VOICE_CONFIG if k[0] == language]
        if not lang_voices:
            return jsonify({"error": f"No voice available for language {language}"}), 404
        key = lang_voices[0]
        language, voice = key

    model_name, pack_name, g2p_lang = VOICE_CONFIG[key]
    pack_path = os.path.join(VOICES_DIR, pack_name)

    if not os.path.exists(pack_path):
        return jsonify({"error": f"Voice pack not found: {pack_name}"}), 404

    try:
        # Phonemize text (ru via dedicated RUAccent worker, en in-process)
        if g2p_lang == "ru":
            ipa, oov = _phonemize_ru(text)
        else:
            g2p = _get_en_g2p()
            ipa, _ = g2p(text)

        logger.info(f"TTS request: lang={language} voice={voice} text_len={len(text)} ipa_len={len(ipa)}")

        # Generate audio in the long-lived worker (survives torch segfaults)
        t0 = time.time()
        wav_bytes = _generate_in_worker(model_name, pack_path, ipa, speed, timeout=60)
        elapsed = time.time() - t0
        logger.info(f"TTS ({voice}) completed in {elapsed:.2f}s")

        buf = io.BytesIO(wav_bytes)
        return send_file(buf, mimetype="audio/wav", as_attachment=False, download_name="speech.wav")

    except FileNotFoundError as e:
        logger.error(f"Model/voice not found: {str(e)}")
        return jsonify({"error": f"Voice {voice} not found"}), 404
    except TimeoutError as e:
        logger.error(f"TTS generation timed out: {e}")
        return jsonify({"error": "TTS generation timed out"}), 504
    except RuntimeError as e:
        logger.error(f"TTS generation failed in worker: {e}")
        return jsonify({"error": "TTS synthesis failed"}), 500
    except Exception as e:
        logger.error(f"Kokoro synthesis error: {str(e)}\n{traceback.format_exc()}")
        return jsonify({"error": "TTS synthesis failed"}), 500


@app.route("/voices", methods=["GET"])
def list_voices():
    voices = []
    for (lang, name), (model, pack, _g2p) in VOICE_CONFIG.items():
        voices.append(
            {
                "language": lang,
                "name": name,
                "model": model,
                "pack": pack,
                "gender": "male" if name == "dima" or name.startswith("am_") else "female",
            }
        )
    return jsonify(voices)


@app.route("/health", methods=["GET"])
def health():
    with _g2p_worker_lock:
        g2p_alive = _g2p_proc is not None and _g2p_proc.is_alive()
    alive = _worker_proc is not None and _worker_proc.is_alive()
    return jsonify({"status": "ok", "worker_alive": alive, "g2p_alive": g2p_alive})


if __name__ == "__main__":
    # Log fatal signals before crash for diagnostics
    def _fatal_signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.critical(f"Received {sig_name} — likely torch segfault. Dumping stack...")
        traceback.print_stack(frame)
        sys.exit(128 + signum)

    for sig in (signal.SIGSEGV, signal.SIGABRT, signal.SIGBUS):
        signal.signal(sig, _fatal_signal_handler)

    # Start the long-lived worker FIRST (fork happens before anything heavy loads,
    # so the worker does not inherit RUAccent's large memory footprint). The worker
    # pre-loads the base model (sveta) so the first request renders instantly.
    # The dima model is heavier and is loaded in the worker on demand when
    # switching voices, keeping peak memory inside the container budget.
    try:
        _start_worker(preload_model="kokoro-ru-v2-base.pth")
    except Exception as e:
        logger.warning(f"Could not start worker: {e}")

    # RUAccent (ru G2P) is NOT preloaded — it is created lazily in its own
    # worker on the first ru request (~10 s, once) and terminated after
    # G2P_IDLE_TIMEOUT of TTS inactivity, so idle RAM stays ~2 GiB.
    # The sweeper prunes the idled worker every 30 s.
    sweeper = threading.Thread(target=_g2p_idle_sweeper, daemon=True)
    sweeper.start()

    app.run(host="0.0.0.0", port=8888, debug=False, threaded=True)
