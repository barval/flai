// app/static/js/chat-messages.js
// Message display and loading functions

function updateMessageCount() {
    if (window.IS_RELOADING) return;
    const count = document.querySelectorAll('.user-message, .assistant-message, .bot-message').length;
    document.getElementById('context-info').textContent = t('messages') + ': ' + count;
}

function loadMessages(sessionId) {
    if (window.IS_RELOADING) return Promise.resolve();
    if (!sessionId) {
        console.error('loadMessages called with empty sessionId');
        return Promise.reject(new Error('Session ID is empty'));
    }
    console.log('originalLoadMessages: loading messages for session', sessionId);
    displayedMessageIds.clear(); // Clear IDs for the new session
    return fetch('/api/sessions/' + sessionId + '/messages')
    .then(res => {
        if (!res.ok) {
            console.error('Failed to load messages:', res.status);
            throw new Error('HTTP error ' + res.status);
        }
        return res.json();
    })
    .then(messages => {
        if (window.IS_RELOADING) return;
        console.log('Received messages:', messages.length);
        // DEBUG: Log message data from server
        messages.forEach((msg, idx) => {
            console.log('Message', idx, ':', {
                id: msg.id,
                role: msg.role,
                hasFilePath: !!msg.file_path,
                filePath: msg.file_path,
                hasFileData: !!msg.file_data,
                fileType: msg.file_type,
                fileName: msg.file_name
            });
        });
        const container = document.getElementById('chat-messages');
        container.innerHTML = '';
        fetch('/api/sessions/' + sessionId + '/model-info')
        .then(res => res.json())
        .then(data => {
            if (window.IS_RELOADING) return;
            defaultModelName = data.model_name || 'qwen3-vl:8b-instruct-q4_K_M';
        })
        .catch(err => console.error('Error loading model info:', err));
        let lastUserMessage = null;
        messages.forEach((msg) => {
            try {
                if (msg.role === 'user') {
                    lastUserMessage = msg;
                    displayMessage(
                        msg.role,
                        msg.content,
                        msg.file_data,
                        msg.file_type,
                        msg.file_name,
                        msg.file_path,
                        msg.timestamp,
                        null, null, null, null, null, null,
                        msg.id
                    );
                } else if (msg.role === 'assistant') {
                    let responseTime = null;
                    if (lastUserMessage) {
                        const userTime = new Date(lastUserMessage.timestamp);
                        const assistantTime = new Date(msg.timestamp);
                        const diffSeconds = (assistantTime - userTime) / 1000;
                        responseTime = Math.round(diffSeconds * 10) / 10;
                    }
                    if (msg.response_time) {
                        if (typeof msg.response_time === 'object') {
                            responseTime = msg.response_time;
                        } else if (!isNaN(parseFloat(msg.response_time))) {
                            responseTime = parseFloat(msg.response_time);
                        }
                    }
                    let mmTime = msg.mm_time;
                    let genTime = msg.gen_time;
                    let mmModel = msg.mm_model;
                    let genModel = msg.gen_model;
                    if (mmTime && genTime) {
                        responseTime = {
                            mm_time: parseFloat(mmTime),
                            gen_time: parseFloat(genTime),
                            mm_model: mmModel || 'unknown',
                            gen_model: genModel || 'unknown'
                        };
                    }
                    displayMessage(
                        msg.role,
                        msg.content,
                        msg.file_data,
                        msg.file_type,
                        msg.file_name,
                        msg.file_path,
                        msg.timestamp,
                        responseTime,
                        msg.model_name || defaultModelName,
                        mmTime,
                        genTime,
                        mmModel,
                        genModel,
                        msg.id
                    );
                    lastUserMessage = null;
                }
            } catch (e) {
                console.error('Error displaying message', msg, e);
            }
        });
        updateMessageCount();
        container.scrollTop = container.scrollHeight;
        setNewMessageIndicator(sessionId, false);
        updateLastVisit(sessionId);
    })
    .catch(err => {
        console.error('Error in originalLoadMessages:', err);
        throw err;
    });
}

