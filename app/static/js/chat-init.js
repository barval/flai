// static/js/chat-init.js
// Main chat initialization and send message logic

// Save references to original functions from chat-messages.js
const originalLoadMessages = loadMessages;
const originalDisplayMessage = displayMessage;

function startResultPolling(requestId) {
    if (window.IS_RELOADING) return;
    console.log('Start polling for request:', requestId);
    let pollCount = 0;
    const maxPolls = 120;
    const pollInterval = setInterval(async () => {
        if (window.IS_RELOADING) {
            clearInterval(pollInterval);
            return;
        }
        pollCount++;
        try {
            const response = await fetch('/api/queue/result/' + requestId);
            const data = await response.json();
            if (window.IS_RELOADING) {
                clearInterval(pollInterval);
                return;
            }
            if (data.status === 'completed') {
                clearInterval(pollInterval);
                if (data.result) {
                    const resultSessionId = data.result.session_id || pendingRequests[requestId]?.sessionId;
                    if (data.result.error) {
                        if (resultSessionId === currentSessionId) {
                            originalDisplayMessage('assistant', '⚠️ ' + data.result.error, null, null, null,
                                data.result.assistant_timestamp || new Date().toISOString(), data.result.response_time, 'system');
                            delete stableSessionStatus[resultSessionId];
                        } else if (resultSessionId) {
                            // will be shown via queue status
                        }
                        lastCompletionTime[resultSessionId] = Date.now() + 5000;
                    } else if (data.result.messages) {
                        for (const msg of data.result.messages) {
                            originalDisplayMessage('assistant', msg.response, msg.generated_image, msg.file_type, msg.file_name,
                                msg.assistant_timestamp, msg.response_time, msg.model_used);
                        }
                    } else if (data.result.response) {
                        let responseTime = data.result.response_time;
                        let modelUsed = data.result.model_used;
                        const isError = data.result.is_error || false;
                        if (data.result.mm_time && data.result.gen_time) {
                            responseTime = { mm_time: data.result.mm_time, gen_time: data.result.gen_time, mm_model: data.result.mm_model, gen_model: data.result.gen_model };
                            modelUsed = data.result.gen_model;
                        } else if (typeof responseTime === 'string' && responseTime.startsWith('{')) {
                            try { responseTime = JSON.parse(responseTime); } catch (e) {}
                        }
                        if (resultSessionId === currentSessionId) {
                            originalDisplayMessage('assistant', data.result.response, data.result.generated_image,
                                data.result.file_type, data.result.file_name,
                                data.result.assistant_timestamp || new Date().toISOString(), responseTime, modelUsed);
                            delete stableSessionStatus[resultSessionId];
                            updateLastVisit(currentSessionId);
                        } else {
                            setNewMessageIndicator(resultSessionId, true);
                            delete stableSessionStatus[resultSessionId];
                        }
                        lastCompletionTime[resultSessionId] = Date.now() + 5000;
                    }
                }
                delete pendingRequests[requestId];
                window.updateStatusCounter();
                fetchQueueStatus();
                setTimeout(() => loadSessionsFromServer(), 500);
            } else if (data.status === 'error') {
                clearInterval(pollInterval);
                const resultSessionId = data.result?.session_id || pendingRequests[requestId]?.sessionId;
                if (resultSessionId === currentSessionId) {
                    originalDisplayMessage('assistant', '⚠️ ' + t('error') + ': ' + (data.error || t('unknown_error')), null, null, null,
                        data.result?.assistant_timestamp || new Date().toISOString(), data.result?.response_time, 'system');
                    delete stableSessionStatus[resultSessionId];
                } else if (resultSessionId) {
                    // will be shown via queue status
                }
                lastCompletionTime[resultSessionId] = Date.now() + 5000;
                delete pendingRequests[requestId];
                window.updateStatusCounter();
                fetchQueueStatus();
            } else if (data.status === 'pending') {
                // status updates via fetchQueueStatus
            }
            if (pollCount >= maxPolls) {
                clearInterval(pollInterval);
                originalDisplayMessage('assistant', '⚠️ ' + t('request_timeout'),
                    null, null, null, new Date().toISOString(), null, 'system');
                delete stableSessionStatus[currentSessionId];
                delete pendingRequests[requestId];
            }
        } catch (error) {
            console.error('Error polling result:', error);
            if (pollCount >= maxPolls) clearInterval(pollInterval);
        }
    }, 3000);
}

