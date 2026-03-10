// static/js/chat-export.js
// Save chat as HTML function with embedded media files (base64)
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
                    reader.onloadend = () => resolve(reader.result);
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });
                logoBase64 = await base64Promise;
            } catch (e) {
                console.error('Failed to load logo for export:', e);
            }
        } else if (logoSrc && logoSrc.startsWith('data:')) {
            logoBase64 = logoSrc;
        }
    }
    const headerLogoHtml = logoBase64 ? '<img src="' + logoBase64 + '" alt="FLAI Logo" class="header-logo">' : '';

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

    // Collect all message elements and their media
    const messageElements = [];
    const mediaToFetch = [];

    // DEBUG: Check what message elements exist in DOM
    console.log('=== EXPORT DEBUG START ===');
    const allUserMessages = document.querySelectorAll('.user-message');
    const allAssistantMessages = document.querySelectorAll('.assistant-message');
    const allBotMessages = document.querySelectorAll('.bot-message');
    console.log('User messages found:', allUserMessages.length);
    console.log('Assistant messages found:', allAssistantMessages.length);
    console.log('Bot messages found:', allBotMessages.length);

    document.querySelectorAll('.user-message, .assistant-message, .bot-message').forEach((msgEl, index) => {
        const role = msgEl.classList.contains('user-message') ? 'user' : 'assistant';
        const msgTimestamp = msgEl.dataset.timestamp;
        const headerEl = msgEl.querySelector('.message-header');
        const timeHtml = headerEl ? headerEl.innerHTML : formatFullDateTime(msgTimestamp);
        const contentEl = msgEl.querySelector('.message-content');
        const contentHtml = contentEl ? contentEl.innerHTML : '';

        // Find all media elements
        const imageEl = msgEl.querySelector('.attached-image');
        const audioEl = msgEl.querySelector('audio');
        const fileEl = msgEl.querySelector('.attached-file');

        // DEBUG: Log media elements for each message
        console.log('Message', index, '-', role, ':', {
            hasImage: !!imageEl,
            imageSrc: imageEl ? imageEl.src.substring(0, 80) : null,
            hasAudio: !!audioEl,
            audioSrc: audioEl ? audioEl.src.substring(0, 80) : null,
            hasFile: !!fileEl
        });

        const mediaInfo = {
            image: null,
            audio: null,
            file: null
        };

        // Collect image - FIX: Check if src CONTAINS /api/files/ not just starts with
        if (imageEl && imageEl.src) {
            if (imageEl.src.includes('/api/files/')) {
                mediaInfo.image = {
                    url: imageEl.src,
                    alt: imageEl.alt || t('image'),
                    index: mediaToFetch.length
                };
                mediaToFetch.push({
                    url: imageEl.src,
                    type: 'image',
                    msgIndex: index
                });
                console.log('Added image to fetch:', imageEl.src);
            } else if (imageEl.src.startsWith('data:')) {
                mediaInfo.image = {
                    src: imageEl.src,
                    alt: imageEl.alt || t('image')
                };
                console.log('Image already base64, skipping fetch');
            }
        }

        // Collect audio - FIX: Check if src CONTAINS /api/files/ not just starts with
        // FIX: Removed !imageEl condition - audio should be collected regardless
        if (audioEl && audioEl.src) {
            if (audioEl.src.includes('/api/files/')) {
                mediaInfo.audio = {
                    url: audioEl.src,
                    index: mediaToFetch.length
                };
                mediaToFetch.push({
                    url: audioEl.src,
                    type: 'audio',
                    msgIndex: index
                });
                console.log('Added audio to fetch:', audioEl.src);
            } else if (audioEl.src.startsWith('data:')) {
                mediaInfo.audio = {
                    src: audioEl.src
                };
                console.log('Audio already base64, skipping fetch');
            }
        }

        // Collect file attachment
        if (fileEl && !imageEl && !audioEl) {
            const linkEl = fileEl.querySelector('a');
            if (linkEl) {
                mediaInfo.file = {
                    name: linkEl.textContent,
                    href: linkEl.href
                };
            }
        }

        messageElements.push({
            role,
            timestamp: msgTimestamp,
            timeHtml,
            contentHtml,
            media: mediaInfo
        });
    });

    console.log('Total messages collected:', messageElements.length);
    console.log('Total media to fetch:', mediaToFetch.length);
    console.log('=== EXPORT DEBUG END ===');

    if (messageElements.length === 0) {
        alert(t('no_messages_to_save'));
        return;
    }

    // Fetch all media files and convert to base64
    console.log('Fetching', mediaToFetch.length, 'media files...');
    const mediaBase64Results = new Array(mediaToFetch.length).fill(null);
    const fetchPromises = mediaToFetch.map(async (mediaItem, idx) => {
        try {
            console.log('Fetching media:', mediaItem.url, 'for message', mediaItem.msgIndex);
            
            // IMPORTANT: include credentials to pass session cookie
            const response = await fetch(mediaItem.url, {
                credentials: 'include',
                headers: {
                    'Accept': mediaItem.type === 'image' ? 'image/*' : 'audio/*'
                }
            });
            
            if (!response.ok) {
                console.error('Failed to fetch media:', mediaItem.url, 
                             'Status:', response.status, 
                             'Text:', await response.text());
                return;
            }
            
            const blob = await response.blob();
            const reader = new FileReader();
            
            return new Promise((resolve) => {
                reader.onloadend = () => {
                    const base64Data = reader.result;
                    mediaBase64Results[idx] = base64Data;
                    console.log('Media fetched successfully:', mediaItem.url, 
                               'Size:', base64Data.length);
                    resolve();
                };
                reader.onerror = () => {
                    console.error('FileReader error for:', mediaItem.url);
                    resolve();
                };
                reader.readAsDataURL(blob);
            });
        } catch (e) {
            console.error('Error fetching media:', mediaItem.url, e);
        }
    });

    await Promise.all(fetchPromises);
    console.log('All media fetched. Results:', mediaBase64Results.filter(r => r !== null).length, 'of', mediaToFetch.length);

    // Build messages HTML with embedded media
    const messagesHtml = messageElements.map((msg, msgIdx) => {
        let fileHtml = '';
        
        // Add image with base64
        if (msg.media.image) {
            let imgSrc = msg.media.image.src;
            if (!imgSrc && msg.media.image.index !== undefined) {
                imgSrc = mediaBase64Results[msg.media.image.index];
            }
            if (imgSrc) {
                fileHtml += '<div class="image-container"><img src="' + imgSrc + '" class="attached-image" alt="' + escapeHtml(msg.media.image.alt) + '"></div>';
            } else {
                // Fallback to original URL if base64 conversion failed
                console.warn('Image missing base64, using original URL:', msg.media.image.url);
                fileHtml += '<div class="image-container"><img src="' + msg.media.image.url + '" class="attached-image" alt="' + escapeHtml(msg.media.image.alt) + '"></div>';
            }
        }

        // Add audio with base64
        if (msg.media.audio) {
            let audioSrc = msg.media.audio.src;
            if (!audioSrc && msg.media.audio.index !== undefined) {
                audioSrc = mediaBase64Results[msg.media.audio.index];
            }
            if (audioSrc) {
                fileHtml += '<div class="audio-container"><audio controls src="' + audioSrc + '"></audio></div>';
            } else {
                console.warn('Audio missing base64, using original URL:', msg.media.audio.url);
                fileHtml += '<div class="audio-container"><audio controls src="' + msg.media.audio.url + '"></audio></div>';
            }
        }

        // Add file attachment (keep as link, note it may not work offline)
        if (msg.media.file) {
            fileHtml += '<div class="attached-file"><span class="file-icon">📄</span><span>' + escapeHtml(msg.media.file.name) + '</span></div>';
        }

        return `
<div class="${msg.role === 'user' ? 'user-message' : 'assistant-message'}">
<small class="message-time">${msg.timeHtml}</small>
<div class="message-content">${msg.contentHtml}</div>
${fileHtml}
</div>
`;
    }).join('');

    // List of CSS files to load
    const cssFiles = [
        '/static/css/base.css',
        '/static/css/header-footer.css',
        '/static/css/chat.css',
        '/static/css/modal.css',
        '/static/css/markdown.css',
        '/static/css/export.css'
    ];

    // Add dark theme CSS if needed
    if (document.body.classList.contains('dark-theme')) {
        cssFiles.push('/static/css/dark-theme.css');
    }

    // Load all CSS files in parallel
    const cssContents = await Promise.all(
        cssFiles.map(async (url) => {
            try {
                const response = await fetch(url);
                if (!response.ok) {
                    console.warn('Failed to load CSS:', url);
                    return '';
                }
                return await response.text();
            } catch (e) {
                console.error('Failed to load CSS:', url, e);
                return '';
            }
        })
    );

    // Combine all styles into one string
    const combinedStyles = cssContents.join('\n');

    const siteTitle = document.querySelector('header h1')?.textContent || 'FLAI';
    const dateOptions = { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' };
    const formattedDate = now.toLocaleString(CURRENT_LANG === 'ru' ? 'ru-RU' : 'en-US', dateOptions);
    const bodyClass = document.body.classList.contains('dark-theme') ? 'dark-theme' : '';

    const html = '<!DOCTYPE html>\n' +
        '<html lang="' + CURRENT_LANG + '">\n' +
        '<head>\n' +
        '<meta charset="UTF-8">\n' +
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n' +
        '<title>' + escapeHtml(rawTitle) + ' - ' + t('saved_chat') + '</title>\n' +
        '<style>' + combinedStyles + '</style>\n' +
        '</head>\n' +
        '<body class="' + bodyClass + '">\n' +
        '<header>\n' +
        headerLogoHtml + '\n' +
        '<h1>' + escapeHtml(siteTitle) + '</h1>\n' +
        '</header>\n' +
        '<main>\n' +
        '<div class="chat-wrapper">\n' +
        '<div class="chat-header">\n' +
        '<h1>' + t('session') + ': ' + displayTitle + '</h1>\n' +
        '<p class="user-info">👤 ' + t('user') + ': ' + escapeHtml(userName) + '</p>\n' +
        '<p>📅 ' + t('saved_on') + ': ' + formattedDate + '</p>\n' +
        '<p>💬 ' + t('total_messages') + ': ' + messageElements.length + '</p>\n' +
        '</div>\n' +
        '<div class="chat-messages">\n' +
        messagesHtml + '\n' +
        '</div>\n' +
        '</div>\n' +
        '</main>\n' +
        '<footer>\n' +
        '<div class="footer-content">\n' +
        '<div class="footer-line1">' + escapeHtml(footerLine1) + '</div>\n' +
        (footerLine2 ? '<div class="footer-line2">' + escapeHtml(footerLine2) + '</div>' : '') + '\n' +
        '</div>\n' +
        '</footer>\n' +
        '</body>\n' +
        '</html>';

    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'chat_' + timestamp + '.html';
    a.click();
    URL.revokeObjectURL(url);

    console.log('Chat export completed!');
}