function displayMessage(role, content, fileData, fileType, fileName, filePath, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel, messageId) {
    if (window.IS_RELOADING) return;
    const container = document.getElementById('chat-messages');
    const msgDiv = document.createElement('div');
    msgDiv.className = (role === 'user') ? 'user-message' : 'assistant-message bot-message';
    if (!timestamp) timestamp = new Date().toISOString();
    msgDiv.setAttribute('data-timestamp', timestamp);
    msgDiv.dataset.sessionId = currentSessionId;
    msgDiv.setAttribute('data-raw-text', content);
    if (messageId) {
        msgDiv.setAttribute('data-message-id', messageId);
        displayedMessageIds.add(messageId);
    }
    if (role === 'assistant') {
        if (modelName) msgDiv.dataset.modelName = modelName;
        if (responseTime && typeof responseTime === 'object') {
            if (responseTime.mm_time) msgDiv.dataset.mmTime = responseTime.mm_time;
            if (responseTime.gen_time) msgDiv.dataset.genTime = responseTime.gen_time;
            if (responseTime.mm_model) msgDiv.dataset.mmModel = responseTime.mm_model;
            if (responseTime.gen_model) msgDiv.dataset.genModel = responseTime.gen_model;
        } else if (mmTime && genTime) {
            msgDiv.dataset.mmTime = mmTime;
            msgDiv.dataset.genTime = genTime;
            msgDiv.dataset.mmModel = mmModel || 'unknown';
            msgDiv.dataset.genModel = genModel || 'unknown';
        }
    }
    let timeDisplay = formatFullDateTime(timestamp);
    if (role === 'user' && fileName && (fileData || filePath)) {
        let fileSizeText = '';
        if (fileData) {
            const base64Length = fileData.length;
            const fileSizeBytes = Math.round((base64Length * 3) / 4);
            fileSizeText = formatFileSize(fileSizeBytes);
        } else if (filePath) {
            fileSizeText = '';
        }
        timeDisplay += ' <span class="file-info">[📎 ' + fileName + (fileSizeText ? ', ' + fileSizeText : '') + ']</span>';
        let downloadUrl = '';
        if (filePath) {
            downloadUrl = '/api/files/' + filePath;
        } else if (fileData) {
            downloadUrl = 'data:' + fileType + ';base64,' + fileData;
        }
        if (downloadUrl) {
            if (fileType && fileType.startsWith('image/')) {
                timeDisplay += ' <a href="' + downloadUrl + '" download="' + (fileName || 'image.jpg') + '" class="download-link-inline" title="' + t('download_image') + '" onclick="event.stopPropagation()">⬇️</a>';
            }
            if (fileType && fileType.startsWith('audio/')) {
                timeDisplay += ' <a href="' + downloadUrl + '" download="' + (fileName || 'audio.webm') + '" class="download-link-inline" title="' + t('download_audio') + '" onclick="event.stopPropagation()">⬇️</a>';
            }
        }
    }
    if (role === 'assistant' && fileName && (fileData || filePath)) {
        const base64Length = fileData ? fileData.length : 0;
        const fileSizeBytes = fileData ? Math.round((base64Length * 3) / 4) : 0;
        const fileSize = fileSizeBytes ? formatFileSize(fileSizeBytes) : '';
        timeDisplay += ' <span class="file-info">[📎 ' + fileName + (fileSize ? ', ' + fileSize : '') + ']</span>';
        let downloadUrl = '';
        if (filePath) {
            downloadUrl = '/api/files/' + filePath;
        } else if (fileData) {
            downloadUrl = 'data:' + fileType + ';base64,' + fileData;
        }
        if (downloadUrl) {
            if (fileType && fileType.startsWith('image/')) {
                timeDisplay += ' <a href="' + downloadUrl + '" download="' + (fileName || 'generated_image.jpg') + '" class="download-link-inline" title="' + t('download_image') + '" onclick="event.stopPropagation()">⬇️</a>';
            }
            if (fileType && fileType.startsWith('audio/')) {
                timeDisplay += ' <a href="' + downloadUrl + '" download="' + (fileName || 'audio.webm') + '" class="download-link-inline" title="' + t('download_audio') + '" onclick="event.stopPropagation()">⬇️</a>';
            }
        }
    }
    let headerHTML = '<span class="message-header">📅 ' + timeDisplay;
    if (role === 'assistant') {
        let headerExtra = '';
        if (modelName) {
            const shortModel = modelName.split('/').pop() || modelName;
            headerExtra += ' <span class="text-muted">| ' + escapeHtml(shortModel) + '</span>';
        }
        let duration = null;
        if (responseTime) {
            if (typeof responseTime === 'object') {
                if (responseTime.mm_time && responseTime.gen_time) {
                    duration = (parseFloat(responseTime.mm_time) + parseFloat(responseTime.gen_time)).toFixed(1);
                } else if (responseTime.mm_time) {
                    duration = parseFloat(responseTime.mm_time).toFixed(1);
                } else if (responseTime.gen_time) {
                    duration = parseFloat(responseTime.gen_time).toFixed(1);
                }
            } else if (typeof responseTime === 'number' || !isNaN(parseFloat(responseTime))) {
                duration = parseFloat(responseTime).toFixed(1);
            }
        }
        if (duration) {
            const langSuffix = t('seconds_suffix');
            headerExtra += ' <span class="text-muted">⏱️ ' + duration + langSuffix + '</span>';
        }
        // TTS button
        headerExtra += ' <button class="tts-button" title="' + t('speak') + '">🗣️</button>';
        // Copy message button
        headerExtra += ' <button class="copy-message-button" title="' + t('copy_text') + '">📋</button>';
        headerHTML += headerExtra;
    }
    headerHTML += '</span>';
    let contentHTML = '<div class="message-content">';
    if (typeof content === 'string') {
        if (content.startsWith('[')) {
            try {
                const parts = JSON.parse(content);
                let textContent = '';
                parts.forEach(part => {
                    if (part.type === 'text') textContent += part.text + '\n';
                });
                if (textContent) {
                    const escapedText = escapeHtml(textContent.trim());
                    contentHTML += marked.parse(escapedText);
                }
            } catch (e) {
                const decodedText = (role === 'assistant') ? decodeHtmlEntities(content) : escapeHtml(content);
                contentHTML += marked.parse(decodedText);
            }
        } else {
            const decodedText = (role === 'assistant') ? decodeHtmlEntities(content) : escapeHtml(content);
            contentHTML += marked.parse(decodedText);
        }
    }
    contentHTML += '</div>';
    msgDiv.innerHTML = headerHTML + contentHTML;
    // File display
    if (fileData || filePath) {
        let fileHTML = '';
        let fileUrl = '';
        console.log('displayMessage: Creating media element for', {
            role,
            fileName,
            fileType,
            hasFilePath: !!filePath,
            filePath,
            hasFileData: !!fileData,
            fileDataLength: fileData ? fileData.length : 0
        });
        if (filePath) {
            fileUrl = '/api/files/' + filePath;
            console.log('displayMessage: Using file path URL:', fileUrl);
        } else if (fileData) {
            fileUrl = 'data:' + fileType + ';base64,' + fileData;
            console.log('displayMessage: Using base64 data URL (length:', fileData.length, ')');
        }
        if (fileUrl) {
            if (fileType && fileType.startsWith('image/')) {
                fileHTML = '<div class="image-container"><img src="' + fileUrl + '" class="attached-image" alt="' + (fileName || 'attached image') + '" title="' + t('click_to_enlarge') + '" onclick="openImageModal(this.src, \'' + (fileName || t('image')) + '\')"></div>';
                console.log('displayMessage: Created image element with src:', fileUrl.substring(0, 80));
            } else if (fileType && fileType.startsWith('audio/')) {
                fileHTML = '<audio controls src="' + fileUrl + '" preload="metadata"></audio>';
                console.log('displayMessage: Created audio element with src:', fileUrl.substring(0, 80));
            } else {
                fileHTML = '<div class="attached-file"><span class="file-icon">📄</span><a href="' + fileUrl + '" download="' + fileName + '">' + fileName + '</a></div>';
                console.log('displayMessage: Created file attachment element');
            }
        }
        msgDiv.innerHTML += fileHTML;
    }
    container.appendChild(msgDiv);
    container.scrollTop = container.scrollHeight;
    updateMessageCount();
    // TTS button handler
    const ttsButton = msgDiv.querySelector('.tts-button');
    if (ttsButton) {
        ttsButton.removeAttribute('onclick');
        ttsButton.addEventListener('click', (e) => {
            e.preventDefault();
            if (window.IS_RELOADING) return;
            // Use global playTTS function if available
            if (typeof window.playTTS === 'function') {
                window.playTTS(ttsButton, msgDiv);
            } else {
                playTTS(ttsButton, msgDiv);
            }
        });
    }
    // Copy message button handler
    const copyButton = msgDiv.querySelector('.copy-message-button');
    if (copyButton) {
        copyButton.addEventListener('click', async (e) => {
            e.preventDefault();
            if (window.IS_RELOADING) return;
            const rawText = msgDiv.dataset.rawText;
            if (!rawText) return;
            const success = await copyToClipboard(rawText);
            const originalHTML = copyButton.innerHTML;
            const originalTitle = copyButton.title;
            if (success) {
                copyButton.innerHTML = '✓';
                copyButton.title = t('copied');
                setTimeout(() => {
                    copyButton.innerHTML = originalHTML;
                    copyButton.title = originalTitle;
                }, 2000);
            } else {
                copyButton.innerHTML = '✗';
                copyButton.title = t('copy_failed');
                setTimeout(() => {
                    copyButton.innerHTML = originalHTML;
                    copyButton.title = originalTitle;
                }, 2000);
            }
        });
    }
    setTimeout(() => {
        if (window.IS_RELOADING) return;
        addCopyButtonsToMessage(msgDiv);
    }, 50);
    return msgDiv;
}