async function sendMessage() {
    const input = document.getElementById('message-input');
    const text = input.value.trim();
    if (!text && !attachedFile) {
        alert(t('enter_message_or_file'));
        return;
    }

    const sendButton = document.getElementById('send-button');
    // Disable button only to prevent double-click during preparation
    sendButton.disabled = true;
    sendButton.innerHTML = '⏳ ' + t('sending');

    try {
        const messageCount = document.querySelectorAll('.user-message').length;
        if (messageCount === 0) {
            let newTitle = text ? text.slice(0, 40) + (text.length > 40 ? '...' : '') : '';
            if (!newTitle && attachedFile) {
                newTitle = attachedFile.name.slice(0, 40) + (attachedFile.name.length > 40 ? '...' : '');
            }
            if (newTitle) {
                updateSessionTitle(currentSessionId, newTitle);
                fetch('/api/sessions/' + currentSessionId + '/update-title', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({title: newTitle})
                }).catch(err => console.error('Error updating title:', err));
            }
        }

        delete lastCompletionTime[currentSessionId];
        const now = new Date();
        const timestamp = now.toISOString();

        const userContent = [];
        if (text) userContent.push({"type": "text", "text": text});

        let fileData = null, fileType = null, fileName = null;
        const tempAttachedFile = attachedFile;
        const tempText = text;

        const displayUserMessage = (fileData, fileType, fileName) => {
            if (window.IS_RELOADING) return;
            if (fileData) {
                let type = "file";
                if (fileType && fileType.startsWith('image/')) type = "image";
                else if (fileType && fileType.startsWith('audio/')) type = "audio";
                userContent.push({ "type": type, "file_data": fileData, "file_type": fileType, "file_name": fileName });
            }
            originalDisplayMessage('user', JSON.stringify(userContent), fileData, fileType, fileName, timestamp);
            input.value = '';
            attachedFile = null;
            document.getElementById('file-preview-container').style.display = 'none';
            document.getElementById('file-input').value = '';
        };

        const sendToServer = async () => {
            if (window.IS_RELOADING) return;
            try {
                let response;
                if (tempAttachedFile) {
                    const formData = new FormData();
                    formData.append('message', tempText);
                    formData.append('file', tempAttachedFile);
                    if (isVoiceRecorded) {
                        formData.append('voice_record', 'true');
                        isVoiceRecorded = false;
                    }
                    response = await fetch('/send_message', { method: 'POST', body: formData });
                } else {
                    response = await fetch('/send_message', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: tempText })
                    });
                }
                if (window.IS_RELOADING) return;
                const data = await response.json();
                if (window.IS_RELOADING) return;
                console.log('Server response:', data);
                if (data.transcribed_text) {
                    if (data.session_id && data.session_id === currentSessionId) {
                        originalDisplayMessage('assistant', '🎤 ' + t('transcribed') + ': ' + data.transcribed_text, null, null, null,
                            new Date().toISOString(), data.response_time, 'whisper');
                    } else if (data.session_id) {
                        setNewMessageIndicator(data.session_id, true);
                    } else {
                        originalDisplayMessage('assistant', '🎤 ' + t('transcribed') + ': ' + data.transcribed_text, null, null, null,
                            new Date().toISOString(), data.response_time, 'whisper');
                    }
                    if (!data.request_id) return;
                }
                if (data.status === 'queued') {
                    pendingRequests[data.request_id] = { sessionId: currentSessionId, processed: false };
                    window.updateStatusCounter();
                    startResultPolling(data.request_id);
                } else if (data.response) {
                    originalDisplayMessage('assistant', data.response, data.generated_image, data.file_type, data.file_name,
                        data.assistant_timestamp, data.response_time, data.model_used);
                }
            } catch (err) {
                if (window.IS_RELOADING) return;
                alert(t('error') + ': ' + err.message);
                console.error('Send message error:', err);
                const lastMessage = document.querySelector('.user-message:last-child');
                if (lastMessage) lastMessage.style.borderLeft = '3px solid #e74c3c';
            }
        };

        if (tempAttachedFile) {
            const reader = new FileReader();
            reader.onload = async function(e) {
                if (window.IS_RELOADING) return;
                try {
                    fileData = e.target.result.split(',')[1];
                    fileType = tempAttachedFile.type;
                    fileName = tempAttachedFile.name;
                    displayUserMessage(fileData, fileType, fileName);
                    // Start sending without awaiting, so button can be re-enabled immediately
                    sendToServer().catch(err => {
                        console.error('Error in sendToServer:', err);
                        if (!window.IS_RELOADING) alert(t('error') + ': ' + err.message);
                    });
                } catch (err) {
                    console.error('Error in reader.onload:', err);
                    if (!window.IS_RELOADING) alert(t('error') + ': ' + err.message);
                } finally {
                    // Re-enable send button immediately after starting the send process
                    sendButton.disabled = false;
                    sendButton.innerHTML = t('send');
                }
            };
            reader.readAsDataURL(tempAttachedFile);
        } else {
            try {
                displayUserMessage(null, null, null);
                // Start sending without awaiting
                sendToServer().catch(err => {
                    console.error('Error in sendToServer:', err);
                    if (!window.IS_RELOADING) alert(t('error') + ': ' + err.message);
                });
            } catch (err) {
                console.error('Error in no-file branch:', err);
                if (!window.IS_RELOADING) alert(t('error') + ': ' + err.message);
            } finally {
                sendButton.disabled = false;
                sendButton.innerHTML = t('send');
            }
        }
    } catch (err) {
        console.error('Unexpected error in sendMessage:', err);
        if (!window.IS_RELOADING) alert(t('error') + ': ' + err.message);
        sendButton.disabled = false;
        sendButton.innerHTML = t('send');
    }
}

