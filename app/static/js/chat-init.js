// app/static/js/chat-init.js
// Main chat initialization and send message logic
const originalLoadMessages = loadMessages;
const originalDisplayMessage = displayMessage;

function isDuplicateMessage(msg) {
    const messages = document.querySelectorAll(`.${msg.role}-message`);
    for (let el of messages) {
        // Check by messageId (priority)
        if (el.dataset.messageId && msg.id && el.dataset.messageId === String(msg.id)) {
            return true;
        }
        // Check by tempId
        if (el.dataset.tempId && msg.timestamp) {
            const tempId = `temp-${msg.timestamp}`;
            if (el.dataset.tempId === tempId) {
                return true;
            }
        }
        // FIX: Check by filename for audio/image files (reliable duplicate detection)
        if (msg.file_name && el.dataset.fileName === msg.file_name) {
            return true;
        }
        // FIX: For audio files, skip content check because client has base64 data
        // while server returns message without base64 in content field
        const isAudio = msg.file_type?.startsWith('audio/') || 
                       msg.file_name?.match(/\.(webm|mp3|wav|ogg)$/);
        
        if (!isAudio) {
            // Check by content and timestamp (fallback) only for text messages
            if (el.dataset.rawText === msg.content &&
                Math.abs(new Date(el.dataset.timestamp) - new Date(msg.timestamp)) < 2000) {
                return true;
            }
        }
    }
    return false;
}

function startMessagePolling() {
    if (messagePollingInterval) clearInterval(messagePollingInterval);
    messagePollingInterval = setInterval(pollNewMessages, 5000);
}

function stopMessagePolling() {
    if (messagePollingInterval) {
        clearInterval(messagePollingInterval);
        messagePollingInterval = null;
    }
}

async function pollNewMessages() {
    if (window.IS_RELOADING || !currentSessionId) return;
    
    const messagesContainer = document.getElementById('chat-messages');
    const lastMessageEl = messagesContainer.lastElementChild;
    
    // FIX: Update lastMessageTimestamp from the last message in DOM before polling
    if (lastMessageEl && lastMessageEl.dataset.timestamp) {
        lastMessageTimestamp = lastMessageEl.dataset.timestamp;
    } else {
        return;
    }
    
    try {
        const response = await fetch(`/api/sessions/${currentSessionId}/messages?since=${encodeURIComponent(lastMessageTimestamp)}`);
        if (!response.ok) {
            console.error('Failed to fetch new messages:', response.status);
            return;
        }
        const newMessages = await response.json();
        
        if (newMessages.length > 0) {
            for (const msg of newMessages) {
                // FIX: Check duplicate by messageId first
                if (msg.id && displayedMessageIds.has(msg.id)) {
                    console.log('pollNewMessages: Skipping duplicate message by ID', msg.id);
                    continue;
                }
                // FIX: Check duplicate by filename, tempId, or content/timestamp
                if (isDuplicateMessage(msg)) {
                    console.log('pollNewMessages: Skipping duplicate message by filename/timestamp/content', msg.id);
                    continue;
                }
                
                let responseTime = null;
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
                
                originalDisplayMessage(
                    msg.role,
                    msg.content,
                    msg.file_data,
                    msg.file_type,
                    msg.file_name,
                    msg.file_path,
                    msg.timestamp,
                    responseTime,
                    msg.model_name,
                    mmTime,
                    genTime,
                    mmModel,
                    genModel,
                    msg.id
                );
            }
            updateLastVisit(currentSessionId);
        }
    } catch (err) {
        console.error('Error polling new messages:', err);
    }
}

