// static/js/header.js
// Handles language, voice gender, and theme switching in the header

document.addEventListener('DOMContentLoaded', function() {
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
    const themeBtn = document.getElementById('theme-toggle');
    if (themeBtn) {
        themeBtn.addEventListener('click', function() {
            const currentTheme = document.body.classList.contains('dark-theme') ? 'dark' : 'light';
            const newTheme = currentTheme === 'dark' ? 'light' : 'dark';
            switchTheme(newTheme);
        });
    }
});

function switchLanguage(lang) {
    // Set reloading flag and clear intervals
    window.IS_RELOADING = true;
    if (window.syncInterval) clearInterval(window.syncInterval);
    if (window.recordTimerInterval) clearInterval(window.recordTimerInterval);
    fetch('/set-language/' + lang, {
        method: 'GET',
        headers: { 'Cache-Control': 'no-cache' }
    }).finally(() => {
        window.location.reload();
    });
}

function switchVoiceGender(gender) {
    // Stop current TTS playback if any (will restart with new voice)
    if (window.resetTtsState) {
        window.resetTtsState();
    }
    
    fetch('/set-voice-gender/' + gender, {
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
    fetch('/set-theme/' + theme, {
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