// Override global functions with wrappers that call the originals
window.loadMessages = function(sessionId) {
    return originalLoadMessages(sessionId).then(() => {
        if (window.IS_RELOADING) return;
        setTimeout(addCopyButtonsToAllCodeBlocks, 100);
    });
};

window.displayMessage = function(role, content, fileData, fileType, fileName, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel) {
    if (window.IS_RELOADING) return;
    const result = originalDisplayMessage(role, content, fileData, fileType, fileName, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel);
    const messages = document.getElementById('chat-messages');
    if (messages) {
        const lastMessage = messages.lastElementChild;
        if (lastMessage) setTimeout(() => addCopyButtonsToMessage(lastMessage), 50);
    }
    return result;
};

function addCopyButtonsToAllCodeBlocks() {
    if (window.IS_RELOADING) return;
    document.querySelectorAll('.user-message, .assistant-message, .bot-message').forEach(addCopyButtonsToMessage);
}

// Initialize on DOM load
document.addEventListener('DOMContentLoaded', function() {
    loadSessionsFromServer().then(() => {
        // Load messages with error handling to prevent unhandled promise rejections
        originalLoadMessages(currentSessionId).catch(err => {
            console.error('Error loading messages after language switch:', err);
            // Optionally show a user-friendly message? Not needed for now.
        });
        startSyncInterval();
    });
    document.getElementById('new-session-button').addEventListener('click', createNewSession);
    document.getElementById('send-button').addEventListener('click', sendMessage);
    document.getElementById('message-input').addEventListener('keypress', function(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    document.getElementById('attach-file-button').addEventListener('click', function() {
        document.getElementById('file-input').click();
    });
    document.getElementById('file-input').addEventListener('change', function(e) {
        if (e.target.files.length > 0) {
            attachedFile = e.target.files[0];
            const preview = document.getElementById('file-preview-container');
            document.getElementById('file-preview-name').textContent = attachedFile.name;
            const fileSize = formatFileSize(attachedFile.size);
            const sizeSpan = document.getElementById('file-preview-size');
            if (sizeSpan) sizeSpan.textContent = ' (' + fileSize + ')';
            preview.style.display = 'block';
        }
    });
    document.getElementById('remove-file-button').addEventListener('click', function() {
        attachedFile = null;
        document.getElementById('file-input').value = '';
        document.getElementById('file-preview-container').style.display = 'none';
    });
    document.getElementById('save-chat-button').addEventListener('click', saveChatAsHTML);
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') closeImageModal();
    });
    window.updateStatusCounter();
    fetchQueueStatus();
    document.getElementById('voice-record-button').addEventListener('click', toggleVoiceRecording);
    setTimeout(setupCopyButtonsObserver, 500);
    setTimeout(addCopyButtonsToAllCodeBlocks, 1000);
});