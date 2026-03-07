// static/js/chat-messages.js
// Message display and loading functions

function updateMessageCount() {
    const count = document.querySelectorAll('.user-message, .assistant-message, .bot-message').length;
    document.getElementById('context-info').textContent = t('messages') + ': ' + count;
}

function loadMessages(sessionId) {
    return fetch('/api/sessions/' + sessionId + '/messages')
    .then(res => res.json())
    .then(messages => {
        const container = document.getElementById('chat-messages');
        container.innerHTML = '';
        fetch('/api/sessions/' + sessionId + '/model-info')
        .then(res => res.json())
        .then(data => {
            defaultModelName = data.model_name || 'qwen3-vl:8b-instruct-q4_K_M';
        })
        .catch(err => console.error('Error loading model info:', err));
        let lastUserMessage = null;
        messages.forEach((msg) => {
            if (msg.role === 'user') {
                lastUserMessage = msg;
                displayMessage(
                    msg.role,
                    msg.content,
                    msg.file_data,
                    msg.file_type,
                    msg.file_name,
                    msg.timestamp,
                    null, null, null, null, null, null
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
                    msg.timestamp,
                    responseTime,
                    msg.model_name || defaultModelName,
                    mmTime,
                    genTime,
                    mmModel,
                    genModel
                );
                lastUserMessage = null;
            }
        });
        updateMessageCount();
        container.scrollTop = container.scrollHeight;
        setNewMessageIndicator(sessionId, false);
        updateLastVisit(sessionId);
    });
}

function displayMessage(role, content, fileData, fileType, fileName, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel) {
    const container = document.getElementById('chat-messages');
    const msgDiv = document.createElement('div');
    msgDiv.className = (role === 'user') ? 'user-message' : 'assistant-message bot-message';
    if (!timestamp) timestamp = new Date().toISOString();
    msgDiv.setAttribute('data-timestamp', timestamp);
    msgDiv.dataset.sessionId = currentSessionId;
    if (role === 'assistant') {
        msgDiv.setAttribute('data-raw-text', content);
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
    if (role === 'user' && fileName && fileData) {
        const base64Length = fileData.length;
        const fileSizeBytes = Math.round((base64Length * 3) / 4);
        const fileSize = formatFileSize(fileSizeBytes);
        timeDisplay += ' <span class="file-info">[📎 ' + fileName + ', ' + fileSize + ']</span>';
        if (fileType && fileType.startsWith('image/')) {
            timeDisplay += ' <a href="data:' + fileType + ';base64,' + fileData + '" download="' + (fileName || 'image.jpg') + '" class="download-link-inline" title="' + t('download_image') + '" onclick="event.stopPropagation()">⬇️</a>';
        }
        if (fileType && fileType.startsWith('audio/')) {
            timeDisplay += ' <a href="data:' + fileType + ';base64,' + fileData + '" download="' + (fileName || 'audio.webm') + '" class="download-link-inline" title="' + t('download_audio') + '" onclick="event.stopPropagation()">⬇️</a>';
        }
    }
    if (role === 'assistant' && fileName && fileData) {
        const base64Length = fileData.length;
        const fileSizeBytes = Math.round((base64Length * 3) / 4);
        const fileSize = formatFileSize(fileSizeBytes);
        timeDisplay += ' <span class="file-info">[📎 ' + fileName + ', ' + fileSize + ']</span>';
        if (fileType && fileType.startsWith('image/')) {
            timeDisplay += ' <a href="data:' + fileType + ';base64,' + fileData + '" download="' + (fileName || 'generated_image.jpg') + '" class="download-link-inline" title="' + t('download_image') + '" onclick="event.stopPropagation()">⬇️</a>';
        }
        if (fileType && fileType.startsWith('audio/')) {
            timeDisplay += ' <a href="data:' + fileType + ';base64,' + fileData + '" download="' + (fileName || 'audio.webm') + '" class="download-link-inline" title="' + t('download_audio') + '" onclick="event.stopPropagation()">⬇️</a>';
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
            const langSuffix = window.CURRENT_LANG === 'ru' ? 'с' : 's';
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
    if (fileData) {
        let fileHTML = '';
        if (fileType && fileType.startsWith('image/')) {
            fileHTML = '<div class="image-container"><img src="data:' + fileType + ';base64,' + fileData + '" class="attached-image" alt="' + (fileName || 'attached image') + '" title="' + t('click_to_enlarge') + '" onclick="openImageModal(this.src, \'' + (fileName || t('image')) + '\')"></div>';
        } else if (fileType && fileType.startsWith('audio/')) {
            fileHTML = '<audio controls src="data:' + fileType + ';base64,' + fileData + '"></audio>';
        } else {
            fileHTML = '<div class="attached-file"><span class="file-icon">📄</span><a href="data:' + fileType + ';base64,' + fileData + '" download="' + fileName + '">' + fileName + '</a></div>';
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
            playTTS(ttsButton, msgDiv);
        });
    }

    // Copy message button handler
    const copyButton = msgDiv.querySelector('.copy-message-button');
    if (copyButton) {
        copyButton.addEventListener('click', async (e) => {
            e.preventDefault();
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
        addCopyButtonsToMessage(msgDiv);
    }, 50);
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
            handleCopyClick(copyButton, codeBlock);
        });
        parent.parentNode.insertBefore(wrapper, parent);
        wrapper.appendChild(parent);
        wrapper.appendChild(copyButton);
    });
    const contentDiv = messageElement.querySelector('.message-content');
    if (contentDiv && !contentDiv.querySelector('.copy-transcript-button')) {
        const text = contentDiv.innerText || contentDiv.textContent;
        if (text.includes('🎤 ' + t('transcribed') + ':')) {
            const copyBtn = document.createElement('button');
            copyBtn.className = 'copy-transcript-button';
            copyBtn.innerHTML = '📋';
            copyBtn.title = t('copy_text');
            copyBtn.onclick = (e) => {
                e.preventDefault();
                e.stopPropagation();
                const textToCopy = text.replace('🎤 ' + t('transcribed') + ':', '').trim();
                copyToClipboard(textToCopy);
                copyBtn.innerHTML = '✓';
                setTimeout(() => copyBtn.innerHTML = '📋', 2000);
            };
            contentDiv.style.position = 'relative';
            contentDiv.appendChild(copyBtn);
        }
    }
}

function setupCopyButtonsObserver() {
    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;
    const observer = new MutationObserver((mutations) => {
        mutations.forEach((mutation) => {
            mutation.addedNodes.forEach((node) => {
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