async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (err) {
        console.error('Clipboard API error:', err);
        try {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            const success = document.execCommand('copy');
            document.body.removeChild(textarea);
            return success;
        } catch (fallbackErr) {
            console.error('Fallback copy error:', fallbackErr);
            return false;
        }
    }
}

async function handleCopyClick(button, codeElement) {
    if (window.IS_RELOADING) return;
    const code = codeElement.textContent || codeElement.innerText;
    const originalHTML = button.innerHTML;
    const originalClass = button.className;
    button.innerHTML = '⏳';
    button.disabled = true;
    const success = await copyToClipboard(code);
    if (success) {
        button.innerHTML = '✓';
        button.className = originalClass + ' copied';
        button.title = t('copied');
        setTimeout(() => {
            button.innerHTML = '📋';
            button.className = originalClass.replace(' copied', '');
            button.title = t('copy_code');
            button.disabled = false;
        }, 2000);
    } else {
        button.innerHTML = '✗';
        button.title = t('copy_failed');
        setTimeout(() => {
            button.innerHTML = '📋';
            button.title = t('copy_code');
            button.disabled = false;
        }, 2000);
    }
}

function addCopyButtonsToMessage(messageElement) {
    if (window.IS_RELOADING) return;
    if (!messageElement) return;
    const codeBlocks = messageElement.querySelectorAll('pre code');
    codeBlocks.forEach((codeBlock) => {
        const parent = codeBlock.parentNode;
        if (parent.classList.contains('code-block-wrapper')) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'code-block-wrapper';
        const copyButton = document.createElement('button');
        copyButton.className = 'copy-code-button';
        copyButton.innerHTML = '📋';
        copyButton.title = t('copy_code');
        copyButton.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (window.IS_RELOADING) return;
            handleCopyClick(copyButton, codeBlock);
        });
        parent.parentNode.insertBefore(wrapper, parent);
        wrapper.appendChild(parent);
        wrapper.appendChild(copyButton);
    });
}

function setupCopyButtonsObserver() {
    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            mutation.addedNodes.forEach((node) => {
                if (window.IS_RELOADING) return;
                if (node.nodeType === Node.ELEMENT_NODE) {
                    if (node.classList && (node.classList.contains('user-message') || node.classList.contains('assistant-message') || node.classList.contains('bot-message'))) {
                        addCopyButtonsToMessage(node);
                    }
                    const messages = node.querySelectorAll?.('.user-message, .assistant-message, .bot-message');
                    if (messages) messages.forEach(addCopyButtonsToMessage);
                }
            });
        });
    });
    observer.observe(chatMessages, { childList: true, subtree: true });
}