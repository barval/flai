# tests/test_admin_monitoring_consistency.py
"""The Users tab must count documents the same way the Documents panel does."""

import pytest

from app.database import get_db
from app.userdb import create_user

pytestmark = pytest.mark.requires_db


@pytest.fixture
def admin_client(client, test_app):
    with test_app.app_context():
        from app.userdb import get_user_by_login, update_password

        if get_user_by_login("admin"):
            update_password("admin", "adminpass")
        else:
            create_user("admin", "adminpass", "Admin User", is_admin=True)

    client.post("/login", data={"login": "admin", "password": "adminpass"})
    return client


def test_users_documents_count_matches_the_documents_panel(admin_client, test_app):
    with test_app.app_context():
        create_user("u1", "pw", "U1")
        with get_db() as conn:
            cursor = conn.cursor()
            for index, (name, ext) in enumerate(
                (("report.pdf", ".pdf"), ("photo.png", ".png"), ("notes.txt", ".txt"))
            ):
                cursor.execute(
                    "INSERT INTO documents (id, user_id, filename, file_ext) VALUES (%s, 'u1', %s, %s)",
                    (f"doc-{index}", name, ext),
                )

    response = admin_client.get("/admin/api/users")
    assert response.status_code == 200, response.data
    row = next(user for user in response.get_json() if user["login"] == "u1")

    assert row["documents_count"] == 3, "images count too, exactly like the Documents panel"