function startResultPolling(requestId) {
    if (window.IS_RELOADING) return;
    console.log('startResultPolling: Start polling for request:', requestId);
    
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
                    
                    // Handle transcription result that may spawn a new processing request
                    if (data.result.transcribed_text) {
                        const resultSessionId = data.result.session_id || pendingRequests[requestId]?.sessionId;
                        if (resultSessionId === currentSessionId) {
                            // Display transcribed text message
                            originalDisplayMessage('assistant', '🎤 ' + t('transcribed') + ': ' + data.result.transcribed_text, null, null, null, null,
                                data.result.assistant_timestamp || new Date().toISOString(), data.result.response_time, 'whisper',
                                null, null, null, null, data.result.transcribed_message_id);
                            // If there is a new request_id for processing, start polling it
                            if (data.result.request_id) {
                                pendingRequests[data.result.request_id] = { sessionId: resultSessionId, processed: false };
                                window.updateStatusCounter();
                                startResultPolling(data.result.request_id);
                                setLocalTranscribing(resultSessionId, false);
                            } else {
                                setLocalTranscribing(resultSessionId, false);
                            }
                        } else {
                            setNewMessageIndicator(resultSessionId, true);
                            if (data.result.request_id) {
                                pendingRequests[data.result.request_id] = { sessionId: resultSessionId, processed: false };
                                startResultPolling(data.result.request_id);
                            }
                            setLocalTranscribing(resultSessionId, false);
                        }
                        delete pendingRequests[requestId];
                        window.updateStatusCounter();
                        fetchQueueStatus();
                        setTimeout(() => loadSessionsFromServer(), 500);
                        return;
                    }
                    
                    if (data.result.error) {
                        if (resultSessionId === currentSessionId) {
                            originalDisplayMessage('assistant', '⚠️ ' + data.result.error, null, null, null, null,
                                data.result.assistant_timestamp || new Date().toISOString(), data.result.response_time, 'system',
                                null, null, null, null, null);
                        }
                        if (resultSessionId) {
                            setLocalTranscribing(resultSessionId, false);
                        }
                    } else if (data.result.messages) {
                        for (const msg of data.result.messages) {
                            if (msg.message_id && displayedMessageIds.has(msg.message_id)) {
                                console.log('Skipping duplicate camera message by ID', msg.message_id);
                                continue;
                            }
                            originalDisplayMessage('assistant', msg.response, msg.file_data, msg.file_type, msg.file_name, msg.file_path,
                                msg.assistant_timestamp, msg.response_time, msg.model_used,
                                null, null, null, null, msg.message_id);
                        }
                        if (resultSessionId) {
                            setLocalTranscribing(resultSessionId, false);
                        }
                    } else if (data.result.response) {
                        if (data.result.message_id && displayedMessageIds.has(data.result.message_id)) {
                            console.log('Skipping duplicate response message by ID', data.result.message_id);
                        } else {
                            let responseTime = data.result.response_time;
                            let modelUsed = data.result.model_used;
                            
                            if (data.result.mm_time && data.result.gen_time) {
                                responseTime = { mm_time: data.result.mm_time, gen_time: data.result.gen_time, mm_model: data.result.mm_model, gen_model: data.result.gen_model };
                                modelUsed = data.result.gen_model;
                            } else if (typeof responseTime === 'string' && responseTime.startsWith('{')) {
                                try { responseTime = JSON.parse(responseTime); } catch (e) {}
                            }
                            
                            if (resultSessionId === currentSessionId) {
                                originalDisplayMessage('assistant', data.result.response, data.result.file_data,
                                    data.result.file_type, data.result.file_name, data.result.file_path,
                                    data.result.assistant_timestamp || new Date().toISOString(), responseTime, modelUsed,
                                    null, null, null, null, data.result.message_id);
                                updateLastVisit(currentSessionId);
                            } else {
                                setNewMessageIndicator(resultSessionId, true);
                            }
                        }
                        if (resultSessionId) {
                            setLocalTranscribing(resultSessionId, false);
                        }
                    }
                    
                    // FIX: Clear queue status before setting new message indicator to allow envelope to show
                    if (resultSessionId) {
                        setLocalTranscribing(resultSessionId, false);
                        // Clear queue info for this session so envelope icon can appear
                        if (sessionQueueInfo[resultSessionId]) {
                            sessionQueueInfo[resultSessionId].processing = false;
                            sessionQueueInfo[resultSessionId].queued = 0;
                        }
                        // Force immediate UI update
                        updateSessionsListFromData();
                    }
                    
                    if (resultSessionId) {
                        fetchQueueStatus();
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
                    originalDisplayMessage('assistant', '⚠️ ' + t('error') + ': ' + (data.error || t('unknown_error')), null, null, null, null,
                        data.result?.assistant_timestamp || new Date().toISOString(), data.result?.response_time, 'system',
                        null, null, null, null, null);
                }
                
                if (resultSessionId) {
                    setLocalTranscribing(resultSessionId, false);
                }
                
                delete pendingRequests[requestId];
                window.updateStatusCounter();
                fetchQueueStatus();
            }
            
            if (pollCount >= maxPolls) {
                clearInterval(pollInterval);
                originalDisplayMessage('assistant', '⚠️ ' + t('request_timeout'),
                    null, null, null, null, new Date().toISOString(), null, 'system',
                    null, null, null, null, null);
                delete pendingRequests[requestId];
            }
        } catch (error) {
            console.error('Error polling result:', error);
            if (pollCount >= maxPolls) clearInterval(pollInterval);
        }
    }, 3000);
}

