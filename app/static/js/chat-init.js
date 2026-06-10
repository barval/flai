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








async function sendMessage() {
    // FIX: Always reset isSending flag at the start
    if (isSending) {
        dlog('Send already in progress, ignoring duplicate');
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

    // Immediately show hourglass in session list (task goes to queue, not processing)
    // queue_position is 0 — real position is unknown until the server responds,
    // so UI shows ⏳ without a number (avoids duplicate "1"/"999" across sessions).
    sessionQueueInfo[currentSessionId] = {
        processing: false,
        queued: 1,
        queue_position: 0,
        has_transcribing: false
    };
    // Register an optimistic pending request BEFORE the network roundtrip.
    // Without this, fetchQueueStatus() (e.g. from SSE reconnect or
    // visibilitychange) could clobber the hourglass because pendingRequestIds
    // wouldn't yet know about the request. The temp ID is not persisted to
    // sessionStorage — only the real server-assigned request_id is.
    const tempRequestId = 'temp-' + (typeof crypto !== 'undefined' && crypto.randomUUID
        ? crypto.randomUUID()
        : Date.now() + '-' + Math.random().toString(36).slice(2));
    trackPendingRequest(tempRequestId, currentSessionId, false);
    window._activeSendTempId = tempRequestId;
    window._lastSessionsJson = null;
    if (typeof updateSessionsListFromData === 'function') updateSessionsListFromData();
    
    const messageCount = document.querySelectorAll('.user-message').length;
    if (messageCount === 0) {
        let newTitle = text ? text.slice(0, 40) + (text.length > 40 ? '...' : '') : '';
        if (!newTitle && attachedFile) {
            newTitle = attachedFile.name.slice(0, 40) + (attachedFile.name.length > 40 ? '...' : '');
        }
        if (newTitle) {
            updateSessionTitle(currentSessionId, newTitle);
            fetchWithCSRF('/api/sessions/' + currentSessionId + '/update-title', {
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
            dlog('displayUserMessage: Set tempId', tempId, 'on message element');
        } else {
            dwarn('displayUserMessage: msgElement is null/undefined, cannot set tempId');
        }

        input.value = '';
        attachedFile = null;
        document.getElementById('file-preview-container').classList.add('hidden');
        document.getElementById('file-input').value = '';
    };
    
    const unlockSendButton = () => {
        dlog('unlockSendButton called, isSending was:', isSending);
        if (sendButton) {
            sendButton.disabled = false;
            sendButton.innerHTML = t('send');
        }
        isSending = false;
        dlog('unlockSendButton finished, isSending now:', isSending);
    };
    
    const sendToServer = () => {
        if (window.IS_RELOADING) return;
        
        dlog('sendToServer starting, isAudioFile:', isAudioFile);
        
        (async () => {
            try {
                let response;
                
                if (tempAttachedFile) {
                    const formData = new FormData();
                    formData.append('message', tempText);
                    formData.append('file', tempAttachedFile);
                    formData.append('session_id', currentSessionId);

                    if (isVoiceRecorded && attachedVoiceBlob) {
                        // Image + voice: send both files
                        formData.append('voice', attachedVoiceBlob);
                        formData.append('voice_record', 'true');
                        isVoiceRecorded = false;
                        attachedVoiceBlob = null;
                    } else if (isVoiceRecorded) {
                        formData.append('voice_record', 'true');
                        isVoiceRecorded = false;
                    }

                    response = await fetchWithCSRF('/api/send_message', { method: 'POST', body: formData });
                } else {
                    response = await fetchWithCSRF('/api/send_message', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: tempText, session_id: currentSessionId })
                    });
                }
                
                if (window.IS_RELOADING) return;

                // Check if response is JSON before parsing
                const contentType = response.headers.get('content-type');
                if (!contentType || !contentType.includes('application/json')) {
                    console.error('Server returned non-JSON response:', response.status);
                    const text = await response.text();
                    console.error('Response content:', text.substring(0, 200));

                    // Check if this is a session/auth issue (HTML login page returned)
                    if (text.includes('login') || text.includes('Login') || response.status === 401 || response.status === 403) {
                        window.location.href = '/login';
                        return;
                    }

                    originalDisplayMessage('assistant', t('server_error_invalid_response'), null, null, null, null,
                        new Date().toISOString(), 0, 'system');
                    unlockSendButton();
                    return;
                }

                const data = await response.json();

                // Handle session expiry
                if (data.session_expired) {
                    dwarn('Session expired — redirecting to login');
                    window.location.href = '/login';
                    return;
                }

                if (window.IS_RELOADING) return;
                
                dlog('Server response:', data);
                
                if (data.resize_notice) {
                    const noticeMsgId = data.resize_notice_id || ('resize-' + timestamp);
                    originalDisplayMessage('assistant', data.resize_notice, null, null, null, null,
                        new Date().toISOString(), 0, 'system', null, null, null, null, noticeMsgId);
                    if (data.resize_notice_id) displayedMessageIds.add(data.resize_notice_id);
                }
                
                // FIX: Update messageId immediately when received from server
                if (data.user_message_id) {
                    // Find message by tempId first
                    let targetMsg = document.querySelector(`.user-message[data-tempId="temp-${timestamp}"]`);
                    
                    if (!targetMsg) {
                        // Fallback: find last user message with matching timestamp
                        const userMessages = document.querySelectorAll('.user-message[data-timestamp="' + timestamp + '"]');
                        if (userMessages.length > 0) {
                            targetMsg = userMessages[userMessages.length - 1];
                        }
                    }
                    
                    if (!targetMsg) {
                        // Last resort: find most recent user message
                        const allUserMessages = document.querySelectorAll('.user-message');
                        if (allUserMessages.length > 0) {
                            targetMsg = allUserMessages[allUserMessages.length - 1];
                            dlog('sendMessage: Using fallback - last user message');
                        }
                    }

                    if (targetMsg) {
                        if (targetMsg.dataset.tempId) {
                            // Remove tempId from tracking
                            delete targetMsg.dataset.tempId;
                        }
                        targetMsg.dataset.messageId = data.user_message_id;
                        displayedMessageIds.add(data.user_message_id);
                        dlog('sendMessage: Updated messageId to', data.user_message_id);
                    } else {
                        dwarn('sendMessage: Could not find user message to update messageId. Total user messages:', document.querySelectorAll('.user-message').length);
                    }
                }
                
                // Audio files are now queued for transcription, no immediate transcribed_text
                if (data.transcribed_text) {
                    dlog('Transcription completed');
                    setLocalTranscribing(currentSessionId, false);
                    
                    if (data.request_id) {
                        if (!sessionQueueInfo[currentSessionId]) {
                            sessionQueueInfo[currentSessionId] = { processing: false, queued: 1 };
                        } else {
                            sessionQueueInfo[currentSessionId].queued += 1;
                        }
                        // Replace optimistic temp ID with the real server-assigned request_id.
                        if (window._activeSendTempId) {
                            clearPendingRequest(window._activeSendTempId);
                            window._activeSendTempId = null;
                        }
                        updateSessionsListFromData();
                        trackPendingRequest(data.request_id, currentSessionId);
                        window.updateStatusCounter();
                        fetchQueueStatus();
                    } else {
                        // Audio file — no further processing, clear ⚡
                        const targetSessionId = data.session_id || currentSessionId;
                        if (targetSessionId === currentSessionId) {
                            var transcribedContent = JSON.stringify({prefix: '🎤 ' + t('transcribed') + ': ', text: data.transcribed_text});
                            const assistantMsgId = originalDisplayMessage('assistant', transcribedContent, null, null, null, null,
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
                        if (typeof clearSessionQueue === 'function') {
                            clearSessionQueue(targetSessionId);
                        }
                    }
                    return;
                }
                
                if (data.status === 'queued') {
                    // Check if another session is already processing (only ONE ⚡ allowed)
                    var alreadyProcessing = false;
                    for (var sid in sessionQueueInfo) {
                        if (sessionQueueInfo[sid].processing && sid !== currentSessionId) {
                            alreadyProcessing = true;
                            break;
                        }
                    }

                    if (!sessionQueueInfo[currentSessionId]) {
                        sessionQueueInfo[currentSessionId] = {
                            processing: !alreadyProcessing,
                            queued: alreadyProcessing ? 1 : 0,
                            queue_position: alreadyProcessing ? (data.position ?? 0) : 0,
                        };
                    } else {
                        sessionQueueInfo[currentSessionId].processing = !alreadyProcessing;
                        sessionQueueInfo[currentSessionId].queued = alreadyProcessing ? 1 : 0;
                        sessionQueueInfo[currentSessionId].queue_position = alreadyProcessing ? (data.position ?? 0) : 0;
                    }

                    // Replace optimistic temp ID with the real server-assigned request_id.
                    // Real ID is persisted to sessionStorage (survives page refresh);
                    // temp ID was in-memory only.
                    if (window._activeSendTempId) {
                        clearPendingRequest(window._activeSendTempId);
                        window._activeSendTempId = null;
                    }
                    updateSessionsListFromData();
                    trackPendingRequest(data.request_id, currentSessionId);
                    window.updateStatusCounter();
                    if (typeof fetchQueueStatus === 'function') fetchQueueStatus();
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
                // Drop the optimistic temp ID — server never saw this request.
                if (window._activeSendTempId) {
                    clearPendingRequest(window._activeSendTempId);
                    window._activeSendTempId = null;
                }
                if (typeof clearSessionQueue === 'function') clearSessionQueue(currentSessionId);
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
                
                dlog('File attached:', fileName, 'Type:', fileType, 'isAudioFile:', isAudioFile);
                
                // FIX: Set transcribing flag for audio files BEFORE displaying message
                if (isAudioFile) {
                    dlog('Audio file detected, setting transcribing flag for session:', currentSessionId);
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
    dlog('loadMessages called for session', sessionId);

    return originalLoadMessages(sessionId)
        .then(() => {
            dlog('loadMessages completed for session', sessionId);

            if (window.IS_RELOADING) return;

            setTimeout(addCopyButtonsToAllCodeBlocks, 100);
        })
        .catch(err => {
            console.error('Error in loadMessages:', err);
            throw err;
        });
};

window.displayMessage = function(role, content, fileData, fileType, fileName, filePath, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel, messageId, responseStyle, completionTokens, fileSize) {
    if (window.IS_RELOADING) return;
    
    const result = originalDisplayMessage(role, content, fileData, fileType, fileName, filePath, timestamp, responseTime, modelName, mmTime, genTime, mmModel, genModel, messageId, responseStyle, completionTokens, fileSize);
    
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
    // Validate currentSessionId before proceeding
    if (!window.initialSessionId) {
        console.error('No initial session ID! Creating new session...');
        createNewSession();
        return;
    }

    // Show loading indicator immediately
    const statusCounter = document.getElementById('status-counter');
    if (statusCounter) {
        statusCounter.innerHTML = '⏳ ' + t('loading');
    }
    showMessagesLoadingIndicator();

    loadSessionsFromServer().then(() => {
        // Use the wrapped loadMessages to ensure proper loading indicator handling
        window.loadMessages(currentSessionId).catch(err => {
            console.error('Error loading messages after language switch:', err);
        }).finally(() => {
            hideMessagesLoadingIndicator();
            // Restore streaming messages from sessionStorage (page reload during stream)
            if (typeof restoreStreamingFromSessionStorage === 'function') {
                restoreStreamingFromSessionStorage();
            }
            // Restore progress bars from Redis (page reload during generation)
            if (typeof restoreTaskProgress === 'function') {
                restoreTaskProgress();
            }
            // Re-check queue status after restoring pendingRequestIds
            if (typeof fetchQueueStatus === 'function') {
                fetchQueueStatus();
            }
            // Restore TTS button state if TTS is playing
            if (typeof restoreTTSButtonState === 'function') {
                restoreTTSButtonState();
            }
        });
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
            // FIX: Remove 'hidden' class instead of setting display (CSS has !important)
            preview.classList.remove('hidden');
        }
    });
    
    document.getElementById('remove-file-button').addEventListener('click', function() {
        attachedFile = null;
        attachedVoiceBlob = null;
        document.getElementById('file-input').value = '';
        // FIX: Add 'hidden' class back instead of setting display
        document.getElementById('file-preview-container').classList.add('hidden');
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
    if (typeof restoreTTSButtonState === 'function') {
        window.restoreTTSButtonState = restoreTTSButtonState;
    }
});

// Initialize collapsible sessions sidebar
document.addEventListener('DOMContentLoaded', function() {
    if (typeof initCollapsibleSessions === 'function') {
        initCollapsibleSessions();
    }
});