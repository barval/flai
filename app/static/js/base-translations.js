// app/static/js/base-translations.js
// Global translations and settings for all pages

// Initialize global translations object
window.TRANSLATIONS = window.TRANSLATIONS || {};

// Set translation defaults (fall back to English only when a template did not
// inject a localized value — this file loads AFTER template inline blocks, so
// plain assignment would clobber the user's language at runtime)
window.TRANSLATIONS['male_voice'] = window.TRANSLATIONS['male_voice'] || 'Male';
window.TRANSLATIONS['female_voice'] = window.TRANSLATIONS['female_voice'] || 'Female';
window.TRANSLATIONS['toggle_voice_gender'] = window.TRANSLATIONS['toggle_voice_gender'] || 'Toggle voice gender';
window.TRANSLATIONS['toggle_theme'] = window.TRANSLATIONS['toggle_theme'] || 'Toggle theme';
window.TRANSLATIONS['loading'] = window.TRANSLATIONS['loading'] || 'Loading...';

// Global settings defaults
window.CURRENT_LANG = 'ru';
window.CURRENT_VOICE_GENDER = 'male';
window.CURRENT_THEME = 'light';