async function sendMessage() {
    // FIX: Always reset isSending flag at the start
    if (isSending) {
        console.log('Send already in progress, ignoring duplicate');
        return;
    }
    isSending = true;
    
    const input = document.getElementById('message-input');
    const text = input.value.trim();
    const sendButton = document.getElementById('send-button');
    
    if (!text && !attachedFile) {
        isSending = false;
        alert(t('enter_message_or_file'));
        return;
    }
    
    // Lock button immediately
    sendButton.disabled = true;
    sendButton.innerHTML = '⏳ ' + t('sending');
    
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
    
    const now = new Date();
    const timestamp = now.toISOString();
    
    const userContent = [];
    if (text) userContent.push({"type": "text", "text": text});
    
    let fileData = null, fileType = null, fileName = null, filePath = null;
    const tempAttachedFile = attachedFile;
    const tempText = text;
    // FIX: Determine if audio file early to control button unlock behavior
    const isAudioFile = tempAttachedFile && tempAttachedFile.type && tempAttachedFile.type.startsWith('audio/');
    
    const displayUserMessage = (fileData, fileType, fileName, filePath) => {
        if (window.IS_RELOADING) return;
        
        if (fileData || filePath) {
            let type = "file";
            if (fileType && fileType.startsWith('image/')) type = "image";
            else if (fileType && fileType.startsWith('audio/')) type = "audio";
            userContent.push({ "type": type, "file_data": fileData, "file_type": fileType, "file_name": fileName, "file_path": filePath });
        }
        
        const msgElement = originalDisplayMessage('user', JSON.stringify(userContent), fileData, fileType, fileName, filePath, timestamp);
        
        // FIX: Update lastMessageTimestamp immediately to prevent polling from fetching this message again
        lastMessageTimestamp = timestamp;
        
        const tempId = `temp-${timestamp}`;
        if (msgElement) {
            msgElement.dataset.tempId = tempId;
        }
        
        input.value = '';
        attachedFile = null;
        document.getElementById('file-preview-container').style.display = 'none';
        document.getElementById('file-input').value = '';
    };
    
    const unlockSendButton = () => {
        console.log('unlockSendButton called, isSending was:', isSending);
        if (sendButton) {
            sendButton.disabled = false;
            sendButton.innerHTML = t('send');
        }
        isSending = false;
        console.log('unlockSendButton finished, isSending now:', isSending);
    };
    
    const sendToServer = () => {
        if (window.IS_RELOADING) return;
        
        console.log('sendToServer starting, isAudioFile:', isAudioFile);
        
        (async () => {
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
                    response = await fetch('/api/send_message', { method: 'POST', body: formData });
                } else {
                    response = await fetch('/api/send_message', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: tempText })
                    });
                }
                
                if (window.IS_RELOADING) return;
                
                const data = await response.json();
                
                if (window.IS_RELOADING) return;
                
                console.log('Server response:', data);
                
                if (data.resize_notice) {
                    originalDisplayMessage('assistant', data.resize_notice, null, null, null, null,
                        new Date().toISOString(), 0, 'system');
                }
                
                // FIX: Update messageId immediately when received from server
                if (data.user_message_id) {
                    const userMessages = document.querySelectorAll('.user-message');
                    const lastUserMsg = userMessages[userMessages.length - 1];
                    if (lastUserMsg && lastUserMsg.dataset.timestamp === timestamp) {
                        if (lastUserMsg.dataset.tempId) {
                            // Remove tempId from tracking
                            delete lastUserMsg.dataset.tempId;
                        }
                        lastUserMsg.dataset.messageId = data.user_message_id;
                        displayedMessageIds.add(data.user_message_id);
                        
                        console.log('sendMessage: Updated messageId to', data.user_message_id, 'and timestamp to', timestamp);
                    }
                }
                
                // Audio files are now queued for transcription, no immediate transcribed_text
                if (data.transcribed_text) {
                    console.log('Transcription completed');
                    setLocalTranscribing(currentSessionId, false);
                    
                    if (data.request_id) {
                        if (!sessionQueueInfo[currentSessionId]) {
                            sessionQueueInfo[currentSessionId] = { processing: false, queued: 1 };
                        } else {
                            sessionQueueInfo[currentSessionId].queued += 1;
                        }
                        updateSessionsListFromData();
                        pendingRequests[data.request_id] = { sessionId: currentSessionId, processed: false };
                        window.updateStatusCounter();
                        startResultPolling(data.request_id);
                        fetchQueueStatus();
                    } else {
                        const targetSessionId = data.session_id || currentSessionId;
                        if (targetSessionId === currentSessionId) {
                            const assistantMsgId = originalDisplayMessage('assistant', '🎤 ' + t('transcribed') + ': ' + data.transcribed_text, null, null, null, null,
                                new Date().toISOString(), data.response_time, 'whisper');
                            if (data.transcribed_message_id) {
                                const assistantMessages = document.querySelectorAll('.assistant-message');
                                const lastAssistant = assistantMessages[assistantMessages.length - 1];
                                if (lastAssistant) {
                                    lastAssistant.dataset.messageId = data.transcribed_message_id;
                                    displayedMessageIds.add(data.transcribed_message_id);
                                }
                            }
                        } else {
                            setNewMessageIndicator(targetSessionId, true);
                        }
                    }
                    return;
                }
                
                if (data.status === 'queued') {
                    if (!sessionQueueInfo[currentSessionId]) {
                        sessionQueueInfo[currentSessionId] = { processing: false, queued: 1 };
                    } else {
                        sessionQueueInfo[currentSessionId].queued += 1;
                    }
                    updateSessionsListFromData();
                    pendingRequests[data.request_id] = { sessionId: currentSessionId, processed: false };
                    window.updateStatusCounter();
                    startResultPolling(data.request_id);
                } else if (data.response) {
                    originalDisplayMessage('assistant', data.response, data.file_data, data.file_type, data.file_name, data.file_path,
                        data.assistant_timestamp, data.response_time, data.model_used);
                }
                
            } catch (err) {
                console.error('Send message error:', err);
                if (!window.IS_RELOADING) alert(t('error') + ': ' + err.message);
                const lastMessage = document.querySelector('.user-message:last-child');
                if (lastMessage) lastMessage.style.borderLeft = '3px solid #e74c3c';
                setLocalTranscribing(currentSessionId, false);
            } finally {
                // Always unlock send button after request completes (success or error)
                unlockSendButton();
            }
        })();
    };
    
    if (tempAttachedFile) {
        const reader = new FileReader();
        let processed = false;
        
        reader.onload = async function(e) {
            if (processed) return;
            processed = true;
            
            try {
                fileData = e.target.result.split(',')[1];
                fileType = tempAttachedFile.type;
                fileName = tempAttachedFile.name;
                
                console.log('File attached:', fileName, 'Type:', fileType, 'isAudioFile:', isAudioFile);
                
                // FIX: Set transcribing flag for audio files BEFORE displaying message
                if (isAudioFile) {
                    console.log('Audio file detected, setting transcribing flag for session:', currentSessionId);
                    setLocalTranscribing(currentSessionId, true);
                    // Force update for all devices
                    setTimeout(() => updateSessionsListFromData(), 100);
                }
                
                displayUserMessage(fileData, fileType, fileName, null);
                sendToServer();
                
                // Note: unlockSendButton is now handled in finally block of sendToServer
            } catch (err) {
                console.error('Error in reader.onload:', err);
                if (!window.IS_RELOADING) alert(t('error') + ': ' + err.message);
                setLocalTranscribing(currentSessionId, false);
                isSending = false;
                unlockSendButton();
            }
        };
        
        reader.onerror = () => {
            console.error('FileReader error');
            setLocalTranscribing(currentSessionId, false);
            isSending = false;
            unlockSendButton();
        };
        
        reader.readAsDataURL(tempAttachedFile);
        
    } else {
        try {
            displayUserMessage(null, null, null, null);
            sendToServer();
            // For text messages, button remains locked until response (unlocked in finally)
        } catch (err) {
            console.error('Error in no-file branch:', err);
            if (!window.IS_RELOADING) alert(t('error') + ': ' + err.message);
            unlockSendButton();
        }
    }
}

