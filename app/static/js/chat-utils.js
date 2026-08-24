// static/js/chat-utils.js
// Utility functions used across chat modules

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

// Calculate response duration string from response_time (object or scalar).
// Returns "12.3" or null. Shared by chat-messages.js and events.js.
function formatResponseDuration(responseTime) {
    if (!responseTime) return null;
    if (typeof responseTime === 'object') {
        if (responseTime.mm_time && responseTime.gen_time) {
            return (parseFloat(responseTime.mm_time) + parseFloat(responseTime.gen_time)).toFixed(1);
        }
        if (responseTime.router && responseTime.chat) {
            return (parseFloat(responseTime.router) + parseFloat(responseTime.chat)).toFixed(1);
        }
        if (responseTime.mm_time) {
            return parseFloat(responseTime.mm_time).toFixed(1);
        }
        if (responseTime.gen_time) {
            return parseFloat(responseTime.gen_time).toFixed(1);
        }
        return null;
    }
    if (typeof responseTime === 'number' || !isNaN(parseFloat(responseTime))) {
        return parseFloat(responseTime).toFixed(1);
    }
    return null;
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
    captionText.textContent = imgAlt;
}

function closeImageModal() {
    const modal = document.getElementById('image-modal');
    modal.style.display = "none";
}
