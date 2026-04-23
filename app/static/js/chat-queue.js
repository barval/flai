// app/static/js/chat-queue.js
// Queue status functions

function startSyncInterval() {
    if (window.syncInterval) clearInterval(window.syncInterval);
    console.debug('startSyncInterval: Starting sync interval (2000ms) for session', currentSessionId);
    window.syncInterval = setInterval(() => {
        if (window.IS_RELOADING || window.isSwitchingSession) {
            console.debug('sync interval: Skipping - IS_RELOADING or switching session');
            return;
        }
        console.debug('sync interval: Running sync for session', currentSessionId);
        syncSessionsAndMessages();
    }, 2000);
}

/**
 * Synchronize sessions and messages across multiple clients.
 * Called periodically to keep all clients in sync.
 */
function syncSessionsAndMessages() {
    if (window.IS_RELOADING || window.isSwitchingSession) return;

    console.debug('syncSessionsAndMessages: Starting sync for session', currentSessionId, 'pendingRequests:', Object.keys(pendingRequests).length);

    // Fetch queue status first — this updates lightning bolt indicators
    if (typeof fetchQueueStatus === 'function') {
        fetchQueueStatus();
    }

    // Sync sessions list
    loadSessionsFromServer().then(sessions => {
        if (window.IS_RELOADING || window.isSwitchingSession) return;

        // Check if current session still exists
        if (currentSessionId && !sessions.find(s => s.id === currentSessionId)) {
            console.warn('Current session no longer exists, redirecting to first session');
            if (sessions.length > 0) {
                switchSession(sessions[0].id);
            }
        }
    }).catch(err => console.error('Error syncing sessions:', err));

    // Sync messages for current session
    if (currentSessionId) {
        console.debug('syncSessionsAndMessages: Calling syncMessagesForCurrentSession');
        syncMessagesForCurrentSession();
    } else {
        console.debug('syncSessionsAndMessages: No current session, skipping message sync');
    }
}

/**
 * Sync messages for current session from server
 * Checks for new messages from other clients
 */
function syncMessagesForCurrentSession() {
    if (window.IS_RELOADING || !currentSessionId) {
        console.debug('syncMessages: Skipping - IS_RELOADING or no currentSessionId');
        return;
    }

    // Skip if current session is no longer in sessionsData (likely deleted on server)
    if (!sessionsData[currentSessionId]) {
        return;
    }

    // Get last message timestamp from DOM
    const messagesContainer = document.getElementById('chat-messages');
    const lastMessageEl = messagesContainer ? messagesContainer.lastElementChild : null;

    // If no messages in DOM, skip — loadMessages will handle it when ready
    if (!lastMessageEl || !lastMessageEl.dataset.timestamp) {
        return;
    }

    const lastTimestamp = lastMessageEl.dataset.timestamp;
    console.debug('syncMessages: Last message timestamp from DOM:', lastTimestamp);
    console.debug('syncMessages: Fetching messages from /api/sessions/', currentSessionId, '/messages?since=', encodeURIComponent(lastTimestamp));

    fetch(`/api/sessions/${currentSessionId}/messages?since=${encodeURIComponent(lastTimestamp)}`)
        .then(res => {
            // Handle 404 silently — session was deleted or user lost access
            if (!res.ok) {
                if (res.status === 404) {
                    if (typeof window.loadSessionsFromServer === 'function') window.loadSessionsFromServer();
                }
                return null;
            }
            return res.json();
        })
        .then(data => {
            if (!data) return;
            // Handle both old format (array) and new format (object with messages)
            // Safety check: if data is not an object, treat as empty
            const newMessages = Array.isArray(data) ? data : (data && data.messages ? data.messages : []);
            console.debug('syncMessages: Received', newMessages.length, 'new messages');
            console.debug('syncMessages: Messages:', newMessages.map(m => ({ id: m.id, role: m.role, timestamp: m.timestamp })));
            if (window.IS_RELOADING || !newMessages || newMessages.length === 0) {
                console.debug('syncMessages: No new messages to display');
                return;
            }

            let displayedCount = 0;

            // Display new messages
            for (const msg of newMessages) {
                console.debug('syncMessages: Processing message:', msg.id, msg.role);

                // Clear unread indicator when we receive new messages for current session
                delete newMessageIndicators[currentSessionId];

                // Skip user messages — they are displayed optimistically by displayUserMessage
                // and will be updated with messageId when server responds.
                // Syncing them would cause duplicates.
                if (msg.role === 'user') {
                    console.debug('syncMessages: Skipping user message', msg.id, '(optimistic display)');
                    if (msg.id) displayedMessageIds.add(msg.id);
                    continue;
                }

                // Skip if already displayed (check DOM first)
                if (msg.id) {
                    const existingMsg = document.querySelector(`[data-message-id="${msg.id}"]`);
                    if (existingMsg) {
                        console.debug('syncMessages: Message', msg.id, 'already in DOM, skipping');
                        displayedMessageIds.add(msg.id);
                        continue;
                    }
                }

                // Skip if already in displayedMessageIds Set
                if (msg.id && displayedMessageIds.has(msg.id)) {
                    console.debug('syncMessages: Message', msg.id, 'already in displayedMessageIds, skipping');
                    continue;
                }

                // Display message
                displayedCount++;
                console.debug('syncMessages: Displaying message:', msg.id, msg.role);

                let responseTime = null;
                if (msg.response_time) {
                    if (typeof msg.response_time === 'object') {
                        responseTime = msg.response_time;
                    } else if (!isNaN(parseFloat(msg.response_time))) {
                        responseTime = parseFloat(msg.response_time);
                    }
                }

                window.displayMessage(
                    msg.role,
                    msg.content,
                    msg.file_data,
                    msg.file_type,
                    msg.file_name,
                    msg.file_path,
                    msg.timestamp,
                    responseTime,
                    msg.model_name,
                    msg.mm_time,
                    msg.gen_time,
                    msg.mm_model,
                    msg.gen_model,
                    msg.id
                );
            }

            console.debug('syncMessages: Displayed', displayedCount, 'messages');
        })
        .catch(err => console.error('Error syncing messages:', err));
}

