// static/js/chat-export.js
// Save chat as HTML function with embedded media files (base64)

function siteTitleFallback() {
    return document.querySelector('header h1')?.textContent?.trim() || 'FLAI';
}

async function saveChatAsHTML() {
    const userNameElement = document.querySelector('.logout-container .user-name');
    const userName = userNameElement ? userNameElement.textContent.trim() : t('user');    const activeSession = document.querySelector('.session-item.active');
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
    const headerLogoHtml = logoBase64 ? '<img src="' + logoBase64 + '" alt="' + escapeHtml(logoImg?.alt || siteTitleFallback()) + '" class="header-logo">' : '';

    const now = new Date();
    const timestamp = now.getFullYear() + '-' + pad(now.getMonth()+1) + '-' + pad(now.getDate()) + '-' + pad(now.getHours()) + pad(now.getMinutes()) + pad(now.getSeconds());

    const footerCopyright = t('footer_copyright') || '';
    // Footer mirrors the live site: one short brand label (clickable) that
    // opens the same About dialog (full name, version, GitHub, copyright).
    const footerBrandLabel = t('footer_short_name') + ' v' + (window.FLAI_VERSION || '');
    const footerModalLogo = logoBase64
        ? '<img src="' + logoBase64 + '" alt="" class="about-logo">'
        : '';

    // Collect all message elements and their media
    const messageElements = [];
    const mediaToFetch = [];

    // DEBUG: Check what message elements exist in DOM
    dlog('=== EXPORT DEBUG START ===');
    const allUserMessages = document.querySelectorAll('.user-message');
    const allAssistantMessages = document.querySelectorAll('.assistant-message');
    const allBotMessages = document.querySelectorAll('.bot-message');
    dlog('User messages found:', allUserMessages.length);
    dlog('Assistant messages found:', allAssistantMessages.length);
    dlog('Bot messages found:', allBotMessages.length);

    document.querySelectorAll('.user-message, .assistant-message, .bot-message').forEach((msgEl, index) => {
        const role = msgEl.classList.contains('user-message') ? 'user' : 'assistant';
        const msgTimestamp = msgEl.dataset.timestamp;
        const headerEl = msgEl.querySelector('.message-header');
        const timeHtml = headerEl ? headerEl.innerHTML : formatFullDateTime(msgTimestamp);
        const contentEl = msgEl.querySelector('.message-content');
        const contentHtml = contentEl ? contentEl.innerHTML : '';

        // Find all media elements (multi-attachment messages carry several
        // images: the legacy first one plus the .extra-attachments row).
        const imageEls = msgEl.querySelectorAll('.attached-image');
        const audioEls = msgEl.querySelectorAll('audio');
        const videoEls = msgEl.querySelectorAll('video');
        const fileEl = msgEl.querySelector('.attached-file');

        // DEBUG: Log media elements for each message
        dlog('Message', index, '-', role, ':', {
            imageCount: imageEls.length,
            audioCount: audioEls.length,
            videoCount: videoEls.length,
            hasFile: !!fileEl
        });

        const mediaInfo = {
            images: [],
            audios: [],
            videos: [],
            file: null
        };

        // Collect images - FIX: Check if src CONTAINS /api/files/ not just starts with
        for (const imageEl of imageEls) {
            if (!imageEl.src) continue;
            if (imageEl.src.includes('/api/files/')) {
                mediaInfo.images.push({
                    url: imageEl.src,
                    alt: imageEl.alt || t('image'),
                    index: mediaToFetch.length
                });
                mediaToFetch.push({
                    url: imageEl.src,
                    type: 'image',
                    msgIndex: index
                });
                dlog('Added image to fetch:', imageEl.src);
            } else if (imageEl.src.startsWith('data:')) {
                mediaInfo.images.push({
                    src: imageEl.src,
                    alt: imageEl.alt || t('image')
                });
                dlog('Image already base64, skipping fetch');
            }
        }

        // Collect audio - FIX: Check if src CONTAINS /api/files/ not just starts with
        for (const audioEl of audioEls) {
            if (!audioEl.src) continue;
            if (audioEl.src.includes('/api/files/')) {
                mediaInfo.audios.push({
                    url: audioEl.src,
                    index: mediaToFetch.length
                });
                mediaToFetch.push({
                    url: audioEl.src,
                    type: 'audio',
                    msgIndex: index
                });
                dlog('Added audio to fetch:', audioEl.src);
            } else if (audioEl.src.startsWith('data:')) {
                mediaInfo.audios.push({
                    src: audioEl.src
                });
                dlog('Audio already base64, skipping fetch');
            }
        }

        // Collect video
        for (const videoEl of videoEls) {
            if (!videoEl.src) continue;
            if (videoEl.src.includes('/api/files/')) {
                mediaInfo.videos.push({
                    url: videoEl.src,
                    index: mediaToFetch.length
                });
                mediaToFetch.push({
                    url: videoEl.src,
                    type: 'video',
                    msgIndex: index
                });
                dlog('Added video to fetch:', videoEl.src);
            } else if (videoEl.src.startsWith('data:')) {
                mediaInfo.videos.push({
                    src: videoEl.src
                });
                dlog('Video already base64, skipping fetch');
            }
        }

        // Collect file attachment
        if (fileEl && !imageEl && !audioEl && !videoEl) {
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

    dlog('Total messages collected:', messageElements.length);
    dlog('Total media to fetch:', mediaToFetch.length);
    dlog('=== EXPORT DEBUG END ===');

    if (messageElements.length === 0) {
        alert(t('no_messages_to_save'));
        return;
    }

    // Fetch all media files and convert to base64
    dlog('Fetching', mediaToFetch.length, 'media files...');
    const mediaBase64Results = new Array(mediaToFetch.length).fill(null);
    const fetchPromises = mediaToFetch.map(async (mediaItem, idx) => {
        try {
            dlog('Fetching media:', mediaItem.url, 'for message', mediaItem.msgIndex);
            
            // IMPORTANT: include credentials to pass session cookie
            const response = await fetch(mediaItem.url, {
                credentials: 'include',
                headers: {
                    'Accept': mediaItem.type === 'image' ? 'image/*' : mediaItem.type === 'video' ? 'video/*' : 'audio/*'
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
                    dlog('Media fetched successfully:', mediaItem.url, 
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
    dlog('All media fetched. Results:', mediaBase64Results.filter(r => r !== null).length, 'of', mediaToFetch.length);

    // Build messages HTML with embedded media
    const messagesHtml = messageElements.map((msg, msgIdx) => {
        let fileHtml = '';
        
        // Add images with base64 (multi-attachment messages carry several)
        for (const image of msg.media.images) {
            let imgSrc = image.src;
            if (!imgSrc && image.index !== undefined) {
                imgSrc = mediaBase64Results[image.index];
            }
            if (imgSrc) {
                fileHtml += '<div class="image-container"><img src="' + imgSrc + '" class="attached-image" alt="' + escapeHtml(image.alt) + '"></div>';
            } else {
                // Fallback to original URL if base64 conversion failed
                dwarn('Image missing base64, using original URL:', image.url);
                fileHtml += '<div class="image-container"><img src="' + image.url + '" class="attached-image" alt="' + escapeHtml(image.alt) + '"></div>';
            }
        }

        // Add audio with base64
        for (const audio of msg.media.audios) {
            let audioSrc = audio.src;
            if (!audioSrc && audio.index !== undefined) {
                audioSrc = mediaBase64Results[audio.index];
            }
            if (audioSrc) {
                fileHtml += '<div class="audio-container"><audio controls src="' + audioSrc + '"></audio></div>';
            } else {
                dwarn('Audio missing base64, using original URL:', audio.url);
                fileHtml += '<div class="audio-container"><audio controls src="' + audio.url + '"></audio></div>';
            }
        }

        // Add video with base64
        for (const video of msg.media.videos) {
            let videoSrc = video.src;
            if (!videoSrc && video.index !== undefined) {
                videoSrc = mediaBase64Results[video.index];
            }
            if (videoSrc) {
                fileHtml += '<div class="video-container"><video controls preload="metadata" src="' + videoSrc + '"></video></div>';
            } else {
                dwarn('Video missing base64, using original URL:', video.url);
                fileHtml += '<div class="video-container"><video controls preload="metadata" src="' + video.url + '"></video></div>';
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
                    dwarn('Failed to load CSS:', url);
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
        '<button type="button" class="footer-brand" id="export-about-btn"' +
        ' title="' + escapeHtml(t('footer_about_hint')) + '">' + escapeHtml(footerBrandLabel) + '</button>\n' +
        '</footer>\n' +
        '<div id="export-about-modal" class="about-modal" hidden>\n' +
        '<div class="about-modal-backdrop" data-close-about></div>\n' +
        '<div class="about-modal-content" role="dialog" aria-modal="true">\n' +
        '<button type="button" class="about-modal-close" data-close-about aria-label="' + escapeHtml(t('close_about')) + '">&times;</button>\n' +
        footerModalLogo + '\n' +
        '<h2>' + escapeHtml(t('footer_text')) + '</h2>\n' +
        '<p class="about-version">v' + (window.FLAI_VERSION || '') + '</p>\n' +
        '<p class="about-github"><a href="https://github.com/barval/flai" target="_blank" rel="noopener noreferrer">https://github.com/barval/flai</a></p>\n' +
        (footerCopyright ? '<p class="about-copyright">' + escapeHtml(footerCopyright) + '</p>' : '') + '\n' +
        '</div>\n' +
        '</div>\n' +
        '<script>\n' +
        '(function () {\n' +
        '    var modal = document.getElementById("export-about-modal");\n' +
        '    var btn = document.getElementById("export-about-btn");\n' +
        '    if (!modal || !btn) return;\n' +
        '    var lastFocus = null;\n' +
        '    function openModal() {\n' +
        '        lastFocus = document.activeElement;\n' +
        '        modal.hidden = false;\n' +
        '        var closeBtn = modal.querySelector(".about-modal-close");\n' +
        '        if (closeBtn) closeBtn.focus();\n' +
        '        document.addEventListener("keydown", onKey);\n' +
        '    }\n' +
        '    function closeModal() {\n' +
        '        modal.hidden = true;\n' +
        '        document.removeEventListener("keydown", onKey);\n' +
        '        if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();\n' +
        '    }\n' +
        '    function onKey(e) {\n' +
        '        if (e.key === "Escape") closeModal();\n' +
        '    }\n' +
        '    btn.addEventListener("click", openModal);\n' +
        '    modal.querySelectorAll("[data-close-about]").forEach(function (el) {\n' +
        '        el.addEventListener("click", closeModal);\n' +
        '    });\n' +
        '})();\n' +
        '<' + '/script>\n' +
        '</body>\n' +
        '</html>';

    const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'chat_' + timestamp + '.html';
    a.click();
    URL.revokeObjectURL(url);

    dlog('Chat export completed!');
}