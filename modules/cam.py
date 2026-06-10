# modules/cam.py
import base64
import json
import logging
import time
from datetime import datetime

import requests
from flask_babel import gettext as _

from app.mixins import TranslationMixin
from app.userdb import check_camera_permission


class CamModule(TranslationMixin):
    """Module for interacting with CCTV camera system.

    Room definitions are loaded from the ``camera_rooms`` DB table
    (single source of truth).  The old hardcoded dictionaries were
    replaced by dynamic ``_load_rooms_from_db()``.
    """

    def __init__(self, app=None):
        self.logger = logging.getLogger(__name__)
        self.camera_api_url = None
        self.available = False
        self.last_check = 0
        self.check_interval = 30
        self.timeout = 15

        # Populated by _load_rooms_from_db()
        self.room_names: dict[str, str] = {}          # code → primary name
        self.room_name_forms: dict[str, list[str]] = {}  # code → all name forms
        self.room_name_keys: dict[str, str] = {}      # code → translation key
        self.room_codes: dict[str, str] = {}           # name_form → code

        if app:
            self.init_app(app)

    def init_app(self, app):
        self.app = app
        self.camera_api_url = app.config.get("CAMERA_API_URL", "http://flai-room-snapshot-api:5000")
        self.timeout = app.config.get("CAMERA_API_TIMEOUT", 15)
        self.check_interval = app.config.get("CAMERA_CHECK_INTERVAL", 30)
        self.max_init_retries = app.config.get("CAMERA_MAX_INIT_RETRIES", 5)
        self.init_retry_delay = app.config.get("CAMERA_INIT_RETRY_DELAY", 2)

        self.logger.info(f"Initializing CamModule with Camera API URL: {self.camera_api_url}")

        # Initial availability check with retries (camera service may start slower than web app)
        for attempt in range(1, self.max_init_retries + 1):
            if self.check_availability(force=True):
                break
            if attempt < self.max_init_retries:
                self.logger.warning(
                    f"Camera API not ready (attempt {attempt}/{self.max_init_retries}), retrying in {self.init_retry_delay}s..."
                )
                import time

                time.sleep(self.init_retry_delay)
            else:
                self.logger.warning(f"Camera API not available after {self.max_init_retries} attempts")

        # Load room definitions from DB AFTER availability check
        # (populate_from_camera_api needs self.available to be True)
        self._load_rooms_from_db()

        if self.available:
            self.logger.info(
                f"CamModule initialized and available (API: {self.camera_api_url}), timeout: {self.timeout}s"
            )
        else:
            self.logger.warning(
                f"CamModule initialized, but camera API unavailable ({self.camera_api_url}). Will retry on each request."
            )

    def _load_rooms_from_db(self):
        """Load room definitions from the camera_rooms DB table.

        Builds three lookup dicts:
        - room_names:        code → primary name (first element of name_forms)
        - room_name_forms:   code → list of all name forms (all declensions)
        - room_codes:        lowercased name form → code (for reverse lookup)

        If the table is empty and camera API is available, auto-populates
        from the room-snapshot-api /rooms endpoint (one-time migration).
        """
        try:
            from app.cameradb import get_all_camera_rooms, populate_from_camera_api

            rooms = get_all_camera_rooms(enabled_only=True)

            # Auto-populate from camera API on first run (empty table + API available)
            if not rooms and self.available:
                count = populate_from_camera_api(self.camera_api_url, self.timeout)
                if count:
                    self.logger.info(f"Auto-imported {count} cameras from room-snapshot-api")
                    rooms = get_all_camera_rooms(enabled_only=True)
        except Exception as e:
            self.logger.warning(f"Could not load camera rooms from DB: {e}. Using empty room list.")
            rooms = []

        self.room_names = {}
        self.room_name_forms = {}
        self.room_name_keys = {}
        self.room_codes = {}

        for room in rooms:
            code = room["code"]
            forms = room["name_forms"] or []
            self.room_names[code] = forms[0] if forms else code
            self.room_name_forms[code] = [f.lower() for f in forms]
            # Translation key: room_{code} (for i18n of room name)
            self.room_name_keys[code] = f"room_{code}"
            # Reverse lookup: each form → code
            for form in forms:
                self.room_codes[form.lower()] = code

        self.logger.info(f"Loaded {len(self.room_names)} camera rooms from DB: {list(self.room_names.keys())}")

    def reload_rooms(self):
        """Reload room definitions from DB. Call after CRUD operations."""
        self._load_rooms_from_db()

    def get_all_rooms(self):
        """Return dictionary {code: name} of all known cameras."""
        return self.room_names.copy()

    def check_permission(self, user_login, room_code):
        """Check if user has permission to access the camera."""
        return check_camera_permission(user_login, room_code)

    def get_available_rooms(self, user_login):
        """Return dictionary of cameras accessible to user (code -> name)."""
        all_rooms = self.get_all_rooms()
        if user_login is None:
            return all_rooms
        from app.userdb import get_user_by_login

        user = get_user_by_login(user_login)
        if user and user["camera_permissions"] is not None:
            try:
                allowed_codes = json.loads(user["camera_permissions"])
                return {code: name for code, name in all_rooms.items() if code in allowed_codes}
            except Exception:
                return {}
        return all_rooms

    def check_availability(self, force=False):
        current_time = time.time()
        if not force and (current_time - self.last_check) < self.check_interval:
            return self.available
        if not self.camera_api_url:
            self.available = False
            self.last_check = current_time
            return False

        health_endpoints = [
            f"{self.camera_api_url}/health",
            f"{self.camera_api_url}/api/health",
            f"{self.camera_api_url}/snapshot/health",
        ]

        for endpoint in health_endpoints:
            try:
                self.logger.debug(f"Checking camera API availability: {endpoint}")
                response = requests.get(endpoint, timeout=3)
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if isinstance(data, dict) and data.get("status") == "ok":
                            self.available = True
                            self.last_check = current_time
                            self.logger.info(f"Camera API available (via {endpoint})")
                            return True
                    except Exception:
                        self.available = True
                        self.last_check = current_time
                        self.logger.info(f"Camera API available (via {endpoint})")
                        return True
            except requests.exceptions.ConnectionError:
                self.logger.debug(f"Connection error to {endpoint}")
                continue
            except requests.exceptions.Timeout:
                self.logger.debug(f"Timeout connecting to {endpoint}")
                continue
            except Exception as e:
                self.logger.debug(f"Error checking {endpoint}: {str(e)}")
                continue

        try:
            response = requests.get(f"{self.camera_api_url}/rooms", timeout=3)
            if response.status_code == 200:
                self.available = True
                self.last_check = current_time
                self.logger.info("Camera API available (via /rooms)")
                return True
        except Exception:
            pass

        self.available = False
        self.last_check = current_time
        self.logger.warning(f"Camera API unavailable at {self.camera_api_url}")
        return False

    def get_status(self):
        self.check_availability()
        status = {
            "available": self.available,
            "url": self.camera_api_url,
            "last_check": datetime.fromtimestamp(self.last_check).isoformat() if self.last_check else None,
            "rooms": list(self.room_names.keys()),
            "room_names": self.room_names,
            "timeout": self.timeout,
            "check_interval": self.check_interval,
            "message": "Available" if self.available else "Unavailable",
        }
        if self.available:
            try:
                response = requests.get(f"{self.camera_api_url}/rooms", timeout=3)
                if response.status_code == 200:
                    api_rooms = response.json()
                    if isinstance(api_rooms, dict):
                        status["available_rooms"] = list(api_rooms.keys())
            except Exception:
                pass
        return status

    def get_room_name(self, room_code, lang="ru"):
        """Get translated room name for the given code."""
        key = self.room_name_keys.get(room_code)
        if key:
            return self._(key, lang)
        return f"room '{room_code}'"

    def get_room_code(self, room_name):
        """Resolve a room name (possibly declined) to its code.

        Search order:
        1. Exact match in self.room_codes (all stored forms).
        2. Substring match: any stored form contained in the query,
           or the query contained in a stored form.
        """
        room_name_lower = room_name.lower().strip()
        # 1. Exact match
        if room_name_lower in self.room_codes:
            return self.room_codes[room_name_lower]
        # 2. Substring match against all stored name forms
        for code, forms in self.room_name_forms.items():
            for form in forms:
                if form in room_name_lower or room_name_lower in form:
                    return code
        return None

    def get_all_rooms_with_forms(self) -> list[tuple[str, list[str]]]:
        """Return list of (code, [name_forms]) for enabled rooms.

        Used by the router prompt builder to generate camera classification rules.
        """
        return [
            (code, self.room_name_forms.get(code, []))
            for code in sorted(self.room_names.keys())
        ]

    def get_snapshot(self, user_login, room_code, lang="ru"):
        self.check_availability()
        if not self.available:
            return {"success": False, "error": self._("CCTV service unavailable", lang), "status_code": 503}

        if room_code not in self.room_names:
            code = self.get_room_code(room_code)
            if code:
                self.logger.info(f"Converted room name '{room_code}' to code '{code}'")
                room_code = code
            else:
                template = self._("Unknown room: {room}", lang)
                return {
                    "success": False,
                    "error": template.format(room=room_code),
                    "status_code": 404,
                    "available_rooms": list(self.room_names.keys()),
                }

        if not self.check_permission(user_login, room_code):
            return {"success": False, "error": self._("Access to this camera is denied", lang), "status_code": 403}

        room_name = self.get_room_name(room_code, lang)

        try:
            endpoints = [
                f"{self.camera_api_url}/snapshot/{room_code}",
                f"{self.camera_api_url}/api/snapshot/{room_code}",
                f"{self.camera_api_url}/camera/{room_code}",
            ]

            last_error = None
            for endpoint in endpoints:
                try:
                    self.logger.info(f"Request to camera: {endpoint}, timeout: {self.timeout}s")
                    response = requests.get(
                        endpoint, timeout=self.timeout, headers={"Accept": "image/jpeg,image/png,*/*"}
                    )

                    if response.status_code == 200:
                        content_type = response.headers.get("content-type", "")

                        if "image" in content_type:
                            image_data = base64.b64encode(response.content).decode("utf-8")
                            file_type = content_type
                        else:
                            try:
                                data = response.json()
                                if isinstance(data, dict):
                                    if "image" in data:
                                        image_data = data["image"]
                                    elif "image_data" in data:
                                        image_data = data["image_data"]
                                    elif "base64" in data:
                                        image_data = data["base64"]
                                    else:
                                        for key in ["data", "snapshot", "frame"]:
                                            if key in data and isinstance(data[key], str):
                                                image_data = data[key]
                                                break
                                        else:
                                            continue
                                    file_type = data.get("content_type", data.get("mime_type", "image/jpeg"))
                                else:
                                    continue
                            except Exception:
                                continue

                        file_size_bytes = int((len(image_data) * 3) / 4)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        # Changed filename format: timestamp_roomcode.jpg
                        filename = f"{timestamp}_{room_code}.jpg"

                        self.logger.info(f"Successfully got snapshot from camera {room_code}")

                        return {
                            "success": True,
                            "image_data": image_data,
                            "image_type": file_type,
                            "file_name": filename,
                            "file_size": file_size_bytes,
                            "room_code": room_code,
                            "room_name": room_name,
                            "timestamp": datetime.now().isoformat(),
                        }
                # Keep pybabel extract from marking these as stale:
                # _("room_tambour") _("room_hallway") _("room_corridor")
                # _("room_bedroom") _("room_office") _("room_children")
                # _("room_living") _("room_kitchen") _("room_balcony")

                except requests.exceptions.ConnectionError:
                    last_error = self._("Connection error", lang)
                    continue
                except requests.exceptions.Timeout:
                    template = self._("Timeout ({timeout}s)", lang)
                    last_error = template.format(timeout=self.timeout)
                    continue
                except Exception as e:
                    last_error = str(e)
                    continue

            template = self._("Failed to get snapshot from camera {room_name}", lang)
            error_msg = template.format(room_name=room_name)
            if last_error:
                error_msg += f": {last_error}"

            self.logger.error(error_msg)
            return {"success": False, "error": error_msg, "status_code": 500}

        except Exception as e:
            self.logger.error(f"Error calling camera API: {str(e)}")
            return {"success": False, "error": f"{self._('Error', lang)}: {str(e)}", "status_code": 500}

    def get_available_rooms_with_status(self, user_login, lang="ru"):
        self.check_availability()
        if not self.available:
            return {
                "success": False,
                "error": self._("CCTV service unavailable", lang),
                "rooms": list(self.room_names.keys()),
                "room_names": self.room_names,
            }

        allowed_rooms = self.get_available_rooms(user_login)
        return {"success": True, "rooms": list(allowed_rooms.keys()), "room_names": allowed_rooms}


