// app/static/js/admin-config.js
// Admin page configuration and translations

(function() {
    // Rooms configuration (will be set by template)
    window.ROOMS = [];
    window.LLAMA_SWAP_URL = '';

    // Admin-specific translations
    const adminTranslations = {
        'delete_user_confirm': 'Delete user {login}?',
        'error': 'Error',
        'internal_server_error': 'Internal server error',
        'missing_fields': 'Missing fields',
        'login_exists': 'Login already exists',
        'password_not_specified': 'New password not specified',
        'ok': 'OK',
        'save': 'Save',
        'cancel': 'Cancel',
        'change': 'Change',
        'delete': 'delete',
        'change_password': 'Change password',
        'delete_user': 'Delete user',
        'status': 'Status',
        'login': 'Login',
        'password': 'Password',
        'name': 'Name',
        'class': 'Class',
        'sessions': 'Sessions',
        'messages': 'Messages',
        'files': 'Files',
        'documents': 'Documents',
        'camera_access': 'Camera access',
        'actions': 'Actions',
        'active': 'Active',
        'create': 'Create',
        'new_password': 'New password',
        'new_user': 'New user',
        'only_lowercase': 'Only lowercase letters and digits',
        'highest': 'highest',
        'medium': 'medium',
        'lowest': 'lowest',
        'leave_unchecked': 'Leave all unchecked to deny access to all cameras.',
        'unknown_error': 'unknown_error',
        'user_db': 'User DB',
        'chat_db': 'Chat DB',
        'files_db': 'Files DB',
        'documents_db': 'Documents DB',
        'mb': 'MB',
        'refresh_models': 'Refresh models from llama-server',
        'saved': 'Saved'
    };

    // Merge with base translations
    if (window.TRANSLATIONS) {
        Object.assign(window.TRANSLATIONS, adminTranslations);
    } else {
        window.TRANSLATIONS = adminTranslations;
    }

    // Translation helper
    window.t = window.t || function(key) {
        if (!(key in window.TRANSLATIONS)) {
            console.warn('Missing translation key:', key);
            return key;
        }
        return window.TRANSLATIONS[key];
    };
})();
