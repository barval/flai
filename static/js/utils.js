// Утилиты

export function pad(n) {
    return n.toString().padStart(2, '0');
}

export function formatFileSize(bytes) {
    if (bytes === 0) return '0 Б';
    if (!bytes) return '';
    const k = 1024;
    const sizes = ['Б', 'КБ', 'МБ', 'ГБ'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

export function decodeHtmlEntities(text) {
    if (!text) return '';
    const textarea = document.createElement('textarea');
    textarea.innerHTML = text;
    return textarea.value;
}

export function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

export function formatFullDateTime(ts) {
    if (!ts) return '';
    try {
        const date = new Date(ts);
        if (isNaN(date.getTime())) {
            return ts.replace('T', ' ').slice(0, 19);
        }
        const year = date.getFullYear();
        const month = pad(date.getMonth() + 1);
        const day = pad(date.getDate());
        const hours = pad(date.getHours());
        const minutes = pad(date.getMinutes());
        const seconds = pad(date.getSeconds());
        return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
    } catch (e) {
        return ts.replace('T', ' ').slice(0, 19);
    }
}