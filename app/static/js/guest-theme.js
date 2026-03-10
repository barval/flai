// static/js/guest-theme.js
// Handles theme switching for unauthenticated users using localStorage

document.addEventListener('DOMContentLoaded', function() {
    const guestThemeBtn = document.getElementById('theme-toggle-guest');
    if (guestThemeBtn) {
        // Load saved theme from localStorage
        const savedTheme = localStorage.getItem('guest_theme') || 'light';
        if (savedTheme === 'dark') {
            document.body.classList.add('dark-theme');
            guestThemeBtn.textContent = '☀️';
        } else {
            document.body.classList.remove('dark-theme');
            guestThemeBtn.textContent = '🌙';
        }

        guestThemeBtn.addEventListener('click', function() {
            const isDark = document.body.classList.contains('dark-theme');
            const newTheme = isDark ? 'light' : 'dark';
            // Update body class
            document.body.classList.toggle('dark-theme', !isDark);
            // Update button icon
            guestThemeBtn.textContent = newTheme === 'dark' ? '☀️' : '🌙';
            // Save to localStorage
            localStorage.setItem('guest_theme', newTheme);
            // Also notify server to store in session (optional, for consistency)
            fetch('/set-theme/' + newTheme, {
                method: 'GET',
                headers: { 'Cache-Control': 'no-cache' }
            }).catch(() => {});
        });
    }
});