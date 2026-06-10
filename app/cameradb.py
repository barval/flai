# app/cameradb.py
"""CRUD operations for camera_rooms table.

Single source of truth for camera/room definitions.
Rooms are imported from room-snapshot-api /rooms endpoint.
RTSP IP/port/stream are managed by room-snapshot-api, not stored here.
"""

import logging

import requests

from app.database import get_db
from app.morph import generate_room_name_forms

logger = logging.getLogger(__name__)


def get_all_camera_rooms(enabled_only: bool = True) -> list[dict]:
    """Return all camera rooms from DB, optionally filtered by enabled."""
    with get_db() as conn:
        c = conn.cursor()
        if enabled_only:
            c.execute(
                "SELECT * FROM camera_rooms WHERE enabled = TRUE ORDER BY sort_order, code"
            )
        else:
            c.execute("SELECT * FROM camera_rooms ORDER BY sort_order, code")
        return [dict(row) for row in c.fetchall()]


def populate_from_camera_api(camera_api_url: str, timeout: int = 15) -> list[str]:
    """Import camera rooms from room-snapshot-api /rooms endpoint.

    GET /rooms returns {"code": "Name", ...} — codes and display names.
    Uses INSERT ... ON CONFLICT to preserve existing enabled status.
    Returns list of imported camera codes. Empty list if API unavailable.
    """
    try:
        resp = requests.get(f"{camera_api_url.rstrip('/')}/rooms", timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"Camera API /rooms returned {resp.status_code}")
            return []
        rooms = resp.json()
        if not isinstance(rooms, dict) or not rooms:
            logger.warning("Camera API /rooms returned empty or unexpected format")
            return []
    except requests.exceptions.ConnectionError:
        logger.warning(f"Camera API unreachable at {camera_api_url}")
        return []
    except requests.exceptions.Timeout:
        logger.warning(f"Camera API timeout at {camera_api_url}")
        return []
    except Exception as e:
        logger.warning(f"Error calling camera API: {e}")
        return []

    codes = []
    for code, name in rooms.items():
        code = code.lower().strip()
        name = name.strip()
        if not code or not name:
            continue
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO camera_rooms (code, name_forms, enabled, sort_order)
                VALUES (%s, %s, TRUE, %s)
                ON CONFLICT (code) DO UPDATE SET
                    name_forms = EXCLUDED.name_forms,
                    sort_order = EXCLUDED.sort_order
                """,
                (code, generate_room_name_forms(name.lower()), len(codes)),
            )
            conn.commit()
        codes.append(code)

    logger.info(f"Imported {len(codes)} camera rooms from room-snapshot-api")
    return codes


def delete_cameras_not_in(codes: list[str]) -> int:
    """Delete camera rooms whose code is not in the provided list.

    Returns number of deleted rows.
    """
    if not codes:
        return 0
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "DELETE FROM camera_rooms WHERE code != ALL(%s)",
            (codes,),
        )
        count = c.rowcount
        conn.commit()
    if count:
        logger.info(f"Removed {count} stale camera rooms")
    return count


def toggle_camera_enabled(code: str, enabled: bool) -> None:
    """Update enabled status for a camera room."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE camera_rooms SET enabled = %s, updated_at = NOW() WHERE code = %s",
            (enabled, code),
        )
        conn.commit()
    logger.info(f"Camera {code} enabled={enabled}")


def migrate_name_forms() -> int:
    """Regenerate name_forms for all rooms using pymorphy3.

    Uses the first element of each room's current name_forms as the
    nominative base, then generates correct declension forms.
    Returns the number of updated rows.
    """
    rooms = get_all_camera_rooms(enabled_only=False)
    if not rooms:
        return 0

    updated = 0
    with get_db() as conn:
        c = conn.cursor()
        for room in rooms:
            forms = room.get("name_forms") or []
            if not forms:
                continue
            # First form is the nominative base (from old suffix or pymorphy3)
            base_name = forms[0].lower().strip()
            new_forms = generate_room_name_forms(base_name)
            # Only update if forms actually changed
            if new_forms != list(forms):
                c.execute(
                    "UPDATE camera_rooms SET name_forms = %s, updated_at = NOW() WHERE code = %s",
                    (new_forms, room["code"]),
                )
                updated += 1
                logger.info(f"Migrated '{room['code']}': {list(forms)} → {new_forms}")
        conn.commit()

    if updated:
        logger.info(f"Migrated name_forms for {updated} camera rooms")
    return updated
