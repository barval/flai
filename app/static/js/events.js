// app/static/js/events.js
// Server-Sent Events (SSE) — replaces HTTP polling for real-time updates

let eventSource = null;
let reconnectTimer = null;
let pendingRequestIds = {};  // requestId -> { sessionId, timestamp }

function connectEventStream() {
    if (eventSource) {
        eventSource.close();
    }

    eventSource = new EventSource('/api/events/stream');

    eventSource.addEventListener('connected', function () {
        dlog('SSE connected');
    });

    eventSource.onmessage = function (e) {
        try {
            const event = JSON.parse(e.data);
            handleEvent(event);
        } catch (err) {
            dlog('SSE parse error:', err);
        }
    };

    eventSource.onerror = function () {
        dlog('SSE connection error, reconnecting...');
        eventSource.close();
        scheduleReconnect();
    };
}

function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(function () {
        connectEventStream();
        // Full sync after reconnect — may have missed events
        if (typeof loadSessionsFromServer === 'function') {
            loadSessionsFromServer();
        }
        if (typeof fetchQueueStatus === 'function') {
            fetchQueueStatus();
        }
        if (typeof loadMessages === 'function' && currentSessionId) {
            loadMessages(currentSessionId);
        }
        if (typeof loadDocuments === 'function') {
            loadDocuments(false);
        }
    }, 3000);
}

function disconnectEventStream() {
    if (eventSource) {
        eventSource.close();
        eventSource = null;
    }
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
}

function handleEvent(event) {
    switch (event.type) {
        case 'result_completed':
            onResultCompleted(event.data);
            break;
        case 'stream_token':
            onStreamToken(event.data);
            break;
        case 'stream_cancelled':
            onStreamCancelled(event.data);
            break;
        case 'camera_image':
            onCameraImage(event.data);
            break;
        case 'message_new':
            onMessageNew(event.data);
            break;
        case 'document_indexed':
            if (typeof loadDocuments === 'function') {
                loadDocuments(false);
            }
            break;
        default:
            dlog('SSE unknown event type:', event.type);
    }
}

// ── camera_image ────────────────────────────────────────────────────

function onCameraImage(data) {
    if (!data || !data.session_id || data.session_id !== currentSessionId) return;
    if (data.message_id && displayedMessageIds.has(data.message_id)) return;
    dlog('onCameraImage:', data.message_id);
    window.displayMessage('assistant', data.response, null,
        data.file_type || null, data.file_name || null, data.file_path || null,
        data.assistant_timestamp || new Date().toISOString(),
        data.response_time, data.model_used,
        null, null, null, null, data.message_id,
        data.response_style);
}

// ── stream_token ─────────────────────────────────────────────────────