window.loadMessages = function(sessionId) {
    console.log('loadMessages called for session', sessionId);
    
    stopMessagePolling();
    
    const statusCounter = document.getElementById('status-counter');
    if (statusCounter) {
        statusCounter.innerHTML = '⏳ ' + t('loading');
    }
    
    return originalLoadMessages(sessionId)
        .then(() => {
            console.log('loadMessages completed for session', sessionId);
            
            if (window.IS_RELOADING) return;
            
            setTimeout(addCopyButtonsToAllCodeBlocks, 100);
            startMessagePolling();
            
            if (statusCounter) {
                window.updateStatusCounter();
            }
        })
        .catch(err => {
            console.error('Error in loadMessages:', err);
            if (statusCounter) {
                statusCounter.innerHTML = '❌';
                setTimeout(() => window.updateStatusCounter(), 2000);
            }
        });
};

window.displayMessage = function(role, content, fileData, fileType, fileName, filePath, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel, messageId) {
    if (window.IS_RELOADING) return;
    
    const result = originalDisplayMessage(role, content, fileData, fileType, fileName, filePath, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel, messageId);
    
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

document.addEventListener('DOMContentLoaded', function() {
    // FIX: Validate currentSessionId before proceeding
    if (!window.initialSessionId) {
        console.error('No initial session ID! Creating new session...');
        createNewSession();
        return;
    }
    
    loadSessionsFromServer().then(() => {
        originalLoadMessages(currentSessionId).catch(err => {
            console.error('Error loading messages after language switch:', err);
        }).finally(() => {
            startMessagePolling();
        });
        startSyncInterval();
    });
    
    if (typeof initDocumentsView === 'function') {
        initDocumentsView();
    }
    
    document.getElementById('new-session-button').addEventListener('click', function(e) {
        e.stopPropagation();
        createNewSession();
    });
    
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
    
    if (typeof playTTS === 'function') {
        window.playTTS = playTTS;
    }
    if (typeof resetTtsState === 'function') {
        window.resetTtsState = resetTtsState;
    }
});