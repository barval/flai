# app/routes/model_hub.py
"""Model Hub admin API: search HF, estimate fit, download, progress, cancel."""

import logging

from flask import Blueprint, current_app, jsonify, request
from flask_babel import gettext as _

from app import model_hub
from app.model_config import get_model_config
from app.routes.admin import admin_required

bp = Blueprint("model_hub", __name__, url_prefix="/admin/api/hub")
logger = logging.getLogger(__name__)


def _block_text(reason: str) -> str:
    """Localized message for a DownloadBlocked.reason code.

    Built per call, not at import time: gettext() outside an app context
    returns the untranslated msgid, so a module-level map would always serve
    English to a Russian operator.
    """
    texts: dict[str, str] = {
        "already_downloading": _("Another download is already running"),
        "file_too_large": _("File exceeds the maximum allowed size (MODEL_HUB_MAX_FILE_GB)"),
        "no_disk_space": _("Not enough free disk space"),
        "already_present": _("This model is already in the models directory"),
        "gated": _("This model requires access on Hugging Face and cannot be downloaded automatically"),
        "bad_path": _("Invalid file path"),
        "not_found": _("File not found in the repository"),
        "unknown_arch": _("Model architecture not found in the repository (config.json missing)"),
        "hub_failed": _("Model Hub request failed. Try again later."),
    }
    return texts.get(reason, reason)


def _hub_err(reason: str, code: int = 400, extra: dict | None = None) -> tuple:
    text = _block_text(reason)
    return jsonify({"status": "error", "reason": reason, "error": f"⚠️ {text}", **(extra or {})}), code


def _hub_ok(payload: dict):
    return jsonify({"status": "ok", **payload})


@bp.route("/search")
@admin_required
def hub_search():
    q = (request.args.get("q") or "").strip()
    limit = min(int(request.args.get("limit", current_app.config.get("MODEL_HUB_SEARCH_LIMIT", 20))), 50)
    ctx_arg = (request.args.get("context") or "").strip()
    try:
        context_length = max(1024, int(ctx_arg))
    except ValueError:
        context_length = None
    try:
        items = model_hub.search_hf(q, limit, context_length=context_length)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"hub search failed: {exc}")
        return _hub_err("hub_failed")
    return _hub_ok({"items": items, "fits_computed": context_length is not None})


@bp.route("/fit")
@admin_required
def hub_fit():
    repo = (request.args.get("repo") or "").strip()
    file_path = (request.args.get("file") or "").strip()
    module = (request.args.get("module") or "multimodal").strip()
    if "/" not in repo or not file_path:
        return _hub_err("bad_path")
    cfg = get_model_config(module) or {}
    try:
        context_length = max(1024, int(request.args.get("context", "")))
    except ValueError:
        context_length = int(cfg.get("context_length", 8192))
    try:
        fit = model_hub.estimate_fit(repo, file_path, module=module, context_length=context_length)
    except model_hub.DownloadBlocked as exc:
        return _hub_err(exc.reason, extra={"module": module, "context_length": context_length})
    except Exception as exc:  # noqa: BLE001
        logger.error(f"hub fit failed: {exc}")
        return _hub_err("hub_failed")
    return _hub_ok({"fit": fit, "module": module, "context_length": context_length})


@bp.route("/fit-all")
@admin_required
def hub_fit_all():
    repo = (request.args.get("repo") or "").strip()
    module = (request.args.get("module") or "multimodal").strip()
    if "/" not in repo:
        return _hub_err("bad_path")
    cfg = get_model_config(module) or {}
    try:
        context_length = max(1024, int(request.args.get("context", "")))
    except ValueError:
        context_length = int(cfg.get("context_length", 8192))
    try:
        fits = model_hub.estimate_fits(repo, module=module, context_length=context_length)
    except model_hub.DownloadBlocked as exc:
        return _hub_err(exc.reason, extra={"module": module, "context_length": context_length})
    except Exception as exc:  # noqa: BLE001
        logger.error(f"hub fit-all failed: {exc}")
        return _hub_err("hub_failed")
    return _hub_ok({"fits": fits, "module": module, "context_length": context_length})


@bp.route("/download", methods=["POST"])
@admin_required
def hub_download():
    data = request.get_json(silent=True) or {}
    repo = (data.get("repo") or "").strip()
    file_path = (data.get("file") or "").strip()
    if "/" not in repo or not file_path:
        return _hub_err("bad_path")
    try:
        job_id = model_hub.start_download(repo, file_path)
    except model_hub.DownloadBlocked as exc:
        return _hub_err(exc.reason, code=409)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"hub download start failed: {exc}")
        return _hub_err("hub_failed")
    return _hub_ok({"job_id": job_id})


@bp.route("/progress/<job_id>")
@admin_required
def hub_progress(job_id: str):
    job = model_hub.get_job(job_id)
    if job is None:
        return _hub_err("not_found")
    return _hub_ok({"job": job})


@bp.route("/cancel/<job_id>", methods=["POST"])
@admin_required
def hub_cancel(job_id: str):
    return _hub_ok({"cancelled": model_hub.cancel_job(job_id)})
