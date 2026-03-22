// static/js/chat-socket.js
// WebSocket connection and event handling

let socket = null;
let socketConnected = false;

function initSocket() {
    if (socket) return;
    
    socket = io({
        transports: ['websocket', 'polling']
    });
    
    socket.on('connect', () => {
        console.log('WebSocket connected');
        socketConnected = true;
    });
    
    socket.on('disconnect', () => {
        console.log('WebSocket disconnected');
        socketConnected = false;
    });
    
    socket.on('queue_status', (data) => {
        console.log('Queue status event:', data);
        handleQueueStatus(data);
    });
    
    socket.on('new_message', (data) => {
        console.log('New message event:', data);
        handleNewMessage(data);
    });
    
    socket.on('sessions_update', (data) => {
        console.log('Sessions update event:', data);
        // Reload sessions list from server (or update locally)
        loadSessionsFromServer();
    });
    
    socket.on('transcribing_status', (data) => {
        console.log('Transcribing status event:', data);
        handleTranscribingStatus(data);
    });
    
    socket.on('connect_error', (err) => {
        console.error('WebSocket connection error:', err);
        socketConnected = false;
        // Fallback to polling mode
    });
}

function handleQueueStatus(data) {
    const { type, request_id, session_id, position, estimated_seconds, result, error } = data;
    
    console.log('handleQueueStatus:', type, session_id, data);
    
    if (type === 'queued') {
        if (!sessionQueueInfo[session_id]) {
            sessionQueueInfo[session_id] = { processing: false, queued: 1 };
        } else {
            sessionQueueInfo[session_id].queued += 1;
        }
        updateSessionsListFromData();
        window.updateStatusCounter();
        
    } else if (type === 'processing') {
        if (sessionQueueInfo[session_id]) {
            sessionQueueInfo[session_id].processing = true;
            sessionQueueInfo[session_id].queued = Math.max(0, (sessionQueueInfo[session_id].queued || 0) - 1);
        } else {
            sessionQueueInfo[session_id] = { processing: true, queued: 0 };
        }
        updateSessionsListFromData();
        
    } else if (type === 'completed') {
        if (result) {
            // Process the result as if from polling
            const resultSessionId = result.session_id || session_id;
            if (result.error) {
                if (resultSessionId === currentSessionId) {
                    displayMessage('assistant', '⚠️ ' + result.error, null, null, null, null,
                        result.assistant_timestamp || new Date().toISOString(), result.response_time, 'system',
                        null, null, null, null, null);
                }
                if (resultSessionId) {
                    setLocalTranscribing(resultSessionId, false);
                }
            } else if (result.messages) {
                for (const msg of result.messages) {
                    if (msg.message_id && displayedMessageIds.has(msg.message_id)) {
                        console.log('Skipping duplicate camera message by ID', msg.message_id);
                        continue;
                    }
                    displayMessage('assistant', msg.response, msg.file_data, msg.file_type, msg.file_name, msg.file_path,
                        msg.assistant_timestamp, msg.response_time, msg.model_used,
                        null, null, null, null, msg.message_id);
                }
                if (resultSessionId) {
                    setLocalTranscribing(resultSessionId, false);
                }
            } else if (result.response) {
                if (result.message_id && displayedMessageIds.has(result.message_id)) {
                    console.log('Skipping duplicate response message by ID', result.message_id);
                } else {
                    let responseTime = result.response_time;
                    let modelUsed = result.model_used;
                    if (result.mm_time && result.gen_time) {
                        responseTime = { mm_time: result.mm_time, gen_time: result.gen_time, mm_model: result.mm_model, gen_model: result.gen_model };
                        modelUsed = result.gen_model;
                    } else if (typeof responseTime === 'string' && responseTime.startsWith('{')) {
                        try { responseTime = JSON.parse(responseTime); } catch (e) {}
                    }
                    if (resultSessionId === currentSessionId) {
                        displayMessage('assistant', result.response, result.file_data,
                            result.file_type, result.file_name, result.file_path,
                            result.assistant_timestamp || new Date().toISOString(), responseTime, modelUsed,
                            null, null, null, null, result.message_id);
                        updateLastVisit(currentSessionId);
                    } else {
                        setNewMessageIndicator(resultSessionId, true);
                    }
                }
                if (resultSessionId) {
                    setLocalTranscribing(resultSessionId, false);
                }
            }
            
            if (resultSessionId) {
                setLocalTranscribing(resultSessionId, false);
                if (sessionQueueInfo[resultSessionId]) {
                    sessionQueueInfo[resultSessionId].processing = false;
                    sessionQueueInfo[resultSessionId].queued = 0;
                }
                updateSessionsListFromData();
            }
        }
        // Remove from pendingRequests if it exists
        delete pendingRequests[request_id];
        window.updateStatusCounter();
        fetchQueueStatus(); // fallback sync
        setTimeout(() => loadSessionsFromServer(), 500);
        
    } else if (type === 'error') {
        const resultSessionId = session_id;
        if (resultSessionId === currentSessionId) {
            displayMessage('assistant', '⚠️ ' + t('error') + ': ' + (error || t('unknown_error')), null, null, null, null,
                new Date().toISOString(), null, 'system',
                null, null, null, null, null);
        }
        if (resultSessionId) {
            setLocalTranscribing(resultSessionId, false);
        }
        delete pendingRequests[request_id];
        window.updateStatusCounter();
        fetchQueueStatus();
    }
}

function handleNewMessage(data) {
    const { session_id, message } = data;
    if (session_id === currentSessionId) {
        // Display message if it's from current session
        if (message.response) {
            displayMessage('assistant', message.response, message.file_data, message.file_type, message.file_name, message.file_path,
                message.assistant_timestamp || new Date().toISOString(), message.response_time, message.model_used,
                null, null, null, null, message.message_id);
            updateLastVisit(currentSessionId);
        } else if (message.messages) {
            for (const msg of message.messages) {
                displayMessage('assistant', msg.response, msg.file_data, msg.file_type, msg.file_name, msg.file_path,
                    msg.assistant_timestamp, msg.response_time, msg.model_used,
                    null, null, null, null, msg.message_id);
            }
        }
    } else {
        // Not current session, just mark unread
        setNewMessageIndicator(session_id, true);
    }
    // Update sessions list to show unread indicator
    loadSessionsFromServer();
}

function handleTranscribingStatus(data) {
    const { session_id, is_transcribing } = data;
    if (session_id) {
        setLocalTranscribing(session_id, is_transcribing);
    }
}

// Call initSocket when DOM ready
document.addEventListener('DOMContentLoaded', function() {
    initSocket();
});

// Expose socket for debugging if needed
window.socket = socket;