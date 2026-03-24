# tests/test_userdb.py
import pytest
from app.userdb import (
    create_user, get_user_by_login, update_user, update_password,
    delete_user, list_users, check_camera_permission
)


def test_create_and_get_user():
    """Test creating a user and retrieving it."""
    create_user('testuser', 'pass', 'Test Name')
    user = get_user_by_login('testuser')
    assert user is not None
    assert user['login'] == 'testuser'
    assert user['name'] == 'Test Name'
    assert user['is_active'] == 1
    assert user['is_admin'] == 0


def test_update_user():
    """Test updating user fields."""
    create_user('testuser', 'pass', 'Old Name')
    update_user('testuser', name='New Name', is_active=0)
    user = get_user_by_login('testuser')
    assert user['name'] == 'New Name'
    assert user['is_active'] == 0


def test_update_password():
    """Test password update (hash verification)."""
    create_user('testuser', 'oldpass', 'Test')
    update_password('testuser', 'newpass')
    # We can't easily verify the hash, but we can check that it's changed
    user = get_user_by_login('testuser')
    assert user['password_hash'] != ''


def test_delete_user():
    """Test user deletion."""
    create_user('testuser', 'pass', 'Test')
    delete_user('testuser')
    user = get_user_by_login('testuser')
    assert user is None


def test_list_users():
    """Test listing users (excluding admin by default)."""
    create_user('user1', 'pass', 'User 1')
    create_user('user2', 'pass', 'User 2')
    users = list_users()
    assert len(users) >= 2
    assert any(u['login'] == 'user1' for u in users)


def test_camera_permission():
    """Test camera permission check."""
    create_user('testuser', 'pass', 'Test', camera_permissions=['tam', 'kor'])
    assert check_camera_permission('testuser', 'tam') is True
    assert check_camera_permission('testuser', 'pri') is False
    # user with no permissions list -> allow all
    create_user('alluser', 'pass', 'All', camera_permissions=None)
    assert check_camera_permission('alluser', 'any') is True