// static/js/chat-utils.js
// Utility functions used across chat modules

function t(key) {
    if (!(key in window.TRANSLATIONS)) {
        console.warn('Missing translation key:', key);
        return key;
    }
    return window.TRANSLATIONS[key];
}

function formatString(str, params) {
    return str.replace(/{(\w+)}/g, (match, key) => params[key] || match);
}

function pad(n) {
    return n.toString().padStart(2, '0');
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 ' + t('byte_abbr');
    if (!bytes) return '';
    const k = 1024;
    const units = [
        t('byte_abbr'),
        t('kb_abbr'),
        t('mb_abbr'),
        t('gb_abbr')
    ];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + units[i];
}

function decodeHtmlEntities(text) {
    if (!text) return '';
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatFullDateTime(ts) {
    if (!ts) return '';
    try {
        const date = new Date(ts);
        if (isNaN(date.getTime())) {
            return ts.replace('T', ' ').slice(0, 19);
        }
        const options = {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit'
        };
        return date.toLocaleString(CURRENT_LANG === 'ru' ? 'ru-RU' : 'en-US', options).replace(',', '');
    } catch (e) {
        return ts.replace('T', ' ').slice(0, 19);
    }
}

function openImageModal(imgSrc, imgAlt) {
    const modal = document.getElementById('image-modal');
    const modalImg = document.getElementById('modal-image');
    const captionText = document.getElementById('modal-caption');
    modal.style.display = "block";
    modalImg.src = imgSrc;
    captionText.innerHTML = imgAlt;
}

function closeImageModal() {
    const modal = document.getElementById('image-modal');
    modal.style.display = "none";
}

// Helper to ensure currentSessionId is valid
function ensureValidSessionId() {
    if (!currentSessionId) {
        console.warn('currentSessionId is empty, trying to recover');
        const sessions = document.querySelectorAll('.session-item');
        if (sessions.length > 0) {
            const firstId = sessions[0].dataset.sessionId;
            if (firstId) {
                currentSessionId = firstId;
                return true;
            }
        }
        return false;
    }
    return true;
}