function fetchQueueStatus() {
    if (window.IS_RELOADING) return;
    fetch('/api/queue/status')
        .then(res => {
            if (!res.ok) {
                // Server unavailable — clear all status icons
                Object.keys(sessionQueueInfo).forEach(sid => {
                    sessionQueueInfo[sid] = { processing: false, queued: 0, queue_position: 0, has_transcribing: false };
                });
                updateUIFromQueueStatus();
                return;
            }
            return res.json();
        })
        .then(data => {
            if (window.IS_RELOADING) return;
            if (!data) return;

            // COMPLETELY rebuild sessionQueueInfo from server data only
            // Do NOT use pendingRequests to determine status icons
            const newInfo = {};

            // Start with all known sessions as idle
            Object.keys(sessionsData).forEach(sessionId => {
                newInfo[sessionId] = {
                    processing: false,
                    queued: 0,
                    queue_position: 0,
                    has_transcribing: false
                };
            });

            // Mark the session that is currently being processed by server (lightning bolt)
            let processingSessionId = null;
            if (data.processing) {
                processingSessionId = data.processing.session_id;
                if (!newInfo[processingSessionId]) {
                    newInfo[processingSessionId] = { processing: false, queued: 0, queue_position: 0, has_transcribing: false };
                }
                newInfo[processingSessionId].processing = true;
                if (data.processing.type === 'transcribe_audio' || data.processing.type === 'audio') {
                    newInfo[processingSessionId].has_transcribing = true;
                }
                console.debug('fetchQueueStatus: processing session', processingSessionId);
            }

            // Mark queued sessions (hourglass)
            if (data.queued && data.queued.length > 0) {
                data.queued.forEach(item => {
                    const sessionId = item.session_id;
                    if (sessionId === processingSessionId) return;
                    if (!newInfo[sessionId]) {
                        newInfo[sessionId] = { processing: false, queued: 0, queue_position: 0, has_transcribing: false };
                    }
                    newInfo[sessionId].queued += 1;
                    newInfo[sessionId].queue_position = item.position_info?.position || 999;
                });
            }

            // SAFETY VALVE: If server reports idle (no processing, no queued), clear pendingRequests
            // This fixes stuck lightning bolts when polling was cancelled
            if (!processingSessionId && (!data.queued || data.queued.length === 0)) {
                Object.keys(pendingRequests).forEach(reqId => {
                    delete pendingRequests[reqId];
                });
            }

            // Update global sessionQueueInfo
            sessionQueueInfo = newInfo;

            // Sync localTranscribingSessions with server status
            Object.keys(newInfo).forEach(sessionId => {
                if (newInfo[sessionId].has_transcribing) {
                    localTranscribingSessions[sessionId] = true;
                } else if (localTranscribingSessions[sessionId]) {
                    delete localTranscribingSessions[sessionId];
                }
            });

            // Update sessionsData with queue_info for display
            Object.keys(newInfo).forEach(sessionId => {
                if (sessionsData[sessionId]) {
                    sessionsData[sessionId].queue_info = { ...newInfo[sessionId] };
                }
            });

            updateUIFromQueueStatus();
        })
        .catch(err => console.error('Error fetching queue status:', err));
}

