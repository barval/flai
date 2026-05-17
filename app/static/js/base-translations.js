// app/static/js/base-translations.js
// Global translations and settings for all pages

// Initialize global translations object
window.TRANSLATIONS = window.TRANSLATIONS || {};

// Set translation defaults (will be overridden by template-injected values)
window.TRANSLATIONS['male_voice'] = 'Male';
window.TRANSLATIONS['female_voice'] = 'Female';
window.TRANSLATIONS['toggle_voice_gender'] = 'Toggle voice gender';
window.TRANSLATIONS['toggle_theme'] = 'Toggle theme';
window.TRANSLATIONS['loading'] = 'Loading...';

// Global settings defaults
window.CURRENT_LANG = 'ru';
window.CURRENT_VOICE_GENDER = 'male';
window.CURRENT_THEME = 'light';

// Debug logging helper
function logTranslationsLoaded() {
    dlog('Base TRANSLATIONS loaded:', window.TRANSLATIONS);
}
