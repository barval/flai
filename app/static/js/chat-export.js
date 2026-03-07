// static/js/chat-export.js
// Save chat as HTML function

async function saveChatAsHTML() {
    let footerText = "";
    try {
        const response = await fetch('/api/footer-text');
        if (response.ok) {
            footerText = await response.text();
            console.log('Footer text fetched:', footerText);
        } else {
            console.error('Footer API returned status:', response.status);
            footerText = t('footer_text');
        }
    } catch (error) {
        console.error('Error fetching footer:', error);
        footerText = t('footer_text');
    }
    const userNameElement = document.querySelector('.logout-container .user-name');
    const userName = userNameElement ? userNameElement.textContent.trim() : t('user');
    const activeSession = document.querySelector('.session-item.active');
    if (!activeSession) {
        alert(t('no_active_session_save'));
        return;
    }
    const rawTitle = activeSession.querySelector('.session-title')?.textContent || t('chat');
    let displayTitle = rawTitle;
    const filenameDateRegex = /(voice_)?(\d{8})_(\d{6})(\.webm)?$/;
    const match = rawTitle.match(filenameDateRegex);
    if (match) {
        const datePart = match[2];
        const timePart = match[3];
        const year = datePart.substring(0, 4);
        const month = datePart.substring(4, 6);
        const day = datePart.substring(6, 8);
        const hours = timePart.substring(0, 2);
        const minutes = timePart.substring(2, 4);
        const seconds = timePart.substring(4, 6);
        const dateObj = new Date(Date.UTC(year, month - 1, day, hours, minutes, seconds));
        const dateOptions = { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' };
        const formattedDate = dateObj.toLocaleString(CURRENT_LANG === 'ru' ? 'ru-RU' : 'en-US', dateOptions);
        displayTitle = '🎤 ' + t('voice_request') + ' (' + formattedDate + ')';
    } else {
        displayTitle = escapeHtml(rawTitle);
    }
    let logoBase64 = '';
    const logoImg = document.querySelector('.header-logo');
    if (logoImg) {
        const logoSrc = logoImg.src;
        if (logoSrc && !logoSrc.startsWith('data:')) {
            try {
                const response = await fetch(logoSrc);
                const blob = await response.blob();
                const reader = new FileReader();
                const base64Promise = new Promise((resolve, reject) => {
                    reader.onloadend = () => resolve(reader.result.split(',')[1]);
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });
                logoBase64 = await base64Promise;
            } catch (e) {
                console.error('Failed to load logo for export:', e);
            }
        } else if (logoSrc && logoSrc.startsWith('data:')) {
            logoBase64 = logoSrc.split(',')[1];
        }
    }
    const headerLogoHtml = logoBase64 ? '<img src="data:image/png;base64,' + logoBase64 + '" alt="FLAI Logo" class="header-logo">' : '';
    const now = new Date();
    const timestamp = now.getFullYear() + '-' + pad(now.getMonth()+1) + '-' + pad(now.getDate()) + '-' + pad(now.getHours()) + pad(now.getMinutes()) + pad(now.getSeconds());
    let footerLine1 = footerText, footerLine2 = '';
    if (footerText.includes('(c)')) {
        const parts = footerText.split('(c)');
        footerLine1 = parts[0].trim();
        footerLine2 = '(c)' + (parts[1] || '').trim();
    } else {
        footerLine1 = footerText;
    }
    const messages = [];
    document.querySelectorAll('.user-message, .assistant-message, .bot-message').forEach(msgEl => {
        const role = msgEl.classList.contains('user-message') ? 'user' : 'assistant';
        const timestamp = msgEl.dataset.timestamp;
        const headerEl = msgEl.querySelector('.message-header');
        let timeHtml = headerEl ? headerEl.innerHTML : formatFullDateTime(timestamp);
        const contentEl = msgEl.querySelector('.message-content');
        let contentHtml = contentEl ? contentEl.innerHTML : '';
        let fileHtml = '';
        const imageEl = msgEl.querySelector('.attached-image');
        if (imageEl) fileHtml += '<div class="image-container">' + imageEl.outerHTML + '</div>';
        const audioEl = msgEl.querySelector('audio');
        if (audioEl && !imageEl) fileHtml += '<div class="audio-container">' + audioEl.outerHTML + '</div>';
        const fileEl = msgEl.querySelector('.attached-file');
        if (fileEl && !imageEl && !audioEl) fileHtml += '<div class="file-container">' + fileEl.outerHTML + '</div>';
        messages.push({ role, timestamp, timeHtml, contentHtml, fileHtml });
    });
    if (messages.length === 0) {
        alert(t('no_messages_to_save'));
        return;
    }
    // List of CSS files to load
    const cssFiles = [
        '/static/base.css',
        '/static/header-footer.css',
        '/static/chat.css',
        '/static/modal.css',
        '/static/markdown.css',
        '/static/export.css'
    ];
    // Add dark theme CSS if needed
    if (document.body.classList.contains('dark-theme')) {
        cssFiles.push('/static/dark-theme.css');
    }
    // Load all CSS files in parallel
    const cssContents = await Promise.all(
        cssFiles.map(async (url) => {
            try {
                const response = await fetch(url);
                return await response.text();
            } catch (e) {
                console.error(`Failed to load ${url}:`, e);
                return ''; // skip on error
            }
        })
    );
    // Combine all styles into one string
    const combinedStyles = cssContents.join('\n');
    const siteTitle = document.querySelector('header h1')?.textContent || 'FLAI';
    const dateOptions = { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' };
    const formattedDate = now.toLocaleString(CURRENT_LANG === 'ru' ? 'ru-RU' : 'en-US', dateOptions);
    const bodyClass = document.body.classList.contains('dark-theme') ? 'dark-theme' : '';
    const html = '<!DOCTYPE html>\n<html lang="' + CURRENT_LANG + '">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0">\n<title>' + escapeHtml(rawTitle) + ' - ' + t('saved_chat') + '</title>\n<style>' + combinedStyles + '</style>\n</head>\n<body class="' + bodyClass + '">\n<header>\n' + headerLogoHtml + '\n<h1>' + escapeHtml(siteTitle) + '</h1>\n</header>\n<main>\n<div class="chat-wrapper">\n<div class="chat-header">\n<h1>' + t('session') + ': ' + displayTitle + '</h1>\n<p class="user-info">👤 ' + t('user') + ': ' + escapeHtml(userName) + '</p>\n<p>📅 ' + t('saved_on') + ': ' + formattedDate + '</p>\n<p>💬 ' + t('total_messages') + ': ' + messages.length + '</p>\n</div>\n<div class="chat-messages">\n' + messages.map(msg => '\n<div class="' + (msg.role === 'user' ? 'user-message' : 'assistant-message') + '">\n<small class="message-time">' + msg.timeHtml + '</small>\n<div class="message-content">' + msg.contentHtml + '</div>\n' + msg.fileHtml + '\n</div>\n').join('') + '\n</div>\n</div>\n</main>\n<footer>\n<div class="footer-content">\n<div class="footer-line1">' + escapeHtml(footerLine1) + '</div>\n' + (footerLine2 ? '<div class="footer-line2">' + escapeHtml(footerLine2) + '</div>' : '') + '\n</div>\n</footer>\n</body>\n</html>';
    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'chat_' + timestamp + '.html';
    a.click();
    URL.revokeObjectURL(url);
}