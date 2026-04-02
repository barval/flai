# tests/test_userdb.py
"""Unit tests for user database functions."""
import pytest
import uuid


def generate_unique_name(prefix='test'):
    """Generate unique name for test isolation."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.unit
class TestUserDB:
    """Test user database operations."""

    def test_create_and_get_user(self, test_app):
        """Test creating and getting a user."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.userdb import create_user, get_user_by_login

            # Create user
            create_user(username, 'pass123', 'Test User')

            # Get user
            user = get_user_by_login(username)
            assert user is not None
            assert user['login'] == username
            assert user['name'] == 'Test User'
            assert user['is_active'] == 1

    def test_update_user(self, test_app):
        """Test updating user data."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.userdb import create_user, update_user, get_user_by_login

            # Create user
            create_user(username, 'pass123', 'Update Test')

            # Update user
            update_user(username, name='Updated Name', service_class=1)

            # Verify update
            user = get_user_by_login(username)
            assert user['name'] == 'Updated Name'
            assert user['service_class'] == 1

    def test_update_password(self, test_app):
        """Test updating user password."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.userdb import create_user, update_password, get_user_by_login
            from werkzeug.security import check_password_hash

            # Create user
            create_user(username, 'oldpass', 'Password Test')

            # Update password
            update_password(username, 'newpass123')

            # Verify password was changed
            user = get_user_by_login(username)
            assert check_password_hash(user['password_hash'], 'newpass123')
            assert not check_password_hash(user['password_hash'], 'oldpass')

    def test_delete_user(self, test_app):
        """Test deleting user."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.userdb import create_user, delete_user, get_user_by_login

            # Create user
            create_user(username, 'pass123', 'Delete Test')

            # Delete user
            delete_user(username)

            # Verify user was deleted
            user = get_user_by_login(username)
            assert user is None

    def test_list_users(self, test_app):
        """Test listing users."""
        username1 = generate_unique_name('listuser1')
        username2 = generate_unique_name('listuser2')
        with test_app.app_context():
            from app.userdb import create_user, list_users, delete_user

            # Create test users
            create_user(username1, 'pass123', 'List User 1')
            create_user(username2, 'pass123', 'List User 2')

            # List users (excluding admin)
            users = list_users(exclude_admin=True)
            assert len(users) >= 2

            # Cleanup
            delete_user(username1)
            delete_user(username2)

    def test_camera_permission(self, test_app):
        """Test camera permission checking."""
        username = generate_unique_name()
        with test_app.app_context():
            from app.userdb import create_user, update_user, check_camera_permission

            # Create user with camera permissions
            create_user(username, 'pass123', 'Camera Test')
            update_user(username, camera_permissions=['cam1', 'cam2'])

            # Check permissions
            assert check_camera_permission(username, 'cam1') is True
            assert check_camera_permission(username, 'cam2') is True
            assert check_camera_permission(username, 'cam3') is False

            # Update permissions
            update_user(username, camera_permissions=['cam1'])
            assert check_camera_permission(username, 'cam1') is True
            assert check_camera_permission(username, 'cam2') is False
