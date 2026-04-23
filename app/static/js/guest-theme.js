// static/js/guest-theme.js
// Handles theme switching for unauthenticated users using localStorage
// Also auto-detects system theme on first visit

// Get CSRF token from meta tag
function getCSRFToken() {
    const token = document.querySelector('meta[name="csrf-token"]');
    return token ? token.getAttribute('content') : '';
}

function applyTheme(theme) {
    if (theme === 'dark') {
        document.body.classList.add('dark-theme');
        document.getElementById('theme-toggle-guest').textContent = '☀️';
    } else {
        document.body.classList.remove('dark-theme');
        document.getElementById('theme-toggle-guest').textContent = '🌙';
    }
    // Save to localStorage (always update)
    localStorage.setItem('guest_theme', theme);
    // Optionally inform server to store in session (for consistency after login)
    fetch('/set-theme/' + theme, {
        method: 'POST',
        headers: { 'Cache-Control': 'no-cache', 'X-CSRFToken': getCSRFToken() }
    }).catch(() => {});
}

document.addEventListener('DOMContentLoaded', function() {
    const guestThemeBtn = document.getElementById('theme-toggle-guest');
    if (!guestThemeBtn) return;

    // Determine theme: from localStorage or system preference
    let savedTheme = localStorage.getItem('guest_theme');
    if (!savedTheme) {
        // Auto-detect system color scheme
        savedTheme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
        localStorage.setItem('guest_theme', savedTheme);
    }
    applyTheme(savedTheme);

    guestThemeBtn.addEventListener('click', function() {
        const isDark = document.body.classList.contains('dark-theme');
        const newTheme = isDark ? 'light' : 'dark';
        applyTheme(newTheme);
    });
});