class CamAPI:
    @staticmethod
    def register_routes(app, cam_module):
        from flask import jsonify, session

        @app.route("/api/cam/status", methods=["GET"])
        def cam_status():
            if "login" not in session:
                return jsonify({"error": _("Not authorized")}), 401
            return jsonify(cam_module.get_status())

        @app.route("/api/cam/rooms", methods=["GET"])
        def cam_rooms():
            if "login" not in session:
                return jsonify({"error": _("Not authorized")}), 401
            user_login = session["login"]
            lang = session.get("language", "ru")
            return jsonify(cam_module.get_available_rooms_with_status(user_login, lang))

        @app.route("/api/cam/snapshot/<room>", methods=["GET"])
        def cam_snapshot(room):
            if "login" not in session:
                return jsonify({"error": _("Not authorized")}), 401
            user_login = session["login"]
            lang = session.get("language", "ru")
            result = cam_module.get_snapshot(user_login, room, lang=lang)
            if result["success"]:
                return jsonify(
                    {
                        "success": True,
                        "image_data": result["image_data"],
                        "image_type": result["image_type"],
                        "room_name": result["room_name"],
                        "timestamp": result.get("timestamp"),
                    }
                )
            else:
                return jsonify(result), result.get("status_code", 500)

        @app.route("/api/cam/health", methods=["GET"])
        def cam_health():
            if "login" not in session:
                return jsonify({"error": _("Not authorized")}), 401
            cam_module.check_availability(force=True)
            return jsonify(
                {
                    "module": "cam",
                    "available": cam_module.available,
                    "url": cam_module.camera_api_url,
                    "timeout": cam_module.timeout,
                    "check_interval": cam_module.check_interval,
                    "timestamp": datetime.now().isoformat(),
                }
            )
