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
        case 'message_new':
            onMessageNew(event.data);
            break;
        default:
            dlog('SSE unknown event type:', event.type);
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
    delete pendingRequestIds[data.task_id];

    if (data.status === 'completed' && data.result) {
        handleCompletedResult(data.result, expectedSessionId);
    } else if (data.status === 'error') {
        handleErrorResult(data, expectedSessionId);
    }
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
        }
        setLocalTranscribing(resultSessionId, false);
    } else {
        if (result.request_id) {
            trackPendingRequest(result.request_id, resultSessionId);
        } else {
            setNewMessageIndicator(resultSessionId, true);
        }
        setLocalTranscribing(resultSessionId, false);
    }
    clearSessionQueue(resultSessionId);
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