function onStreamToken(data) {
    if (!data || !data.task_id || !data.token) return;
    dlog('onStreamToken:', data.task_id, 'token len:', data.token.length);

    const reqInfo = pendingRequestIds[data.task_id];
    if (!reqInfo) {
        dlog('onStreamToken: no pending request found for', data.task_id);
        return;
    }

    // Accumulate content
    if (!reqInfo.accumulatedContent) {
        reqInfo.accumulatedContent = '';
    }
    reqInfo.accumulatedContent += data.token;

    // Save to sessionStorage for recovery after page reload
    _saveStreamToSessionStorage(data.task_id, data.session_id, reqInfo.accumulatedContent);

    // Only update DOM for active session
    if (data.session_id !== currentSessionId) return;

    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;

    let streamMsg = chatMessages.querySelector('.assistant-message[data-streaming="true"]');

    if (!streamMsg) {
        // Create new message element with proper structure
        streamMsg = document.createElement('div');
        streamMsg.className = 'assistant-message bot-message';
        streamMsg.setAttribute('data-streaming', 'true');
        streamMsg.setAttribute('data-task-id', data.task_id);
        streamMsg.setAttribute('data-role', 'assistant');
        streamMsg.dataset.sessionId = data.session_id;
        chatMessages.appendChild(streamMsg);

        // Header with timestamp, streaming indicator, cancel button
        const headerDiv = document.createElement('span');
        headerDiv.className = 'message-header';

        const timeSpan = document.createElement('span');
        timeSpan.textContent = '📅 ' + formatFullDateTime(new Date().toISOString());
        headerDiv.appendChild(timeSpan);

        const indicatorSpan = document.createElement('span');
        indicatorSpan.className = 'streaming-indicator blink';
        indicatorSpan.textContent = '⚡ ' + t('generating');
        headerDiv.appendChild(indicatorSpan);

        const cancelBtn = document.createElement('button');
        cancelBtn.className = 'cancel-stream-button';
        cancelBtn.title = t('stop_generating');
        cancelBtn.textContent = '■';
        cancelBtn.addEventListener('click', function () {
            cancelBtn.disabled = true;
            cancelBtn.textContent = '⏳';
            cancelBtn.title = t('cancelling');
            fetchWithCSRF('/api/cancel_task/' + data.task_id, { method: 'POST' }).catch(function () {});
        });
        headerDiv.appendChild(cancelBtn);

        streamMsg.appendChild(headerDiv);

        // Content div
        const contentDiv = document.createElement('div');
        contentDiv.className = 'message-content';
        streamMsg.appendChild(contentDiv);
    }

    // Update content
    const contentDiv = streamMsg.querySelector('.message-content');
    if (contentDiv) {
        contentDiv.textContent = reqInfo.accumulatedContent;
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
}

function _saveStreamToSessionStorage(taskId, sessionId, content) {
    try {
        sessionStorage.setItem('stream_' + taskId, JSON.stringify({
            sessionId: sessionId,
            content: content,
            timestamp: Date.now()
        }));
    } catch (e) {
        // sessionStorage full or unavailable — ignore
    }
}

function _clearStreamFromSessionStorage(taskId) {
    try {
        sessionStorage.removeItem('stream_' + taskId);
    } catch (e) {
        // ignore
    }
}

function restoreStreamingFromSessionStorage() {
    if (!currentSessionId) return;
    const keysToRemove = [];
    const chatMessages = document.getElementById('chat-messages');
    if (!chatMessages) return;

    for (var i = 0; i < sessionStorage.length; i++) {
        var key = sessionStorage.key(i);
        if (!key || !key.startsWith('stream_')) continue;
        try {
            var saved = JSON.parse(sessionStorage.getItem(key));
            if (!saved || !saved.sessionId || !saved.content) {
                keysToRemove.push(key);
                continue;
            }
            if (saved.sessionId !== currentSessionId) {
                keysToRemove.push(key);
                continue;
            }
            // Restore DOM for this session
            var existing = chatMessages.querySelector('[data-task-id="' + key.replace('stream_', '') + '"]');
            if (existing) {
                keysToRemove.push(key);
                continue;
            }
            var taskId = key.replace('stream_', '');
            var msgDiv = document.createElement('div');
            msgDiv.className = 'assistant-message bot-message';
            msgDiv.setAttribute('data-streaming', 'true');
            msgDiv.setAttribute('data-task-id', taskId);
            msgDiv.setAttribute('data-role', 'assistant');
            msgDiv.dataset.sessionId = currentSessionId;
            chatMessages.appendChild(msgDiv);

            var headerDiv = document.createElement('span');
            headerDiv.className = 'message-header';

            var timeSpan = document.createElement('span');
            timeSpan.textContent = '📅 ' + formatFullDateTime(new Date(saved.timestamp).toISOString());
            headerDiv.appendChild(timeSpan);

            var indicatorSpan = document.createElement('span');
            indicatorSpan.className = 'streaming-indicator blink';
            indicatorSpan.textContent = '⚡ ' + t('generating');
            headerDiv.appendChild(indicatorSpan);

            var cancelBtn = document.createElement('button');
            cancelBtn.className = 'cancel-stream-button';
            cancelBtn.title = t('stop_generating');
            cancelBtn.textContent = '■';
            cancelBtn.addEventListener('click', function () {
                cancelBtn.disabled = true;
                cancelBtn.textContent = '⏳';
                cancelBtn.title = t('cancelling');
                fetchWithCSRF('/api/cancel_task/' + taskId, { method: 'POST' }).catch(function () {});
            });
            headerDiv.appendChild(cancelBtn);

            msgDiv.appendChild(headerDiv);

            var contentDiv = document.createElement('div');
            contentDiv.className = 'message-content';
            contentDiv.textContent = saved.content;
            msgDiv.appendChild(contentDiv);

            // Re-register in pendingRequestIds
            if (!pendingRequestIds[taskId]) {
                pendingRequestIds[taskId] = {
                    sessionId: currentSessionId,
                    timestamp: saved.timestamp,
                    accumulatedContent: saved.content
                };
            }
        } catch (e) {
            keysToRemove.push(key);
        }
    }
    // Clean stale keys
    for (var j = 0; j < keysToRemove.length; j++) {
        try { sessionStorage.removeItem(keysToRemove[j]); } catch (e) {}
    }
}

// ── result_completed ─────────────────────────────────────────────────

function onResultCompleted(data) {
    if (!data || !data.task_id) return;
    dlog('onResultCompleted:', data.task_id, data.status);

    const reqInfo = pendingRequestIds[data.task_id];
    if (!reqInfo) {
        dlog('onResultCompleted: no pending request found for', data.task_id);
        return;
    }

    const expectedSessionId = reqInfo.sessionId;

    // Handle streamed completion differently
    if (reqInfo.accumulatedContent !== undefined) {
        finalizeStreamedMessage(data, reqInfo, expectedSessionId);
        return;
    }

    delete pendingRequestIds[data.task_id];

    if (data.status === 'completed' && data.result) {
        handleCompletedResult(data.result, expectedSessionId);
    } else if (data.status === 'error') {
        handleErrorResult(data, expectedSessionId);
    }
}

function onStreamCancelled(data) {
    if (!data || !data.task_id) return;
    dlog('onStreamCancelled:', data.task_id);

    // The result_completed will follow shortly — let finalizeStreamedMessage handle cleanup.
    // Just mark the DOM as cancelled.
    const streamMsg = document.querySelector('.assistant-message[data-streaming="true"]');
    if (streamMsg) {
        // Update header: remove cancel button, show cancelled label
        const cancelBtn = streamMsg.querySelector('.cancel-stream-button');
        if (cancelBtn) cancelBtn.remove();
        const indicator = streamMsg.querySelector('.streaming-indicator');
        if (indicator) {
            indicator.className = 'cancelled-label';
            indicator.textContent = '⚠️ ' + t('cancelled');
        }
    }
    _clearStreamFromSessionStorage(data.task_id);
}

function finalizeStreamedMessage(data, reqInfo, expectedSessionId) {
    const resultSessionId = data.result?.session_id || data.session_id || expectedSessionId;
    const taskId = data.task_id || reqInfo.taskId;

    // Update sessions count
    if (resultSessionId && sessionsData[resultSessionId]) {
        sessionsData[resultSessionId].message_count = (sessionsData[resultSessionId].message_count || 0) + 1;
        sessionsData[resultSessionId].updated_at = new Date().toISOString();
        delete newMessageIndicators[resultSessionId];
    }

    // Find stream message by task-id (more reliable than generic [data-streaming])
    const streamMsg = document.querySelector('.assistant-message[data-task-id="' + taskId + '"]');
    if (streamMsg) {
        streamMsg.removeAttribute('data-streaming');
        streamMsg.removeAttribute('data-task-id');
        if (data.result?.message_id) {
            streamMsg.dataset.messageId = data.result.message_id;
            displayedMessageIds.add(data.result.message_id);
        }
        // Remove streaming-only elements
        var cancelBtn = streamMsg.querySelector('.cancel-stream-button');
        if (cancelBtn) cancelBtn.remove();
        var indicator = streamMsg.querySelector('.streaming-indicator');
        if (indicator) indicator.remove();
        var cancelledLabel = streamMsg.querySelector('.cancelled-label');
        if (cancelledLabel) cancelledLabel.remove();

        // Rebuild header with metadata from result
        var result = data.result;
        if (result) {
            var oldHeader = streamMsg.querySelector('.message-header');
            if (oldHeader) {
                var newHeader = document.createElement('span');
                newHeader.className = 'message-header';

                // Timestamp
                var ts = result.assistant_timestamp || new Date().toISOString();
                newHeader.innerHTML = '📅 ' + formatFullDateTime(ts);

                // Model name
                if (result.model_used) {
                    var shortModel = result.model_used.split('/').pop() || result.model_used;
                    var modelSpan = document.createElement('span');
                    modelSpan.className = 'text-muted';
                    modelSpan.textContent = ' | ' + shortModel;
                    newHeader.appendChild(modelSpan);
                }

                // Response time
                if (result.response_time) {
                    var duration = null;
                    if (typeof result.response_time === 'object') {
                        if (result.response_time.mm_time && result.response_time.gen_time) {
                            duration = (parseFloat(result.response_time.mm_time) + parseFloat(result.response_time.gen_time)).toFixed(1);
                        } else if (result.response_time.mm_time) {
                            duration = parseFloat(result.response_time.mm_time).toFixed(1);
                        } else if (result.response_time.gen_time) {
                            duration = parseFloat(result.response_time.gen_time).toFixed(1);
                        }
                    } else if (typeof result.response_time === 'number' || !isNaN(parseFloat(result.response_time))) {
                        duration = parseFloat(result.response_time).toFixed(1);
                    }
                    if (duration) {
                        var langSuffix = t('seconds_suffix');
                        var timeSpan = document.createElement('span');
                        timeSpan.className = 'text-muted';
                        timeSpan.textContent = ' | ⏱️ ' + duration + langSuffix + ' |';
                        newHeader.appendChild(timeSpan);
                    }
                }

                // Response style emoji
                if (result.response_style) {
                    var styleEmoji = getResponseStyleEmoji(result.response_style);
                    if (styleEmoji) {
                        var styleLabel = t('response_style_' + result.response_style) || result.response_style;
                        var styleSpan = document.createElement('span');
                        styleSpan.className = 'response-style-indicator';
                        styleSpan.title = styleLabel;
                        styleSpan.textContent = styleEmoji;
                        newHeader.appendChild(styleSpan);
                    }
                }

                // TTS button
                var ttsBtn = document.createElement('button');
                ttsBtn.className = 'tts-button';
                ttsBtn.title = t('speak');
                ttsBtn.textContent = '🗣️';
                newHeader.appendChild(ttsBtn);

                // Copy button
                var copyBtn = document.createElement('button');
                copyBtn.className = 'copy-message-button';
                copyBtn.title = t('copy_text');
                copyBtn.textContent = '📋';
                newHeader.appendChild(copyBtn);

                oldHeader.replaceWith(newHeader);

                // Attach TTS click handler
                ttsBtn = newHeader.querySelector('.tts-button');
                if (ttsBtn) {
                    ttsBtn.removeAttribute('onclick');
                    ttsBtn.addEventListener('click', function(e) {
                        e.preventDefault();
                        if (window.IS_RELOADING) return;
                        var fn = window.playTTS || playTTS;
                        if (typeof fn === 'function') fn(ttsBtn, streamMsg);
                    });
                }

                // Attach copy button click handler
                copyBtn = newHeader.querySelector('.copy-message-button');
                if (copyBtn) {
                    copyBtn.addEventListener('click', function(e) {
                        e.preventDefault();
                        if (window.IS_RELOADING) return;
                        var rawText = streamMsg.dataset.rawText || streamMsg.dataset.cleanText || result.response;
                        if (!rawText) return;
                        copyToClipboard(rawText).then(function(success) {
                            var originalHTML = copyBtn.innerHTML;
                            var originalTitle = copyBtn.title;
                            if (success) {
                                copyBtn.innerHTML = '✓';
                                copyBtn.title = t('copied');
                                setTimeout(function() {
                                    copyBtn.innerHTML = originalHTML;
                                    copyBtn.title = originalTitle;
                                }, 2000);
                            }
                        });
                    });
                }
            }
        }

        // Set raw text for copy button
        if (result && result.response) {
            streamMsg.setAttribute('data-raw-text', result.response);
        }

        // Replace content with full rendered markdown
        var contentDiv = streamMsg.querySelector('.message-content');
        if (contentDiv && result && result.response) {
            contentDiv.innerHTML = marked.parse(result.response);
        }
        if (typeof updateLastVisit === 'function') updateLastVisit(currentSessionId);
        if (typeof addCopyButtonsToMessage === 'function') addCopyButtonsToMessage(streamMsg);
    } else if (resultSessionId === currentSessionId && data.result?.response) {
        // Fallback: create the message via displayMessage
        window.displayMessage('assistant', data.result.response, null, null, null, null,
            data.result.assistant_timestamp || new Date().toISOString(),
            data.result.response_time, data.result.model_used,
            null, null, null, null, data.result.message_id,
            data.result.response_style);
    }

    if (resultSessionId && resultSessionId !== currentSessionId) {
        setNewMessageIndicator(resultSessionId, true);
    }

    // Cleanup
    if (resultSessionId) {
        setLocalTranscribing(resultSessionId, false);
        clearSessionQueue(resultSessionId);
    }
    delete pendingRequestIds[taskId];
    _clearStreamFromSessionStorage(taskId);
}

function trackPendingRequest(requestId, sessionId) {
    pendingRequestIds[requestId] = { sessionId: sessionId, timestamp: Date.now() };
}

function handleCompletedResult(result, expectedSessionId) {
    const resultSessionId = result.session_id || expectedSessionId;

    // Update sessionsData count to keep sidebar in sync
    if (resultSessionId && sessionsData[resultSessionId]) {
        sessionsData[resultSessionId].message_count = (sessionsData[resultSessionId].message_count || 0) + 1;
        sessionsData[resultSessionId].updated_at = new Date().toISOString();
        delete newMessageIndicators[resultSessionId];
    }

    // Transcription result — may spawn chained processing
    if (result.transcribed_text !== undefined && result.transcribed_text !== null) {
        handleTranscriptionResult(result, resultSessionId, expectedSessionId);
        return;
    }

    // Error result
    if (result.error) {
        if (resultSessionId === currentSessionId) {
            window.displayMessage('assistant', '⚠️ ' + result.error, null, null, null, null,
                result.assistant_timestamp || new Date().toISOString(), result.response_time, 'system',
                null, null, null, null, result.message_id, null);
        }
        if (resultSessionId) setLocalTranscribing(resultSessionId, false);
        clearSessionQueue(resultSessionId);
        return;
    }

    // Multiple messages (camera tasks)
    if (result.messages) {
        handleCameraResult(result, resultSessionId);
        return;
    }

    // Single response (text / image gen)
    if (result.response) {
        if (result.message_id && displayedMessageIds.has(result.message_id)) {
            dlog('Skipping duplicate response by ID', result.message_id);
        } else {
            let responseTime = result.response_time;
            let modelUsed = result.model_used;

            if (result.mm_time && result.gen_time) {
                responseTime = { mm_time: result.mm_time, gen_time: result.gen_time, mm_model: result.mm_model, gen_model: result.gen_model };
                modelUsed = result.gen_model;
            }

            if (resultSessionId === currentSessionId) {
                window.displayMessage('assistant', result.response, result.file_data,
                    result.file_type, result.file_name, result.file_path,
                    result.assistant_timestamp || new Date().toISOString(), responseTime, modelUsed,
                    null, null, null, null, result.message_id,
                    result.response_style);
                if (typeof updateLastVisit === 'function') updateLastVisit(currentSessionId);
            } else {
                setNewMessageIndicator(resultSessionId, true);
            }
        }
        if (resultSessionId) setLocalTranscribing(resultSessionId, false);
        clearSessionQueue(resultSessionId);
        return;
    }

    // No recognizable payload — clean up anyway
    if (resultSessionId) {
        setLocalTranscribing(resultSessionId, false);
        clearSessionQueue(resultSessionId);
    }
}

function handleTranscriptionResult(result, resultSessionId, expectedSessionId) {
    if (resultSessionId === currentSessionId) {
        if (!result.transcribed_message_id || !displayedMessageIds.has(result.transcribed_message_id)) {
            const transcribedText = result.transcribed_text || '(empty transcription)';
            var transcribedContent = JSON.stringify({prefix: '🎤 ' + t('transcribed') + ': ', text: transcribedText});
            window.displayMessage('assistant', transcribedContent, null, null, null, null,
                result.assistant_timestamp || new Date().toISOString(), result.response_time, 'whisper',
                null, null, null, null, result.transcribed_message_id, null);
            if (result.transcribed_message_id) displayedMessageIds.add(result.transcribed_message_id);
        }
        if (result.request_id) {
            trackPendingRequest(result.request_id, resultSessionId);
            sessionQueueInfo[resultSessionId] = { processing: true, queued: 0, queue_position: 0, has_transcribing: false };
            window.updateStatusCounter();
            if (typeof fetchQueueStatus === 'function') fetchQueueStatus();
        } else {
            // Audio file — no further processing, clear ⚡
            clearSessionQueue(resultSessionId);
            if (typeof fetchQueueStatus === 'function') fetchQueueStatus();

            // Also clear the safety‑valve cache to force UI redraw
            if (typeof updateSessionsListFromData === 'function') {
                window._lastSessionsJson = null;
                updateSessionsListFromData();
            }
        }
        setLocalTranscribing(resultSessionId, false);
    } else {
        if (result.request_id) {
            trackPendingRequest(result.request_id, resultSessionId);
        } else {
            setNewMessageIndicator(resultSessionId, true);
        }
        setLocalTranscribing(resultSessionId, false);
        window.updateStatusCounter();
    }
}

function handleCameraResult(result, resultSessionId) {
    const cameraSessionId = result.session_id || resultSessionId;
    if (cameraSessionId === currentSessionId) {
        for (const msg of result.messages) {
            if (msg.message_id && displayedMessageIds.has(msg.message_id)) continue;
            window.displayMessage('assistant', msg.response, msg.file_data, msg.file_type, msg.file_name, msg.file_path,
                msg.assistant_timestamp, msg.response_time, msg.model_used,
                null, null, null, null, msg.message_id, msg.response_style);
        }
        if (typeof updateLastVisit === 'function') updateLastVisit(currentSessionId);
    } else if (cameraSessionId) {
        setNewMessageIndicator(cameraSessionId, true);
    }
    if (resultSessionId) setLocalTranscribing(resultSessionId, false);
    clearSessionQueue(resultSessionId);
}

function handleErrorResult(data, expectedSessionId) {
    const errorSessionId = data.result?.session_id || expectedSessionId;
    if (errorSessionId === currentSessionId) {
        window.displayMessage('assistant', '⚠️ ' + t('error') + ': ' + (data.error || t('unknown_error')), null, null, null, null,
            new Date().toISOString(), null, 'system', null, null, null, null, null, null);
    }
    if (errorSessionId) {
        setLocalTranscribing(errorSessionId, false);
        clearSessionQueue(errorSessionId);
    }
}

function clearSessionQueue(sessionId) {
    if (!sessionId) return;
    sessionQueueInfo[sessionId] = { processing: false, queued: 0, queue_position: 0, has_transcribing: false };
    if (sessionsData[sessionId]) sessionsData[sessionId].queue_info = null;
    if (typeof updateUIFromQueueStatus === 'function') updateUIFromQueueStatus();
}

// ── message_new ──────────────────────────────────────────────────────

function onMessageNew(data) {
    if (!data || !data.session_id || !data.message_id) return;
    dlog('onMessageNew: session', data.session_id, 'msg', data.message_id, 'role', data.role);

    // Skip if message is for a different session or already displayed
    if (data.session_id !== currentSessionId) {
        setNewMessageIndicator(data.session_id, true);
        return;
    }
    if (displayedMessageIds.has(data.message_id)) {
        dlog('onMessageNew: already displayed', data.message_id);
        return;
    }
    if (document.querySelector('[data-message-id="' + data.message_id + '"]')) {
        displayedMessageIds.add(data.message_id);
        return;
    }

    // Fetch the full message data from server
    fetch('/api/sessions/' + data.session_id + '/messages?limit=50')
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (json) {
            if (!json || !json.messages) return;
            var messages = json.messages;
            // Find the specific message
            for (var i = 0; i < messages.length; i++) {
                var msg = messages[i];
                if (msg.id == data.message_id) {
                    if (displayedMessageIds.has(msg.id)) return;
                    displayedMessageIds.add(msg.id);
                    var responseTime = null;
                    if (msg.response_time) {
                        if (typeof msg.response_time === 'object') responseTime = msg.response_time;
                        else if (!isNaN(parseFloat(msg.response_time))) responseTime = parseFloat(msg.response_time);
                    }
                    window.displayMessage(
                        msg.role, msg.content, msg.file_data, msg.file_type, msg.file_name, msg.file_path,
                        msg.timestamp, responseTime, msg.model_name,
                        msg.mm_time, msg.gen_time, msg.mm_model, msg.gen_model, msg.id,
                        msg.response_style
                    );
                    if (sessionsData[data.session_id]) {
                        sessionsData[data.session_id].message_count = (sessionsData[data.session_id].message_count || 0) + 1;
                    }
                    return;
                }
            }
        })
        .catch(function () {});
}

// ── Initialisation ───────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', function () {
    connectEventStream();
});

// Reconnect SSE when tab becomes visible again (mobile fix)
document.addEventListener('visibilitychange', function () {
    if (!document.hidden && (!eventSource || eventSource.readyState !== EventSource.OPEN)) {
        dlog('Tab visible, reconnecting SSE...');
        connectEventStream();
        if (typeof loadSessionsFromServer === 'function') loadSessionsFromServer();
        if (typeof fetchQueueStatus === 'function') fetchQueueStatus();
    }
});

// Make functions globally accessible
window.connectEventStream = connectEventStream;
window.disconnectEventStream = disconnectEventStream;
window.trackPendingRequest = trackPendingRequest;
window.restoreStreamingFromSessionStorage = restoreStreamingFromSessionStorage;
