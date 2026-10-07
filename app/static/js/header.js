// static/js/header.js
// Handles language, voice gender, and theme switching in the header

// CSRF token helper
function getCSRFToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : '';
}

function fetchWithCSRF(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    if (['POST', 'PUT', 'DELETE', 'PATCH'].includes(method)) {
        const headers = options.headers || {};
        if (!headers['X-CSRFToken'] && !headers['X-CSRF-TOKEN']) {
            headers['X-CSRFToken'] = getCSRFToken();
        }
        options.headers = headers;
    }
    options.credentials = 'same-origin';
    return fetch(url, options);
}

function t(key) {
    if (!(key in window.TRANSLATIONS)) {
        dwarn('Missing translation key:', key);
        return key;
    }
    return window.TRANSLATIONS[key];
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// Mobile layout: the language dropdown shows only «Ру»/«En» and the voice
// gender / theme buttons live in the footer row (left / right corner).
function isMobileLayout() {
    return window.matchMedia('(max-width: 768px)').matches;
}

// Mobile: hide the native select, show the custom dropdown (toggle «Ру ▾» +
// a menu listing the full names). Desktop: the select alone. The menu always
// starts closed; [hidden] is honoured via CSS rules, not UA defaults.
function applyCompactLangOptions(compact) {
    document.querySelectorAll('.language-switcher').forEach(box => {
        const toggle = box.querySelector('.lang-toggle');
        const menu = box.querySelector('.lang-menu');
        const select = box.querySelector('select');
        if (toggle) toggle.hidden = !compact;
        if (menu) menu.hidden = true; // never open on layout switch
        if (select) select.hidden = compact;
    });
}

// Short label for the closed toggle: «Ру ▾» / «En ▾» from the select's
// data-short attributes (authoritative, matches the option language).
function langShortLabel(box) {
    const select = box.querySelector('select');
    if (!select) return 'Ру';
    const selected = select.querySelector('option:checked') || select.querySelector('option');
    return (selected && selected.dataset.short) || 'Ру';
}

// Wire the custom mobile language dropdown: the toggle shows the short label,
// the menu lists the full names («Русский»/«English»).
function initLangDropdown() {
    document.querySelectorAll('.language-switcher').forEach(box => {
        const toggle = box.querySelector('.lang-toggle');
        const menu = box.querySelector('.lang-menu');
        if (!toggle || !menu || toggle.dataset.bound) return;
        toggle.dataset.bound = '1';

        const syncToggleLabel = () => {
            toggle.textContent = langShortLabel(box) + ' ▾';
        };
        syncToggleLabel();
        // Re-sync after the session language changes (full page reload) and
        // whenever the select value changes while the dropdown is hidden.
        box.querySelector('select').addEventListener('change', syncToggleLabel);

        toggle.addEventListener('click', function(e) {
            e.stopPropagation();
            menu.hidden = !menu.hidden;
        });
        menu.querySelectorAll('.lang-option').forEach(btn => {
            btn.addEventListener('click', function() {
                menu.hidden = true;
                switchLanguage(this.dataset.lang);
            });
        });
        // Close on outside click
        document.addEventListener('click', function(e) {
            if (!menu.hidden && !box.contains(e.target)) menu.hidden = true;
        });
    });
}

// Move the voice-gender and theme switchers between the header rows and the
// footer bar. The elements keep their event handlers — only the parent node
// changes. Mobile: voice goes to the footer left corner, theme to the right.
// Desktop: both sit in row1 BEFORE the user name, so the logout form stays
// the rightmost control. The guest (login) page has no voice switcher and no
// rows — the theme switcher returns directly into <header>.
function relocateHeaderControls() {
    const theme = document.querySelector('.theme-switcher');
    if (!theme) return;
    const voice = document.querySelector('.voice-gender-switcher');
    if (isMobileLayout()) {
        const footer = document.querySelector('footer');
        if (!footer || theme.dataset.location === 'footer') return;
        theme.dataset.location = 'footer';
        if (voice) {
            voice.dataset.location = 'footer';
            footer.insertBefore(voice, footer.firstChild); // left corner
        }
        footer.appendChild(theme); // right corner
    } else {
        if (theme.dataset.location !== 'footer') return;
        delete theme.dataset.location;
        if (voice) delete voice.dataset.location;
        const anchor = document.getElementById('api-keys-btn');
        const row1 = document.querySelector('.header-row.row1');
        if (row1 && anchor) {
            // Voice, then theme, then the user name — logout stays rightmost.
            if (voice) row1.insertBefore(voice, anchor);
            row1.insertBefore(theme, voice ? voice : anchor);
        } else {
            const header = document.querySelector('header');
            if (!header) return;
            if (voice) header.appendChild(voice);
            header.appendChild(theme);
        }
    }
}

document.addEventListener('DOMContentLoaded', function() {
    // Language switcher
    const langSelect = document.getElementById('language-select');
    if (langSelect) {
        langSelect.addEventListener('change', function() {
            switchLanguage(this.value);
        });
    }

    // Compact language options + footer relocation on mobile
    applyCompactLangOptions(isMobileLayout());
    initLangDropdown();
    relocateHeaderControls();
    window.addEventListener('resize', function() {
        applyCompactLangOptions(isMobileLayout());
        relocateHeaderControls();
    });

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
    fetchWithCSRF('/set-language/' + lang, {
        method: 'POST',
        headers: { 'Cache-Control': 'no-cache' }
    }).then(() => {
        window.location.reload();
    }).catch(() => {
        window.location.reload();
    });
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
        method: 'POST',
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
        method: 'POST',
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