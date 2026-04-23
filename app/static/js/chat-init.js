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
    if (window.IS_RELOADING || window.isSwitchingSession || !currentSessionId) return;

    // Skip polling if current session is no longer in sessionsData (likely deleted on server)
    if (!sessionsData[currentSessionId]) {
        return;
    }

    // FIX: Don't poll if there are active pending requests
    // This prevents loading messages from DB while waiting for response
    const hasActiveRequests = Object.keys(pendingRequests).length > 0;
    if (hasActiveRequests) {
        return;
    }

    const messagesContainer = document.getElementById('chat-messages');
    const lastMessageEl = messagesContainer.lastElementChild;

    // Update lastMessageTimestamp from the last message in DOM before polling
    if (lastMessageEl && lastMessageEl.dataset.timestamp) {
        lastMessageTimestamp = lastMessageEl.dataset.timestamp;
    } else {
        return;
    }

    try {
        const response = await fetch(`/api/sessions/${currentSessionId}/messages?since=${encodeURIComponent(lastMessageTimestamp)}`);
        if (!response.ok) {
            // Silently handle 404 (session deleted or inaccessible) — don't spam console
            if (response.status !== 404) {
                console.error('Failed to fetch new messages:', response.status);
            }
            return;
        }
        const newMessages = await response.json();

        if (newMessages.length > 0) {
            for (const msg of newMessages) {
                // FIX: Check if message already exists in DOM by messageId
                if (msg.id) {
                    const existingMsg = document.querySelector(`[data-message-id="${msg.id}"]`);
                    if (existingMsg) {
                        console.debug('pollNewMessages: Message', msg.id, 'already in DOM, skipping');
                        displayedMessageIds.add(msg.id);
                        continue;
                    }
                }

                // Check duplicate by messageId first
                if (msg.id && displayedMessageIds.has(msg.id)) {
                    console.debug('pollNewMessages: Skipping duplicate message by ID', msg.id);
                    continue;
                }
                
                // Check duplicate by tempId (for messages displayed before server response)
                if (msg.id) {
                    const tempId = `temp-${msg.timestamp}`;
                    const existingWithTempId = document.querySelector(`[data-tempId="${tempId}"]`);
                    if (existingWithTempId) {
                        console.debug('pollNewMessages: Skipping message with tempId', tempId);
                        displayedMessageIds.add(msg.id);
                        continue;
                    }
                }
                
                // Display message (both user and assistant)
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

                // Update sessionsData count to keep sidebar in sync with DOM
                // This prevents the background sync loop from flagging this session as "Unread" later
                if (sessionsData[currentSessionId]) {
                    sessionsData[currentSessionId].message_count = (sessionsData[currentSessionId].message_count || 0) + 1;
                    sessionsData[currentSessionId].updated_at = msg.timestamp || new Date().toISOString();
                }
                
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

let activePolls = {}; // requestId -> { intervalId, abortController }: prevents zombie polling after session switch

function startResultPolling(requestId) {
    if (window.IS_RELOADING) return;

    // Clear existing poll if any (e.g. user clicked send twice)
    if (activePolls[requestId]) {
        // Abort any in-flight fetch request
        if (activePolls[requestId].abortController) {
            activePolls[requestId].abortController.abort();
        }
        clearInterval(activePolls[requestId].intervalId);
    }

    console.debug('startResultPolling: Start polling for request:', requestId);

    // Create an AbortController to cancel in-flight fetch requests
    const abortController = new AbortController();
    let pollCount = 0;
    const maxPolls = 240; // 12 minutes at 3s interval (was 120 = 6 min)
    let isCancelled = false; // Flag to silently drop responses after cancellation

    const pollInterval = setInterval(async () => {
        // If polling was cancelled externally (e.g. session switch), exit immediately
        if (window.IS_RELOADING || isCancelled || !activePolls[requestId]) {
            clearInterval(pollInterval);
            delete activePolls[requestId];
            return;
        }

        pollCount++;

        // Store reference for external cancellation
        activePolls[requestId] = {
            intervalId: pollInterval,
            abortController,
            cancel: () => { isCancelled = true; }
        };

        try {
            const response = await fetch('/api/queue/result/' + requestId, {
                signal: abortController.signal
            });

            // Check if response is JSON (session may have expired)
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                console.warn('Poll returned non-JSON — session likely expired');
                clearInterval(pollInterval);
                window.location.href = '/login';
                return;
            }

            // Handle 404 (task expired or not found) — just stop polling silently
            if (response.status === 404) {
                clearInterval(pollInterval);
                delete activePolls[requestId];
                if (isCancelled) return; // Silently drop if already cancelled
                // Clean up pending request and session queue state
                const pendingSessionId = pendingRequests[requestId]?.sessionId;
                delete pendingRequests[requestId];

                // Only update status if this session is still tracked
                // (if user already switched, don't interfere with new session's status)
                if (pendingSessionId && sessionQueueInfo[pendingSessionId]) {
                    sessionQueueInfo[pendingSessionId] = {
                        processing: false,
                        queued: 0,
                        queue_position: 0,
                        has_transcribing: false
                    };
                    // Only refresh status if user is still on this session
                    if (pendingSessionId === currentSessionId) {
                        window.updateStatusCounter();
                        fetchQueueStatus();
                    }
                }
                return;
            }

            const data = await response.json();

            // Handle session expiry
            if (data.session_expired) {
                console.warn('Session expired during polling — redirecting to login');
                clearInterval(pollInterval);
                window.location.href = '/login';
                return;
            }

            if (data.status === 'error' && data.error) {
                clearInterval(pollInterval);
                if (window.IS_RELOADING) return;
                return;
            }

            if (window.IS_RELOADING) {
                clearInterval(pollInterval);
                return;
            }

            if (data.status === 'completed') {
                clearInterval(pollInterval);

                if (data.result) {
                    console.debug('[POLL] Received result for request:', requestId, 'result keys:', Object.keys(data.result));
                    const resultSessionId = data.result.session_id || pendingRequests[requestId]?.sessionId;
                    const expectedSessionId = pendingRequests[requestId]?.sessionId;

                    // Safety check: if result's session_id doesn't match what we expected,
                    // this might be a stale result from a different session — ignore it
                    if (resultSessionId && expectedSessionId && resultSessionId !== expectedSessionId) {
                        console.debug('Result session_id mismatch: expected=' + expectedSessionId + ', got=' + resultSessionId + ' — ignoring');
                        delete pendingRequests[requestId];
                        return;
                    }

                    // Update sessionsData count to keep sidebar in sync with DOM
                    // This prevents the background sync loop from flagging this session as "Unread" later
                    if (resultSessionId && sessionsData[resultSessionId]) {
                        sessionsData[resultSessionId].message_count = (sessionsData[resultSessionId].message_count || 0) + 1;
                        sessionsData[resultSessionId].updated_at = new Date().toISOString();
                        // Explicitly clear unread indicator if it was set by a race condition
                        delete newMessageIndicators[resultSessionId];
                    }

                    // Handle transcription result that may spawn a new processing request
                    if (data.result.transcribed_text !== undefined && data.result.transcribed_text !== null) {
                        const resultSessionId = data.result.session_id || pendingRequests[requestId]?.sessionId;
                        if (resultSessionId === currentSessionId) {
                            // Check for duplicate by message_id
                            if (data.result.transcribed_message_id && displayedMessageIds.has(data.result.transcribed_message_id)) {
                                console.debug('Skipping duplicate transcribed message by ID', data.result.transcribed_message_id);
                            } else {
                                const transcribedText = data.result.transcribed_text || '(empty transcription)';
                                // Display transcribed text message
                                const msgElement = originalDisplayMessage('assistant', '🎤 ' + t('transcribed') + ': ' + transcribedText, null, null, null, null,
                                    data.result.assistant_timestamp || new Date().toISOString(), data.result.response_time, 'whisper',
                                    null, null, null, null, data.result.transcribed_message_id);
                                // Explicitly add to displayedMessageIds to prevent sync duplicates
                                if (data.result.transcribed_message_id) {
                                    displayedMessageIds.add(data.result.transcribed_message_id);
                                }
                                console.debug('[TRANSCRIBE] Displayed transcribed message:', data.result.transcribed_message_id, 'text length:', transcribedText.length);
                                // Update session metadata
                                if (sessionsData[resultSessionId]) {
                                    sessionsData[resultSessionId].message_count = (sessionsData[resultSessionId].message_count || 0) + 1;
                                    sessionsData[resultSessionId].updated_at = new Date().toISOString();
                                }
                            }
                            // If there is a new request_id for processing, start polling it
                            if (data.result.request_id) {
                                pendingRequests[data.result.request_id] = { sessionId: resultSessionId, processed: false };

                                // Immediately set processing state for the chained task
                                sessionQueueInfo[resultSessionId] = {
                                    processing: true,
                                    queued: 0,
                                    queue_position: 0,
                                    has_transcribing: false
                                };

                                // Force-clear local transcribing flag (microphone icon)
                                if (localTranscribingSessions[resultSessionId]) {
                                    delete localTranscribingSessions[resultSessionId];
                                }

                                window.updateStatusCounter();
                                startResultPolling(data.result.request_id);

                                // Now clear transcribing - the chained task is now processing (lightning will show)
                                setLocalTranscribing(resultSessionId, false);
                            } else {
                                setLocalTranscribing(resultSessionId, false);
                            }
                        } else {
                            // Result is for a different session (user switched sessions)
                            if (data.result.request_id) {
                                // There's a chained processing task - set it up
                                pendingRequests[data.result.request_id] = { sessionId: resultSessionId, processed: false };
                                startResultPolling(data.result.request_id);

                                // Set processing state immediately
                                sessionQueueInfo[resultSessionId] = {
                                    processing: true,
                                    queued: 0,
                                    queue_position: 0,
                                    has_transcribing: false
                                };

                                // Force-clear local transcribing flag (microphone icon)
                                if (localTranscribingSessions[resultSessionId]) {
                                    delete localTranscribingSessions[resultSessionId];
                                }
                            } else {
                                // No chained task - show unread indicator
                                setNewMessageIndicator(resultSessionId, true);
                            }
                            setLocalTranscribing(resultSessionId, false);
                        }
                        delete pendingRequests[requestId];

                        // Clear queue info for the completed transcription task
                        if (resultSessionId) {
                            sessionQueueInfo[resultSessionId] = {
                                processing: false,
                                queued: 0,
                                queue_position: 0,
                                has_transcribing: false
                            };
                        }

                        window.updateStatusCounter();
                        fetchQueueStatus();
                        return;
                    }
                    
                    if (data.result.error) {
                        if (data.result.message_id && displayedMessageIds.has(data.result.message_id)) {
                            console.debug('Skipping duplicate error message by ID', data.result.message_id);
                        } else if (resultSessionId === currentSessionId) {
                            originalDisplayMessage('assistant', '⚠️ ' + data.result.error, null, null, null, null,
                                data.result.assistant_timestamp || new Date().toISOString(), data.result.response_time, 'system',
                                null, null, null, null, data.result.message_id);
                            if (data.result.message_id) {
                                displayedMessageIds.add(data.result.message_id);
                            }
                        }
                        if (resultSessionId) {
                            setLocalTranscribing(resultSessionId, false);
                        }
                    } else if (data.result.messages) {
                        // Camera tasks return multiple messages — only display in the originating session
                        const cameraSessionId = data.result.session_id || resultSessionId;
                        console.debug('Camera result: resultSessionId=' + resultSessionId + ', cameraSessionId=' + cameraSessionId + ', currentSessionId=' + currentSessionId);
                        if (cameraSessionId === currentSessionId) {
                            for (const msg of data.result.messages) {
                                if (msg.message_id && displayedMessageIds.has(msg.message_id)) {
                                    console.debug('Skipping duplicate camera message by ID', msg.message_id);
                                    continue;
                                }
                                originalDisplayMessage('assistant', msg.response, msg.file_data, msg.file_type, msg.file_name, msg.file_path,
                                    msg.assistant_timestamp, msg.response_time, msg.model_used,
                                    null, null, null, null, msg.message_id);
                            }
                            updateLastVisit(currentSessionId);
                        } else if (cameraSessionId) {
                            // Camera result arrived but user is in a different session
                            setNewMessageIndicator(cameraSessionId, true);
                        }
                        if (resultSessionId) {
                            setLocalTranscribing(resultSessionId, false);
                        }
                    } else if (data.result.response) {
                        if (data.result.message_id && displayedMessageIds.has(data.result.message_id)) {
                            console.debug('Skipping duplicate response message by ID', data.result.message_id);
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

                    // FIX: Delete the completed request BEFORE checking hasPendingForSession,
                    // otherwise the current request is counted as "still pending" and
                    // processing: false is never set (lightning stays forever).
                    delete pendingRequests[requestId];

                    // Now check if there are STILL other pending requests for this session
                    // (excluding the one we just deleted above)
                    if (resultSessionId) {
                        const hasPendingForSession = Object.values(pendingRequests).some(
                            pr => pr.sessionId === resultSessionId
                        );

                        if (!hasPendingForSession) {
                            // No more pending tasks — clear ALL queue info
                            sessionQueueInfo[resultSessionId] = {
                                processing: false,
                                queued: 0,
                                queue_position: 0,
                                has_transcribing: false
                            };
                            if (sessionsData[resultSessionId]) {
                                sessionsData[resultSessionId].queue_info = null;
                            }
                        } else {
                            // Still has pending chained tasks — only clear completed task's queued state
                            sessionQueueInfo[resultSessionId].queued = 0;
                            sessionQueueInfo[resultSessionId].queue_position = 0;
                            // Don't clear processing — fetchQueueStatus will update it
                        }

                        // Force immediate UI update without debounce
                        const sessions = Object.keys(sessionsData).map(id => ({
                            id: id,
                            title: sessionsData[id].title,
                            updated_at: sessionsData[id].updated_at,
                            message_count: sessionsData[id].message_count,
                            has_unread: (sessionsData[id].has_unread || newMessageIndicators[id]) ? true : false,
                            queue_info: sessionQueueInfo[id] || null
                        }));
                        if (typeof updateSessionsList === 'function') {
                            updateSessionsList(sessions);
                        }
                    }

                    if (resultSessionId) {
                        // Small delay to let server clean up processing/queued data
                        setTimeout(() => {
                            fetchQueueStatus();
                        }, 100);
                    }
                }

                window.updateStatusCounter();

                // If we got here WITHOUT data.result (edge case), still clean up
                if (!data.result) {
                    const orphanedSessionId = pendingRequests[requestId]?.sessionId;
                    delete pendingRequests[requestId];
                    if (orphanedSessionId) {
                        setLocalTranscribing(orphanedSessionId, false);
                        sessionQueueInfo[orphanedSessionId] = {
                            processing: false,
                            queued: 0,
                            queue_position: 0,
                            has_transcribing: false
                        };
                        updateSessionsListFromData();
                    }
                    fetchQueueStatus();
                }
            } else if (data.status === 'error') {
                clearInterval(pollInterval);

                const errorSessionId = data.result?.session_id || pendingRequests[requestId]?.sessionId;

                if (errorSessionId === currentSessionId) {
                    originalDisplayMessage('assistant', '⚠️ ' + t('error') + ': ' + (data.error || t('unknown_error')), null, null, null, null,
                        data.result?.assistant_timestamp || new Date().toISOString(), data.result?.response_time, 'system',
                        null, null, null, null, null);
                }

                if (errorSessionId) {
                    setLocalTranscribing(errorSessionId, false);
                    sessionQueueInfo[errorSessionId] = {
                        processing: false,
                        queued: 0,
                        queue_position: 0,
                        has_transcribing: false
                    };
                    updateSessionsListFromData();
                }

                delete pendingRequests[requestId];
                window.updateStatusCounter();
                fetchQueueStatus();
            }

            // Timeout check - verify with server before showing warning
            if (pollCount >= maxPolls) {
                clearInterval(pollInterval);

                // First check if the task is still processing on the server
                try {
                    const statusResponse = await fetch('/api/queue/status');
                    const statusData = await statusResponse.json();
                    const stillProcessing = statusData.processing && statusData.processing.session_id === pendingRequests[requestId]?.sessionId;

                    if (stillProcessing) {
                        // Task is still processing on server - extend polling
                        console.debug('Server still processing task, extending poll timeout');
                        pollCount = 0; // Reset counter
                        return;
                    }
                } catch (e) {
                    console.warn('Could not check server status before timeout:', e);
                }

                // Task is not on server - show timeout warning
                originalDisplayMessage('assistant', '⚠️ ' + t('request_timeout'),
                    null, null, null, null, new Date().toISOString(), null, 'system',
                    null, null, null, null, null);

                const timeoutSessionId = pendingRequests[requestId]?.sessionId;
                delete pendingRequests[requestId];
                if (timeoutSessionId) {
                    setLocalTranscribing(timeoutSessionId, false);
                    sessionQueueInfo[timeoutSessionId] = {
                        processing: false,
                        queued: 0,
                        queue_position: 0,
                        has_transcribing: false
                    };
                    updateSessionsListFromData();
                }
                window.updateStatusCounter();
                fetchQueueStatus();
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
        console.debug('Send already in progress, ignoring duplicate');
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
            console.debug('displayUserMessage: Set tempId', tempId, 'on message element');
        } else {
            console.warn('displayUserMessage: msgElement is null/undefined, cannot set tempId');
        }

        input.value = '';
        attachedFile = null;
        document.getElementById('file-preview-container').classList.add('hidden');
        document.getElementById('file-input').value = '';
    };
    
    const unlockSendButton = () => {
        console.debug('unlockSendButton called, isSending was:', isSending);
        if (sendButton) {
            sendButton.disabled = false;
            sendButton.innerHTML = t('send');
        }
        isSending = false;
        console.debug('unlockSendButton finished, isSending now:', isSending);
    };
    
    const sendToServer = () => {
        if (window.IS_RELOADING) return;
        
        console.debug('sendToServer starting, isAudioFile:', isAudioFile);
        
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
                    response = await fetchWithCSRF('/api/send_message', { method: 'POST', body: formData });
                } else {
                    response = await fetchWithCSRF('/api/send_message', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: tempText })
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
                    console.warn('Session expired — redirecting to login');
                    window.location.href = '/login';
                    return;
                }

                if (window.IS_RELOADING) return;
                
                console.debug('Server response:', data);
                
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
                            console.debug('sendMessage: Using fallback - last user message');
                        }
                    }

                    if (targetMsg) {
                        if (targetMsg.dataset.tempId) {
                            // Remove tempId from tracking
                            delete targetMsg.dataset.tempId;
                        }
                        targetMsg.dataset.messageId = data.user_message_id;
                        displayedMessageIds.add(data.user_message_id);
                        console.debug('sendMessage: Updated messageId to', data.user_message_id);
                    } else {
                        console.warn('sendMessage: Could not find user message to update messageId. Total user messages:', document.querySelectorAll('.user-message').length);
                    }
                }
                
                // Audio files are now queued for transcription, no immediate transcribed_text
                if (data.transcribed_text) {
                    console.debug('Transcription completed');
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
                            // Only show unread if there's no further processing
                            if (!data.request_id) {
                                setNewMessageIndicator(targetSessionId, true);
                            }
                        }
                    }
                    return;
                }
                
                if (data.status === 'queued') {
                    // Worker picks up tasks via blpop almost instantly.
                    // By the time this response reaches the frontend, the task is already processing.
                    // Show processing icon immediately - fetchQueueStatus will confirm/update.
                    if (!sessionQueueInfo[currentSessionId]) {
                        sessionQueueInfo[currentSessionId] = {
                            processing: true,  // Worker already has it
                            queued: 0,
                            queue_position: 0
                        };
                    } else {
                        sessionQueueInfo[currentSessionId].processing = true;
                        sessionQueueInfo[currentSessionId].queued = 0;
                        sessionQueueInfo[currentSessionId].queue_position = 0;
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
                
                console.debug('File attached:', fileName, 'Type:', fileType, 'isAudioFile:', isAudioFile);
                
                // FIX: Set transcribing flag for audio files BEFORE displaying message
                if (isAudioFile) {
                    console.debug('Audio file detected, setting transcribing flag for session:', currentSessionId);
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
    console.debug('loadMessages called for session', sessionId);

    return originalLoadMessages(sessionId)
        .then(() => {
            console.debug('loadMessages completed for session', sessionId);

            if (window.IS_RELOADING) return;

            setTimeout(addCopyButtonsToAllCodeBlocks, 100);
            startMessagePolling();
        })
        .catch(err => {
            console.error('Error in loadMessages:', err);
            throw err;
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
            startMessagePolling();
            // Restore TTS button state if TTS is playing
            if (typeof restoreTTSButtonState === 'function') {
                restoreTTSButtonState();
            }
            // Start sync interval AFTER initial load to prevent race conditions
            startSyncInterval();
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