/**
 * Update the sessions list UI based on current sessionQueueInfo
 * Called after fetchQueueStatus or status changes.
 * Uses JSON-based deduplication to avoid full DOM rebuild when
 * nothing meaningful changed (prevents flickering on slow/mobile connections).
 */
function updateUIFromQueueStatus() {
    const sessions = Object.keys(sessionsData).map(id => ({
        id: id,
        title: sessionsData[id].title,
        updated_at: sessionsData[id].updated_at,
        message_count: sessionsData[id].message_count,
        has_unread: (sessionsData[id].has_unread || newMessageIndicators[id]) ? true : false,
        queue_info: sessionQueueInfo[id] || null
    }));

    // Skip full DOM rebuild if sessions data hasn't changed
    const sessionsJson = JSON.stringify(sessions);
    if (window._lastSessionsJson === sessionsJson) {
        // Only update the status counter — sessions list is unchanged
        window.updateStatusCounter();
        return;
    }
    window._lastSessionsJson = sessionsJson;

    if (typeof updateSessionsList === 'function') {
        updateSessionsList(sessions);
    }

    // Update status counter
    window.updateStatusCounter();
}

function setLocalTranscribing(sessionId, isTranscribing) {
    if (!sessionId) {
        console.warn('setLocalTranscribing called with empty sessionId');
        return;
    }

    console.debug('setLocalTranscribing called:', sessionId, isTranscribing);

    if (isTranscribing) {
        localTranscribingSessions[sessionId] = true;
    } else {
        delete localTranscribingSessions[sessionId];
    }

    // Use the standard debounced update instead of immediate full rebuild
    if (typeof updateUIFromQueueStatus === 'function') {
        updateUIFromQueueStatus();
    }

    console.debug('Transcribing flag', isTranscribing ? 'SET' : 'CLEARED', 'for session:', sessionId);
}

// Make function globally accessible
window.setLocalTranscribing = setLocalTranscribing;

window.updateStatusCounter = function() {
    if (window.IS_RELOADING) return;
    fetch('/api/queue/counts')
        .then(response => {
            // Check if response is JSON
            const contentType = response.headers.get('content-type');
            if (!contentType || !contentType.includes('application/json')) {
                console.error('Queue counter returned non-JSON response:', response.status);
                return null;
            }
            return response.json();
        })
        .then(data => {
            if (window.IS_RELOADING || !data) return;
            const counter = document.getElementById('status-counter');
            if (counter) {
                counter.textContent = '📊 ' + data.user_queued + '/' + data.total_queued;
                counter.title = t('your_requests');
                console.debug('updateStatusCounter:', data.user_queued + '/' + data.total_queued);
            }
        })
        .catch(err => console.error('Error updating counter:', err));
};

// Force sync when tab becomes visible again (fixes mobile "stuck" status)
document.addEventListener('visibilitychange', function() {
    if (!document.hidden) {
        console.debug('Tab became visible, forcing immediate sync');
        fetchQueueStatus();
        syncSessionsAndMessages();
    }
});