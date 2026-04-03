// static/js/header.js
// Handles language, voice gender, and theme switching in the header

// CSRF token helper
function getCSRFToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
}

function fetchWithCSRF(url, options = {}) {
    const csrfToken = getCSRFToken();
    if (!options.headers) options.headers = {};
    options.headers['X-CSRFToken'] = csrfToken;
    options.credentials = 'same-origin';
    return fetch(url, options);
}

document.addEventListener('DOMContentLoaded', function() {
    // Language switcher
    const langSelect = document.getElementById('language-select');
    if (langSelect) {
        langSelect.addEventListener('change', function() {
            switchLanguage(this.value);
        });
    }

    // Voice gender toggle
    const voiceBtn = document.getElementById('voice-gender-toggle');
    if (voiceBtn) {
        voiceBtn.addEventListener('click', function() {
            const currentIcon = document.getElementById('voice-gender-icon').textContent;
            const newGender = currentIcon === '👨' ? 'female' : 'male';
            switchVoiceGender(newGender);
        });
    }

    // Theme toggle
    const themeBtn = document.getElementById('theme-toggle') || document.getElementById('theme-toggle-guest');
    if (themeBtn) {
        themeBtn.addEventListener('click', function() {
            const currentTheme = document.body.classList.contains('dark-theme') ? 'dark' : 'light';
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            switchTheme(newTheme);
        });
    }
});

function switchLanguage(lang) {
    // Simple redirect - no need for fetch
    window.location.href = '/set-language/' + lang;
}

// Login page theme setup
function setupLoginTheme() {
    const themeInput = document.getElementById('login-theme');
    if (themeInput) {
        themeInput.value = localStorage.getItem('guest_theme') || 'light';
    }
}

document.addEventListener('DOMContentLoaded', function() {
    setupLoginTheme();
    
    // Initialize global flags
    window.IS_RELOADING = false;
});

function switchVoiceGender(gender) {
    // Stop current TTS playback if any (will restart with new voice)
    if (window.resetTtsState) {
        window.resetTtsState();
    }

    fetchWithCSRF('/set-voice-gender/' + gender, {
        method: 'GET',
        headers: { 'Cache-Control': 'no-cache' }
    }).then(() => {
        const icon = document.getElementById('voice-gender-icon');
        icon.textContent = gender === 'female' ? '👩' : '👨';
        // Update button class and title
        const btn = document.getElementById('voice-gender-toggle');
        btn.className = 'voice-gender-button ' + gender;
        btn.title = gender === 'female'
            ? window.TRANSLATIONS['female_voice']
            : window.TRANSLATIONS['male_voice'];
    }).catch(() => {
        window.location.reload();
    });
}

function switchTheme(theme) {
    // Do NOT stop TTS playback when switching theme
    fetchWithCSRF('/set-theme/' + theme, {
        method: 'GET',
        headers: { 'Cache-Control': 'no-cache' }
    }).then(() => {
        // Update body class
        document.body.classList.toggle('dark-theme', theme === 'dark');
        // Update button icon
        const btn = document.getElementById('theme-toggle');
        btn.textContent = theme === 'dark' ? '☀️' : '🌙';
    }).catch(() => {
        window.location.reload();
    });
}